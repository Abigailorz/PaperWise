import asyncio, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

PAPERS = {
    "3dgs_2308.04079": "2308.04079",
    "langsplat_2312.16084": "2312.16084",
    "feature3dgs_2312.03203": "2312.03203",
    "gaussaingrouping_2312.00732": "2312.00732",
}
DEST = PROJECT / "tests" / "test_data" / "real_papers"

async def main():
    import httpx
    DEST.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=90, follow_redirects=True, trust_env=False) as c:
        for name, aid in PAPERS.items():
            url = f"https://export.arxiv.org/pdf/{aid}"
            try:
                r = await c.get(url)
                r.raise_for_status()
                p = DEST / f"{name}.pdf"
                p.write_bytes(r.content)
                print(f"OK {name} {p.stat().st_size} bytes")
            except Exception as e:
                print(f"FAIL {name} {type(e).__name__}: {str(e)[:160]}")

asyncio.run(main())
