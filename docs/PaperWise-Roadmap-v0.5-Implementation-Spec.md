# PaperWise 架构演进实施 Spec v0.5

> 本文档落盘于 `docs/PaperWise-Roadmap-v0.5-Implementation-Spec.md`，
> 记录 PaperWise 从 L2 Workflow Agent 向 L5 Research-native Agent 演进的实施路线、关键模块接口与验收标准。

---

## 1. 演进主线

```text
L1 Tool Agent                    ✅ ReAct + Tools + Guardrails
  ▼
L2 Workflow Agent                ✅ Router / DAG / Multi-Agent / Review / Replan
  ▼
L3 State-aware Agent             🔶 核心骨架已形成
  │  P0 Trace                    ✅
  │  P1 Memory → Decision        ✅
  │  P2 Dynamic DAG              ✅（主路径，静态降级为 safety net）
  │  P3 Experience Learning      🔶 架构完成，学习效果待验证
  ▼
L4 Self-improving Agent          🔶 P3 刚打开入口
  │  P3.5 Learning Evaluation    🔶 机制完成，真实增益证据待积累
  │  Strategy Validation         ⬜
  │  Failure Attribution         ⬜
  │  Policy Learning             ⬜
  ▼
L4+ Proactive Research Agent     🚧 P4 Research Opportunity Engine
  │  Phase 1 Opportunity Detection  ✅（检测+pending 落盘）
  │  Phase 2 Action Planner         ⬜（机会进 Dynamic DAG）
  │  Phase 3 Proactive 升级         ⬜（pending 按需 surfaced）
  ▼
P4.5 Retrieval-native Agent      ⬜ Chunking / Hybrid Retrieval / Evidence Pack / Citation Grounding
  ▼
L5 Research-native Agent         ⬜ P5 Research Graph
Research Agent
```

当前状态（2026-08-30）：**L3 核心骨架已形成，正在从 L3 向 L4 过渡。**

成熟度宏观评估（架构维度，非严格工程指标）：

| 层级 | 成熟度 | 说明 |
|------|--------|------|
| L2 Workflow Agent | 90%+ | DAG + Multi-Agent + Review + Replan 完整 |
| L3 State-aware Agent | 70~80% | Memory→Decision 已打通；Dynamic DAG 成为主路径；Experience Learning 架构完成 |
| L4 Self-improving Agent | 15~25% | P3 打开入口；P3.5 验证机制已落地，**真实论文上的增益证据待积累（效果验证 Pending）** |
| L5 Research-native Agent | <10% | Research Graph 未启动 |

### 1.1 P0→P3 已形成的闭环

```text
Memory → Planning → DAG → Execute → Review → Trace → Learning → Memory / Strategy
```

- P0 Trace：我做了什么？
- P1 Memory → Decision：我知道用户是谁、过去做过什么
- P2 Dynamic DAG：我知道应该怎么规划
- P3 Experience Learning：我知道上一次哪里做得不好

### 1.2 战略定位

PaperWise 的目标**不是**通用 Coding Agent（Codex / Claude Code 的方向），
而是 **Research-native Agent**——竞争点是研究工作流的智能化程度：

```text
Papers → Methods → Evidence → Research Questions
      → Experiments → Research State → Research Opportunities
```

### 1.3 下一阶段必须用评测数据回答的三个问题

1. 它是否因为"记得"而做得更好？（Memory → Decision → Better Result）
2. 它是否因为"经历过"而做得更好？（Experience → Strategy → Better Future Execution）
3. 它是否能发现用户没有明确提出、但对研究有价值的事情？（Research State → Opportunity → Proactive Action）

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
    use_dynamic_plan: bool = False  # P2 时默认关闭；P2 收尾（见第 7 节）起 Orchestrator 默认开启
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

- `SmartOrchestrator.__init__` 增加 `use_dynamic_plan: bool = False`（P2 收尾起默认 `True`）
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

## 6. 当前已完成（Iteration 5: P3.5 — Learning Validation）

### 6.1 目标

