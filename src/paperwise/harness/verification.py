"""验证器 — 输出正确性校验

对应书中第 8.1 节三层轨迹验证的底层结果验证器（代码化）
"""

from pathlib import Path
from typing import Optional


class VerificationResult:
    """验证结果"""
    def __init__(self, passed: bool, details: str = ""):
        self.passed = passed
        self.details = details


class OutputVerifier:
    """输出验证器 — 用代码而非 LLM 执行的基础验证。

    对应书中三层验证的最底层：结果验证器
    """

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def verify_file_exists(self, path: str) -> VerificationResult:
        """验证文件是否真实存在。"""
        p = self.workspace / path
        if p.exists():
            return VerificationResult(True, f"File exists: {path}")
        return VerificationResult(False, f"File not found: {path}")

    def verify_content_length(self, path: str, min_chars: int = 100) -> VerificationResult:
        """验证文件内容长度。"""
        p = self.workspace / path
        if not p.exists():
            return VerificationResult(False, f"File not found: {path}")
        content = p.read_text(encoding="utf-8")
        if len(content) >= min_chars:
            return VerificationResult(True, f"Content length OK: {len(content)} chars")
        return VerificationResult(False, f"Content too short: {len(content)} < {min_chars} chars")

    def verify_json_valid(self, content: str) -> VerificationResult:
        """验证 JSON 格式。"""
        import json
        try:
            json.loads(content)
            return VerificationResult(True, "Valid JSON")
        except json.JSONDecodeError as e:
            return VerificationResult(False, f"Invalid JSON: {e}")

    def verify_report_structure(self, report_dir: Path) -> VerificationResult:
        """验证报告目录结构完整性。"""
        expected = [
            "report.md",
        ]
        missing = []
        for f in expected:
            if not (report_dir / f).exists():
                missing.append(f)

        if missing:
            return VerificationResult(False, f"Missing files: {missing}")
        return VerificationResult(True, "All expected files present")
