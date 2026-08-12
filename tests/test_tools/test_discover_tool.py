"""动态工具发现测试"""

import asyncio

from paperwise.tools.registry import ToolRegistry


def test_discover_tool_returns_matching_definition(tmp_path):
    registry = ToolRegistry.create_default(tmp_path)
    tool = registry.get("discover_tool")

    out = asyncio.run(tool.execute("read"))
    assert "read_file" in out
    assert "参数" in out


def test_discover_tool_empty_query_lists_all(tmp_path):
    registry = ToolRegistry.create_default(tmp_path)
    tool = registry.get("discover_tool")

    out = asyncio.run(tool.execute(""))
    assert "write_file" in out
    assert "grep" in out


def test_discover_tool_no_match_suggests(tmp_path):
    registry = ToolRegistry.create_default(tmp_path)
    tool = registry.get("discover_tool")

    out = asyncio.run(tool.execute("zzz_no_such_thing"))
    assert "未找到" in out