验证 P3 产生的经验**是否真正让 Agent 变好**，打通
`Experience → Strategy → Planning → Behavior → Improvement` 的证据链。

### 6.2 关键改动

#### `Strategy` 验证字段（`learning/strategy_library.py`）
- 新增 `success_count` / `failure_count` / `expected_gain` / `actual_gain`
- 新增 `confidence` 属性：Laplace 平滑 `(success_count + 1) / (total + 2)`，
  无观测时为先验 0.5
- `select()` 排序键改为 `(confidence, success_rate, use_count)`：
  **经过验证的策略优先，未验证策略自动降权**
- `record_outcome()` 累积 success/failure 计数
- 新增 `record_evaluation(strategy_id, actual_gain, expected_gain)`

#### `learning/strategy_evaluator.py`（新增）
```python
class StrategyEvaluator:
    def evaluate(self, strategy, tasks, run_fn, expected_gain=None) -> StrategyEvalReport: ...
```
- A/B 设计：同一批任务跑 baseline（strategy=None）与 treatment，
  `actual_gain = mean(treatment) - mean(baseline)`
- `run_fn` 由调用方注入（真实执行 + TraceEvaluator 打分，或测试 mock），
  本模块不依赖 LLM，评测逻辑确定性
- 评测结果自动回写 StrategyLibrary

#### 执行结果回写闭环
- `OrchestratorMemoryAdapter.apply_strategies_to_plan()` 记录**实际改变 Plan** 的策略 id
- 新增 `OrchestratorMemoryAdapter.record_strategy_outcomes(success)`：
  只回写真正被应用的策略；`SmartOrchestrator._run_complex()` 结束时调用

### 6.3 完整证据链（验收标准对应的演示路径）

```text
Review 发现 critical（P3 learn_from_review）
  → StrategyLibrary 生成 enforce-citations 策略
  → 下次同类任务 apply_strategies_to_plan 插入 expand_evidence 节点
  → 执行结束 record_strategy_outcomes 回写 success_count
  → StrategyEvaluator A/B 验证 actual_gain > 0
  → 策略 confidence 上升，select() 优先选中
```

### 6.4 测试

新增 `tests/test_learning/test_strategy_evaluator.py`：10 个测试
`tests/test_learning/test_memory_adapter_learning.py`：+1 个闭环测试

learning 套件共 36 个测试通过；全量回归 181 通过（排除需真实 LLM 的集成测试）。

---

## 7. 当前已完成（Iteration 6: P2 收尾 — Dynamic DAG 成为主路径）

### 7.1 目标

Dynamic DAG 默认开启；Static DAG 降级为 regression safety net。
原则：**Node Capability 受控（Registry 白名单 + 已注册 handler），Graph Composition 动态。**

### 7.2 关键改动

#### 动态 → 可执行适配层（`orchestration/dynamic_planner.py`）
```python
EXECUTABLE_NODE_IDS / NODE_TO_EXECUTABLE / executable_id_for(node_id)
def to_executable_plan(plan: Plan) -> Plan: ...
```
- DynamicDAGPlanner 自由组合 Registry 节点；执行前折叠为 SmartOrchestrator
  已注册 handler 的可执行节点（如 report_outline/section/assemble → generate_report）
- 依赖在可执行空间重建：去自依赖、去重、max_retries 取最大
- 折叠出的节点自动附加条件门：generate_pptx→requires_pptx、
  verify_data→requires_verification、revise_report→critic_has_issues（干净审查不再白跑 revision）
- 未知节点保守归入 analyze_method，保证 Plan 永远可执行

#### Orchestrator
- `use_dynamic_plan` 默认改为 `True`
- handler 注册收敛为 `_handler_map()`，注册与校验共用同一映射，防止漂移
- `_select_plan()`：build → 拓扑校验 → `to_executable_plan` → 再校验
  （拓扑 + 全部节点有 handler）→ 任一环节失败回退静态 `_build_complex_plan`

#### Registry 修正
- `revision` 节点 category 从 `generation` 改为 `revision`（优先级 6），
  修复"先改后审"的语义倒置

