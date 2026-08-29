# PaperWise 架构演进实施 Spec v0.5

> 本文档落盘于 `docs/PaperWise-Roadmap-v0.5-Implementation-Spec.md`，
> 记录 PaperWise 从 L2 Workflow Agent 向 L5 Research-native Agent 演进的实施路线、关键模块接口与验收标准。

---

## 1. 演进主线

```text
L1 Tool Agent
  ↓
L2 Workflow Agent
  ↓
L3 State-aware Agent
  ↓
L4 Self-improving Agent
  ↓
L5 Research-native Agent
```

当前状态（2026-08-30）：L2 完成，L3（State-aware + Experience Learning）进行中。

---

## 2. 当前已完成（Iteration 1: P0）

### 2.1 Trace 基础设施

**目标**：建立可运行、可测试、可持久化的 Agent 执行轨迹收集与评估闭环。

**关键文件**：
- `src/paperwise/core/trace_collector.py`
- `src/paperwise/evaluation/trace_store.py`
- `src/paperwise/evaluation/trace_evaluator.py`
- `src/paperwise/evaluation/benchmark.py`
- `src/paperwise/evaluation/__init__.py`
- `src/paperwise/evaluation/rubric.py`
- `src/paperwise/evaluation/hallucination.py`

**接口规范**：

#### `TraceCollector`（抽象）
```python
class TraceCollector(ABC):
    @abstractmethod
    def start_trace(self, task, trace_id=None, session_id=None, user_id="default",
                    parent_trace_id=None, parent_event_id=None, metadata=None) -> AgentTrace: ...
    @abstractmethod
    def end_trace(self, agent_result: Optional[AgentResult] = None) -> Optional[AgentTrace]: ...
    @abstractmethod
    def add_event(self, event_type, data=None, step=None, node_id=None,
                  parent_event_id=None, latency_ms=None) -> Optional[TraceEvent]: ...
    @abstractmethod
    def current_trace(self) -> Optional[AgentTrace]: ...
    @abstractmethod
    def is_active(self) -> bool: ...
    @abstractmethod
    async def aflush(self) -> None: ...
```

#### `InMemoryTraceCollector`
- 使用 trace 栈支持嵌套调用
- `aflush()` / `flush()` 等待所有已派发持久化任务
- 同步 store 在异步上下文中通过 `run_in_executor` 执行，并跟踪 future
- 自动截断大 payload

#### `TraceStore`
- 基于 `StorageBackend`（SQLite / JSON）
- 记录形状：`{"trace": AgentTrace.to_dict(), "updated_at": ISO}`
- 支持 `get`, `list`, `list_sessions`, `delete`, `count`, `get_metrics`
- `get_metrics` 正确比较 `TraceEventType` 枚举

#### `TraceEvaluator`
- `evaluate(trace) -> dict`
- `evaluate_result(agent_result, store) -> dict`
- `evaluate_by_id(trace_id, store) -> dict`
- 六维 grader：Routing / Planning / Retrieval / Evidence / ToolUsage / Execution
- `TraceMetricsExtractor` 提取标准化过程指标

#### `PassKEvaluator`
- 可选 `trace_evaluator` + `trace_store`
- `EvalRun` 增加 `trace_score` / `trace_details`
- `BenchmarkResult` 增加 `avg_trace_score`

### 2.2 Trace 与执行路径的集成

- `Agent.run()` 启动/结束 trace，回填 `result.trace_id`
- `AgentSession.chat()` 每个返回点均构造 `AgentResult` 并 `end_trace(result)`
- `SmartOrchestrator._run_simple()` 将 `trace_collector` 传给子 Agent
- `DAGExecutor` 通过 `ExecutionConfig.trace_collector` 接收 collector

### 2.3 循环导入消除

将 `RubricEvaluator` / `HallucinationDetector` 抽取到独立子模块：
- `src/paperwise/evaluation/rubric.py`
- `src/paperwise/evaluation/hallucination.py`

### 2.4 WIP 中发现的依赖 bug 修复

- `Plan.to_dict()` 新增
- `TaskStatus.NEEDS_REPLAN` 补齐
- `ResearchStateManager.save()` 作为 `update()` 的公开别名
- `DAGExecutor` 构造参数修正为 `ExecutionConfig(trace_collector=...)`

### 2.5 测试

新增 `tests/test_evaluation/`：
- `test_trace_collector.py`：12 个测试
- `test_trace_store.py`：8 个测试
- `test_trace_evaluator.py`：11 个测试
- `test_benchmark_trace_integration.py`：3 个测试

全部 31 个测试通过。

---

## 3. 当前已完成（Iteration 2: P1）

### 3.1 目标
让 `ContextEngine` 进入 Orchestrator 决策路径，建立 **Memory → Decision** 闭环。

### 3.2 关键新增模块

