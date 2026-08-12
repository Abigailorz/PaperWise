"""MCP Client — 连接外部 MCP Server，动态发现和调用外部工具

Agent 可连接外部 MCP Server 并自动将外部工具注册到 ToolRegistry，
实现工具的即插即用扩展。

支持的传输方式：
- stdio: 启动子进程，通过 stdin/stdout 通信
- SSE: HTTP Server-Sent Events (未来支持)

Usage:
    # 连接文件系统 MCP Server
    client = MCPClient("filesystem", {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]})
    external_tools = await client.connect()
    for tool in external_tools:
        registry.register(MCPToolWrapper(tool, client))

    # 连接数据库 MCP Server
    client = MCPClient("sqlite", {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "data.db"]})
"""

import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MCPToolDef:
    """外部 MCP 工具定义。"""
    name: str
    description: str
    inputSchema: dict = field(default_factory=dict)


@dataclass
class MCPServerConfig:
    """MCP 服务器连接配置。

    两种方式：
    1. command + args: 启动子进程（stdio 传输）
    2. url: 连接到 HTTP/SSE 端点
    """
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None


class MCPClient:
    """MCP 客户端 — 连接外部 MCP 服务器。

    使用方式：
        client = MCPClient("my-server", MCPServerConfig(command="python", args=["server.py"]))
        tools = await client.connect()
        result = await client.call_tool("tool-name", {"arg": "value"})
        await client.disconnect()
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, server_name: str, config: MCPServerConfig):
        self.server_name = server_name
        self.config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tools: dict[str, MCPToolDef] = {}
        self._connected = False
        self._recv_task: Optional[asyncio.Task] = None

    # ═══════════ 连接管理 ═══════════

    async def connect(self) -> list[MCPToolDef]:
        """建立连接并返回可用的外部工具列表。"""
        if self._connected:
            return list(self._tools.values())

        if self.config.command:
            await self._connect_stdio()
        elif self.config.url:
            await self._connect_sse()
        else:
            raise ValueError("Must provide either command or url in config")

        # 协议握手
        await self._initialize()

        # 获取工具列表
        self._tools = await self._list_tools()

        self._connected = True
        self._log(f"Connected to MCP server '{self.server_name}' "
                  f"— {len(self._tools)} tools available")
        return list(self._tools.values())

    async def disconnect(self):
        """断开连接。"""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._writer:
            self._writer.close()
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                self._process.kill()
        self._log("Disconnected")

    async def _connect_stdio(self):
        """通过 stdio 启动子进程连接。"""
        env = {**os.environ, **self.config.env}
        self._process = await asyncio.create_subprocess_exec(
            self.config.command, *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader = self._process.stdout
        self._writer = self._process.stdin
        # 启动后台读取任务
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _connect_sse(self):
        """通过 SSE/HTTP 连接（暂未实现，保留接口）。"""
        raise NotImplementedError("SSE transport not yet implemented. Use stdio.")

    # ═══════════ 协议操作 ═══════════

    async def _initialize(self):
        """MCP 协议握手。"""
        result = await self._request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {
                "name": "paperwise-agent",
                "version": "0.4.0",
            },
        })
        server_info = result.get("serverInfo", {})
        self._log(f"Server: {server_info.get('name', 'unknown')} "
                  f"v{server_info.get('version', '?')} "
                  f"(protocol {result.get('protocolVersion', '?')})")

        # 发送 initialized 通知
        self._send_notification("notifications/initialized", {})

    async def _list_tools(self) -> dict[str, MCPToolDef]:
        """获取远程工具列表。"""
        result = await self._request("tools/list", {})
        tools = {}
        for td in result.get("tools", []):
            tool = MCPToolDef(
                name=td["name"],
                description=td.get("description", ""),
                inputSchema=td.get("inputSchema", {}),
            )
            tools[tool.name] = tool
        return tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用远程工具。"""
        if not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        self._log(f"Calling: {name}({json.dumps(arguments, ensure_ascii=False)[:100]})")

        result = await self._request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # 提取文本内容
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "image":
                texts.append(f"[Image: {item.get('data', '')[:100]}...]")
            elif item.get("type") == "resource":
                texts.append(f"[Resource: {item.get('resource', {})}]")

        output = "\n".join(texts)
        if result.get("isError"):
            output = f"[Error] {output}"
        return output

    # ═══════════ JSON-RPC 通信 ═══════════

    async def _request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        self._request_id += 1
        req_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_json(request)

        try:
            return await asyncio.wait_for(future, timeout=60.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"MCP request '{method}' timed out after 60s")
        except Exception:
            self._pending.pop(req_id, None)
            raise

    def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无 id，无响应）。"""
        asyncio.create_task(self._send_json({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }))

    async def _send_json(self, data: dict) -> None:
        """向服务器发送 JSON。"""
        if self._writer:
            line = json.dumps(data, ensure_ascii=False) + "\n"
            self._writer.write(line.encode("utf-8"))
            await self._writer.drain()

    async def _recv_loop(self):
        """后台任务：持续读取服务器响应。"""
        if not self._reader:
            return

        buffer = b""
        while self._connected:
            try:
                line = await self._reader.readline()
                if not line:
                    break  # EOF

                buffer += line
                try:
                    data = json.loads(buffer.decode("utf-8"))
                    buffer = b""
                except json.JSONDecodeError:
                    continue

                await self._handle_response(data)

            except asyncio.CancelledError:
                break
            except Exception:
                self._log(f"Recv error: {traceback.format_exc()}")
                break

    async def _handle_response(self, data: dict) -> None:
        """处理服务器响应。"""
        req_id = data.get("id")

        if req_id is not None and req_id in self._pending:
            future = self._pending.pop(req_id)
            if "result" in data:
                future.set_result(data["result"])
            elif "error" in data:
                err = data["error"]
                future.set_exception(
                    RuntimeError(f"MCP Error [{err.get('code')}]: {err.get('message')}")
                )
        elif "method" in data:
            # 服务器发来的通知 → 记录日志，当前不需要处理
            method = data.get("method", "")
            self._log(f"Server notification: {method}")
        # 无 id 且无 method → 服务器对通知的响应（忽略）

    # ═══════════ 工具包装 ═══════════

    def get_tool_definitions(self) -> list[dict]:
        """获取所有远程工具的 OpenAI function-calling 格式定义。"""
        defs = []
        for tool in self._tools.values():
            defs.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{self.server_name}_{tool.name}",
                    "description": f"[MCP:{self.server_name}] {tool.description}",
                    "parameters": tool.inputSchema,
                },
            })
        return defs

    def _log(self, message: str) -> None:
        """日志输出到 stderr。"""
        print(f"[MCP Client:{self.server_name}] {message}", file=sys.stderr, flush=True)


# ═══════════ Agent 集成 ═══════════

class MCPToolWrapper:
    """将外部 MCP 工具包装为 PaperWise BaseTool，可直接注册到 ToolRegistry。

    Agent 可以像调用本地工具一样调用外部 MCP 工具，
    无需关心底层是本地还是远程。
    """

    def __init__(self, tool_def: MCPToolDef, client: MCPClient,
                 workspace: Path, prefix: str = ""):
        from paperwise.tools.base import BaseTool, ToolDefinition
        from paperwise.core.types import ToolRisk

        self._def = tool_def
        self._client = client
        self._ws = workspace
        self._prefix = prefix or f"mcp_{client.server_name}_"

        # 构造本地工具名称（加前缀避免冲突）
        self._local_name = f"{self._prefix}{tool_def.name}"

        # 构造 ToolDefinition
        self._definition = ToolDefinition(
            name=self._local_name,
            description=f"[MCP:{client.server_name}] {tool_def.description}",
            parameters=tool_def.inputSchema,
            risk=ToolRisk.MEDIUM,
        )

        # 实现 BaseTool 接口
        self.workspace = workspace

    @property
    def definition(self):
        return self._definition

    async def execute(self, **kwargs) -> str:
        """转发工具调用到远程 MCP 服务器。"""
        return await self._client.call_tool(self._def.name, kwargs)

    def validate_args(self, **kwargs) -> None:
        """验证参数（基本检查）。"""
        required = self._def.inputSchema.get("required", [])
        for key in required:
            if key not in kwargs:
                raise ValueError(f"Missing required parameter: '{key}'")

    def allow_read_path(self, path: Path) -> None:
        """MCP 工具不维护白名单（由远程服务器管理安全）。"""
        pass


async def connect_mcp_server(server_name: str, config: MCPServerConfig,
                             registry, workspace: Path) -> int:
    """连接一个外部 MCP 服务器并将其工具注册到 ToolRegistry。

    Returns:
        注册的工具数量。失败返回 0。
    """
    client = MCPClient(server_name, config)
    try:
        tools = await client.connect()
    except Exception as e:
        print(f"[MCP] Failed to connect to '{server_name}': {e}", file=sys.stderr)
        return 0

    count = 0
    for tool_def in tools:
        wrapper = MCPToolWrapper(tool_def, client, workspace)
        registry.register(wrapper)
        count += 1

    return count