### 7.3 测试

新增 `tests/test_orchestration/test_executable_plan.py`：8 个测试
（映射、折叠、依赖重建、条件门、默认开启动态、静态回退）。
全量回归 190 通过（排除需真实 LLM 的集成测试）。

---

## 8. 当前已完成（Iteration 7: P4 Phase 1 — Opportunity Detection）

### 8.1 目标

从 DAG 执行结果中发现用户未明确提出、但可能有研究价值的机会。
**Phase 1 边界：只检测并落盘 pending；不主动推送、不改 UI、不自动执行 DAG。**

设计稿：`docs/OPPORTUNITY_ENGINE_DESIGN.md`

### 8.2 关键新增模块（`src/paperwise/opportunity/`）

```python
# models.py
class ResearchOpportunity:  # type / evidence / confidence / importance / novelty / status
    def signature(self) -> str: ...   # 去重签名
class OpportunityType:   # KNOWLEDGE_GAP / MISSING_EVIDENCE / CONTRADICTION / METHOD_COMPLEMENTARITY
class OpportunityStatus: # PENDING / ACTING / ACTED / DISMISSED / EXPIRED
class EvidenceRef:       # source_type / source_id / excerpt / location

# rules.py —— 4 条确定性检测规则（不调 LLM，precision 优先）
DEFAULT_RULES = [KnowledgeGapRule, MissingEvidenceRule, ContradictionRule, MethodComplementarityRule]

# evidence.py
class EvidenceVerifier:  # 无证据机会直接丢弃；MissingEvidence 可选 KB 反证
    def verify(self, opportunity) -> bool: ...

# scorer.py
class OpportunityScorer:  # confidence/importance/novelty 三维打分 + min_confidence 过滤
    def score(self, candidates, existing=()) -> list[ResearchOpportunity]: ...

# detector.py
class OpportunityDetector:
    def detect(self, research_state, reviewer_findings=None, existing=None, depth=0): ...
class OpportunityPolicy:
    max_per_run=3; max_depth=1; min_confidence=0.5; allow_proactive_interrupt=False
```

### 8.3 防递归五约束（detector 强制）

1. **depth limit**：`depth >= max_depth` 直接返回空（机会触发的 DAG 不再级联检测）
2. **budget**：单次检测 ≤ `max_per_run`（默认 3）
3. **confidence**：scorer 的 `min_confidence` 过滤低置信机会
4. **cooldown + dedup**：同签名机会在仍 pending/acting 时不重复（novelty 降权 + 单轮签名去重）
5. **interrupt policy**：Phase 1 所有机会强制 `status=pending`，绝不主动打断

### 8.4 集成点

- `ResearchState` 扩展 `opportunities` 字段 + `add_opportunity()` /
  `get_active_opportunities()` / `dismiss_opportunity()`
- `SmartOrchestrator._run_complex()` 结束后：`OpportunityDetector.detect(depth=0)`
  → 机会落盘 research_state + 写入 `orchestration_status.json` 的 `opportunities` 键
- 修复循环 import：rules.py / detector.py 对 ResearchState 改用 TYPE_CHECKING

### 8.5 测试

新增 `tests/test_opportunity/test_detector.py`：15 个测试，对应 4 条验收标准：
① 4 类机会都能检测 ② 无证据机会被丢弃 ③ 空输入/无锚点输入不产垃圾
④ depth/budget/dedup/pending-only 防递归约束生效。全量回归 205 通过。

---

## 9. 后续方向

> 2026-08-30 路线调整：L3 骨架已成型，下一阶段不再堆基础能力，
> 优先把 P3 的经验学习做成**可验证的闭环**（P3.5 ✅ 已完成机制），再进入 P4。

### P4：Research Opportunity Engine ⭐⭐⭐⭐⭐  🚧 Phase 1 已完成

> **架构设计与 Gap Analysis：`docs/OPPORTUNITY_ENGINE_DESIGN.md`**
> **Phase 1（检测+落盘 pending）已实现，见第 8 节。**

