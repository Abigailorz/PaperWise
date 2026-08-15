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
        self._skill_dirs: dict[str, Path] = {}
        self._allowed_tools: dict[str, list[str]] = {}
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
                allowed_tools = metadata.get("allowed-tools", [])
                if not isinstance(allowed_tools, list):
                    allowed_tools = []
                self._catalog.append({
                    "name": name,
                    "description": description,
                    "allowed_tools": allowed_tools,
                })
                self._skills[name] = skill_file
                self._skill_dirs[name] = skill_dir
                self._allowed_tools[name] = allowed_tools
            except Exception:
                continue

    def _parse_frontmatter(self, filepath: Path) -> dict:
        """解析 SKILL.md 的 YAML frontmatter（轻量实现，无需 pyyaml）。

        支持标量、`>`/`|` 折叠块、以及 `- item` / `[a, b]` 列表。
        """
        import re
        content = filepath.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        raw = parts[1].strip("\n")
        result: dict = {}
        lines = raw.split("\n")
        i = 0
        while i < len(lines):
            m = re.match(r'^([\w][\w\-]*)\s*:\s*(.*)$', lines[i].strip())
            if not m:
                i += 1
                continue
            key, val = m.group(1), m.group(2).strip()

            if val in ("", ">", "|"):
                # 折叠/字面块 或 列表
                block: list[str] = []
                has_list = False
                i += 1
                while i < len(lines) and not re.match(
                    r'^\s*[\w][\w\-]*\s*:', lines[i]
                ):
                    s = lines[i].strip()
                    if s:
                        if s.startswith("- "):
                            has_list = True
                            block.append(s[2:].strip())
                        else:
                            block.append(s)
                    i += 1
                result[key] = block if has_list else " ".join(block)
                continue

            if val.startswith("[") and val.endswith("]"):
                result[key] = [
                    x.strip().strip("'\"")
                    for x in val[1:-1].split(",")
                    if x.strip()
                ]
                i += 1
                continue

            result[key] = val
            i += 1
        return result

    # === 公开接口 ===

    def get_catalog_text(self) -> str:
        """生成 Skill 元数据目录文本（注入 system prompt 用）。"""
        if not self._catalog:
            return ""

        lines = ["<available_skills>"]
        for skill in self._catalog:
            lines.append(f"  - {skill['name']}: {skill['description'][:200]}")
        lines.append("</available_skills>")
        lines.append(
            "使用规则：开始任何任务前，先判断是否有 skill 的 description 与当前任务匹配；"
            "有则立即 skill_load(name=\"...\") 加载并严格遵循，"
            "再用 load_skill_resource(skill=\"...\", resource=\"...\") 读取其引用的子文件。"
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

    def load_resource(self, name: str, rel_path: str) -> Optional[str]:
        """按需加载 skill 目录内的子文件（references/static/assets 等）。

        rel_path 是相对于该 skill 目录的相对路径，例如：
        - manifest.yaml
        - references/self-review.md
        - static/core/workflow.md
        """
        skill_dir = self._skill_dirs.get(name)
        if not skill_dir:
            return None
        root = skill_dir.resolve()
        target = (skill_dir / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except Exception:
            return None

    def list_resources(self, name: str) -> list[str]:
        """列出某个 skill 目录下的所有子文件（相对路径，正斜杠）。"""
        skill_dir = self._skill_dirs.get(name)
        if not skill_dir or not skill_dir.exists():
            return []
        return sorted(
            str(p.relative_to(skill_dir)).replace("\\", "/")
            for p in skill_dir.rglob("*")
            if p.is_file()
        )

    def get_allowed_tools(self, name: str) -> list[str]:
        """返回某 skill 声明的 allowed-tools（未声明则空列表）。"""
        return self._allowed_tools.get(name, [])

    def list_skills(self) -> list[str]:
        """列出所有可用 Skill 名称。"""
        return [s["name"] for s in self._catalog]
