"""文件操作工具测试"""

import pytest
from pathlib import Path
from paperwise.tools.file_tools import ReadFileTool, WriteFileTool, EditFileTool


class TestReadFileTool:
    async def test_read_existing_file(self, workspace: Path):
        tool = ReadFileTool(workspace)
        (workspace / "test.txt").write_text("line1\nline2\nline3")
        result = await tool.execute(path="test.txt")
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    async def test_read_nonexistent_file(self, workspace: Path):
        tool = ReadFileTool(workspace)
        result = await tool.execute(path="nonexistent.txt")
        assert "[Error]" in result

    async def test_read_with_offset_limit(self, workspace: Path):
        tool = ReadFileTool(workspace)
        (workspace / "test.txt").write_text("\n".join(str(i) for i in range(100)))
        result = await tool.execute(path="test.txt", offset=5, limit=3)
        assert "     5|4" in result
        assert "     6|5" in result
        assert "     7|6" in result


class TestWriteFileTool:
    async def test_write_new_file(self, workspace: Path):
        tool = WriteFileTool(workspace)
        result = await tool.execute(path="output.md", content="# Title\nContent")
        assert "Successfully wrote" in result
        assert (workspace / "output.md").exists()

    async def test_overwrite_file(self, workspace: Path):
        tool = WriteFileTool(workspace)
        (workspace / "output.md").write_text("old content")
        await tool.execute(path="output.md", content="new content")
        assert (workspace / "output.md").read_text() == "new content"


class TestEditFileTool:
    async def test_edit_unique_string(self, workspace: Path):
        tool = EditFileTool(workspace)
        (workspace / "file.txt").write_text("Hello World")
        result = await tool.execute(path="file.txt", search="Hello", replace="Hi")
        assert "Successfully" in result
        assert (workspace / "file.txt").read_text() == "Hi World"

    async def test_edit_nonunique_string(self, workspace: Path):
        tool = EditFileTool(workspace)
        (workspace / "file.txt").write_text("Hello\nHello")
        result = await tool.execute(path="file.txt", search="Hello", replace="Hi")
        assert "[Error]" in result
        assert "found 2 times" in result

    async def test_edit_not_found(self, workspace: Path):
        tool = EditFileTool(workspace)
        (workspace / "file.txt").write_text("Hello")
        result = await tool.execute(path="file.txt", search="NotFound", replace="X")
        assert "[Error]" in result
        assert "not found" in result