`ProactiveEngine` 从"论文推荐器"升级为"研究机会探测器"——
**在执行中发现机会，而非定时推送**：

- Opportunity 类型（第一版锁死 4 种）：KnowledgeGap / MissingEvidence /
  Contradiction / MethodComplementarity
- 触发源：DAG 执行 Trace + ResearchState findings + reviewer findings.json
- 防递归：Opportunity Budget / Depth Limit / Confidence Threshold /
  Cooldown+Dedup / User Interrupt Policy
- 三阶段落地：Phase 1 检测 ✅ → Phase 2 Action Planner（机会进 Dynamic DAG）⬜
  → Phase 3 Proactive 升级（pending 机会按需 surfaced）⬜

### P4.5：Retrieval-native Paper Agent（与 P4 并行）⭐⭐⭐⭐⭐

解决当前最大技术债——"全文进上下文"：

- PDF 解析后按章节/段落 chunk 化，保留 section / 行号 / 图表 metadata
- Dense + Sparse 混合检索 + Rerank
- DAG 节点按需取 Evidence，引用强制接地（Citation Grounding）
- 解决 Context Window / Cost / Attention Dilution / Retrieval Noise 四个问题

### P5：Research Graph ⭐⭐⭐⭐

- 实体：User - Project - Question - Paper - Method - Evidence - Experiment - Hypothesis
- 从 Paper-centric knowledge 升级为 Research-centric knowledge
- P4 的 Opportunity Detection 成熟后再启动

### P6：Multi-Agent Collaboration ⭐⭐⭐

- Shared Blackboard / Agent Protocol / Role Negotiation / Delegation / Conflict Resolution
- 超越当前 Orchestrator 单向调度

---

## 10. 验收标准

| 迭代 | 必须通过的测试 | 关键验证点 |
|------|----------------|-----------|
| P0 | `pytest tests/test_evaluation/ -v` | 31/31 通过；`TraceStore.get_metrics` 指标正确；simple path trace 包含子 Agent 事件 |
| P1 | `pytest tests/test_orchestration/test_memory_driven.py tests/test_memory/test_context_engine_orchestrator.py -v` | 11/11 通过；ContextPackage 进入子 Agent prompt；gaps 驱动 Plan |
| P2 | `pytest tests/test_orchestration/test_capability_registry.py tests/test_orchestration/test_dynamic_planner.py -v` | 15/15 通过；DynamicDAGPlanner 生成拓扑合法 Plan |
| P3 | `pytest tests/test_learning/ -v` | learn_procedure 真正写入 ProceduralMemory；策略库持久化并可驱动 Plan 插入。**状态：架构完成，学习效果待验证** |
| P3.5 | `pytest tests/test_learning/test_strategy_evaluator.py -v` | 10/10 通过；A/B gain 正确计算并回写；未验证策略在选择中降权；outcome 只回写实际应用的策略。**状态：机制完成，真实增益证据待积累** |
| P2 收尾 | `pytest tests/test_orchestration/test_executable_plan.py -v` | 8/8 通过；动态 Plan 折叠后只含可执行节点；动态失败回退静态 |
| P4 Phase 1 | `pytest tests/test_opportunity/ -v` | 15/15 通过；4 类机会可检测、无证据机会被丢弃、空输入不产垃圾、防递归五约束生效 |

---

## 11. 已知问题

- `tests/test_api/test_sessions.py::test_sessions_list_roundtrip` 需要配置 `DEEPSEEK_API_KEY`。
- `tests/test_integration/test_e2e_paper.py` 中两个集成测试在 mock LLM + orchestration 路径下行为漂移，需在后续迭代中稳定化。

---

## 12. Git 工作流

每个迭代：
1. 代码改动 + 测试
2. `pytest tests/test_xxx/ -v`
3. `git add ...`
4. `git commit -m "P[X]: ..."`
5. `git push origin main`

Iteration 1 checkpoint 已保留在本地分支 `trace-infra-wip-checkpoint`。
