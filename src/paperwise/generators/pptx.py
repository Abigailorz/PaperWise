"""PPT 生成器 — 基于 python-pptx 生成学术汇报演示文稿

对应书中 5.2.3 节：代码驱动的多媒体生成
PPT 本质上是通过代码生成 OOXML 格式的文档
"""

from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR


class PPTXGenerator:
    """学术论文演示文稿生成器。

    标准幻灯片结构（10-15 页）:
    1.  Title Slide       — 论文标题、作者、会议
    2.  Overview          — 论文概要（1 页）
    3.  Background        — 研究背景与动机（1-2 页）
    4.  Problem Statement — 研究问题（1 页）
    5.  Core Method       — 核心方法（2-3 页，含公式）
    6.  Experiments       — 实验设计与结果（2-3 页，含图表）
    7.  Critical Analysis — 优势与局限（1 页）
    8.  Conclusion        — 总结与展望（1 页）
    9.  Thank You         — 致谢/Q&A

    使用 python-pptx 直接生成，输出标准 PowerPoint 可打开文件。
    """

    # 配色方案（学术风格）
    COLORS = {
        "primary": RGBColor(0x1A, 0x56, 0xDB),     # 深蓝
        "secondary": RGBColor(0x2D, 0x3A, 0x4A),   # 深灰
        "accent": RGBColor(0xE8, 0x6A, 0x17),      # 橙色强调
        "bg_light": RGBColor(0xF5, 0xF7, 0xFA),    # 浅灰背景
        "white": RGBColor(0xFF, 0xFF, 0xFF),
        "black": RGBColor(0x1A, 0x1A, 0x1A),
        "gray": RGBColor(0x6B, 0x7B, 0x8D),
        "green": RGBColor(0x27, 0xAE, 0x60),
        "red": RGBColor(0xE7, 0x4C, 0x3C),
    }

    FONT_TITLE = "Arial"
    FONT_BODY = "Arial"

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.prs = Presentation()
        self.prs.slide_width = Inches(13.333)  # 16:9 宽屏
        self.prs.slide_height = Inches(7.5)

    def generate(self, paper_data: dict, output_path: str = None) -> str:
        """生成完整 PPT。

        Args:
            paper_data: {title, authors, venue, sections: {overview, motivation, ...}}
            output_path: 输出 PPTX 文件路径

        Returns:
            输出文件路径
        """
        self._add_title_slide(paper_data)
        self._add_overview_slide(paper_data)
        self._add_section_slide("Background & Motivation", paper_data.get("motivation", ""), 2)
        self._add_section_slide("Problem Statement", paper_data.get("problem", ""), 1)
        self._add_section_slide("Core Methodology", paper_data.get("methodology", ""), 3)
        self._add_section_slide("Experimental Results", paper_data.get("experiments", ""), 3)
        self._add_critical_analysis_slide(paper_data)
        self._add_conclusion_slide(paper_data)
        self._add_thank_you_slide()

        output = output_path or str(self.workspace / "presentation" / "slides.pptx")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(output)
        return output

    # === 幻灯片构建方法 ===

    def _add_title_slide(self, data: dict):
        """标题页"""
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])  # Blank
        self._set_bg(slide, self.COLORS["primary"])

        # 标题
        title_box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(11.3), Inches(2))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = data.get("title", "Paper Title")
        p.font.size = Pt(40)
        p.font.bold = True
        p.font.color.rgb = self.COLORS["white"]
        p.font.name = self.FONT_TITLE
        p.alignment = PP_ALIGN.LEFT

        # 作者
        author_box = slide.shapes.add_textbox(Inches(1), Inches(4.5), Inches(11.3), Inches(1))
        tf = author_box.text_frame
        p = tf.paragraphs[0]
        p.text = data.get("authors", "")
        p.font.size = Pt(20)
        p.font.color.rgb = RGBColor(0xBB, 0xCC, 0xEE)
        p.font.name = self.FONT_BODY

        # 日期
        venue_box = slide.shapes.add_textbox(Inches(1), Inches(5.3), Inches(11.3), Inches(0.5))
        tf = venue_box.text_frame
        p = tf.paragraphs[0]
        p.text = data.get("venue", "") + (f" ({data.get('year', '')})" if data.get("year") else "")
        p.font.size = Pt(16)
        p.font.color.rgb = RGBColor(0x99, 0xAA, 0xCC)
        p.font.name = self.FONT_BODY

        # 底部标记
        self._add_footer(slide, "PaperWise — AI-Generated Presentation")

    def _add_overview_slide(self, data: dict):
        """论文概要页"""
        slide = self._create_content_slide("Paper Overview")

        content = data.get("overview", data.get("sections", {}).get("overview", ""))
        bullets = self._extract_key_points(content, max_points=5)

        self._add_bullet_list(slide, bullets, Inches(1), Inches(2), Inches(11.3), Inches(5))

    def _add_section_slide(self, title: str, content: str, max_subslides: int):
        """通用内容章节（自动分页）"""
        points = self._extract_key_points(content, max_points=max_subslides * 4)

        # 按 4 条一组分页
        for i in range(0, len(points), 4):
            chunk = points[i:i + 4]
            if not chunk:
                break

            slide_title = title if i == 0 else f"{title} (cont.)"
            slide = self._create_content_slide(slide_title)
            self._add_bullet_list(slide, chunk, Inches(1), Inches(2), Inches(11.3), Inches(5))

    def _add_critical_analysis_slide(self, data: dict):
        """批判性分析 — 优势 / 局限 两栏布局"""
        slide = self._create_content_slide("Critical Analysis")

        # 左侧：优势
        left_box = slide.shapes.add_textbox(Inches(0.8), Inches(2), Inches(5.5), Inches(4.5))
        tf = left_box.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = "Strengths"
        p.font.size = Pt(22)
        p.font.bold = True
        p.font.color.rgb = self.COLORS["green"]
        p.font.name = self.FONT_TITLE

        strengths = self._extract_key_points(
            data.get("strengths", data.get("critical_analysis", "")), max_points=4, prefix="strength"
        )
        for pt in strengths:
            p = tf.add_paragraph()
            p.text = f"✓ {pt}"
            p.font.size = Pt(16)
            p.font.color.rgb = self.COLORS["secondary"]
            p.font.name = self.FONT_BODY
            p.space_after = Pt(12)

        # 右侧：局限
        right_box = slide.shapes.add_textbox(Inches(7), Inches(2), Inches(5.5), Inches(4.5))
        tf = right_box.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = "Limitations"
        p.font.size = Pt(22)
        p.font.bold = True
        p.font.color.rgb = self.COLORS["red"]
        p.font.name = self.FONT_TITLE

        limitations = self._extract_key_points(
            data.get("limitations", data.get("critical_analysis", "")), max_points=4, prefix="limitation"
        )
        for pt in limitations:
            p = tf.add_paragraph()
            p.text = f"⚠ {pt}"
            p.font.size = Pt(16)
            p.font.color.rgb = self.COLORS["secondary"]
            p.font.name = self.FONT_BODY
            p.space_after = Pt(12)

    def _add_conclusion_slide(self, data: dict):
        """总结页"""
        slide = self._create_content_slide("Summary & Future Directions")

        # 核心贡献
        content = data.get("conclusion", data.get("sections", {}).get("conclusion", ""))
        points = self._extract_key_points(content, max_points=6)
        self._add_bullet_list(slide, points, Inches(1), Inches(2), Inches(11.3), Inches(4.5))

    def _add_thank_you_slide(self):
        """致谢页"""
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self._set_bg(slide, self.COLORS["primary"])

        text_box = slide.shapes.add_textbox(Inches(1), Inches(3), Inches(11.3), Inches(2))
        tf = text_box.text_frame
        p = tf.paragraphs[0]
        p.text = "Thank You"
        p.font.size = Pt(48)
        p.font.bold = True
        p.font.color.rgb = self.COLORS["white"]
        p.font.name = self.FONT_TITLE
        p.alignment = PP_ALIGN.CENTER

        p2 = tf.add_paragraph()
        p2.text = "Questions & Discussion"
        p2.font.size = Pt(24)
        p2.font.color.rgb = RGBColor(0xAA, 0xBB, 0xDD)
        p2.font.name = self.FONT_BODY
        p2.alignment = PP_ALIGN.CENTER
        p2.space_before = Pt(20)

        self._add_footer(slide, "Generated by PaperWise")

    # === 辅助方法 ===

    def _create_content_slide(self, title: str):
        """创建带标题栏的内容页。"""
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])  # Blank

        # 顶部标题栏
        title_bar = slide.shapes.add_shape(
            1, Inches(0), Inches(0), self.prs.slide_width, Inches(1.3)  # MSO_SHAPE.RECTANGLE = 1
        )
        title_bar.fill.solid()
        title_bar.fill.fore_color.rgb = self.COLORS["primary"]
        title_bar.line.fill.background()

        tf = title_bar.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = self.COLORS["white"]
        p.font.name = self.FONT_TITLE
        p.alignment = PP_ALIGN.LEFT
        tf.margin_left = Inches(0.8)
        tf.margin_top = Inches(0.3)

        # 底部分隔线
        line = slide.shapes.add_shape(
            1, Inches(0), Inches(7.2), self.prs.slide_width, Inches(0.05)
        )
        line.fill.solid()
        line.fill.fore_color.rgb = self.COLORS["accent"]
        line.line.fill.background()

        self._add_footer(slide, self._slide_number(slide))
        return slide

    def _add_bullet_list(self, slide, items: list, left, top, width, height):
        """在幻灯片上添加项目符号列表。"""
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True

        for i, item in enumerate(items):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = f"• {item}"
            p.font.size = Pt(18)
            p.font.color.rgb = self.COLORS["secondary"]
            p.font.name = self.FONT_BODY
            p.space_after = Pt(14)
            p.level = 0

    def _add_footer(self, slide, text: str):
        """添加页脚。"""
        footer = slide.shapes.add_textbox(Inches(0.5), Inches(7.0), Inches(5), Inches(0.4))
        tf = footer.text_frame
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = Pt(10)
        p.font.color.rgb = self.COLORS["gray"]
        p.font.name = self.FONT_BODY

    def _set_bg(self, slide, color):
        """设置幻灯片背景色。"""
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    def _slide_number(self, slide) -> str:
        """获取幻灯片编号。"""
        try:
            idx = list(self.prs.slides).index(slide)
            return f"Slide {idx + 1}"
        except ValueError:
            return ""

    def _extract_key_points(self, text: str, max_points: int = 5, prefix: str = "") -> list[str]:
        """从文本中提取关键要点。

        简单的启发式：按段落/换行分割，取前 N 条有意义的句子。
        """
        if not text:
            return ["(No content available)"]

        # 按换行分割
        lines = [l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip()]

        # 过滤太短或太长的行
        points = []
        for line in lines:
            # 移除 markdown 标记
            clean = line.lstrip("#-*• \t")
            if 15 < len(clean) < 300 and not clean.startswith("```"):
                points.append(clean)

        # 如果按行分割不够，尝试按句子
        if len(points) < max_points // 2:
            import re
            sentences = re.split(r'(?<=[.!?])\s+', text)
            points = [s.strip() for s in sentences if 15 < len(s.strip()) < 300]

        return points[:max_points] if points else ["(No key points extracted)"]


def get_pptx_system_prompt() -> str:
    """PPT 生成的 Agent 系统提示词。"""
    return """You are a presentation designer specialized in academic paper presentations.

<your_task>
Create a professional PowerPoint presentation (10-15 slides) summarizing an academic paper.
The presentation should be clear, well-structured, and suitable for a research seminar.
</your_task>

<slide_structure>
1. Title Slide (1 slide)
2. Overview (1 slide) — One-paragraph summary + key contributions
3. Background & Motivation (1-2 slides) — Problem context, prior work limitations
4. Problem Statement (1 slide) — Clear research question
5. Core Method (2-3 slides) — Key ideas, architecture, important equations
6. Experimental Results (2-3 slides) — Setup, main results, key comparisons
7. Critical Analysis (1 slide) — Strengths vs Limitations
8. Conclusion (1 slide) — Summary + future work
9. Thank You / Q&A (1 slide)
</slide_structure>

<design_guidelines>
- Each slide should have a clear single message
- Use bullet points, not paragraphs
- Include key numbers and metrics
- Reference specific figures/tables from the paper
- Keep text concise — slides support the talk, not replace it
</design_guidelines>
"""
