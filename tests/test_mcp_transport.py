"""MCP stdio 传输层集成测试"""
import asyncio, sys, json, os
from pathlib import Path

async def main():
    # 启动 paperwise MCP server 子进程（同步模式）
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'paperwise.mcp.server',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.getcwd(),
    )

    rid = 0
    async def request(method, params=None):
        nonlocal rid; rid += 1
        payload = json.dumps({"jsonrpc":"2.0","id":rid,"method":method,"params":params or {}})
        proc.stdin.write((payload + "\n").encode())
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        return json.loads(line.decode())

    async def notify(method, params=None):
        payload = json.dumps({"jsonrpc":"2.0","method":method,"params":params or {}})
        proc.stdin.write((payload + "\n").encode())
        await proc.stdin.drain()

    try:
        # 1. initialize
        r = await request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "paperwise-test", "version": "1.0"},
        })
        result = r["result"]
        assert "protocolVersion" in result
        assert "tools" in result["capabilities"]
        print(f"[1] initialize OK (server: {result.get('serverInfo',{}).get('name','?')} v{result.get('serverInfo',{}).get('version','?')})")

        # 2. initialized notification
        await notify("notifications/initialized", {})
        print("[2] initialized notification sent")

        # 3. tools/list
        r = await request("tools/list")
        tools = r["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "read_file" in names
        assert "write_file" in names
        assert "grep" in names
        assert "request_file_access" in names
        print(f"[3] tools/list OK ({len(tools)} tools: {', '.join(sorted(names)[:5])}...)")

        # 4. tools/call - write + read round-trip
        r = await request("tools/call", {
            "name": "write_file",
            "arguments": {"path": "mcp_test.txt", "content": "Hello from MCP stdio transport!"},
        })
        text = r["result"]["content"][0]["text"]
        assert "Successfully" in text
        print(f"[4] tools/call write OK: {text[:60]}")

        r = await request("tools/call", {
            "name": "read_file",
            "arguments": {"path": "mcp_test.txt"},
        })
        text = r["result"]["content"][0]["text"]
        assert "Hello from MCP stdio transport" in text
        print(f"[5] tools/call read OK: {text[:60]}")

        # 5. resources/list
        r = await request("resources/list")
        resources = r["result"]["resources"]
        has_test = any("mcp_test.txt" in res.get("name","") for res in resources)
        print(f"[6] resources/list OK ({len(resources)} resources, has test: {has_test})")

        # 6. resources/read
        r = await request("resources/read", {"uri": "file:///mcp_test.txt"})
        text = r["result"]["contents"][0]["text"]
        assert "Hello from MCP stdio transport" in text
        print(f"[7] resources/read OK")

        # 7. prompts/list (includes skills + built-in)
        r = await request("prompts/list")
        prompts = r["result"]["prompts"]
        assert len(prompts) >= 3
        names = {p["name"] for p in prompts}
        assert "skill-academic-reading" in names
        assert "analyze-paper" in names
        print(f"[8] prompts/list OK ({len(prompts)} prompts, skills included)")

        # 8. Call a tool that triggers our access gate
        r = await request("tools/call", {
            "name": "read_file",
            "arguments": {"path": "/etc/passwd"},
        })
        text = r["result"]["content"][0]["text"]
        assert "Error" in text, f"Should block dangerous path, got: {text[:80]}"
        print(f"[9] dangerous path blocked via MCP: {text[:60]}")

        # 9. Clean up
        (Path("workspace") / "mcp_test.txt").unlink(missing_ok=True)

        print(f"\nALL 9 MCP TRANSPORT CHECKS PASSED")

    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except:
            proc.kill()

if __name__ == "__main__":
    asyncio.run(main())