#### `src/paperwise/orchestration/memory_adapter.py`
```python
class OrchestratorMemoryAdapter:
    def __init__(self, workspace, user_id="default"): ...
    def assemble_context(self, research_state: ResearchState) -> ContextPackage: ...
    def assemble_context_for_subagent(self, node_id, research_state, max_chars=4000) -> ContextPackage: ...
    def record_episode(self, research_state, trace, result) -> None: ...
    def learn_procedure(self, task_type, plan, success) -> None: ...
    def apply_gaps_to_plan(self, plan, research_state) -> Plan: ...
    def update_state_from_execution(self, research_state, completed_nodes, failed_nodes, gaps) -> ResearchState: ...
```

#### `ContextEngine` 扩展
- `ContextPackage.size()` / `ContextPackage.truncate(max_chars)`
- `ContextPackage.for_node(node_id)` — 按节点类型过滤上下文段
- `ContextEngine.assemble_for_subagent(node_id, research_state) -> ContextPackage`

#### `ResearchState` 扩展
- `get_high_priority_gaps(limit=3)`
- `has_unresolved_gaps()`
- `add_finding_from_node(...)`
- `add_gap(...)` / `close_gap(...)`

### 3.3 集成点

- `SmartOrchestrator.__init__` 初始化 `OrchestratorMemoryAdapter`
- `SmartOrchestrator.run()` 在分类后组装 `ContextPackage`
- `_run_complex()` 构建 Plan 前调用 `apply_gaps_to_plan`
- `_run_complex()` 执行后更新 `findings` / `gaps`
- `_run_complex()` 返回前调用 `record_episode` / `learn_procedure`
- `_run_sub_agent()` 将 `context_xml` 注入子 Agent system prompt
- `SubAgentSpec` / `NodeSpec` 新增 `context_xml` 字段

### 3.4 测试

新增：
- `tests/test_orchestration/test_memory_driven.py`：7 个测试
- `tests/test_memory/test_context_engine_orchestrator.py`：4 个测试

全部 11 个测试通过。

---

## 4. 当前已完成（Iteration 3: P2）

### 4.1 目标
建立真正的 Dynamic DAG Planner，基于 Capability Registry 动态组合节点。

### 4.2 关键新增模块

#### `src/paperwise/orchestration/dynamic_planner.py`
```python
class DynamicDAGPlanner:
    def build_plan(self, task: str, task_route: TaskRoute,
                   research_state: ResearchState,
                   policy: PlanCompositionPolicy) -> Plan: ...

    @staticmethod
    def is_topologically_valid(plan: Plan) -> bool: ...

class PlanCompositionPolicy:
    use_dynamic_plan: bool = False  # 默认关闭，保留静态 fallback
```

#### `registries.py` 扩展
- `CapabilityRegistry.find_for_task(task, required_output_artifacts) -> list[Capability]`
- `CapabilityRegistry.resolve_nodes(capability, node_registry) -> list[str]`
- `NodeRegistry.select_by_category(category)` / `filter_by_capabilities(...)` / `filter_by_output_artifact(...)`
- `WorkflowRegistry.select(task_route) -> WorkflowTemplate`（支持 task 文本打分 fallback）

#### `replanner.py` 扩展
- `ReplanAgent.replan_from_gaps(plan, research_state, state) -> Plan`
- `ReplanAgent.replan_from_capability_failure(...)`

#### `plan.py` 扩展
- `Plan.merge(new_plan) -> Plan`
- `Plan.to_dependency_graph() -> dict`

### 4.3 集成点

- `SmartOrchestrator.__init__` 增加 `use_dynamic_plan: bool = False`
- `SmartOrchestrator._select_plan()` 在启用时调用 `DynamicDAGPlanner.build_plan`
- 动态 Plan 生成失败自动回退到静态 `_build_complex_plan`

### 4.4 测试

新增：
- `tests/test_orchestration/test_capability_registry.py`：8 个测试
- `tests/test_orchestration/test_dynamic_planner.py`：7 个测试

全部 15 个测试通过。

---

## 5. 当前已完成（Iteration 4: P3 — Experience / Strategy Learning）

### 5.1 目标
建立 **Execute → Review → Learn → Re-plan** 的经验闭环：Reviewer 升级为
Learning Signal Generator，执行轨迹聚合为失败模式，经验沉淀进 Strategy Library
并驱动后续 Plan 组合。

### 5.2 关键新增模块

#### `src/paperwise/learning/signals.py`
```python
class LearningSignal:  # signal_type / source / severity / task_type / subject / detail
    ...
class LearningSignalGenerator:
    def from_findings(self, findings: dict, task_type="analysis") -> list[LearningSignal]: ...
    def from_trace(self, trace: AgentTrace, task_type="analysis") -> list[LearningSignal]: ...
```
- 信号类型：hallucination / quality_gap / omission / verification_gap /
  node_failure / planning_failure / instability / success
- 纯规则映射，不调用 LLM（Mechanism over prompt）

