"""MCP (Model Context Protocol) 支持 — Server + Client

协议版本: 2024-11-05

提供:
- MCPServer: JSON-RPC over stdio，将 ToolRegistry 暴露给外部 MCP 客户端
  (Claude Desktop、VS Code Copilot 等可直接连接)
- MCPClient: 连接外部 MCP Server，将其工具动态注册到 Agent 的 ToolRegistry
  (例如连接文件系统 MCP Server、数据库 MCP Server 等)
"""

from paperwise.mcp.server import MCPServer
from paperwise.mcp.client import MCPClient

__all__ = ["MCPServer", "MCPClient"]
