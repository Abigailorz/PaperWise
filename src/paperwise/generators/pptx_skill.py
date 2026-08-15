"""nature-paper2ppt 技能加载器。

把已安装到项目 skills/ 目录的 nature-paper2ppt skill 转成 PPT 生成的系统提示词，
让前端的「生成 PPT」按钮（对应 /api/generate/pptx）真正由该 skill 驱动，
而不是依赖内置的硬编码提示词。
"""

from __future__ import annotations

from pathlib import Path


SKILL_NAME = "nature-paper2ppt"

# 每次生成都会加载的核心片段
_CORE_FILES = [
    "static/core/principles.md",
    "static/core/workflow.md",
    "static/core/output-and-quality.md",
]

# 包裹在 skill 内容之前的系统提示：确立角色 + 输出契约，避免模型去生成
# speaker notes / QA 报告 / 文件路径等本环境不负责的内容。
_SYSTEM_WRAPPER = (
    "你是资深学术 PPT 设计师。以下是「nature-paper2ppt」技能的规范，"
    "请严格遵循其中的论证主线、论文类型叙事弧线、图表优先、防文字溢出、"
    "版式节奏变化与不得编造数据等规则。\n"
    "在本环境中，你的唯一输出是用户消息里规定的 slide JSON（schema 见用户消息），"
    "渲染器会据此生成 .pptx。不要输出 speaker notes、QA 报告、文件路径、"
    "markdown 代码块或 JSON 之外的任何内容。\n\n"
    "=== 技能规范 ===\n"
)


def locate_skills_dir() -> Path | None:
    """定位包含 nature-paper2ppt 的项目 skills 内容目录。

    注意：不能只找任意名为 skills/ 的目录，因为 src/paperwise/skills 是 Python 包；
    必须确认该目录下确实存在 nature-paper2ppt/SKILL.md。
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "skills"
        if (candidate / SKILL_NAME / "SKILL.md").is_file():
            return candidate
    return None


def detect_paper_type(paper_text: str = "", title: str = "") -> str:
    """用轻量启发式判断论文类型，默认 methods（AI/方法类最常用）。"""
    text = f"{title}\n{paper_text[:4000]}".lower()
    if any(k in text for k in ("benchmark", "dataset", "corpus", "atlas", "resource", "omics")):
        return "resource"
    if any(k in text for k in ("review", "survey", "meta-analysis", "perspective", "commentary")):
        return "review"
    if any(k in text for k in (
        "method", "model", "algorithm", "architecture", "framework", "training-free",
        "system", "tool", "neural", " llm", "agent", "latent", "communication",
    )):
        return "methods"
    return "discovery"


def load_pptx_skill_prompt(paper_text: str = "", title: str = "") -> str:
    """加载 skill 内容并组装成系统提示词；未安装时返回空串。"""
    skills_dir = locate_skills_dir()
    if not skills_dir:
        return ""
    skill_dir = skills_dir / SKILL_NAME
    if not (skill_dir / "SKILL.md").exists():
        return ""

    chunks = [_SYSTEM_WRAPPER]
    for rel in _CORE_FILES:
        p = skill_dir / rel
        if p.exists():
            chunks.append(p.read_text(encoding="utf-8"))

    ptype = detect_paper_type(paper_text, title)
    arc = skill_dir / "static" / "fragments" / "paper_type" / f"{ptype}.md"
    if arc.exists():
        chunks.append(arc.read_text(encoding="utf-8"))

    return "\n\n".join(chunks)
