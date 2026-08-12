"""PaperWise API — 对话式 Agent 后端

Usage: python -m paperwise.api.server → http://localhost:8000
"""

import asyncio, json, time, uuid
from asyncio import Future
from pathlib import Path
from datetime import datetime
from typing import Optional

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEB_STATIC = ROOT / "src" / "paperwise" / "web" / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：启动后台周期任务，关闭时清理。"""
    task = asyncio.create_task(_periodic_flush())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="PaperWise API", version="0.4.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 全局 Session 管理
sessions: dict[str, "AgentSession"] = {}
ws_clients: dict[str, list[WebSocket]] = {}
ws_buffer: dict[str, list[str]] = {}
pending_access: dict[str, asyncio.Future] = {}  # request_id → Future[bool]


# ═══════════ REST API ═══════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.4.1"}


@app.post("/api/sessions")
async def create_session():
    """创建新的对话 Session"""
    tid = uuid.uuid4().hex[:8]
    sessions[tid] = None  # 延迟初始化（等第一个消息或文件上传）
    return {"session_id": tid, "status": "created"}


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
async def upload_paper(sid: str, file: UploadFile = File(...)):
    """在 Session 中上传论文"""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(400, "仅支持 PDF 文件")

    from paperwise.config.settings import get_settings
    ws_dir = get_settings().workspace_dir
    ws_dir.mkdir(parents=True, exist_ok=True)

    up_dir = ws_dir / f"upload_{sid}"
    up_dir.mkdir(exist_ok=True)
    pdf_path = up_dir / file.filename
    pdf_path.write_bytes(await file.read())

    # 初始化 Session
    session = await _ensure_session(sid, ws_dir / f"session_{sid}")

    # 让 Agent 处理论文
    response = await session.handle_file_upload(pdf_path)

    return {"session_id": sid, "response": response, "paper_loaded": True}


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: str, payload: dict):
    """向 Agent 发送消息"""
    msg = payload.get("message", "")
    if not msg.strip():
        raise HTTPException(400, "消息不能为空")

    from paperwise.config.settings import get_settings
    ws_dir = get_settings().workspace_dir
    session = await _ensure_session(sid, ws_dir / f"session_{sid}")

    response = await session.chat(msg)
    return {"session_id": sid, "response": response}


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
    """生成 PPTX"""
    from paperwise.generators.pptx import PPTXGenerator

    pd = Path(paper_dir)
    if not pd.exists():
        raise HTTPException(404, "论文目录不存在")

    meta = {}
    if (pd / "metadata.json").exists():
        meta = json.loads((pd / "metadata.json").read_text(encoding="utf-8"))

    sections = {}
    for sec in ["overview", "motivation", "methodology", "experiments", "critical_analysis", "conclusion"]:
        for sub in ["analysis", "report/sections"]:
            sp = pd / sub / f"{sec}.md"
            if sp.exists():
                sections[sec] = sp.read_text(encoding="utf-8")[:3000]
                break

    gen = PPTXGenerator(pd)
    out = gen.generate({"title": meta.get("title", pd.name), "authors": meta.get("author", ""),
                        "venue": meta.get("subject", ""), "sections": sections, **sections})

    return {"path": out, "slides": len(gen.prs.slides),
            "download_url": f"/api/download?path={out}"}


@app.get("/api/download")
async def download_file(path: str = Query(...)):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, filename=p.name)


# ═══════════ 获取/创建 Session ═══════════

async def _ensure_session(sid: str, workspace: Path) -> "AgentSession":
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
    global_store = settings.workspace_dir / ".paperwise"
    global_store.mkdir(parents=True, exist_ok=True)

    provider = settings.llm_provider
    model = settings.default_model
    llm = LLMClient(provider=provider, model=model)
    tools = ToolRegistry.create_default(workspace)
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

    # 复制 skills 到 workspace 内，让 Agent 可以通过 read_file 访问
    import shutil
    ws_skills = workspace / "skills"
    if skills_dir.exists() and not ws_skills.exists():
        shutil.copytree(skills_dir, ws_skills)
    if not ws_skills.exists():
        ws_skills.mkdir()
        for name in skills.list_skills():
            content = skills.load_skill(name)
            if content:
                (ws_skills / name / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
                (ws_skills / name / "SKILL.md").write_text(content, encoding="utf-8")
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
    dead = []
    for ws in ws_clients.get(sid, []):
        try: await ws.send_text(msg)
        except: dead.append(ws)
    for ws in dead:
        ws_clients[sid].remove(ws)


async def _periodic_flush():
    while True:
        await asyncio.sleep(0.8)
        for sid in list(ws_buffer.keys()):
            if ws_buffer.get(sid):
                combined = "\n".join(ws_buffer[sid])
                ws_buffer[sid] = []
                await _send_ws(sid, combined)


# ═══════════ 静态文件 ═══════════

@app.get("/")
async def index():
    ip = WEB_STATIC / "index.html"
    if ip.exists():
        return HTMLResponse(ip.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PaperWise</h1>")

if WEB_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")


def main():
    import uvicorn
    print("PaperWise 对话式 Agent 启动 → http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
