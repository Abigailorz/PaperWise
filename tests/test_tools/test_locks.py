"""文件锁测试 — 多 Agent 写入冲突保护"""

import asyncio

from paperwise.tools.locks import FileLockManager
from paperwise.tools.file_tools import WriteFileTool


def test_write_blocked_while_locked(tmp_path):
    ws = tmp_path
    target = "data.txt"
    lock_mgr = FileLockManager(ws)
    lock_id = lock_mgr.acquire(ws / target, owner="other_agent")
    assert lock_id

    tool = WriteFileTool(ws)
    out = asyncio.run(tool.execute(target, "hello"))
    assert "Blocked" in out
    assert "other_agent" in out

    lock_mgr.release(ws / target, lock_id)
    out2 = asyncio.run(tool.execute(target, "hello"))
    assert "Successfully wrote" in out2


def test_lock_expired_can_be_reacquired(tmp_path):
    lock_mgr = FileLockManager(tmp_path)
    p = tmp_path / "f.txt"

    first = lock_mgr.acquire(p, owner="a", ttl_seconds=-1)
    assert first
    second = lock_mgr.acquire(p, owner="b", ttl_seconds=-1)
    assert second and second != first
