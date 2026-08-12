"""论文上传流程测试 — 快速返回 + 后台 Sidecar/索引任务被调度"""

import asyncio

import fitz
from pathlib import Path

from paperwise.core.session import AgentSession
from paperwise.harness.harness import Harness
from paperwise.tools.registry import ToolRegistry


class MockLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages=None, **kwargs):
        self.calls += 1
        return type("Resp", (), {
            "content": '{"suspicious": false, "severity": "none", "reason": "ok"}',
        })()

    async def chat_stream(self, **kwargs):
        yield type("Event", (), {"type": "done"})()


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Attention Is All You Need")
    page.insert_text((72, 90), "We propose the Transformer architecture.")
    page.insert_text((72, 108), "Experiments show strong results.")
    doc.save(str(path))
    doc.close()


def test_upload_returns_promptly_and_schedules_background(tmp_path):
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf)

    ws = tmp_path / "ws"
    tools = ToolRegistry.create_default(ws)
    harness = Harness(ws)
    llm = MockLLM()
    sess = AgentSession(
        workspace=ws, llm_client=llm, tools=tools, harness=harness,
        memory=None, knowledge_base=None, skills=None,
    )

    async def run():
        response = await sess.handle_file_upload(pdf)
        # 给后台 Sidecar 任务留出执行时间
        await asyncio.sleep(0.3)
        return response

    response = asyncio.run(run())

    assert "我已经解析了这篇论文" in response
    assert sess.state.paper_parsed is True
    assert llm.calls >= 1, "后台 Sidecar 审查任务应被调度执行"
    # 解析产物目录应加入读取白名单（Agent 可读 text.md）
    paper_dir = Path(sess.state.current_paper)
    assert sess.tools.get("read_file").has_read_access(paper_dir / "text.md")
