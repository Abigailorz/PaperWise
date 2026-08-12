"""Skill 加载器 — 三层渐进式披露

对应书中 2.5 节 Skills 和 2.5.2 节 Skills 在上下文中的位置

三层结构:
1. 元数据目录（常驻 system prompt，仅 name + description）
2. 核心流程（按需加载完整 SKILL.md）
3. 细则文档（深入子文档）
"""

from pathlib import Path
from typing import Optional


class SkillLoader:
    """Agent Skills 加载器。

    实现渐进式披露（Progressive Disclosure）:
    - 启动时: 仅加载元数据目录 (~200 tokens)
    - 调用时: 按需加载完整 Skill 内容
    - 深入时: 可进一步加载子文档
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        if skills_dir is None:
            # 默认在项目根目录的 skills/ 下
            current = Path(__file__).resolve()
            # 找到项目根目录（包含 skills/ 目录）
            for parent in current.parents:
                if (parent / "skills").is_dir():
                    skills_dir = parent / "skills"
                    break
            if skills_dir is None:
                skills_dir = Path("skills")

        self.skills_dir = Path(skills_dir)
        self._catalog: list[dict] = []
        self._skills: dict[str, Path] = {}
        self._load_catalog()

    def _load_catalog(self) -> None:
        """扫描 skills/ 目录，加载所有 SKILL.md 的元数据。"""
        if not self.skills_dir.exists():
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                metadata = self._parse_frontmatter(skill_file)
                name = metadata.get("name", skill_dir.name)
                description = metadata.get("description", "")
                self._catalog.append({"name": name, "description": description})
                self._skills[name] = skill_file
            except Exception:
                continue

    def _parse_frontmatter(self, filepath: Path) -> dict:
        """解析 SKILL.md 的 YAML frontmatter（轻量实现，无需 pyyaml）。"""
        import re
        content = filepath.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        raw = parts[1].strip()
        result: dict = {}
        current_key: str = ""
        current_val: list[str] = []
        for line in raw.split("\n"):
            # 匹配 "key: value" 或 "key: >" 折叠块
            if m := re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line):
                self._flush_kv(current_key, current_val, result)
                current_key = m.group(1)
                val = m.group(2).strip()
                current_val = [val] if val and val != ">" else []
            elif current_key and line.strip():
                current_val.append(line.strip())
        self._flush_kv(current_key, current_val, result)
        return result

    @staticmethod
    def _flush_kv(key: str, val_parts: list[str], result: dict) -> None:
        if not key:
            return
        result[key] = " ".join(val_parts).strip() if val_parts else ""

    # === 公开接口 ===

    def get_catalog_text(self) -> str:
        """生成 Skill 元数据目录文本（注入 system prompt 用，约 200 tokens）。

        对应书中 2.5.2 节：元数据目录在 system prompt 中的位置
        """
        if not self._catalog:
            return ""

        lines = ["<available_skills>"]
        for skill in self._catalog:
            lines.append(f"  - {skill['name']}: {skill['description'][:100]}")
        lines.append("</available_skills>")
        lines.append(
            "To use a skill, call skill_list to see full descriptions, "
            "then skill_load(name=\"...\") to activate it."
        )
        return "\n".join(lines)

    def get_catalog_list(self) -> list[dict]:
        """返回结构化目录列表（用于 MCP/tools/list 等结构化接口）。"""
        return [
            {"name": s["name"], "description": s["description"]}
            for s in self._catalog
        ]

    def load_skill(self, name: str) -> Optional[str]:
        """加载完整 Skill 内容（按需加载）。"""
        if name not in self._skills:
            return None
        return self._skills[name].read_text(encoding="utf-8")

    def list_skills(self) -> list[str]:
        """列出所有可用 Skill 名称。"""
        return [s["name"] for s in self._catalog]
