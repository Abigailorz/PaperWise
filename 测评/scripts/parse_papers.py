import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\13970\Desktop\PaperWise\src")

from paperwise.parsers.pdf_parser import PDFParser

REAL = Path(r"C:\Users\13970\Desktop\PaperWise\tests\test_data\real_papers")
OUT = Path(r"C:\Users\13970\Documents\Codex\2026-08-13\new-chat-2\work\eval\parsed")

PAPERS = {
    "3dgs_2308.04079": "3dgs_2308.04079.pdf",
    "langsplat_2312.16084": "langsplat_2312.16084.pdf",
    "feature3dgs_2312.03203": "feature3dgs_2312.03203.pdf",
}


def main():
    parser = PDFParser()
    for pid, fn in PAPERS.items():
        pdf = REAL / fn
        t0 = time.time()
        print(f"parsing {fn} ...", flush=True)
        try:
            parsed = parser.parse(str(pdf), output_dir=str(OUT / pid))
            meta = parsed.metadata
            print(
                f"  OK {pid}: pages={meta.get('page_count')} "
                f"title={meta.get('title', '')[:90]!r} "
                f"text_chars={len(parsed.text)} dt={time.time() - t0:.1f}s",
                flush=True,
            )
        except Exception as e:
            print(f"  FAIL {pid}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
