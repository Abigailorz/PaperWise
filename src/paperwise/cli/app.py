"""PaperWise CLI — AI 学术论文智能解读

Usage:
    paperwise parse <pdf_path> [--output-dir <dir>]
    paperwise analyze <pdf_path> [--model <model>] [--provider <provider>] [--output <dir>]
    paperwise generate pptx <paper_dir> [--output <path>]
    paperwise evaluate <report_path> <paper_dir>
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="paperwise",
    help="AI-powered academic paper interpretation system",
    add_completion=False,
)
console = Console()


@app.callback()
def callback():
    """PaperWise — AI 学术论文解读系统"""


@app.command()
def parse(
    pdf_path: Path = typer.Argument(..., help="PDF 论文路径"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o", help="输出目录"),
):
    """解析论文 PDF — 提取文本、图表、表格和公式"""
    from paperwise.parsers.pdf_parser import PDFParser

    console.print(Panel(f"Parsing: [bold]{pdf_path.name}[/bold]", title="PaperWise Parse"))

    parser = PDFParser()
    try:
        parsed = parser.parse(str(pdf_path), str(output_dir) if output_dir else None)

        # 显示结果
        table = Table(title="Parse Results")
        table.add_column("Item", style="cyan")
        table.add_column("Details", style="green")
        table.add_row("Paper ID", parsed.paper_id)
        table.add_row("Title", parsed.metadata.get("title", "N/A")[:80])
        table.add_row("Pages", str(parsed.metadata.get("page_count", "N/A")))
        table.add_row("Text Lines", str(parsed.structure.get("total_lines", 0)))
        table.add_row("Figures", str(len(parsed.figures)))
        table.add_row("Tables", str(len(parsed.tables)))
        table.add_row("Formulas", str(len(parsed.formulas)))
        table.add_row("References", str(len(parsed.references)))
        table.add_row("Output Dir", str(parsed.output_dir))

        console.print(table)
        console.print("\n[green]✓[/green] PDF parsed successfully!")

    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def analyze(
    pdf_path: Path = typer.Argument(..., help="PDF 论文路径"),
    model: str = typer.Option("deepseek-chat", "--model", "-m", help="LLM 模型"),
    provider: str = typer.Option("deepseek", "--provider", "-p", help="LLM 提供商 (deepseek|moonshot|openai)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="输出目录"),
):
    """分析论文 — 使用 AI Agent 生成深度解读报告"""
    from paperwise.config.settings import get_settings
    from paperwise.core.llm_client import LLMClient
    from paperwise.core.agent import Agent, AgentConfig
    from paperwise.tools.registry import ToolRegistry
    from paperwise.harness.harness import Harness
    from paperwise.parsers.pdf_parser import PDFParser
    from paperwise.generators.report import ReportGenerator

    settings = get_settings()

    console.print(Panel(
        f"Analyzing: [bold]{pdf_path.name}[/bold]\n"
        f"Model: {model} ({provider})",
        title="PaperWise Analyze"
    ))

    async def run_analysis():
        # Step 1: Parse PDF
        console.print("[cyan]→[/cyan] Parsing PDF...")
        parser = PDFParser()
        parsed = parser.parse(str(pdf_path))

        console.print(f"  [green]✓[/green] Parsed: {len(parsed.structure.get('sections', []))} sections, "
                       f"{len(parsed.figures)} figures, {len(parsed.tables)} tables")

        # Step 2: Set up full AgentSession (Skills + Memory + KB)
        console.print("[cyan]→[/cyan] Initializing Agent (Skills + Memory + KB)...")
        from paperwise.core.session import AgentSession
        from paperwise.memory.user_memory import UserMemory
        from paperwise.memory.knowledge_base import KnowledgeBase
        from paperwise.skills.loader import SkillLoader

        llm = LLMClient(provider=provider, model=model)
        tools = ToolRegistry.create_default(parsed.output_dir)
        # 将 PDF 所在目录加入读取白名单（Agent 可能需要访问原始文件）
        tools.allow_read_path(pdf_path.parent)
        harness = Harness(parsed.output_dir, max_steps=settings.max_steps)
        harness.context_manager.llm = llm
        # 全局共享记忆和知识库（跨 Session / CLI 运行持久化）
        global_store = settings.workspace_dir / ".paperwise"
        global_store.mkdir(parents=True, exist_ok=True)
        memory = UserMemory(global_store / "memory")
        kb = KnowledgeBase(global_store / "kb")
        kb.set_llm_client(llm)
        try:
            mem_report = memory.maybe_consolidate()
            if not mem_report.get("skipped"):
                console.print(f"  [dim]记忆整合完成: {mem_report}[/dim]")
        except Exception:
            pass
        # Skills 目录显式指定
        _root = Path(__file__).resolve().parent.parent.parent.parent
        skills = SkillLoader(_root / "skills")
        # 复制 skills 到 workspace 内，让 Agent 可以通过 read_file 访问
        import shutil
        ws_skills = parsed.output_dir / "skills"
        if (_root / "skills").exists() and not ws_skills.exists():
            shutil.copytree(_root / "skills", ws_skills)

        # 注册 KB 搜索工具给 Agent
        from paperwise.tools.base import BaseTool, ToolDefinition
        from paperwise.core.types import ToolRisk
        class KBSearchTool(BaseTool):
            def __init__(self, kb_i, ws): super().__init__(ws); self.kb = kb_i
            @property
            def definition(self):
                d = self.kb.get_search_tool_description()
                return ToolDefinition(name=d["name"], description=d["description"],
                                      parameters=d["parameters"], risk=ToolRisk.LOW)
            async def execute(self, query, top_k=5, search_chunks=False):
                results = (self.kb.search_chunks(query, top_k=top_k) if search_chunks
                          else self.kb.search(query, top_k=top_k))
                return "\n\n---\n".join(f"**{d.metadata.get('title', d.id)}**: {d.content[:300]}..."
                                        for d in results[:top_k]) if results else "未找到相关信息。"
        tools.register(KBSearchTool(kb, parsed.output_dir))

        session = AgentSession(
            workspace=parsed.output_dir, llm_client=llm, tools=tools,
            harness=harness, memory=memory, knowledge_base=kb, skills=skills,
            session_id=parsed.paper_id,
        )
        # 注入 skill_loader 到 skill 工具
        tools.set_skill_loader(skills)

        # 设置文件访问确认回调（CLI 模式：终端输入）
        async def confirm_file_access(question: str, detail: str) -> bool:
            console.print(f"\n[bold yellow]🔐 {question}[/bold yellow]")
            console.print(f"[dim]{detail}[/dim]")
            import asyncio as _asyncio
            try:
                answer = await _asyncio.to_thread(
                    input, "  允许？(y/n): "
                )
            except (EOFError, KeyboardInterrupt):
                return False
            return answer.strip().lower() in ("y", "yes", "是", "允许")

        # 注入到 request_file_access 工具
        access_tool = tools.get("request_file_access")
        access_tool._user_confirm = confirm_file_access

        def on_event(event_type, detail):
            if "thinking" in event_type: console.print(f"  [dim]{detail[:120]}[/dim]")
            elif event_type == "tool_start": console.print(f"  [yellow]🔧[/yellow] {detail}")
            elif event_type == "tool_end": console.print(f"  [green]✓[/green] {detail}")
            elif event_type == "retry": console.print(f"  [yellow]↻[/yellow] {detail}")
            elif event_type == "paper_loaded": console.print(f"  [green]📄[/green] {detail}")
            elif event_type == "kb_hit": console.print(f"  [blue]📚[/blue] {detail}")
        session.on_event(on_event)

        # Step 3: Process paper through session
        console.print("[cyan]→[/cyan] Agent analyzing paper...")
        console.print("─" * 60)
        response = await session.handle_file_upload(pdf_path)
        console.print(f"\n[bold]{response}[/bold]\n")

        # Step 4: Generate report
        report_gen = ReportGenerator(parsed.output_dir)
        result = await session.chat(report_gen.get_report_task(str(parsed.output_dir)))

        console.print("─" * 60)
        console.print(f"\n[green]✓[/green] Complete")
        console.print(f"  Output: {parsed.output_dir / 'report' / 'report.md'}")
        console.print(f"  Memory: {memory.stats()['total']} items stored")
        console.print(f"  KB: {kb.stats()['documents']} docs indexed")
        return result, parsed

    try:
        asyncio.run(run_analysis())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def pipeline(
    pdf_path: Path = typer.Argument(..., help="PDF 论文路径"),
    model: str = typer.Option("deepseek-chat", "--model", "-m", help="LLM 模型"),
    provider: str = typer.Option("deepseek", "--provider", "-p", help="LLM 提供商"),
    max_review_rounds: int = typer.Option(3, "--max-review-rounds", help="最大审核轮次"),
):
    """端到端流水线 — 解析 → 分析 → 报告 → 对抗式审核 → 修订（revise-until-pass）"""
    from paperwise.config.settings import get_settings
    from paperwise.core.llm_client import LLMClient
    from paperwise.agents.orchestrator import AgentOrchestrator
    from paperwise.parsers.pdf_parser import PDFParser

    console.print(Panel(
        f"Pipeline: [bold]{pdf_path.name}[/bold]\n"
        f"Model: {model} ({provider}) | Max review rounds: {max_review_rounds}",
        title="PaperWise Pipeline"
    ))

    async def run_pipeline():
        settings = get_settings()
        parser = PDFParser()
        console.print("[cyan]→[/cyan] Parsing PDF...")
        parsed = parser.parse(str(pdf_path))
        console.print(f"  [green]✓[/green] Parsed → {parsed.output_dir}")

        llm = LLMClient(provider=provider, model=model)
        orchestrator = AgentOrchestrator(
            llm_client=llm, workspace=parsed.output_dir, model=model,
            max_steps_per_agent=settings.max_steps,
        )

        def on_event(etype, detail):
            if etype in ("agent_start", "pipeline"):
                console.print(f"  [cyan]▶[/cyan] {detail}")
            elif etype == "review_round":
                console.print(f"\n  [magenta]🔍[/magenta] {detail}")
            elif etype == "review_revise":
                console.print(f"  [yellow]✏️[/yellow] {detail}")
            elif etype == "review_done":
                console.print(f"  [green]✅[/green] {detail}")
            elif etype == "review_manual":
                console.print(f"  [red]⚠[/red] {detail}")
            elif etype == "agent_done":
                console.print(f"  [green]✓[/green] {detail}")
            elif etype == "agent_error":
                console.print(f"  [red]✗[/red] {detail}")

        orchestrator.on_event(on_event)
        result = await orchestrator.run_paper_analysis(
            parsed.output_dir, max_review_rounds=max_review_rounds,
        )

        console.print("─" * 60)
        status_icon = "✅" if result["status"] == "passed" else (
            "⚠" if result["status"] == "needs_manual_review" else "❌")
        console.print(f"\n{status_icon} Pipeline status: [bold]{result['status']}[/bold]")
        console.print(f"  Review record: {result.get('record_path', 'N/A')}")
        console.print(f"  Report: {parsed.output_dir / 'report' / 'report.md'}")

    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def fetch_arxiv(
    arxiv_id: str = typer.Argument(..., help="arXiv ID 或 URL，如 2401.12345"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o",
                                               help="下载目录（默认 workspace/arxiv）"),
):
    """从 arXiv 下载论文 PDF"""
    from paperwise.config.settings import get_settings
    from paperwise.parsers.arxiv import extract_arxiv_id, download_arxiv_pdf

    console.print(Panel(f"Fetching arXiv: [bold]{arxiv_id}[/bold]", title="PaperWise Fetch"))

    async def run_fetch():
        aid = extract_arxiv_id(arxiv_id)
        if not aid:
            console.print(f"[red]✗ 无法识别 arXiv ID: {arxiv_id}[/red]")
            raise typer.Exit(code=1)
        dest_dir = output_dir or (get_settings().workspace_dir / "arxiv")
        console.print(f"[cyan]→[/cyan] Downloading https://arxiv.org/pdf/{aid} ...")
        path = await download_arxiv_pdf(aid, dest_dir)
        console.print(f"[green]✓[/green] Saved to {path}")

    try:
        asyncio.run(run_fetch())
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def generate(
    target: str = typer.Argument(..., help="生成目标: pptx"),
    paper_dir: Path = typer.Argument(..., help="解析后的论文目录"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="输出 PPTX 路径"),
):
    """生成演示文稿 PPTX"""
    import json
    from paperwise.generators.pptx import PPTXGenerator

    console.print(Panel(f"Generating PPTX: [bold]{paper_dir.name}[/bold]", title="PaperWise Generate"))

    paper_dir = Path(paper_dir)
    try:
        metadata = {}
        if (paper_dir / "metadata.json").exists():
            metadata = json.loads((paper_dir / "metadata.json").read_text(encoding="utf-8"))

        # 如果有分析结果，加载各章节内容
        sections = {}
        for section in ["overview", "motivation", "methodology", "experiments", "critical_analysis", "conclusion"]:
            sec_path = paper_dir / "analysis" / f"{section}.md"
            if not sec_path.exists():
                sec_path = paper_dir / "report" / "sections" / f"{section}.md"
            if sec_path.exists():
                sections[section] = sec_path.read_text(encoding="utf-8")[:3000]

        paper_data = {
            "title": metadata.get("title", paper_dir.name),
            "authors": metadata.get("author", ""),
            "venue": metadata.get("subject", ""),
            "sections": sections,
            **sections,
        }

        gen = PPTXGenerator(paper_dir)
        out = gen.generate(paper_data, str(output) if output else None)

        console.print(f"\n[green]PPTX generated:[/green] {out}")
        console.print(f"  Slides: {len(gen.prs.slides)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def evaluate(
    report_path: Path = typer.Argument(..., help="生成的报告路径"),
    paper_dir: Path = typer.Argument(..., help="解析后的论文目录"),
    model: str = typer.Option("deepseek-chat", "--model", "-m"),
    provider: str = typer.Option("deepseek", "--provider", "-p"),
):
    """评估报告质量 — Rubric 评分 + 幻觉检测"""
    from paperwise.core.llm_client import LLMClient
    from paperwise.evaluation import RubricEvaluator, HallucinationDetector

    console.print(Panel("Evaluating Report", title="PaperWise Evaluate"))

    async def run_eval():
        report = Path(report_path).read_text(encoding="utf-8")
        paper = Path(paper_dir) / "text.md"
        paper_text = paper.read_text(encoding="utf-8") if paper.exists() else ""

        llm = LLMClient(provider=provider, model=model)

        # Rubric 评分
        console.print("[cyan]→[/cyan] Running rubric evaluation...")
        evaluator = RubricEvaluator(llm)
        eval_result = await evaluator.evaluate(report, paper_text)

        # 幻觉检测
        console.print("[cyan]→[/cyan] Running hallucination detection...")
        detector = HallucinationDetector(llm)
        hall_result = await detector.detect(report, paper_text)

        # 显示结果
        table = Table(title="Evaluation Results")
        table.add_column("Dimension", style="cyan")
        table.add_column("Score", style="green")
        table.add_column("Weight")

        for name, s in eval_result.scores.items():
            dim_names = {"accuracy": "Accuracy", "completeness": "Completeness",
                         "insight_depth": "Insight Depth", "evidence_quality": "Evidence Quality"}
            table.add_row(dim_names.get(name, name), f"{s['score']}/4", f"x{s['weight']}")

        table.add_row("─" * 15, "─" * 6, "─" * 6)
        table.add_row("[bold]Overall[/bold]", f"[bold]{eval_result.overall_score}/4.0[/bold]",
                      "PASS" if eval_result.passed else "[red]FAIL[/red]")

        console.print(table)

        # 幻觉结果
        if not hall_result["passed"]:
            console.print(f"\n[red]⚠ HALLUCINATION DETECTED ({hall_result['severity']})[/red]")
            for h in hall_result["flagged"]:
                console.print(f"  • {h.get('claim', '')[:100]}...")
        else:
            console.print(f"\n[green]✓ No hallucinations detected[/green] ({hall_result['severity']})")

        console.print(f"\n[dim]{eval_result.details}[/dim]")

    try:
        asyncio.run(run_eval())
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(code=1)


def main():
    """CLI 入口"""
    app()


@app.command()
def mcp_serve():
    """启动 MCP Server（JSON-RPC over stdio）

    将 PaperWise 的 13 个工具暴露为 MCP 标准接口，
    供 Claude Desktop、VS Code Copilot 等 MCP 客户端连接。

    Claude Desktop 配置示例 (claude_desktop_config.json):
      {
        "mcpServers": {
          "paperwise": {
            "command": "python",
            "args": ["-m", "paperwise.cli.app", "mcp-serve"]
          }
        }
      }
    """
    from paperwise.mcp.server import main as mcp_main
    console.print("[bold]PaperWise MCP Server[/bold] starting (stdio mode)")
    console.print("Waiting for MCP client connection...")
    mcp_main()


if __name__ == "__main__":
    main()
