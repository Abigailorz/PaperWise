"""ApplyPatch tool -- structured, precise file edits.

Replaces coarse write_file/edit_file for multi-line edits.
Uses the same patch format as the internal apply_patch helper.
"""

import re
from pathlib import Path

from paperwise.tools.base import BaseTool, ToolDefinition, AccessRequested
from paperwise.core.types import ToolRisk


def _format_access_denied(path: str, mode: str = "write") -> str:
    return (
        f"[Permission Required] 文件 '{path}' 不在当前工作目录内，需要授权才能{mode}。\n\n"
        f"请使用 request_file_access 工具申请访问权限。"
    )


class ApplyPatchTool(BaseTool):
    """Apply a structured patch to a file. Safer than overwriting whole files.

    Patch format (same as internal apply_patch grammar):

    *** Begin Patch
    *** Update File: path/to/file.py
    @@
     original line 1
    -line to remove
    +line to insert
     original line 2
    *** End Patch
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_patch",
            description=(
                "Apply a structured patch to an existing file. Use for precise "
                "multi-line edits, adding/removing sections, or fixing reports. "
                "The patch must use the exact format shown in the parameters. "
                "DO NOT use for: creating files from scratch (use write_file), "
                "reading files (use read_file)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to patch, relative to workspace or absolute."
                    },
                    "patch": {
                        "type": "string",
                        "description": (
                            "The patch string. Must start with '*** Begin Patch' and end with '*** End Patch'. "
                            "Supported headers: '*** Update File: <path>', '*** Add File: <path>', '*** Delete File: <path>'. "
                            "Change lines start with '+' (add), '-' (remove), or ' ' (context/match)."
                        )
                    }
                },
                "required": ["path", "patch"]
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, path: str, patch: str) -> str:
        try:
            file_path = self._resolve_path(path, for_write=True)
        except AccessRequested:
            return _format_access_denied(path)
        except ValueError as e:
            return f"[Error] {e}"

        from paperwise.tools.locks import FileLockManager
        owner = getattr(self, "_agent_name", "main")
        lock_mgr = FileLockManager(self.workspace)
        lock_id = lock_mgr.acquire(file_path, owner=owner)
        if lock_id is None:
            holder = lock_mgr.owner(file_path)
            return f"[Blocked] 文件 '{path}' 正被 Agent '{holder or '?'}' 写入。"

        try:
            try:
                result = apply_patch_to_file(file_path, patch)
            except PatchError as e:
                return f"[Error] Patch failed: {e}"

            # Validate Python syntax if applicable
            if file_path.suffix == ".py":
                try:
                    import py_compile
                    py_compile.compile(str(file_path), doraise=True)
                except py_compile.PyCompileError as e:
                    # Roll back is hard without a backup; just report it
                    return (
                        f"[Warning] Patch applied but file has Python syntax error: {e}. "
                        f"Please re-patch or overwrite."
                    )

            return f"Successfully applied patch to {path} ({result})"
        finally:
            lock_mgr.release(file_path, lock_id)


class PatchError(Exception):
    pass


def apply_patch_to_file(file_path: Path, patch: str) -> str:
    """Parse a patch string and apply it to a single file.

    Returns a short summary string.
    """
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PatchError("Patch must start with '*** Begin Patch'")
    if lines[-1].strip() != "*** End Patch":
        raise PatchError("Patch must end with '*** End Patch'")

    # Collect hunks
    hunks: list[tuple[str, str, list[tuple[str, str]]]] = []
    i = 1
    while i < len(lines) - 1:
        line = lines[i]
        if line.startswith("*** Update File: "):
            hunk_path = line[len("*** Update File: "):].strip()
            i, changes = _read_change_block(lines, i + 1)
            hunks.append(("update", hunk_path, changes))
        elif line.startswith("*** Add File: "):
            hunk_path = line[len("*** Add File: "):].strip()
            i, changes = _read_change_block(lines, i + 1)
            hunks.append(("add", hunk_path, changes))
        elif line.startswith("*** Delete File: "):
            hunk_path = line[len("*** Delete File: "):].strip()
            hunks.append(("delete", hunk_path, []))
            i += 1
        elif line.strip() == "":
            i += 1
        else:
            raise PatchError(f"Unexpected line in patch: {line}")

    if not hunks:
        raise PatchError("Patch contains no hunks")

    # Apply only hunks for the requested file path (relative/absolute tolerant)
    target = file_path.resolve()
    applied = 0
    for op, hunk_path, changes in hunks:
        hunk_abs = (Path(hunk_path) if Path(hunk_path).is_absolute() else (file_path.parent / hunk_path)).resolve()
        if hunk_abs != target:
            continue
        if op == "delete":
            if file_path.exists():
                file_path.unlink()
            return "deleted"
        if op == "add":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(text for _, text in changes)
            file_path.write_text(content, encoding="utf-8")
            return f"created with {len(changes)} lines"
        if op == "update":
            if not file_path.exists():
                raise PatchError(f"File does not exist: {hunk_path}")
            _apply_update(file_path, changes)
            applied += 1

    if not applied:
        raise PatchError("No hunks matched the requested file")
    return f"{applied} hunk(s) updated"


def _read_change_block(lines: list[str], start: int) -> tuple[int, list[tuple[str, str]]]:
    """Read change lines until next hunk header or end."""
    changes = []
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if stripped == "*** End Patch" or stripped.startswith(("*** Update File:", "*** Add File:", "*** Delete File:")):
            break


        if stripped == "":
            i += 1
            continue
        # Change line: marker is first char
        if stripped[0] in ("+", "-", " "):
            op = stripped[0]
            text = stripped[1:]
            changes.append((op, text))
        elif stripped.startswith("@@"):
            # Context marker ignored
            pass
        else:
            raise PatchError(f"Invalid change line: {line}")
        i += 1
    return i, changes


def _apply_update(file_path: Path, changes: list[tuple[str, str]]) -> None:
    """Apply a single update hunk to a file."""
    original_lines = file_path.read_text(encoding="utf-8").splitlines()
    result: list[str] = []
    ptr = 0
    change_idx = 0

    while change_idx < len(changes):
        op, text = changes[change_idx]
        if op == " ":
            # Context line: must match at or after current pointer
            found = -1
            for j in range(ptr, len(original_lines)):
                if original_lines[j] == text:
                    found = j
                    break
            if found == -1:
                raise PatchError(f"Context line not found: {text!r}")
            result.extend(original_lines[ptr:found])
            result.append(text)
            ptr = found + 1
            change_idx += 1
        elif op == "-":
            if ptr >= len(original_lines):
                raise PatchError(f"Cannot remove line beyond file end: {text!r}")
            if original_lines[ptr] != text:
                raise PatchError(
                    f"Remove line does not match at position {ptr}: "
                    f"expected {text!r}, got {original_lines[ptr]!r}"
                )
            ptr += 1
            change_idx += 1
        elif op == "+":
            result.append(text)
            change_idx += 1
        else:
            raise PatchError(f"Unknown op: {op}")

    # Append remaining original lines
    result.extend(original_lines[ptr:])
    file_path.write_text("\n".join(result), encoding="utf-8")