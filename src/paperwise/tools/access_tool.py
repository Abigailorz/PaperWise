"""文件访问申请工具 — 实现"阻断 → 门禁"的动态授权机制

对应书中 4.2 节：工具的权限模型设计

核心设计：
- read 模式：用户同意后，将路径父目录加入所有工具的读取白名单
- write 模式：用户同意后，将文件拷贝到 workspace/.sandbox/ 中操作，保护原文件
- 危险路径：永不提供申请通道（由 check_path_dangerous 保证）
"""

import shutil
from pathlib import Path
from typing import Optional, Callable, Awaitable

from paperwise.tools.base import BaseTool, ToolDefinition
from paperwise.core.types import ToolRisk
from paperwise.harness.security import check_path_dangerous


class RequestFileAccessTool(BaseTool):
    """文件访问申请工具。

    当 Agent 需要读取或修改 workspace 之外的文件时，
    使用此工具向用户申请授权。

    授权分级：
    - "read"  → 用户同意后直接读取原文件（只读）
    - "write" → 文件拷贝到 workspace/.sandbox/ 中，Agent 在沙箱内操作，原文件不被修改
    """

    def __init__(self, workspace: Path, tool_registry=None,
                 user_confirm: Optional[Callable[[str, str], Awaitable[bool]]] = None):
        super().__init__(workspace)
        self._registry = tool_registry   # ToolRegistry 引用，用于广播 allowlist
        self._user_confirm = user_confirm  # 异步回调: (question, detail) -> bool

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="request_file_access",
            description=(
                "当你需要读取或写入 workspace 之外的文件时，使用此工具向用户申请授权。\n\n"
                "何时使用：\n"
                "- 用户让你分析一篇不在工作目录中的论文\n"
                "- 需要引用外部数据文件或配置文件\n"
                "- 需要在工作目录外的文件中写入结果\n\n"
                "不要用于：\n"
                "- workspace 内的文件（直接操作即可，无需申请）\n"
                "- 系统文件（/etc/、C:\\Windows\\、.ssh/ 等，出于安全原因不允许）\n"
                "- 试探性访问（先确认用户是否需要，再申请）\n\n"
                "参数说明：\n"
                "- mode='read'  → 申请读取权限（只读原文件）\n"
                "- mode='write' → 申请写入权限（拷贝到 sandbox，原文件安全）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "需要访问的文件或目录的绝对路径。例如：'D:/Desktop/论文.pdf'"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "申请权限类型：'read' 只读，'write' 读写（拷贝到 sandbox）"
                    },
                    "reason": {
                        "type": "string",
                        "description": "简短说明为什么需要访问此文件。例如：'用户要求分析此论文'"
                    },
                },
                "required": ["path", "mode", "reason"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, path: str, mode: str, reason: str) -> str:
        # Step 1: 解析路径
        try:
            file_path = Path(path).resolve()
        except Exception:
            return f"[Error] 无法解析路径: {path}"

        # Step 2: 危险路径检查（永不放过）—— 必须在文件存在性检查之前
        # 同时检查原始输入和 resolved 路径（防止 Windows 上 /etc/ 被转为 C:\etc\）
        dangerous = check_path_dangerous(path) or check_path_dangerous(str(file_path))
        if dangerous:
            return (
                f"[Blocked] 出于安全原因，此路径不允许访问。\n"
                f"路径: {file_path}\n"
                f"匹配危险模式: {dangerous}\n"
                f"系统目录、凭证文件、浏览器数据等永远不允许通过 Agent 访问。"
            )

        # Step 3: 文件存在性检查
        if not file_path.exists():
            return (
                f"[Error] 文件不存在: {path}\n"
                f"请确认路径是否正确。如果文件在其他位置，请提供完整路径。"
            )

        # Step 4: 用户确认
        if mode == "read":
            question = f"允许 PaperWise 读取以下文件？"
            detail = (
                f"文件: {file_path}\n"
                f"原因: {reason}\n"
                f"权限: 只读（原文件不会被修改）"
            )
        else:  # write
            sandbox_dir = self.workspace / ".sandbox"
            # 保持相对路径结构
            try:
                rel = file_path.relative_to(Path.home())
                sandbox_path = sandbox_dir / "home" / rel
            except ValueError:
                sandbox_path = sandbox_dir / file_path.name
            question = f"允许 PaperWise 编辑以下文件？"
            detail = (
                f"文件: {file_path}\n"
                f"原因: {reason}\n"
                f"操作: 文件将被拷贝到工作目录的 sandbox 中\n"
                f"拷贝目标: {sandbox_path}\n"
                f"⚠️ 原文件不会被修改。修改后的文件在 sandbox 中。"
            )

        if self._user_confirm:
            try:
                approved = await self._user_confirm(question, detail)
            except Exception:
                approved = False
        else:
            # 无用户确认通道 → 返回提示
            return (
                f"[需用户确认]\n\n"
                f"{question}\n\n"
                f"{detail}\n\n"
                f"请用户在对话中回复 '允许' 或 '拒绝'。\n"
                f"CLI 模式下请使用 --allow-external-read 参数启动。"
            )

        if not approved:
            return (
                f"[已拒绝] 用户拒绝了访问 '{file_path}' 的{mode}权限申请。\n"
                f"请寻找其他方式完成任务，或与用户沟通是否需要重新申请。"
            )

        # Step 4: 授权通过 → 执行操作
        if mode == "read":
            return await self._grant_read(file_path)
        else:
            return await self._grant_write(file_path, sandbox_path)

    async def _grant_read(self, file_path: Path) -> str:
        """授予读取权限：将父目录加入白名单。"""
        parent = file_path.parent

        # 添加到此工具自己的白名单
        self.allow_read_path(parent)

        # 广播到 ToolRegistry 中所有工具
        if self._registry:
            self._registry.allow_read_path(parent)

        return (
            f"[已授权] 读取权限已授予。\n"
            f"路径: {parent}\n"
            f"你现在可以使用 read_file、grep、glob 等工具访问此目录下的文件。\n"
            f"授权在当前会话期间有效。"
        )

    async def _grant_write(self, file_path: Path, sandbox_path: Path) -> str:
        """授予写入权限：拷贝到 sandbox。"""
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if file_path.is_file():
                shutil.copy2(file_path, sandbox_path)
            elif file_path.is_dir():
                if sandbox_path.exists():
                    shutil.rmtree(sandbox_path)
                shutil.copytree(file_path, sandbox_path)
        except Exception as e:
            return f"[Error] 拷贝文件到 sandbox 失败: {e}"

        # 将 sandbox 目录加入白名单
        sandbox_parent = sandbox_path.parent
        self.allow_read_path(sandbox_parent)
        if self._registry:
            self._registry.allow_read_path(sandbox_parent)

        return (
            f"[已授权] 文件已拷贝到 sandbox 中。\n"
            f"原文件: {file_path}\n"
            f"沙箱路径: {sandbox_path}\n\n"
            f"请使用沙箱路径进行操作。例如:\n"
            f"  read_file(\"{sandbox_path}\")\n"
            f"  write_file(\"{sandbox_path}\", content=\"...\")\n"
            f"  edit_file(\"{sandbox_path}\", search=\"...\", replace=\"...\")\n\n"
            f"原文件保持不变。如需将修改后的文件写回原位置，请告知用户手动操作。"
        )
