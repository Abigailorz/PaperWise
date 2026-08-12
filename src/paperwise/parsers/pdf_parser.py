"""PDF 解析主编排器"""

import json
from pathlib import Path

import fitz  # PyMuPDF

from paperwise.core.types import ParsedPaper


class PDFParser:
    """编排完整的 PDF 解析管道。

    输出结构:
    workspace/{paper_id}/
    ├── metadata.json
    ├── text.md
    ├── structure.json
    ├── figures/
    │   ├── figure_1.png
    │   └── figure_1_desc.json
    ├── tables/
    │   └── table_1.json
    ├── formulas/
    │   └── formula_1.tex
    └── references.json
    """

    def parse(self, pdf_path: str, output_dir: str = None) -> ParsedPaper:
        """运行完整解析管道。

        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录（默认: workspace/{pdf_name}/）

        Returns:
            ParsedPaper 包含所有提取的内容和元数据
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        paper_id = pdf_path.stem
        if output_dir:
            out_dir = Path(output_dir)
        else:
            from paperwise.config.settings import get_settings
            settings = get_settings()
            out_dir = settings.workspace_dir / paper_id

        out_dir.mkdir(parents=True, exist_ok=True)

        # 打开 PDF
        doc = fitz.open(str(pdf_path))
        page_count = len(doc)

        # Stage 1: 提取文本和结构
        text, structure = self._extract_text(doc, out_dir)

        # Stage 2: 提取图表
        figures = self._extract_figures(doc, out_dir)

        # Stage 3: 提取表格
        tables = self._extract_tables(doc, out_dir)

        # Stage 4: 提取公式
        formulas = self._extract_formulas(doc, text, out_dir)

        # Stage 5: 提取元数据
        metadata = self._extract_metadata(doc, pdf_path, page_count)

        # Stage 6: 提取参考文献
        references = self._extract_references(text)

        doc.close()

        return ParsedPaper(
            paper_id=paper_id,
            output_dir=out_dir,
            metadata=metadata,
            text=text,
            structure=structure,
            figures=figures,
            tables=tables,
            formulas=formulas,
            references=references,
        )

    # === 文本提取 ===

    def _extract_text(self, doc: fitz.Document, out_dir: Path) -> tuple[str, dict]:
        """提取文本和章节结构。"""
        all_lines = []
        sections = []
        current_section = None

        for page_num, page in enumerate(doc):
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

            for block in blocks:
                if block["type"] != 0:  # 跳过非文本块
                    continue

                for line in block["lines"]:
                    text = "".join([span["text"] for span in line["spans"]])
                    if not text.strip():
                        continue

                    # 获取字体大小
                    sizes = [span.get("size", 10) for span in line["spans"]]
                    max_size = max(sizes) if sizes else 10

                    # 简单的章节检测
                    is_bold = any("Bold" in span.get("font", "") for span in line["spans"])
                    if (max_size > 12 or is_bold) and len(text.strip()) < 100:
                        level = 1 if max_size > 14 else 2 if max_size > 12 else 3
                        sections.append({
                            "title": text.strip(),
                            "level": level,
                            "page": page_num + 1,
                            "line": len(all_lines),
                        })

                    all_lines.append(text)

        text = "\n\n".join(all_lines)
        structure = {"sections": sections, "total_lines": len(all_lines)}

        # 写出文件
        (out_dir / "text.md").write_text(text, encoding="utf-8")
        (out_dir / "structure.json").write_text(
            json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return text, structure

    # === 图表提取 ===

    def _extract_figures(self, doc: fitz.Document, out_dir: Path) -> list[dict]:
        """提取嵌入的图片为独立文件。"""
        figures_dir = out_dir / "figures"
        figures_dir.mkdir(exist_ok=True)
        figures = []

        for page_num, page in enumerate(doc):
            images = page.get_images(full=True)

            for img_idx, img in enumerate(images):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue

                image_bytes = base_image["image"]
                ext = base_image["ext"]

                # 跳过太小的图片（图标、logo）
                if len(image_bytes) < 5000:
                    continue

                fig_path = figures_dir / f"figure_{len(figures) + 1}.{ext}"
                fig_path.write_bytes(image_bytes)

                # 尝试找到附近的标题
                caption = self._find_caption(page, "Figure")

                figures.append({
                    "index": len(figures) + 1,
                    "page": page_num + 1,
                    "caption": caption,
                    "path": str(fig_path.relative_to(out_dir.parent if out_dir.parent != out_dir else out_dir)),
                    "width": base_image.get("width", 0),
                    "height": base_image.get("height", 0),
                })

        # 写出图表索引
        (figures_dir / "index.json").write_text(
            json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return figures

    def _find_caption(self, page: fitz.Page, prefix: str) -> str:
        """在页面中查找图表/表格标题。"""
        text = page.get_text("text")
        patterns = [f"{prefix} ", f"{prefix}.", f"{prefix}:", f"{prefix}-"]
        for pattern in patterns:
            idx = text.lower().find(pattern.lower())
            if idx >= 0:
                end = text.find("\n", idx)
                if end < 0:
                    end = min(idx + 200, len(text))
                return text[idx:end].strip()
        return ""

    # === 表格提取 ===

    def _extract_tables(self, doc: fitz.Document, out_dir: Path) -> list[dict]:
        """使用 PyMuPDF 内置表格检测提取表格。"""
        tables_dir = out_dir / "tables"
        tables_dir.mkdir(exist_ok=True)
        tables = []

        for page_num, page in enumerate(doc):
            try:
                found_tables = page.find_tables()
            except Exception:
                continue

            for tab in found_tables:
                try:
                    data = tab.extract()
                except Exception:
                    continue

                if not data or len(data) < 2:
                    continue

                headers = [str(cell) if cell else "" for cell in data[0]]
                rows = [[str(cell) if cell else "" for cell in row] for row in data[1:]]
                caption = self._find_caption(page, "Table")

                table_data = {
                    "index": len(tables) + 1,
                    "page": page_num + 1,
                    "caption": caption,
                    "headers": headers,
                    "rows": rows,
                }

                table_path = tables_dir / f"table_{len(tables) + 1}.json"
                table_path.write_text(
                    json.dumps(table_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                tables.append(table_data)

        return tables

    # === 公式提取 ===

    def _extract_formulas(self, doc: fitz.Document, text: str, out_dir: Path) -> list[dict]:
        """基于正则表达式提取 LaTeX 公式。"""
        import re

        formulas_dir = out_dir / "formulas"
        formulas_dir.mkdir(exist_ok=True)
        formulas = []

        # 匹配常见数学符号模式
        math_patterns = [
            r'\$\$(.+?)\$\$',           # 块级公式
            r'\$(.+?)\$',               # 行内公式
            r'\\begin\{equation\}(.+?)\\end\{equation\}',
            r'\\begin\{align\}(.+?)\\end\{align\}',
        ]

        for pattern in math_patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                latex = match.group(1).strip()
                if len(latex) > 3:
                    formulas.append({
                        "index": len(formulas) + 1,
                        "latex": latex,
                        "type": "display" if "$$" in match.group(0) or "begin" in match.group(0) else "inline",
                    })

        # 写出
        for f in formulas:
            tex_path = formulas_dir / f"formula_{f['index']}.tex"
            tex_path.write_text(f["latex"], encoding="utf-8")

        return formulas

    # === 元数据 ===

    def _extract_metadata(self, doc: fitz.Document, pdf_path: Path, page_count: int) -> dict:
        """提取论文元数据。"""
        meta = doc.metadata or {}

        # 尝试从第一页提取标题（最大字体文本）
        title = meta.get("title", "")
        if not title:
            first_page = doc[0]
            blocks = first_page.get_text("dict")["blocks"]
            largest_text = ""
            largest_size = 0
            for block in blocks:
                if block["type"] == 0:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            size = span.get("size", 10)
                            if size > largest_size and len(span["text"].strip()) > 10:
                                largest_size = size
                                largest_text = span["text"].strip()
            title = largest_text or pdf_path.stem

        return {
            "title": title,
            "author": meta.get("author", "Unknown"),
            "subject": meta.get("subject", ""),
            "keywords": meta.get("keywords", ""),
            "page_count": page_count,
            "file_size": pdf_path.stat().st_size,
            "filename": pdf_path.name,
        }

    def _extract_references(self, text: str) -> list[dict]:
        """提取参考文献（基于正则表达式的启发式方法）。"""
        import re

        references = []

        # 查找 References / Bibliography 部分
        ref_section_patterns = [
            r'(?i)^references?\s*$',
            r'(?i)^bibliography\s*$',
            r'(?i)^works cited\s*$',
        ]

        lines = text.split("\n")
        ref_start = -1
        for i, line in enumerate(lines):
            for pat in ref_section_patterns:
                if re.match(pat, line.strip()):
                    ref_start = i
                    break
            if ref_start >= 0:
                break

        if ref_start >= 0:
            ref_pattern = r'^\[(\d+)\]\s*(.+)$'
            for line in lines[ref_start + 1:ref_start + 200]:
                match = re.match(ref_pattern, line.strip())
                if match:
                    references.append({
                        "id": int(match.group(1)),
                        "text": match.group(2),
                    })

        return references
