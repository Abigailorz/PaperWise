"""生成工具 — generate_pptx（LLM 内容 + 确定性渲染）"""

import json
from pathlib import Path

from paperwise.tools.base import BaseTool, ToolDefinition
from paperwise.core.types import ToolRisk


class GeneratePPTXTool(BaseTool):
    """把已解析的论文生成为 .pptx 演示文稿。

    优先用 LLM 生成 slide 内容（结构化大纲），再交给确定性渲染器排版；
    无 LLM 或 LLM 失败时回退到确定性要点提取。
    """

    def __init__(self, workspace: Path, llm_client=None):
        super().__init__(workspace)
        self.llm_client = llm_client

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_pptx",
            description=(
                "Generate a real academic presentation (.pptx) from a parsed paper. "
                "Pass the absolute path to the parsed paper directory (the directory "
                "containing text.md, shown as 解析位置 in the paper_loaded message). "
                "The paper must already be parsed. DO NOT use for: writing "
                "Markdown/Marp slides (this produces a real .pptx file)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paper_dir": {
                        "type": "string",
                        "description": "Absolute path to the parsed paper directory (contains text.md).",
                    },
                },
                "required": ["paper_dir"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, paper_dir: str) -> str:
        from paperwise.harness.security import check_path_dangerous
        from paperwise.generators.slides import (
            SlideContentBuilder, SlideDeckRenderer, build_fallback_slides,
        )

        pd = Path(paper_dir)
        if check_path_dangerous(paper_dir):
            return f"[Error] 拒绝访问受保护的系统路径：{paper_dir}"
        if not pd.exists():
            return f"[Error] 论文目录不存在：{paper_dir}"
        if not (pd / "text.md").exists():
            return "[Error] 该目录下没有 text.md，请先解析论文再生成 PPT"

        meta = {}
        mp = pd / "metadata.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        paper_text = (pd / "text.md").read_text(encoding="utf-8", errors="replace")
        sections = self._collect_sections(pd)

        title = meta.get("title") or pd.name
        deck = None
        if self.llm_client is not None:
            try:
                deck = await SlideContentBuilder(self.llm_client).build(
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

        out = (pd / "presentation" / "slides.pptx")
        renderer = SlideDeckRenderer(base_dir=pd)
        path = renderer.render(deck, str(out))
        n = len(renderer.prs.slides)
        return f"已生成 {n} 页演示文稿：{path}"

    @staticmethod
    def _collect_sections(pd: Path) -> dict:
        sections = {}
        for sec in ("overview", "motivation", "methodology", "experiments",
                    "critical_analysis", "related_work", "conclusion"):
            for sub in ("analysis", "report/sections"):
                sp = pd / sub / f"{sec}.md"
                if sp.exists():
                    sections[sec] = sp.read_text(encoding="utf-8", errors="replace")[:8000]
                    break
        if not sections.get("overview") and (pd / "text.md").exists():
            sections["overview"] = (pd / "text.md").read_text(encoding="utf-8", errors="replace")[:4000]
        return sections
