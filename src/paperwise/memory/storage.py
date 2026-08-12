"""存储抽象层 — 可插拔的持久化后端

默认使用 SQLite（零外部依赖，ACID 事务，并发安全），
同时保留 JSON 文件后端用于向后兼容。
接口开放给 Redis/Milvus/PostgreSQL 等外部数据库。

架构：
    StorageBackend (ABC)
    ├── SQLiteBackend   ← 默认（推荐）
    └── JSONFileBackend ← 向后兼容
"""

import json
import sqlite3
import threading
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Optional, Any


# ══════════ 抽象基类 ══════════

class StorageBackend(ABC):
    """持久化存储后端抽象。"""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def get(self, collection: str, key: str) -> Optional[dict]: ...

    @abstractmethod
    def put(self, collection: str, key: str, data: dict) -> None: ...

    @abstractmethod
    def delete(self, collection: str, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, collection: str, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def count(self, collection: str) -> int: ...


# ══════════ SQLite 后端（默认） ══════════

class SQLiteBackend(StorageBackend):
    """SQLite 存储后端 — 零外部依赖，ACID 事务，并发安全。

    表结构：
        CREATE TABLE IF NOT EXISTS {collection} (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )

    优势 vs JSON 文件：
    - 无需加载全部数据到内存（O(1) 查询）
    - ACID 事务（不会写入损坏）
    - 并发安全（WAL 模式）
    - SQL 查询 + 索引
    - 自动迁移旧 JSON 数据
    """

    def __init__(self, db_path: Path, auto_migrate: bool = True):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._auto_migrate = auto_migrate

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")     # 并发安全
        self._conn.execute("PRAGMA synchronous=NORMAL")   # 性能与安全平衡
        self._conn.execute("PRAGMA cache_size=-64000")    # 64MB 缓存
        # 自动迁移旧 JSON 数据
        if self._auto_migrate:
            self._migrate_from_json()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_table(self, collection: str) -> None:
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._safe_name(collection)} (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{self._safe_name(collection)}_updated
            ON {self._safe_name(collection)}(updated_at)
        """)

    def _safe_name(self, name: str) -> str:
        """安全化表名：只保留字母数字下划线。"""
        import re
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    def _migrate_from_json(self) -> None:
        """自动从旧 JSON 文件迁移数据。"""
        json_dir = self._db_path.parent
        for json_file in sorted(json_dir.glob("*.json")):
            collection = json_file.stem
            if not self._conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (self._safe_name(collection),)
            ).fetchone():
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if collection == "cards":
                        items = data.get("cards", [])
                    elif collection == "index":
                        items = [{"key": d["id"], "data": d} for d in data.get("docs", [])]
                    elif collection == "evolution_state":
                        items = [
                            {"key": f"traj_{i}", "data": t}
                            for i, t in enumerate(data.get("trajectories", []))
                        ]
                    else:
                        items = [{"key": k, "data": v} for k, v in data.items()]
                    for item in items:
                        self.put(collection, item["key"], item["data"])
                    print(f"[Storage] Migrated {len(items)} items from {json_file.name}")
                except Exception:
                    pass

    def get(self, collection: str, key: str) -> Optional[dict]:
        if not self._conn: self.connect()
        self._ensure_table(collection)
        row = self._conn.execute(
            f"SELECT data FROM {self._safe_name(collection)} WHERE key=?",
            (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, collection: str, key: str, data: dict) -> None:
        if not self._conn: self.connect()
        self._ensure_table(collection)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {self._safe_name(collection)} (key, data, updated_at) "
                f"VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, json.dumps(data, ensure_ascii=False))
            )
            self._conn.commit()

    def delete(self, collection: str, key: str) -> bool:
        if not self._conn: self.connect()
        self._ensure_table(collection)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM {self._safe_name(collection)} WHERE key=?", (key,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def list_keys(self, collection: str, prefix: str = "") -> list[str]:
        if not self._conn: self.connect()
        self._ensure_table(collection)
        if prefix:
            rows = self._conn.execute(
                f"SELECT key FROM {self._safe_name(collection)} WHERE key LIKE ?",
                (f"{prefix}%",)
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT key FROM {self._safe_name(collection)}"
            ).fetchall()
        return [r[0] for r in rows]

    def count(self, collection: str) -> int:
        if not self._conn: self.connect()
        self._ensure_table(collection)
        return self._conn.execute(
            f"SELECT COUNT(*) FROM {self._safe_name(collection)}"
        ).fetchone()[0]


# ══════════ JSON 文件后端（向后兼容） ══════════

class JSONFileBackend(StorageBackend):
    """JSON 文件后端 — 向后兼容旧格式。

    每个 collection 一个 JSON 文件。
    适合小数据集（< 10K 条记录）。
    """

    def __init__(self, data_dir: Path):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def connect(self) -> None: pass

    def close(self) -> None: pass

    def _file(self, collection: str) -> Path:
        return self._dir / f"{collection}.json"

    def _load(self, collection: str) -> dict:
        if collection not in self._cache:
            f = self._file(collection)
            if f.exists():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    # 处理不同格式
                    if "cards" in data:
                        self._cache[collection] = {c["card_id"]: c for c in data["cards"]}
                    elif "docs" in data:
                        self._cache[collection] = {d["id"]: d for d in data["docs"]}
                    else:
                        self._cache[collection] = data
                except Exception:
                    self._cache[collection] = {}
            else:
                self._cache[collection] = {}
        return self._cache[collection]

    def _save(self, collection: str) -> None:
        if collection in self._cache:
            f = self._file(collection)
            f.write_text(json.dumps(self._cache[collection], ensure_ascii=False, indent=2),
                         encoding="utf-8")

    def get(self, collection: str, key: str) -> Optional[dict]:
        return self._load(collection).get(key)

    def put(self, collection: str, key: str, data: dict) -> None:
        with self._lock:
            self._load(collection)[key] = data
            self._save(collection)

    def delete(self, collection: str, key: str) -> bool:
        with self._lock:
            d = self._load(collection)
            if key in d:
                del d[key]; self._save(collection); return True
            return False

    def list_keys(self, collection: str, prefix: str = "") -> list[str]:
        keys = list(self._load(collection).keys())
        return [k for k in keys if k.startswith(prefix)] if prefix else keys

    def count(self, collection: str) -> int:
        return len(self._load(collection))


# ══════════ 工厂函数 ══════════

def create_storage(backend: str = "sqlite", path: Path = None, **kwargs) -> StorageBackend:
    """创建存储后端。

    Args:
        backend: "sqlite" (推荐), "json" (向后兼容)
        path: 数据目录或数据库文件路径
        **kwargs: 传递给后端的额外参数

    Examples:
        # 默认 SQLite
        store = create_storage(path=Path("./data"))

        # JSON 兼容
        store = create_storage("json", path=Path("./data"))

        # 外部数据库（占位接口）
        store = create_storage("redis", url="redis://localhost:6379")
    """
    if backend == "sqlite":
        # 统一语义：path 始终是数据目录（不存在则创建），数据库文件为 paperwise.db
        db_dir = Path(path)
        db_dir.mkdir(parents=True, exist_ok=True)
        return SQLiteBackend(db_dir / "paperwise.db")
    elif backend == "json":
        return JSONFileBackend(path)
    elif backend == "redis":
        raise NotImplementedError("Redis backend: 需要安装 redis-py。传入 url 参数。")
    elif backend == "milvus":
        raise NotImplementedError("Milvus backend: 用于向量检索。传入 host/port。")
    else:
        raise ValueError(f"Unknown backend: {backend}")
