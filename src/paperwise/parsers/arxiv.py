"""arXiv 摄入 — ID 解析与 PDF 下载。

支持以下输入形式：
- 裸 ID: 2401.12345 / 2401.12345v2
- 摘要页: https://arxiv.org/abs/2401.12345
- PDF 页: https://arxiv.org/pdf/2401.12345
"""

import re
from pathlib import Path
from typing import Optional


ARXIV_ID_PATTERN = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


def extract_arxiv_id(text: str) -> Optional[str]:
    """从 URL 或裸 ID 中提取 arXiv ID。无法识别返回 None。"""
    if not text:
        return None
    s = text.strip()
    m = ARXIV_ID_PATTERN.search(s)
    if not m:
        return None
    arxiv_id = m.group(1)
    # 如果输入带路径/协议（看起来像 URL），必须来自 arxiv.org
    if "/" in s or ":" in s:
        if not re.search(r"arxiv\.org/(?:abs|pdf)/", s, re.IGNORECASE):
            return None
    return arxiv_id


def is_arxiv_id(text: str) -> bool:
    return extract_arxiv_id(text) is not None


async def download_arxiv_pdf(arxiv_id: str, dest_dir: Path,
                             timeout: float = 60.0) -> Path:
    """从 arxiv.org 下载论文 PDF。

    Args:
        arxiv_id: arXiv ID（如 2401.12345）
        dest_dir: 保存目录

    Returns:
        下载后的 PDF 路径
    """
    import httpx

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"arxiv_{arxiv_id}.pdf"
    url = f"https://arxiv.org/pdf/{arxiv_id}"

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest
