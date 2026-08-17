"""Agent 持续进化系统

对应书中第 8 章：Agent 的持续进化

四种更新方式（8.2 节）：
1. 经验知识库 — 事实、经验规律、例外 → Markdown 文档
2. Prompt & Skill — 可语言化的判断原则 → 更新 SKILL.md
3. 程序 & Harness — 确定性流程与强约束 → 工具/工作流
4. 模型参数 — 高维感知与隐式策略 → SFT/RL（需外部）

进化闭环（8.3 节）：
运行轨迹 → 三层验证 → 聚合分析 → 生成候选更新 → 验证 → 发布
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass, field

from paperwise.core.types import AgentResult


@dataclass
class TrajectoryRecord:
    """单次运行轨迹的评估记录"""
    trajectory_id: str
    task_type: str
    paper_id: str
    result: dict  # AgentResult 序列化
    evaluation: dict  # Rubric 评分 + 幻觉检测
    lessons: list[str] = field(default_factory=list)  # 从这次运行中学到的教训
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def was_successful(self) -> bool:
        return self.evaluation.get("overall_score", 0) >= 3.0

    def get_failure_tags(self) -> list[str]:
        """获取失败标签：用于归类问题类型。"""
        tags = []
        if not self.was_successful():
            scores = self.evaluation.get("scores", {})
            for dim, s in scores.items():
                if s.get("score", 4) < 3:
                    tags.append(f"low_{dim}")
            if self.evaluation.get("hallucination", {}).get("severity") in ("critical", "major"):
                tags.append("hallucination")
        return tags


@dataclass
class EvolutionPattern:
    """从多条轨迹中提取的进化模式"""
    pattern_type: str  # "knowledge" | "instruction" | "program" | "parameter"
    description: str
    evidence: list[str]  # 轨迹 ID 列表
    suggested_update: str
    confidence: float = 0.0
    status: str = "proposed"  # proposed → validated → deployed → retired


class EvolutionEngine:
    """持续进化引擎 — 使用统一存储后端。"""

    def __init__(self, storage_dir: Path, backend: str = "sqlite"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        from paperwise.memory.storage import create_storage
        self.store = create_storage(backend, self.storage_dir)
        self.trajectories: list[TrajectoryRecord] = []
        self.patterns: list[EvolutionPattern] = []
        self._load()

    # === 轨迹管理 ===

    def record(self, result: AgentResult, evaluation: dict, paper_id: str = "") -> TrajectoryRecord:
        """记录一条运行轨迹及其评估结果。"""
        import uuid

        record = TrajectoryRecord(
            trajectory_id=uuid.uuid4().hex[:8],
            task_type="paper_analysis",
            paper_id=paper_id,
            result={
                "steps": result.steps,
                "tool_stats": result.tool_stats,
                "success": result.success,
                "error": result.error_message,
            },
            evaluation=evaluation,
        )

        # 提取教训
        if not record.was_successful():
            record.lessons = self._extract_lessons(record)
        else:
            record.lessons = ["Task completed successfully"]

        self.trajectories.append(record)
        self._save()
        return record

    def _extract_lessons(self, record: TrajectoryRecord) -> list[str]:
        """从失败的轨迹中提取教训。"""
        lessons = []
        tags = record.get_failure_tags()

        if "hallucination" in tags:
            lessons.append("Agent fabricated content not in the paper — hallucination detected")
        if "low_accuracy" in tags:
            lessons.append("Agent made inaccurate claims about paper content — citation quality issue")
        if "low_completeness" in tags:
            lessons.append("Report missed key sections — task decomposition or early termination issue")
        if "low_insight_depth" in tags:
            lessons.append("Report too superficial — need deeper critical analysis prompting")

        return lessons or ["Unknown failure mode — needs manual review"]

    # === 聚合分析 ===

    def analyze_patterns(self, min_occurrences: int = 3) -> list[EvolutionPattern]:
        """跨轨迹聚合分析，提取可进化的模式。

        对应书中 8.2 节：从多条成功和失败轨迹中对照提取共性
        """
        from collections import Counter

        new_patterns = []
        failure_tags = Counter()

        for traj in self.trajectories:
            for tag in traj.get_failure_tags():
                failure_tags[tag] += 1

        # 高频失败模式 → 候选改进
        for tag, count in failure_tags.most_common():
            if count >= min_occurrences:
                evidence = [
                    t.trajectory_id for t in self.trajectories
                    if tag in t.get_failure_tags()
                ]

                pattern = self._tag_to_pattern(tag, evidence, count)
                if pattern:
                    new_patterns.append(pattern)
                    self.patterns.append(pattern)

        if new_patterns:
            self._save()

        return new_patterns

    def _tag_to_pattern(self, tag: str, evidence: list[str], count: int) -> Optional[EvolutionPattern]:
        """将失败标签映射为具体的进化模式。"""
        confidence = min(count / 10, 0.95)

        patterns_map = {
            "hallucination": EvolutionPattern(
                pattern_type="instruction",
                description="Agent frequently fabricates content not in the paper",
                evidence=evidence,
                suggested_update=(
                    "Add strict citation rules to system prompt: "
                    "'Every factual claim MUST be verified by searching the paper text. "
                    "If you cannot find evidence, state clearly that the claim cannot be verified.' "
                    "Use the verification Skill to cross-check claims before writing."
                ),
                confidence=confidence,
            ),
            "low_accuracy": EvolutionPattern(
                pattern_type="knowledge",
                description="Agent makes inaccurate statements about paper content",
                evidence=evidence,
                suggested_update=(
                    "Update evidence_quality guidelines: require line-number citations for ALL claims. "
                    "Add post-generation verification step using grep to confirm key numbers."
                ),
                confidence=confidence,
            ),
            "low_completeness": EvolutionPattern(
                pattern_type="instruction",
                description="Reports missing key sections",
                evidence=evidence,
                suggested_update=(
                    "Add explicit section checklist to system prompt with mandatory completion "
                    "before generating final report. Use Agent Status Bar TODO list enforcement."
                ),
                confidence=confidence,
            ),
            "low_insight_depth": EvolutionPattern(
                pattern_type="instruction",
                description="Reports are too superficial",
                evidence=evidence,
                suggested_update=(
                    "Update academic-reading Skill to include critical reading framework. "
                    "Add probing questions: 'What are the hidden assumptions?', "
                    "'Is the evaluation convincing?', 'What did the authors not discuss?'"
                ),
                confidence=confidence,
            ),
        }

        return patterns_map.get(tag)

    # === 部署 ===

    def deploy_pattern(self, pattern_id: int) -> bool:
        """将已验证的模式部署到系统中。"""
        if pattern_id >= len(self.patterns):
            return False

        pattern = self.patterns[pattern_id]
        pattern.status = "deployed"

        # 根据类型应用到不同载体
        if pattern.pattern_type == "knowledge":
            self._deploy_to_knowledge(pattern)
        elif pattern.pattern_type == "instruction":
            self._deploy_to_instruction(pattern)
        elif pattern.pattern_type == "program":
            self._deploy_to_program(pattern)

        self._save()
        return True



    # === 安全门控部署 ===

    async def deploy(self, pattern_id: int, dry_run: bool = False,
                     require_approval: bool = True,
                     approver: Optional[Callable[[EvolutionPattern], bool]] = None) -> dict:
        """带安全门控的部署。

        流程：
          1. 保存当前系统状态快照
          2. 跑回归基线（pytest + agent 能力测试）
          3. 应用候选更新
          4. 跑回归验证
          5. 验证失败则自动回滚
          6. 可选人工审批

        Args:
            pattern_id: 待部署模式索引
            dry_run: True 时不真正应用更新，只返回建议内容
            require_approval: 是否需要外部审批函数返回 True
            approver: 审批函数 (pattern) -> bool

        Returns:
            {"status": "deployed"|"dry_run"|"rejected"|"rolled_back"|"pending_approval",
             "pattern": ..., "baseline": ..., "after": ..., "reason": ...}
        """
        import asyncio
        import subprocess

        if pattern_id >= len(self.patterns):
            return {"status": "rejected", "reason": "invalid_pattern_id"}

        pattern = self.patterns[pattern_id]

        # 1. 保存快照
        snapshot = self._snapshot_state()

        # 2. 基线测试
        baseline = await self._run_regression()
        if not baseline.get("passed"):
            return {"status": "rejected", "reason": "baseline_failed",
                    "baseline": baseline, "pattern": pattern}

        if dry_run:
            return {"status": "dry_run", "pattern": pattern,
                    "suggested_update": pattern.suggested_update,
                    "baseline": baseline}

        # 3. 人工审批门控
        if require_approval:
            if approver is None or not approver(pattern):
                pattern.status = "pending_approval"
                self._save()
                return {"status": "pending_approval", "pattern": pattern, "baseline": baseline}

        # 4. 应用更新
        try:
            self._apply_pattern(pattern)
        except Exception as e:
            self._restore_snapshot(snapshot)
            return {"status": "rolled_back", "reason": f"apply_failed:{e}",
                    "pattern": pattern, "baseline": baseline}

        # 5. 验证测试
        after = await self._run_regression()
        if not after.get("passed"):
            self._restore_snapshot(snapshot)
            return {"status": "rolled_back", "reason": "regression_failed",
                    "pattern": pattern, "baseline": baseline, "after": after}

        pattern.status = "deployed"
        self._save()
        return {"status": "deployed", "pattern": pattern,
                "baseline": baseline, "after": after}

    def _snapshot_state(self) -> dict:
        """创建当前系统状态快照（仅覆盖可能被进化修改的文件）。"""
        project_root = Path(__file__).resolve().parent.parent.parent
        snapshot = {
            "learned_knowledge": self._read_if_exists(project_root / "learned_knowledge.md"),
            "skill_improvements": self._read_if_exists(self.storage_dir / "skill_improvements.md"),
            "harness_improvements": self.store.get("evolution", "harness_improvements"),
            "skills_dir": {},
        }
        skills_dir = project_root / "skills"
        if skills_dir.exists():
            for skill_md in skills_dir.rglob("SKILL.md"):
                rel = str(skill_md.relative_to(project_root))
                snapshot["skills_dir"][rel] = skill_md.read_text(encoding="utf-8")
        return snapshot

    def _restore_snapshot(self, snapshot: dict) -> None:
        """从快照恢复系统状态。"""
        project_root = Path(__file__).resolve().parent.parent.parent
        if snapshot.get("learned_knowledge") is not None:
            (project_root / "learned_knowledge.md").write_text(
                snapshot["learned_knowledge"], encoding="utf-8")
        if snapshot.get("skill_improvements") is not None:
            (self.storage_dir / "skill_improvements.md").write_text(
                snapshot["skill_improvements"], encoding="utf-8")
        if snapshot.get("harness_improvements") is not None:
            self.store.put("evolution", "harness_improvements", snapshot["harness_improvements"])
        for rel, content in snapshot.get("skills_dir", {}).items():
            (project_root / rel).write_text(content, encoding="utf-8")

    def _read_if_exists(self, path: Path) -> Optional[str]:
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _apply_pattern(self, pattern: EvolutionPattern) -> None:
        """直接应用模式更新（供 deploy 调用）。"""
        if pattern.pattern_type == "knowledge":
            self._deploy_to_knowledge(pattern)
        elif pattern.pattern_type == "instruction":
            self._deploy_to_instruction(pattern)
        elif pattern.pattern_type == "program":
            self._deploy_to_program(pattern)

    async def _run_regression(self) -> dict:
        """运行回归测试套件。

        当前实现调用 pytest 和 agent 能力测试（单次）。
        未来可接入更完整的基准。"""
        import asyncio
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parent.parent.parent
        results = {"passed": True, "checks": []}

        async def run(cmd: list[str], name: str, timeout: int = 180) -> dict:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, *cmd,
                cwd=str(project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                ok = proc.returncode == 0
                return {"name": name, "passed": ok,
                        "stdout": stdout.decode("utf-8", errors="replace")[-2000:],
                        "stderr": stderr.decode("utf-8", errors="replace")[-1000:]}
            except asyncio.TimeoutError:
                proc.kill()
                return {"name": name, "passed": False, "reason": "timeout"}

        # pytest 单元测试
        r1 = await run(["-m", "pytest", "tests/", "-q"], "pytest")
        results["checks"].append(r1)

        # agent 能力测试（单次，不跑多次 k）
        r2 = await run(["tests/run_agent_tests.py", "--k", "1", "--paper", "simple"],
                       "agent_tests")
        results["checks"].append(r2)

        results["passed"] = all(c["passed"] for c in results["checks"])
        return results
    def _deploy_to_knowledge(self, pattern: EvolutionPattern):
        """将教训写入经验知识库，并注入到系统提示词文件。"""
        # 1. 写入进化日志
        kb_path = self.storage_dir / "learned_knowledge.md"
        entry = (
            f"\n\n## Pattern: {pattern.description}\n"
            f"*Confidence: {pattern.confidence:.0%} | Evidence: {len(pattern.evidence)} trajectories*\n\n"
            f"{pattern.suggested_update}\n"
        )
        with open(kb_path, "a", encoding="utf-8") as f:
            f.write(entry)

        # 2. 同时更新项目根目录的进化知识文件（Agent 实际读取）
        root_kb = Path(__file__).parent.parent / "learned_knowledge.md"
        with open(root_kb, "a", encoding="utf-8") as f:
            f.write(f"\n{entry}")

    def _deploy_to_instruction(self, pattern: EvolutionPattern):
        """更新实际 Skills 文件中的指导原则。"""
        skill_path = self.storage_dir / "skill_improvements.md"
        entry = (
            f"\n\n## Suggested Skill Update\n"
            f"**Problem:** {pattern.description}\n"
            f"**Confidence:** {pattern.confidence:.0%}\n\n"
            f"**Suggested Change:**\n{pattern.suggested_update}\n"
        )
        with open(skill_path, "a", encoding="utf-8") as f:
            f.write(entry)

        # 自动更新 Skills 文件（如果匹配）
        # 路径：src/paperwise/evolution.py → 项目根目录/skills
        skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
        if skills_dir.exists():
            for skill_dir in skills_dir.iterdir():
                if skill_dir.is_dir():
                    skill_md = skill_dir / "SKILL.md"
                    if skill_md.exists():
                        content = skill_md.read_text(encoding="utf-8")
                        if pattern.description[:30].lower() in content.lower():
                            content += f"\n\n<!-- Evolution Update: {pattern.description} -->\n{pattern.suggested_update}\n"
                            skill_md.write_text(content, encoding="utf-8")

    def _deploy_to_program(self, pattern: EvolutionPattern):
        """将教训固化为 Harness 改进。"""
        existing = self.store.get("evolution", "harness_improvements") or {"items": []}
        existing["items"].append({
            "description": pattern.description,
            "suggestion": pattern.suggested_update,
            "confidence": pattern.confidence,
            "deployed_at": datetime.now().isoformat(),
        })
        self.store.put("evolution", "harness_improvements", existing)

    # === 持久化 ===

    def _save(self) -> None:
        try:
            self.store.put("evolution", "state", {
                "trajectories": [
                    {"trajectory_id": t.trajectory_id, "task_type": t.task_type,
                     "paper_id": t.paper_id, "result": t.result, "evaluation": t.evaluation,
                     "lessons": t.lessons, "timestamp": t.timestamp}
                    for t in self.trajectories[-100:]
                ],
                "patterns": [
                    {"pattern_type": p.pattern_type, "description": p.description,
                     "evidence": p.evidence, "suggested_update": p.suggested_update,
                     "confidence": p.confidence, "status": p.status}
                    for p in self.patterns
                ],
            })
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"Evolution save failed: {e}")

    def _load(self) -> None:
        data = self.store.get("evolution", "state")
        if data:
            self.trajectories = [
                TrajectoryRecord(**t) for t in data.get("trajectories", [])
            ]
            self.patterns = [
                EvolutionPattern(**p) for p in data.get("patterns", [])
            ]

    def stats(self) -> dict:
        """进化引擎统计。"""
        total = len(self.trajectories)
        successful = sum(1 for t in self.trajectories if t.was_successful())
        return {
            "total_trajectories": total,
            "success_rate": f"{successful/total:.1%}" if total else "N/A",
            "patterns_discovered": len(self.patterns),
            "patterns_deployed": sum(1 for p in self.patterns if p.status == "deployed"),
            "top_failure_modes": [
                (tag, count) for tag, count in
                __import__('collections').Counter(
                    tag for t in self.trajectories for tag in t.get_failure_tags()
                ).most_common(5)
            ],
        }
