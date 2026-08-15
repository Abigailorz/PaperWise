"""PaperWise API — 对话式 Agent 后端

Usage: python -m paperwise.api.server → http://localhost:8000
"""

import asyncio, json, time, uuid
from asyncio import Future
from pathlib import Path
from datetime import datetime
from typing import Optional

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEB_STATIC = ROOT / "src" / "paperwise" / "web" / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：启动后台周期任务，关闭时清理。"""
    from paperwise.core.scheduler import Scheduler
    Scheduler.instance().start()
    task = asyncio.create_task(_periodic_flush())
    recommend_task = asyncio.create_task(_recommend_loop())
    try:
        yield
    finally:
        task.cancel()
        recommend_task.cancel()
        await Scheduler.instance().stop()


app = FastAPI(title="PaperWise API", version="0.4.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 全局 Session 管理
sessions: dict[str, "AgentSession"] = {}
session_user: dict[str, str] = {}  # sid → user_id（用户数据隔离 + 推荐推送）
ws_clients: dict[str, list[WebSocket]] = {}
ws_buffer: dict[str, list[str]] = {}
pending_access: dict[str, asyncio.Future] = {}  # request_id → Future[bool]
_last_reco_push: dict[str, float] = {}  # sid → 上次主动推荐时间（monotonic）

RECO_PUSH_INTERVAL = 300  # 同一会话主动推荐的节流间隔（秒）


def _fire_scheduler_event(sid: str, event: dict) -> None:
    """调度器事件 → WebSocket 广播 + 注入会话上下文（主动服务）。

    事件会作为 user 消息追加到会话末尾，Agent 下一轮对话即可感知。
    """
    try:
        asyncio.create_task(_broadcast(sid, "system_event", json.dumps({
            "type": event.get("type", "timer"),
            "message": event.get("message", ""),
        }, ensure_ascii=False)))
    except Exception:
        pass

    from paperwise.core.types import Message, Role
    active = sessions.get(sid)
    if active is not None:
        try:
            active.state.messages.append(Message(
                role=Role.USER,
                content=(
                    "<system_event>\n"
                    f"你收到一个主动事件（{event.get('type', 'timer')}）："
                    f"{event.get('message', '')}\n"
                    "如果该事件与当前任务相关，请主动处理并告知用户。"
                    "</system_event>"
                ),
            ))
        except Exception:
            pass


# ═══════════ REST API ═══════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.4.1"}


@app.post("/api/sessions")
async def create_session(request: Request):
    """创建新的对话 Session（X-User-Id 头用于用户数据隔离）"""
    tid = uuid.uuid4().hex[:8]
    user_id = request.headers.get("X-User-Id", "default")
    sessions[tid] = None  # 延迟初始化（等第一个消息或文件上传）
    session_user[tid] = user_id
    return {"session_id": tid, "user_id": user_id, "status": "created"}


@app.get("/api/sessions")
async def list_sessions():
    """列出历史会话（从磁盘存储恢复元数据）。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.storage import create_storage

    ws_dir = get_settings().workspace_dir
    items = []
    for d in sorted(ws_dir.glob("session_*")):
        sessions_dir = d / ".sessions"
        if not d.is_dir() or not sessions_dir.exists():
            continue
        try:
            store = create_storage("sqlite", sessions_dir)
            keys = store.list_keys("sessions")
        except Exception:
            continue
        for k in keys:
            data = store.get("sessions", k) or {}
            items.append({
                "session_id": data.get("session_id", k),
                "created_at": data.get("created_at", ""),
                "last_active": data.get("last_active", ""),
                "topic": data.get("topic", ""),
                "current_paper": data.get("current_paper", ""),
                "message_count": data.get("message_count", 0),
            })
    items.sort(key=lambda x: x["last_active"], reverse=True)
    return {"sessions": items}


