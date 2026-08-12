"""文件锁 — 多 Agent 共享文件系统的写入冲突保护。

对应书中 10.5 节：共享文件系统并发冲突检测。
写入工具在执行前申请锁，其他 Agent 写同一文件时会被阻塞并提示。
锁带 TTL，超时后可被抢占（避免死锁）。
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional


class FileLockManager:
    """基于 workspace 内 .locks.json 的轻量文件锁。"""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self._locks_file = self.workspace / ".locks.json"

    def acquire(self, path: Path, owner: str = "main",
                ttl_seconds: int = 600) -> Optional[str]:
        """申请文件锁。

        Args:
            path: 目标文件路径（已 resolve 或相对 workspace）
            owner: 申请者标识（Agent 名）
            ttl_seconds: 锁有效期，超时后可被抢占

        Returns:
            成功返回 lock_id；被占用返回 None。
        """
        resolved = str(Path(path).resolve())
        locks = self._load()
        existing = locks.get(resolved)
        if existing:
            if time.time() - existing.get("time", 0) > ttl_seconds:
                locks.pop(resolved, None)  # 过期锁可抢占
            else:
                return None
        lock_id = uuid.uuid4().hex[:8]
        locks[resolved] = {
            "owner": owner,
            "time": time.time(),
            "lock_id": lock_id,
        }
        self._save(locks)
        return lock_id

    def release(self, path: Path, lock_id: str) -> bool:
        """释放锁。仅当 lock_id 匹配时才释放（防止误释放）。"""
        resolved = str(Path(path).resolve())
        locks = self._load()
        if locks.get(resolved, {}).get("lock_id") == lock_id:
            locks.pop(resolved, None)
            self._save(locks)
            return True
        return False

    def owner(self, path: Path) -> Optional[str]:
        """查询当前锁持有者。"""
        resolved = str(Path(path).resolve())
        locks = self._load()
        return locks.get(resolved, {}).get("owner")

    def _load(self) -> dict:
        try:
            return json.loads(self._locks_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, locks: dict) -> None:
        self._locks_file.parent.mkdir(parents=True, exist_ok=True)
        self._locks_file.write_text(
            json.dumps(locks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
