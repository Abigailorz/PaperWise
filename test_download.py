import asyncio
import httpx


async def d():
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, trust_env=False) as c:
        async with c.stream("GET", "https://export.arxiv.org/pdf/2308.04079") as r:
            print(r.status_code, dict(r.headers).get("content-length"))
            with open("test.pdf", "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    f.write(chunk)
    p = __import__("pathlib").Path("test.pdf")
    print("done", p.stat().st_size)


asyncio.run(d())