@app.post("/api/sessions/{sid}/upload")
async def upload_paper(sid: str, request: Request, file: UploadFile = File(...)):
    """在 Session 中上传论文"""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(400, "仅支持 PDF 文件")

    from paperwise.config.settings import get_settings
    ws_dir = Path(get_settings().workspace_dir).resolve()
    ws_dir.mkdir(parents=True, exist_ok=True)

    up_dir = ws_dir / f"upload_{sid}"
    up_dir.mkdir(exist_ok=True)
    pdf_path = up_dir / file.filename
    pdf_path.write_bytes(await file.read())

    # 初始化 Session
    user_id = request.headers.get("X-User-Id", "default")
    session = await _ensure_session(sid, ws_dir / f"session_{sid}", user_id=user_id)

    # 让 Agent 处理论文
    response = await session.handle_file_upload(pdf_path)
    asyncio.create_task(_maybe_push_recommendations(sid))

    return {
        "session_id": sid,
        "response": response,
        "paper_loaded": True,
        "paper_dir": session.state.current_paper,
    }


@app.post("/api/sessions/{sid}/arxiv")
async def ingest_arxiv(sid: str, request: Request, payload: dict):
    """通过 arXiv URL / ID 摄入论文。"""
    from paperwise.config.settings import get_settings
    from paperwise.parsers.arxiv import extract_arxiv_id, download_arxiv_pdf

    url = str(payload.get("url", ""))
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        raise HTTPException(400, "无法识别 arXiv 链接/ID（支持 abs/pdf 链接或裸 ID）")

    ws_dir = Path(get_settings().workspace_dir).resolve()
    try:
        pdf = await asyncio.wait_for(
            download_arxiv_pdf(arxiv_id, ws_dir / "arxiv"),
            timeout=150,
        )
    except (asyncio.TimeoutError, Exception) as e:
        if isinstance(e, asyncio.TimeoutError):
            raise HTTPException(504, "arXiv 下载超时，请稍后重试")
        raise HTTPException(502, f"arXiv 下载失败：{type(e).__name__}")
    pdf = Path(pdf).resolve()
    user_id = request.headers.get("X-User-Id", "default")
    session = await _ensure_session(sid, ws_dir / f"session_{sid}", user_id=user_id)
    response = await session.handle_file_upload(pdf)
    asyncio.create_task(_maybe_push_recommendations(sid))

    return {
        "session_id": sid,
        "arxiv_id": arxiv_id,
        "response": response,
        "paper_dir": session.state.current_paper,
    }


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: str, request: Request, payload: dict):
    """向 Agent 发送消息"""
    msg = payload.get("message", "")
    if not msg.strip():
        raise HTTPException(400, "消息不能为空")

    from paperwise.config.settings import get_settings
    ws_dir = Path(get_settings().workspace_dir).resolve()
    user_id = request.headers.get("X-User-Id", "default")
    session = await _ensure_session(sid, ws_dir / f"session_{sid}", user_id=user_id)

    response = await session.chat(msg)
    # 记忆更新后，事件驱动地尝试主动推荐（内部有节流与缓存）
    asyncio.create_task(_maybe_push_recommendations(sid))
    return {"session_id": sid, "response": response}


@app.post("/api/sessions/{sid}/timer")
async def set_session_timer(sid: str, payload: dict):
    """为会话设置主动定时提醒（到期后注入 Agent 上下文并广播）。"""
    from paperwise.core.scheduler import Scheduler

    seconds = max(1, int(payload.get("seconds", 60)))
    message = str(payload.get("message", "定时提醒：请检查你的任务进度"))
    scheduler = Scheduler.instance()
    timer_id = scheduler.add_timer(
        seconds, message, sid,
        callback=lambda ev: _fire_scheduler_event(sid, ev),
    )
    return {
        "timer_id": timer_id,
        "seconds": seconds,
        "message": message,
        "scheduled_at": datetime.now().strftime("%H:%M:%S"),
    }


@app.get("/api/sessions/{sid}/history")
async def get_history(sid: str):
    """获取对话历史"""
    if sid not in sessions or sessions[sid] is None:
        return {"messages": []}

    session = sessions[sid]
    msgs = []
    for m in session.state.messages:
        if m.role.value in ("user", "assistant"):
            msgs.append({
                "role": m.role.value,
                "content": (m.content or "")[:2000],
            })
    return {"session_id": sid, "messages": msgs[-50:]}


