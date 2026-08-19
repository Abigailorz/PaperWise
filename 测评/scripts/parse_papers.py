import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.parsers.pdf_parser import PDFParser

REAL = PROJECT / "tests" / "test_data" / "real_papers"
OUT = PROJECT / "workspace" / "benchmarks" / "parsed"

PAPERS = {
    "3dgs_2308.04079": "3dgs_2308.04079.pdf",
    "langsplat_2312.16084": "langsplat_2312.16084.pdf",
    "feature3dgs_2312.03203": "feature3dgs_2312.03203.pdf",
    "gaussaingrouping_2312.00732": "gaussaingrouping_2312.00732.pdf",
    # Additional semantic 3DGS papers for dataset expansion
    "mipsplatting_2311.16493": "mipsplatting_2311.16493.pdf",
    "gaussianeditor_2311.14521": "gaussianeditor_2311.14521.pdf",
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
