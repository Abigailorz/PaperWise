"""工具基类 — 所有工具的抽象接口，MCP 兼容

对应书中 4.2 节：工具设计通用原则
- 描述写清楚"何时用"而非仅"能做什么"
- 反例（DO NOT use for）必不可少
- 参数用具体例子
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable, Awaitable

from paperwise.core.types import ToolRisk


@dataclass
class ToolDefinition:
    """工具定义 — 对应 MCP 协议的工具描述格式"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    risk: ToolRisk = ToolRisk.LOW


class AccessRequested(Exception):
    """文件访问需要用户授权的异常。

    Agent 工具在遇到此异常时不应视为错误，
    而应返回引导信息，提示 Agent 使用 request_file_access 工具申请授权。

    仅对非危险路径抛出此异常；
    危险路径（DANGEROUS_PATH_PATTERNS 匹配）仍抛出 ValueError。
    """
    def __init__(self, path: str, resolved: Path, mode: str = "read"):
        self.path = path
        self.resolved = resolved
        self.mode = mode  # "read" | "write"
        super().__init__(
            f"[Permission Required] 访问 '{path}' 需要授权。"
            f"请使用 request_file_access 工具申请 {mode} 权限。"
        )


class BaseTool(ABC):
    """所有工具的抽象基类。

    子类需要实现：
    - definition 属性：返回 ToolDefinition
    - execute 方法：执行工具逻辑
    """

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        # 读取白名单：允许读取的外部路径（如 PDF 上传目录）
        self._allowed_read_paths: list[Path] = [self.workspace]

    def allow_read_path(self, path: Path) -> None:
        """添加一个外部路径到读取白名单。路径将被 resolve 后加入。"""
        p = Path(path).resolve()
        if p.is_file():
            p = p.parent
        if p not in self._allowed_read_paths:
            self._allowed_read_paths.append(p)

    def has_read_access(self, path: Path) -> bool:
        """检查给定已 resolve 的路径是否可读。"""
        rp = Path(path).resolve()
        for allowed in self._allowed_read_paths:
            try:
                rp.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回工具的定义（名称、描述、参数 schema、风险等级）"""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """执行工具逻辑。返回结果字符串。"""
        ...

    def validate_args(self, **kwargs) -> None:
        """验证参数是否符合工具 schema。"""
        schema = self.definition.parameters
        required = schema.get("required", [])
        for key in required:
            if key not in kwargs:
                raise ValueError(f"Missing required parameter: '{key}'")

    def _resolve_path(self, path: str, for_write: bool = False) -> Path:
        """将路径解析为绝对路径，实施分级沙箱策略。

        安全分级：
        - 写操作（for_write=True）：严格限制在 workspace 内
        - 读操作（for_write=False）：限制在 workspace + 白名单路径内
          · 在白名单内 → 直接通过
          · 不在白名单但不危险 → 抛出 AccessRequested（Agent 可申请授权）
          · 危险路径 → 抛出 ValueError（永不放过）
        - 所有操作：绝对路径需显式在白名单中
        """
        import platform
        p = Path(path)
        is_windows = platform.system() == "Windows"

        # Unix 风格绝对路径在 Windows 上不识别为 absolute
        # 手动检测：以 / 开头的非 Windows 路径（如 /etc/passwd, /proc/cpuinfo）
        is_unix_abs = bool(
            is_windows and path.startswith("/")
            and not path.startswith("//")
            and not (len(path) > 2 and path[1] == ":")
        )

        if p.is_absolute() or is_unix_abs:
            # 对 Unix 绝对路径在 Windows 上先做危险检查
            if is_unix_abs:
                from paperwise.harness.security import check_path_dangerous
                if check_path_dangerous(path):
                    raise ValueError(
                        f"访问被拒绝：'{path}' 指向受保护的系统路径。"
                        f"出于安全考虑，此路径不允许通过任何方式访问。"
                    )
                raise AccessRequested(path, Path(path), mode="read")

            resolved = p.resolve() if p.is_absolute() else Path(path)

            if for_write:
                ws = self.workspace
                if not str(resolved).startswith(str(ws)):
                    raise AccessRequested(path, resolved, mode="write")
                return resolved

            # 读操作：检查是否在白名单内
            if p.is_absolute() and self.has_read_access(resolved):
                return resolved

            # 不在白名单 → 检查是否为危险路径
            from paperwise.harness.security import check_path_dangerous
            if check_path_dangerous(str(path)):
                raise ValueError(
                    f"访问被拒绝：'{path}' 指向受保护的系统路径。"
                    f"出于安全考虑，此路径不允许通过任何方式访问。"
                )

            # 非危险路径 → 提示 Agent 可以申请授权
            raise AccessRequested(path, resolved if p.is_absolute() else Path(path),
                                  mode="read")

        # 相对路径：相对于 workspace 解析
        resolved = (self.workspace / p).resolve()
        ws = self.workspace

        if for_write:
            if not str(resolved).startswith(str(ws)):
                raise ValueError(
                    f"写入越界被阻止：'{path}' 解析到 '{resolved}'，"
                    f"超出工作目录 '{ws}'。写操作严格限制在工作目录内。"
                )
        else:
            if not self.has_read_access(resolved):
                from paperwise.harness.security import check_path_dangerous
                if check_path_dangerous(str(resolved)):
                    raise ValueError(
                        f"访问被拒绝：'{path}' 指向受保护的系统路径。"
                    )
                raise AccessRequested(path, resolved, mode="read")

        return resolved