@app.post("/api/generate/pptx")
async def generate_pptx(paper_dir: str = Query(...)):
    """生成 PPTX（LLM 内容 + 确定性渲染）。"""
    from paperwise.config.settings import get_settings
    from paperwise.core.llm_client import LLMClient
    from paperwise.generators.slides import (
        SlideContentBuilder, SlideDeckRenderer, build_fallback_slides,
    )

    pd = Path(paper_dir)
    if not pd.exists():
        raise HTTPException(404, "论文目录不存在")

    meta = {}
    if (pd / "metadata.json").exists():
        meta = json.loads((pd / "metadata.json").read_text(encoding="utf-8"))

    sections = {}
    for sec in ["overview", "motivation", "methodology", "experiments",
                "critical_analysis", "related_work", "conclusion"]:
        for sub in ["analysis", "report/sections"]:
            sp = pd / sub / f"{sec}.md"
            if sp.exists():
                sections[sec] = sp.read_text(encoding="utf-8")[:8000]
                break

    paper_text = ""
    if (pd / "text.md").exists():
        paper_text = (pd / "text.md").read_text(encoding="utf-8", errors="replace").strip()

    title = meta.get("title", pd.name)
    deck = None
    try:
        s = get_settings()
        llm = LLMClient(provider=s.llm_provider, model=s.default_model)
        deck = await SlideContentBuilder(llm).build(
            title=title, paper_text=paper_text, report_sections=sections,
        )
    except Exception:
        deck = None
    if deck is None:
        deck = build_fallback_slides({
            "title": title,
            "authors": meta.get("author", ""),
            "venue": meta.get("subject", ""),
            "year": meta.get("year", ""),
            "sections": sections,
            "overview": paper_text,
        })

    out = pd / "presentation" / "slides.pptx"
    renderer = SlideDeckRenderer(base_dir=pd)
    path = renderer.render(deck, str(out))

    from paperwise.generators.pptx_skill import detect_paper_type
    paper_type = detect_paper_type(paper_text, title)

    return {"path": path, "slides": len(renderer.prs.slides),
            "skill": "nature-paper2ppt", "paper_type": paper_type,
            "download_url": f"/api/download?path={path}"}


@app.get("/api/interests")
async def get_interests(request: Request):
    """返回从记忆中自动学习的兴趣画像（无需手动填写研究方向）。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.user_memory import UserMemory
    from paperwise.recommender import PaperRecommender

    user_id = request.headers.get("X-User-Id", "default")
    ws_dir = get_settings().workspace_dir
    mem = UserMemory(ws_dir / ".paperwise" / user_id / "memory")
    recommender = PaperRecommender(ws_dir, memory=mem)
    profile = recommender.build_interest_profile(user_id)
    return {"profile": profile}


@app.get("/api/recommend")
async def recommend_papers(request: Request, limit: int = 5):
    """获取与用户研究方向相关的近期论文（arXiv）。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.user_memory import UserMemory
    from paperwise.recommender import PaperRecommender

    user_id = request.headers.get("X-User-Id", "default")
    ws_dir = get_settings().workspace_dir
    mem = UserMemory(ws_dir / ".paperwise" / user_id / "memory")
    recommender = PaperRecommender(ws_dir, memory=mem)
    result = await recommender.recommend(user_id=user_id, limit=min(limit, 10))
    return result


@app.get("/api/paper/sections")
async def get_sections(paper_dir: str = Query(...)):
    """获取解析后论文的各报告章节内容（供编辑/预览）。"""
    pd = Path(paper_dir)
    if not pd.exists():
        raise HTTPException(404, "论文目录不存在")

    sections = {}
    for sec in ["overview", "motivation", "methodology", "experiments",
                "critical_analysis", "related_work", "conclusion"]:
        sp = pd / "report" / "sections" / f"{sec}.md"
        if sp.exists():
            sections[sec] = sp.read_text(encoding="utf-8")
    return {"paper_dir": str(pd), "sections": sections}


@app.post("/api/paper/sections")
async def save_section(payload: dict):
    """保存某个报告章节的编辑内容。"""
    allowed = {"overview", "motivation", "methodology", "experiments",
               "critical_analysis", "related_work", "conclusion"}
    paper_dir = Path(payload.get("paper_dir", ""))
    section = payload.get("section", "")
    content = payload.get("content", "")
    if section not in allowed:
        raise HTTPException(400, f"非法章节名: {section}")
    if not paper_dir.exists():
        raise HTTPException(404, "论文目录不存在")

    sp = paper_dir / "report" / "sections" / f"{section}.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(content, encoding="utf-8")
    return {"saved": True, "section": section, "path": str(sp)}


