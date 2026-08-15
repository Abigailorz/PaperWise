"""报告生成器 — 基于 LLM 的结构化 Markdown 报告

对应书中 5.2.3 节：代码驱动的多媒体生成
"""

import json
from pathlib import Path

from paperwise.core.types import ParsedPaper


class ReportGenerator:
    """生成深度解读报告。

    报告结构:
    1. Paper Overview — 论文概览
    2. Research Problem & Motivation — 研究问题与动机
    3. Core Methodology — 核心方法
    4. Experimental Design & Results — 实验设计与结果
    5. Critical Analysis — 批判性分析
    6. Related Work Context — 相关工作
    7. Conclusion & Future Directions — 结论与展望
    """

    REPORT_SECTIONS = [
        ("overview", "Paper Overview"),
        ("motivation", "Research Problem & Motivation"),
        ("methodology", "Core Methodology"),
        ("experiments", "Experimental Design & Results"),
        ("critical_analysis", "Critical Analysis"),
        ("related_work", "Related Work Context"),
        ("conclusion", "Conclusion & Future Directions"),
    ]

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def get_report_system_prompt(self) -> str:
        """生成报告生成的 Agent 系统提示词。

        对应书中 2.4 节：结构化提示 + 流程驱动
        """
        return """You are PaperWise Report Writer, an expert academic paper analyst.

<your_role>
Generate comprehensive, structured deep-reading reports for academic papers.
Your reports are accurate, well-organized, and insightful — not just summaries.
</your_role>

<writing_guidelines>
1. Every factual claim MUST cite the source by section/line number from the parsed paper
2. Reference paper figures and tables by number (e.g., "Figure 3 shows...", "Table 1 reports...")
3. Include relevant LaTeX formulas from the paper when discussing methods
4. For critical analysis, be balanced — identify real strengths AND limitations
5. Write in fluent academic English with clear organization
6. Use proper Markdown formatting (headings, lists, emphasis, code blocks for formulas)
7. Each section should be self-contained but cross-reference other sections where helpful
</writing_guidelines>

<output_format>
Save each section as a separate file (report/sections/{name}.md).
After all sections are complete, assemble the final report at report/report.md.
</output_format>
"""

    def get_report_task(self, paper_dir: str) -> str:
        """构建报告生成任务描述。

        Returns:
            Agent 任务字符串
        """
        paper_dir = Path(paper_dir)
        return f"""Generate a deep reading report for the academic paper at: {paper_dir}

The parsed paper content is available in these files:
- {paper_dir}/text.md — Full paper text
- {paper_dir}/structure.json — Section structure
- {paper_dir}/metadata.json — Paper metadata (title, authors, etc.)
- {paper_dir}/figures/ — Extracted figures
- {paper_dir}/tables/ — Extracted tables
- {paper_dir}/formulas/ — Extracted LaTeX formulas
- {paper_dir}/references.json — References

## Task Steps

1. FIRST, read the paper metadata and full text to understand the paper
2. Read figures/ and tables/ to understand the visual content
3. Write a skeleton report/report.md NOW (YAML frontmatter + table of contents) so the report file always exists
4. For EACH section below, write detailed analysis to report/sections/{{name}}.md
5. Finally, re-assemble report/report.md by inlining all completed sections in order

## Report Sections to Generate

For each section, provide thorough analysis with specific evidence from the paper:

1. **report/sections/overview.md** — Paper Overview
   - Title, authors, venue, year
   - One-paragraph executive summary (5-minute read)
   - Paper structure overview (how is it organized?)
   - Key terms and definitions used throughout

2. **report/sections/motivation.md** — Research Problem & Motivation
   - What problem does this paper solve?
   - Why is this problem important? (practical and theoretical significance)
   - What are the specific limitations of prior approaches?
   - What is the core research question or hypothesis?

3. **report/sections/methodology.md** — Core Methodology
   - What is the proposed approach? (high-level intuition first, then details)
   - Key technical innovations and how they differ from prior work
   - Mathematical formulation (cite equation numbers from the paper)
   - Algorithm description and architecture (reference figures)
   - Implementation details that matter for reproducibility

4. **report/sections/experiments.md** — Experimental Design & Results
   - Datasets used (size, domain, splits)
   - Baselines and why they were chosen
   - Evaluation metrics and their appropriateness
   - Main results (reference tables, include key numbers)
   - Ablation studies and what they reveal
   - Statistical significance where reported

5. **report/sections/critical_analysis.md** — Critical Analysis
   - Strengths: what does this paper do exceptionally well?
   - Limitations: what are the honest weaknesses?
   - Assumptions: what does the method assume that may not hold generally?
   - Validity threats: internal, external, construct, statistical conclusion
   - Reproducibility: could you implement this from the paper alone?
   - Comparison to concurrent/competing approaches

6. **report/sections/related_work.md** — Related Work Context
   - How does this work fit into the broader literature?
   - Key related papers and their relationship to this work
   - What does this paper do that prior work doesn't?
   - Are there relevant works the authors didn't cite?

7. **report/sections/conclusion.md** — Conclusion & Future Directions
   - Summary of contributions (be specific, not generic)
   - Open questions raised by this work
   - Potential future research directions
   - Broader impact (positive and negative)
   - Personal assessment: is this paper likely to be influential? Why/why not?

## Final Assembly

Re-assemble report/report.md (already created as a skeleton in step 3) so that it:
- Keeps the YAML frontmatter (title, authors, date, paper_id)
- Keeps the Table of Contents linking to each section
- Inlines all completed sections in order
- Is professionally formatted

## Important Reminders
- Every claim needs a source reference (section/line number from text.md)
- Reference figures by number (Figure 1, Figure 2, etc.)
- Reference tables by number with key data points
- Be honest in critical analysis — don't just praise
- The report should be useful to someone who hasn't read the paper"""

    def assemble(self, paper_dir: str | Path) -> Path:
        """确定性组装 report/sections/*.md 为 report/report.md。

        作为兜底：即使 Agent 在步骤预算内没来得及手动组装，也能保证
        report.md 一定存在且内容完整。
        """
        paper_dir = Path(paper_dir)
        sections_dir = paper_dir / "report" / "sections"
        report_path = paper_dir / "report" / "report.md"
        if not sections_dir.exists():
            return report_path

        sections = sorted(sections_dir.glob("*.md"))
        if not sections:
            return report_path

        title = paper_dir.name
        meta_path = paper_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                title = meta.get("title", title)
            except Exception:
                pass

        toc, body = [], []
        for i, sp in enumerate(sections, 1):
            name = sp.stem
            content = sp.read_text(encoding="utf-8", errors="replace").strip()
            toc.append(f"{i}. {name}")
            body.append(f"## {name}\n\n{content}")

        report = (
            f"---\ntitle: {title}\n---\n\n"
            f"# {title}\n\n"
            f"## Table of Contents\n\n" + "\n".join(toc) + "\n\n"
            + "\n\n".join(body) + "\n"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        return report_path
