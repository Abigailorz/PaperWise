"""MCP Server — JSON-RPC 2.0 over stdio

将 PaperWise 的 ToolRegistry 暴露为标准 MCP 接口，
外部 MCP 客户端（Claude Desktop 等）可直接连接并使用全部 13+ 个工具。

协议: MCP 2024-11-05
传输: JSON-RPC 2.0 over stdin/stdout (newline-delimited JSON)

Usage:
    paperwise mcp-serve              # 从 CLI 启动
    python -m paperwise.mcp.server   # 直接运行
"""

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Optional

from paperwise.tools.registry import ToolRegistry


class MCPServer:
    """MCP 标准 stdio 服务器。

    暴露 PaperWise 全部工具给外部 MCP 客户端。

    支持的方法：
    - initialize         → 协议握手，交换 capabilities
    - notifications/initialized → 客户端就绪确认
    - tools/list         → 返回所有可用工具的 JSON Schema 列表
    - tools/call         → 执行指定工具并返回结果
    - resources/list     → 返回可用资源列表（workspace 文件）
    - resources/read     → 读取指定资源内容
    - ping               → 心跳检测
    """

    SERVER_NAME = "paperwise"
    SERVER_VERSION = "0.5.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, tool_registry: ToolRegistry, workspace: Optional[Path] = None,
                 skill_loader=None):
        self.tool_registry = tool_registry
        self.workspace = workspace or tool_registry.workspace
        self.skill_loader = skill_loader
        self._initialized = False

    # ═══════════ 主循环 ═══════════

    def run_sync(self):
        """启动 stdio 事件循环（同步模式，使用后台线程）。

        每行一个 JSON-RPC 消息（newline-delimited JSON）。
        服务器在独立线程中处理 I/O，工具执行在 async 事件循环中。
        """
        import threading
        import queue

        self._log("PaperWise MCP Server started (stdio, sync mode)")

        # 创建事件循环用于异步工具执行
        loop = asyncio.new_event_loop()
        thread_queue: queue.Queue = queue.Queue()

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        loop_thread = threading.Thread(target=run_loop, daemon=True)
        loop_thread.start()

        # 主循环：同步读取 stdin，每行一个 JSON-RPC 消息
        try:
            for line in sys.stdin:
                text = line.strip()
                if not text:
                    continue

                try:
                    request = json.loads(text)
                except json.JSONDecodeError:
                    self._log(f"Invalid JSON: {text[:100]}")
                    continue

                # 在事件循环中处理请求
                future = asyncio.run_coroutine_threadsafe(
                    self._handle_request(request), loop
                )
                # 非通知请求等待结果
                if request.get("id") is not None:
                    try:
                        future.result(timeout=300)
                    except Exception:
                        self._log(f"Request timeout: {request.get('method', '?')}")

        except KeyboardInterrupt:
            pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=5)
            self._log("Server stopped")

    async def run(self):
        """启动 stdio 事件循环（async 模式，需要 stdin pipe）。

        使用 connect_read_pipe 连接 stdin buffer。
        适用于以 asyncio 子进程方式启动时。
        """
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            sys.stdin.buffer
        )

        self._log("PaperWise MCP Server started (stdio, async mode)")

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    request = json.loads(text)
                except json.JSONDecodeError:
                    self._log(f"Invalid JSON: {text[:100]}")
                    continue

                await self._handle_request(request)

            except asyncio.CancelledError:
                break
            except Exception:
                self._log(f"Error in main loop: {traceback.format_exc()}")

    def _write_stdout(self, data: str) -> None:
        """写入 stdout（UTF-8 编码，立即刷新）。

        使用 buffer.write 而非 sys.stdout.write，
        避免 Windows 中文系统 GBK 编码导致乱码。
        """
        sys.stdout.buffer.write(data.encode("utf-8"))
        sys.stdout.buffer.flush()

    # ═══════════ 请求分发 ═══════════

    async def _handle_request(self, request: dict) -> None:
        """分发 JSON-RPC 请求。"""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "notifications/initialized":
                self._initialized = True
                return  # 通知不返回响应
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self._handle_tools_list()
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "resources/list":
                result = self._handle_resources_list()
            elif method == "resources/read":
                result = await self._handle_resources_read(params)
            elif method == "prompts/list":
                result = self._handle_prompts_list()
            elif method == "prompts/get":
                result = self._handle_prompts_get(params)
            else:
                self._send_error(req_id, -32601, f"Method not found: {method}")
                return

            self._send_result(req_id, result)

        except Exception as e:
            self._log(f"Error handling {method}: {traceback.format_exc()}")
            self._send_error(req_id, -32603, f"Internal error: {e}")

    # ═══════════ initialize ═══════════

    def _handle_initialize(self, params: dict) -> dict:
        """协议握手。返回服务器 capabilities。"""
        client_info = params.get("clientInfo", {})
        self._log(f"Client connected: {client_info.get('name', 'unknown')} "
                  f"v{client_info.get('version', '?')}")

        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},        # 支持工具调用
                "resources": {},    # 支持资源访问
                "prompts": {},      # 支持提示模板
            },
            "serverInfo": {
                "name": self.SERVER_NAME,
                "version": self.SERVER_VERSION,
            },
        }

    # ═══════════ tools/list ═══════════

    def _handle_tools_list(self) -> dict:
        """返回所有可用工具列表（MCP 标准格式）。"""
        tool_defs = self.tool_registry.get_definitions()
        tools = []
        for td in tool_defs:
            tools.append({
                "name": td["function"]["name"],
                "description": td["function"]["description"],
                "inputSchema": td["function"]["parameters"],
            })
        return {"tools": tools}

    # ═══════════ tools/call ═══════════

    async def _handle_tools_call(self, params: dict) -> dict:
        """执行工具调用。"""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        self._log(f"Tool call: {tool_name}({json.dumps(arguments, ensure_ascii=False)[:100]})")

        try:
            tool = self.tool_registry.get(tool_name)
        except KeyError:
            return {
                "content": [{"type": "text", "text": f"Tool not found: {tool_name}"}],
                "isError": True,
            }

        try:
            result = await tool.execute(**arguments)
            is_error = isinstance(result, str) and result.startswith("[Error]")
        except Exception as e:
            result = f"[Error] Tool execution failed: {e}"
            is_error = True

        # 截断过长输出
        if len(result) > 50000:
            result = result[:50000] + "\n[... output truncated ...]"

        return {
            "content": [{"type": "text", "text": result}],
            "isError": is_error,
        }

    # ═══════════ resources/list ═══════════

    def _handle_resources_list(self) -> dict:
        """返回 workspace 中的可用资源。"""
        resources = []
        if self.workspace and self.workspace.exists():
            for f in sorted(self.workspace.rglob("*"))[:100]:
                if f.is_file() and f.suffix in (".md", ".txt", ".json", ".tex", ".py", ".yaml"):
                    try:
                        rel = f.relative_to(self.workspace)
                        resources.append({
                            "uri": f"file:///{rel.as_posix()}",
                            "name": rel.as_posix(),
                            "mimeType": "text/plain",
                        })
                    except ValueError:
                        pass
        return {"resources": resources}

    # ═══════════ resources/read ═══════════

    async def _handle_resources_read(self, params: dict) -> dict:
        """读取指定资源。"""
        uri = params.get("uri", "")
        # 从 URI 中提取相对路径
        if uri.startswith("file:///"):
            rel_path = uri[len("file:///"):]
        else:
            rel_path = uri

        file_path = self.workspace / rel_path if self.workspace else Path(rel_path)

        try:
            if not file_path.exists():
                return {
                    "contents": [{"uri": uri, "mimeType": "text/plain",
                                  "text": f"Resource not found: {rel_path}"}]
                }
            content = file_path.read_text(encoding="utf-8")
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": content[:50000],
                }]
            }
        except Exception as e:
            return {
                "contents": [{"uri": uri, "mimeType": "text/plain",
                              "text": f"Error reading resource: {e}"}]
            }

    # ═══════════ prompts/list ═══════════

    def _handle_prompts_list(self) -> dict:
        """返回可用提示模板（基于真实 Skills + 内置模板）。"""
        prompts = []

        # 从 SkillLoader 动态加载
        if self.skill_loader:
            for skill in self.skill_loader._catalog:
                prompts.append({
                    "name": f"skill-{skill['name']}",
                    "description": skill["description"][:120],
                    "arguments": [],
                })

        # 内置模板
        prompts.extend([
            {
                "name": "analyze-paper",
                "description": "深度分析一篇学术论文，生成结构化解读报告",
                "arguments": [
                    {"name": "paper_title", "description": "论文标题", "required": True},
                    {"name": "focus_area", "description": "分析重点（methodology/experiments/overview）",
                     "required": False},
                ],
            },
            {
                "name": "generate-report",
                "description": "基于分析结果生成正式报告",
                "arguments": [
                    {"name": "paper_id", "description": "论文标识符", "required": True},
                    {"name": "format", "description": "输出格式 (markdown/latex)", "required": False},
                ],
            },
        ])
        return {"prompts": prompts}

    def _handle_prompts_get(self, params: dict) -> dict:
        """获取指定提示模板。"""
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        prompts = {
            "analyze-paper": (
                f"请深度分析论文「{arguments.get('paper_title', 'Unknown')}」。\n"
                f"重点：{arguments.get('focus_area', '全面分析')}。\n"
                f"按以下结构输出：1. 概述 2. 动机 3. 方法 4. 实验 5. 批判性分析 6. 结论"
            ),
            "generate-report": (
                f"请基于已分析的内容，为论文 {arguments.get('paper_id', '')} "
                f"生成一份完整的学术解读报告，格式为 {arguments.get('format', 'markdown')}。"
            ),
            "generate-slides": (
                f"请基于已分析的内容，为论文 {arguments.get('paper_id', '')} "
                f"生成一份 10-15 页的学术汇报 PPT。"
            ),
        }
        text = prompts.get(name, f"Prompt not found: {name}")
        return {
            "description": f"Prompt: {name}",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }

    # ═══════════ JSON-RPC 序列化 ═══════════

    def _send_result(self, req_id, result: dict) -> None:
        """发送成功响应。"""
        if req_id is None:
            return  # 通知不返回响应
        self._send_json({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send_error(self, req_id, code: int, message: str) -> None:
        """发送错误响应。"""
        if req_id is None:
            return
        self._send_json({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message},
        })

    def _send_json(self, data: dict) -> None:
        """将 JSON 写入 stdout（每行一个消息，立即刷新）。"""
        line = json.dumps(data, ensure_ascii=False) + "\n"
        self._write_stdout(line)

    # ═══════════ 日志 ═══════════

    def _log(self, message: str) -> None:
        """向 stderr 写日志（避免污染 stdout JSON-RPC 流）。"""
        print(f"[MCP Server] {message}", file=sys.stderr, flush=True)


# ═══════════ 入口 ═══════════

def main():
    """CLI 入口：paperwise mcp-serve"""
    from paperwise.config.settings import get_settings
    from paperwise.skills.loader import SkillLoader

    settings = get_settings()
    ws = settings.workspace_dir
    ws.mkdir(parents=True, exist_ok=True)

    # 加载 Skills
    skills_dir = Path(__file__).resolve().parent.parent.parent.parent / "skills"
    if not skills_dir.exists():
        skills_dir = Path("skills")
    skill_loader = SkillLoader(skills_dir) if Path(skills_dir).exists() else None

    # 创建默认工具注册中心
    tools = ToolRegistry.create_default(ws)
    if skill_loader:
        tools.set_skill_loader(skill_loader)

    # 创建并启动 MCP 服务器（同步模式，线程安全）
    server = MCPServer(tools, ws, skill_loader=skill_loader)
    server.run_sync()


if __name__ == "__main__":
    main()