@app.get("/api/memory")
async def list_memory(request: Request):
    """列出用户记忆卡（Advanced JSON Cards）。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.user_memory import UserMemory
    user_id = request.headers.get("X-User-Id", "default")
    mem = UserMemory(get_settings().workspace_dir / ".paperwise" / user_id / "memory")
    cards = mem.query(limit=200)
    return {"cards": [c.to_dict() for c in cards]}


@app.delete("/api/memory/{card_id}")
async def delete_memory(card_id: str, request: Request):
    """删除一条记忆卡。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.user_memory import UserMemory
    user_id = request.headers.get("X-User-Id", "default")
    mem = UserMemory(get_settings().workspace_dir / ".paperwise" / user_id / "memory")
    ok = mem.forget(card_id)
    return {"deleted": ok, "card_id": card_id}


@app.get("/api/download")
async def download_file(path: str = Query(...)):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, filename=p.name)


@app.get("/api/exists")
async def file_exists(path: str = Query(...)):
    """返回文件是否存在（前端生成 PPT 后据此判断是否提供下载）。"""
    return {"exists": Path(path).exists()}


@app.get("/api/eval/results")
async def eval_results():
    """返回 Agent 能力测试的历史结果（Pass@k / Pass^k）。"""
    from paperwise.config.settings import get_settings
    ws = get_settings().workspace_dir / "test_runs"
    items = []
    if ws.exists():
        for f in sorted(ws.glob("test_results_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["file"] = f.name
                data["timestamp"] = f.stem.replace("test_results_", "")
                items.append(data)
            except Exception:
                continue
    return {"runs": items}


# ═══════════ 获取/创建 Session ═══════════

async def _ensure_session(sid: str, workspace: Path,
                          user_id: str = "default") -> "AgentSession":
    """获取已有 Session 或创建新 Session。"""
    if sid in sessions and sessions[sid] is not None:
        return sessions[sid]

    from paperwise.config.settings import get_settings
    from paperwise.core.llm_client import LLMClient
    from paperwise.tools.registry import ToolRegistry
    from paperwise.harness.harness import Harness
    from paperwise.memory.user_memory import UserMemory
    from paperwise.memory.knowledge_base import KnowledgeBase
    from paperwise.core.session import AgentSession
    from paperwise.skills.loader import SkillLoader

    settings = get_settings()
    workspace.mkdir(parents=True, exist_ok=True)

    # 全局共享记忆和知识库（跨 Session 持久化）
    # 用户数据隔离：每个 user_id 独立的 memory / kb 目录
    global_store = settings.workspace_dir / ".paperwise" / user_id
    global_store.mkdir(parents=True, exist_ok=True)

    provider = settings.llm_provider
    model = settings.default_model
    llm = LLMClient(provider=provider, model=model)
    tools = ToolRegistry.create_default(workspace)
    # 给 generate_pptx 工具注入 LLM，使其能生成结构化 slide 内容
    try:
        tools.get("generate_pptx").llm_client = llm
    except Exception:
        pass
    harness = Harness(workspace, max_steps=settings.max_steps)
    harness.context_manager.llm = llm
    memory = UserMemory(global_store / "memory")     # 跨 Session 共享
    kb = KnowledgeBase(global_store / "kb")           # 跨 Session 共享
    kb.set_llm_client(llm)
    # 周期性记忆整合（间隔内自动跳过）
    try:
        mem_report = memory.maybe_consolidate()
        if not mem_report.get("skipped"):
            print(f"[Memory] Consolidate: {mem_report}")
    except Exception:
        pass
    # Skills 目录：使用项目根目录的 skills/
    skills_dir = ROOT / "skills"
    if not skills_dir.exists():
        skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
    skills = SkillLoader(skills_dir)

    # 复制 skills 到 workspace 内，让 Agent 可以通过 read_file 访问（含 _shared 依赖）
    import shutil
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True, exist_ok=True)
    if skills_dir.exists():
        for item in skills_dir.iterdir():
            dst = ws_skills / item.name
            if not dst.exists():
                shutil.copytree(item, dst)
    # 配置 API embeddings（如果提供了 key）
    if settings.embedding_api_key:
        kb.retriever.dense.set_api_embedder(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
        )
        print(f"[RAG] Using API embeddings: {settings.embedding_model}")

    # 将 KB 注册为 Agent 工具（Agentic RAG）
    from paperwise.tools.base import BaseTool, ToolDefinition
    from paperwise.core.types import ToolRisk

    class KBSearchTool(BaseTool):
        def __init__(self, kb_instance, ws):
            super().__init__(ws)
            self.kb = kb_instance
        @property
        def definition(self):
            d = self.kb.get_search_tool_description()
            return ToolDefinition(name=d["name"], description=d["description"],
                                  parameters=d["parameters"], risk=ToolRisk.LOW)
        async def execute(self, query: str, top_k: int = 5, search_chunks: bool = False):
            results = (self.kb.search_chunks(query, top_k=top_k) if search_chunks
                      else self.kb.search(query, top_k=top_k))
            if not results:
                return "未在知识库中找到相关信息。"
            items = [f"**{d.metadata.get('title', d.id)}**: {d.content[:300]}..."
                     for d in results[:top_k]]
            return "\n\n---\n".join(items)

    tools.register(KBSearchTool(kb, workspace))

    # 注入系统调度器：set_timer / monitor_shell 到期后主动通知会话
    from paperwise.core.scheduler import Scheduler
    scheduler = Scheduler.instance()
    for tool_name in ("set_timer", "monitor_shell"):
        tool = tools.get(tool_name)
        tool._scheduler = scheduler
        tool._session_id = sid
        tool._scheduler_callback = lambda ev: _fire_scheduler_event(sid, ev)

    # 设置文件访问确认回调（Web 模式：WebSocket + Future）
    async def confirm_file_access(question: str, detail: str) -> bool:
        req_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        pending_access[req_id] = future
        await _broadcast(sid, "file_access_request", json.dumps({
            "request_id": req_id,
            "question": question,
            "detail": detail,
        }, ensure_ascii=False))
        try:
            result = await asyncio.wait_for(future, timeout=120.0)
            return result
        except asyncio.TimeoutError:
            pending_access.pop(req_id, None)
            return False

    # 注入回调到 request_file_access 工具
    access_tool = tools.get("request_file_access")
    access_tool._user_confirm = confirm_file_access

    # 尝试从磁盘恢复历史会话（服务重启后仍可继续对话）
    restored = None
    try:
        restored = AgentSession.load(
            sid, workspace, llm, tools, harness,
            memory=memory, knowledge_base=kb, skills=skills,
        )
    except Exception:
        restored = None

    if restored is not None:
        session = restored
        session_user[sid] = user_id
        print(f"[Session] Restored session {sid} from disk")
    else:
        session = AgentSession(
            workspace=workspace,
            llm_client=llm,
            tools=tools,
            harness=harness,
            memory=memory,
            knowledge_base=kb,
            skills=skills,
            session_id=sid,
        )
        session_user[sid] = user_id

    # 注册事件 → WebSocket 广播
    def on_event(etype, detail):
        asyncio.create_task(_broadcast(sid, etype, detail))

    session.on_event(on_event)
    sessions[sid] = session
    return session


# ═══════════ WebSocket ═══════════

@app.websocket("/ws/{task_id}")
async def ws_trajectory(ws: WebSocket, task_id: str):
    await ws.accept()
    ws_clients.setdefault(task_id, []).append(ws)
    ws_buffer.setdefault(task_id, [])
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
            elif data.startswith("{"):
                # 尝试解析为 JSON（文件访问响应等）
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "file_access_response":
                        req_id = msg.get("request_id", "")
                        approved = msg.get("approved", False)
                        future = pending_access.pop(req_id, None)
                        if future and not future.done():
                            future.set_result(approved)
                except json.JSONDecodeError:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients[task_id].remove(ws)


async def _broadcast(sid: str, etype: str, detail: str):
    if sid not in ws_clients:
        return
    msg = json.dumps({"type": etype, "detail": detail,
                       "time": datetime.now().strftime("%H:%M:%S")}, ensure_ascii=False)
    if etype == "thinking":
        ws_buffer.setdefault(sid, []).append(msg)
    else:
        buf = ws_buffer.get(sid, [])
        if buf:
            combined = "\n".join(buf)
            ws_buffer[sid] = []
            await _send_ws(sid, combined)
        await _send_ws(sid, msg)


async def _send_ws(sid: str, msg: str):
    """向会话的所有 WS 客户端发送消息。

    带超时保护：失效/僵死的连接会在超时后被剔除，
    避免 send_text 永久阻塞事件循环（生产事故防护）。
    """
    dead = []
    for ws in ws_clients.get(sid, []):
        try:
            await asyncio.wait_for(ws.send_text(msg), timeout=5)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            ws_clients[sid].remove(ws)
        except ValueError:
            pass


async def _periodic_flush():
    while True:
        await asyncio.sleep(0.8)
        for sid in list(ws_buffer.keys()):
            if ws_buffer.get(sid):
                combined = "\n".join(ws_buffer[sid])
                ws_buffer[sid] = []
                await _send_ws(sid, combined)


async def _push_recommendations_for_session(sid: str) -> bool:
    """为单个活跃会话生成并推送论文推荐。返回是否成功推送。"""
    from paperwise.config.settings import get_settings
    from paperwise.memory.user_memory import UserMemory
    from paperwise.recommender import PaperRecommender

    session = sessions.get(sid)
    if session is None:
        return False
    user_id = session_user.get(sid, "default")
    ws_dir = get_settings().workspace_dir
    try:
        mem = UserMemory(ws_dir / ".paperwise" / user_id / "memory")
        recommender = PaperRecommender(ws_dir, memory=mem)
        result = await recommender.recommend(user_id=user_id, limit=3)
    except Exception:
        return False
    if not result.get("papers"):
        return False

    # 1. 注入会话上下文：Agent 下一轮对话即可主动提及
    try:
        from paperwise.core.types import Message, Role
        lines = [
            f"- [{p.get('title', '')[:60]}]({p.get('url', '')}) "
            f"[匹配度 {p.get('score', 0):.0%}]"
            for p in result["papers"]
        ]
        session.state.messages.append(Message(
            role=Role.USER,
            content=(
                "<paper_recommendations>\n"
                f"为你找到 {len(result['papers'])} 篇可能与你的研究兴趣相关的新论文：\n"
                + "\n".join(lines) +
                "\n如果用户感兴趣，主动提议解读其中一篇，并给出推荐理由。"
                "</paper_recommendations>"
            ),
        ))
    except Exception:
        pass

    # 2. WebSocket 广播：前端展示推荐横幅
    await _broadcast(sid, "paper_recommendations", json.dumps({
        "papers": result["papers"],
        "topics": result.get("topics", []),
    }, ensure_ascii=False))
    return True


async def _maybe_push_recommendations(sid: str) -> None:
    """事件驱动的主动推荐：记忆更新后按节流间隔触发一次。"""
    now = time.monotonic()
    if now - _last_reco_push.get(sid, 0) < RECO_PUSH_INTERVAL:
        return
    _last_reco_push[sid] = now
    try:
        await _push_recommendations_for_session(sid)
    except Exception:
        pass


async def _run_daily_recommendations() -> None:
    """为所有活跃会话生成并推送论文推荐（每日定时兜底）。"""
    for sid in list(sessions.keys()):
        try:
            await _push_recommendations_for_session(sid)
        except Exception:
            continue


async def _recommend_loop(first_delay: int = 120) -> None:
    """主动推荐循环：启动 2 分钟后首次推送，之后每 24 小时一次。"""
    await asyncio.sleep(first_delay)
    while True:
        try:
            await _run_daily_recommendations()
        except Exception:
            import logging
            logging.getLogger("paperwise").exception("Daily recommendations failed")
        await asyncio.sleep(24 * 3600)


# ═══════════ 静态文件 ═══════════

@app.get("/")
async def index():
    ip = WEB_STATIC / "index.html"
    if ip.exists():
        return HTMLResponse(ip.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PaperWise</h1>")


@app.get("/dashboard")
async def dashboard():
    """评估 Dashboard（Pass@k / Pass^k 可视化）。"""
    dp = WEB_STATIC / "dashboard.html"
    if dp.exists():
        return HTMLResponse(dp.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PaperWise Dashboard</h1>")

if WEB_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")


def main():
    import uvicorn
    print("PaperWise 对话式 Agent 启动 → http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