#### `src/paperwise/learning/failure_patterns.py`
```python
class FailurePattern:  # category / subject / occurrences / trace_ids / example_messages
    ...
class FailurePatternExtractor:
    def extract(self, traces: list[AgentTrace]) -> list[FailurePattern]: ...
    def extract_from_store(self, store: TraceStore, limit=100) -> list[FailurePattern]: ...
```
- 聚合 NODE_FAILED / ERROR / RETRY / REPLAN 事件
- `min_occurrences` 阈值过滤偶发噪声（默认 2）

#### `src/paperwise/learning/strategy_library.py`
```python
class Strategy:  # task_type / name / plan_hints / avoid / success_rate / use_count
    ...
class StrategyLibrary:
    def add_or_update(self, strategy) -> Strategy: ...            # 按 (task_type, name) 去重合并
    def select(self, task_type, min_success_rate=0.5, limit=3) -> list[Strategy]: ...
    def record_outcome(self, strategy_id, success) -> Strategy: ...  # 滚动更新成功率
    def learn_from_signals(self, task_type, signals) -> list[Strategy]: ...
```
- 持久化复用 `StorageBackend`（SQLite / JSON），落盘 `workspace/.paperwise/{user}/strategies/`
- 只有 critical/major 级信号才会生成或强化策略

### 5.3 集成点

- `OrchestratorMemoryAdapter` 新增 `strategy_library` / `signal_generator`
- `OrchestratorMemoryAdapter.learn_from_review(task_type, findings)`：
  findings -> signals -> strategy library
- `OrchestratorMemoryAdapter.apply_strategies_to_plan(plan, task_type)`：
  保守插入白名单节点（verify_data / expand_evidence），依赖必须已存在，保证拓扑合法
- `SmartOrchestrator._run_complex()`：规划阶段调用 `apply_strategies_to_plan`；
  review 循环结束后调用 `learn_from_review`

### 5.4 顺带修复的存量 bug

- `OrchestratorMemoryAdapter.learn_procedure()` 曾向 `ProceduralMemory.learn()`
  传不存在的 `signature` 关键字参数，TypeError 被静默吞掉，程序性记忆从未真正写入；
  已改为 `context_signature={"plan_signature": ...}`，并加回归测试。
- `SmartOrchestrator._run_pptx_writer()` 构造 spec 后缺少
  `return await self._run_sub_agent(...)`，导致 generate_pptx 节点必失败；已补上。

### 5.5 测试

新增 `tests/test_learning/`：
- `test_signals.py`：7 个测试
- `test_failure_patterns.py`：6 个测试
- `test_strategy_library.py`：7 个测试
- `test_memory_adapter_learning.py`：5 个测试

全部 25 个测试通过；全量回归 170 通过（排除需真实 LLM 的集成测试）。

---

## 6. 后续方向（P4 及以后）

### P4：Proactive Research Intelligence
- `ProactiveEngine` 从 Paper Recommender 升级为 Research Opportunity Detector
- 支持 Knowledge Gap、Contradiction、Missing Evidence、New Method 等 Opportunity 类型
- 论文推荐只是 Action 之一

### P5：Research-native Intelligence
- 构建 Research Graph（User - Project - Question - Paper - Method - Evidence - Experiment - Hypothesis）
- Agent 参与研究本身，而非仅执行指定任务

---

## 7. 验收标准

| 迭代 | 必须通过的测试 | 关键验证点 |
|------|----------------|-----------|
| P0 | `pytest tests/test_evaluation/ -v` | 31/31 通过；`TraceStore.get_metrics` 指标正确；simple path trace 包含子 Agent 事件 |
| P1 | `pytest tests/test_orchestration/test_memory_driven.py tests/test_memory/test_context_engine_orchestrator.py -v` | 11/11 通过；ContextPackage 进入子 Agent prompt；gaps 驱动 Plan |
| P2 | `pytest tests/test_orchestration/test_capability_registry.py tests/test_orchestration/test_dynamic_planner.py -v` | 15/15 通过；DynamicDAGPlanner 生成拓扑合法 Plan |
| P3 | `pytest tests/test_learning/ -v` | 25/25 通过；learn_procedure 真正写入 ProceduralMemory；策略库持久化并可驱动 Plan 插入 |

---

## 8. 已知问题

- `tests/test_api/test_sessions.py::test_sessions_list_roundtrip` 需要配置 `DEEPSEEK_API_KEY`。
- `tests/test_integration/test_e2e_paper.py` 中两个集成测试在 mock LLM + orchestration 路径下行为漂移，需在后续迭代中稳定化。

---

## 9. Git 工作流

每个迭代：
1. 代码改动 + 测试
2. `pytest tests/test_xxx/ -v`
3. `git add ...`
4. `git commit -m "P[X]: ..."`
5. `git push origin main`

Iteration 1 checkpoint 已保留在本地分支 `trace-infra-wip-checkpoint`。
