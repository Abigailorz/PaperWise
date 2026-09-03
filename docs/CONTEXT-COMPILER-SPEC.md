# PaperWise Context Compiler Spec（可演化方向）

> 版本：1.0-draft  
> 状态：活文档（Evolving）——阶段落地后滚动更新第 0 节与决策记录  
> 前置条件：P9 收尾完成并打 tag `v0.6.0-p9-research-native` 之后启动  
> 核心命题：从 Message-Centric Context Management 升级为 State-Centric Context Compilation  
> 演化约束：不新增 Agent 类别、不改 DAG 执行语义、不推翻五层压缩——只做职责重组与增量扩展  

---

## 0. 演化状态（随实施滚动更新）

| 阶段 | 内容 | 状态 | 版本目标 | 落盘提交 |
|------|------|------|----------|----------|
| C1 | Context Compiler + Execution State | 已落地 | `v0.7.0-context-native` | 本轮实施提交 |
| C2 | Incremental Session Memory | 已落地 | `v0.7.0-context-native` | 本轮实施提交 |
| C3 | User Memory Candidate Pipeline | 未开始 | `v0.8.0-memory-pipeline` | — |
| E1 | Selective Activation（扩展点） | 未排期 | — | — |
| E2 | 统一动态预算分配（扩展点） | 未排期 | — | — |

状态取值：`未开始 / spec ready / 实施中 / 已落地 / 已调整`。  
规则：每阶段完成必须回填本表并在附录 A 追加决策记录，否则视为未冻结。

---

## 1. 问题陈述（现状盘点）

P0–P9 之后，系统已有五层压缩、Advanced JSON Cards、Hybrid RAG、Research
Graph、Research State 等多个上下文与记忆来源，但目前存在**三条并列的装配
路径**，每轮真正发给 LLM 的上下文由不同调用点各自拼装：

1. `harness/context.py` — `ContextManager`：五层压缩 + `build_initial_context`
   （静态前缀 + 任务后缀），压缩与初始装配混在同一个类。
2. `core/agent.py` — `HierarchicalMemory`：`build_initial_context` /
   `add_turn` / `amaybe_compress` / `to_messages`，独立的一条装配与压缩路径。
3. `orchestration/memory_adapter.py` — `OrchestratorMemoryAdapter.assemble_context`：
   从 research_state 视图的第三条拼装路径。

此外 `UserMemory.to_context_string()` 与 KnowledgeBase 检索结果由调用点
自行插入。具体问题：

1. **拼装逻辑分散**——system prompt、记忆召回、论文知识、会话摘要、最近
   消息的组装散落在 agent loop、orchestrator 和各 memory 模块。
2. **压缩职责越位**——`ContextManager` 既管压缩又管初始装配；压缩引擎不应
   决定「哪些状态值得进入本轮上下文」。
3. **预算无归属**——各来源没有统一的 token 预算约束，无法按任务类型倾斜。

结论：问题不是能力缺失，而是缺一层 Context Compiler。

---

## 2. 目标架构

```text
Full Source State
    ├── Transcript（原始消息历史）
    ├── Memory & State（用户记忆 / ResearchState / SessionMemory）
    └── External Knowledge（KnowledgeBase / EvidenceRetriever）
          │
          ▼
ContextCompiler.compile(query, runtime_state, transcript, memories, knowledge)
          │
          ▼
    Context IR（分区化中间表示）
          │
          ▼
    Budget Manager（分区 token 预算）
          │
     Select + Compress（压缩委托给现有 ContextManager / HierarchicalMemory）
          │
          ▼
    Final Context（messages） → LLM → Tool / Agent
          │
          ▼
    Trace 回写：Transcript / State Update / Memory Trigger
```

### 2.1 Context IR 分区

| 分区 | 内容 | 来源模块 | 生命周期 | 压缩责任 |
|------|------|----------|----------|----------|
| `system` | 身份 + 规则 + 工具目录（KV Cache 静态前缀） | `ContextManager` | 静态 | 不压缩 |
| `task` | 当前任务 + DAG 状态 | `ResearchState` | 任务级 | 摘要 |
| `execution_state` | 本轮预算 / plan / TODO | `AgentState` / `StatusBar` | 轮级 | 截断 |
| `memory` | 用户记忆召回 | `UserMemory` | 轮级 | selector 限量 |
| `knowledge` | 论文知识 / 证据检索 | `KnowledgeBase` / `EvidenceRetriever` | 轮级 | rerank 后限量 |
| `session_summary` | 增量会话摘要（C2 引入） | `SessionMemory` | 任务级 | 增量维护 |
| `recent_turns` | 最近 N 轮原始消息 | transcript | 轮级 | 滑动窗口 |
| `tool_results` | 本轮工具结果 | transcript | 轮级 | Layer 1 已管 |
| `user_input` | 当前问题 | transcript | 轮级 | 不压缩 |

### 2.2 不变量（Invariants）

- **I1 静态前缀不变**：`system` 分区跨请求字节级不变（KV Cache 友好）。
- **I2 编译确定性**：`compile()` 同输入同输出；LLM 摘要类分区必须缓存摘要，
  避免同任务内漂移。
- **I3 压缩不决定选择**：`ContextManager` 只提供压缩原语；「哪些状态进入
  本轮上下文」由 selector 决定。
- **I4 全程可追溯**：Context IR 记录每块来源模块与 token 数，写入 trace。

---

## 3. C1 — Context Compiler + Execution State

新增：

```text
src/paperwise/context/
    __init__.py
    models.py     # ContextBlock / ContextIR / BudgetPlan
    budget.py     # 静态比例预算（C1）；动态预算留给 E2
    selectors.py  # 每分区一个 selector：runtime_state -> ContextBlock
    compiler.py   # 编排：selectors -> IR -> budget -> 压缩委托 -> messages
```

职责重划：

| 组件 | C1 之后 | 不再负责 |
|------|---------|----------|
| `ContextCompiler` | 装配唯一所有者 | — |
| `ContextManager` | 压缩原语提供者（5 层） | `build_initial_context` 逐步迁移 |
| `HierarchicalMemory` | 会话级压缩后端（被 compiler 调用） | 直接改写 `state.messages` |
| `OrchestratorMemoryAdapter` | 向 compiler 供给 research_state 视图 | `assemble_context` 拼装 |

迁移策略（行为等价优先）：

1. **Shadow mode**：compiler 内部先委托旧路径，trace 对比新旧 messages
   差异（长度、分区覆盖）。
2. **切换**：回归测试全绿后，`agent.py` / orchestrator 默认走 compiler。
3. **退役**：旧 `build_initial_context` 保留为 deprecated 适配层，一个版本
   后移除。

验收标准：

- 新增 `tests/test_context/`：IR 序列化、预算分配、selector 降级。
- 全量回归（当前基线 308 个）通过。
- trace 记录每轮 IR 分区 token 数。
- I1 静态前缀不变量有专项测试。

---

## 4. C2 — Incremental Session Memory

新增 `src/paperwise/memory/session_memory.py`。

核心接口：

```python
class SessionMemory:
    def extract_delta(self, from_message_id: str | None) -> SessionDelta: ...
    def commit(self, delta: SessionDelta) -> None:
        """幂等提交，推进 last_processed_message_id。"""
```

触发时机（满足其一即触发，不每轮强制）：

1. **Token Delta**——自上次提取后新增消息 token 超过阈值。
2. **Semantic Event**——状态更新事件（opportunity / contradiction /
   question 状态变化）。
3. **Before Compaction**——任何压缩执行前必须先触发一次提取，保证摘要
   不丢信息。这是硬性兜底。

与现有模块的关系：

- 不替代 `HierarchicalMemory`（那是 messages 的压缩视图）；SessionMemory
  是语义增量的沉淀层。
- 产出的 `SessionSummary` 是 Context IR `session_summary` 分区的唯一来源。
- `episodic_memory` 记录跨任务 Episode；SessionMemory 只管会话内。

持久化：`last_processed_message_id` 沿用 `memory/storage.py` 的后端，
支持崩溃后断点续传。

验收标准：

- 幂等性：重复 extract 同一区间结果一致；重复 commit 无副作用。
- 压缩前触发钩子有专项测试。
- 崩溃恢复：kill 后重启从 `last_processed_message_id` 续传，不重不漏。

---

## 5. C3 — User Memory Candidate Pipeline

现状：`UserMemory.extract_from_conversation` 让 LLM 直接产出 active
MemoryCard。目标流水线：

```text
Detect -> Candidate -> Score -> Consolidate -> Confirm
```

`MemoryCard` 新增字段（`confidence` 已有）：

| 字段 | 含义 |
|------|------|
| `importance` | 重要性评分，影响召回排序 |
| `stability` | 随观察次数收敛的稳定度 |
| `observation_count` | 该信息被独立观察到的次数 |
| `source_message_ids` | 来源消息，可追溯 |

生命周期状态机：`candidate -> active -> superseded | dropped`。

- **Detect**：LLM 提取产出 `status=candidate` 的卡，不直接生效。
- **Score**：经 `_find_similar` 命中相似卡时合并——`observation_count + 1`、
  `confidence` 提升。
- **Consolidate**：复用现有 `consolidate()`，候选卡沿用合并 / 降级规则。
- **Confirm**：`confidence >= 0.8` 且 `observation_count >= 2` 自动转
  active；否则进入记忆面板 pending 队列人工确认（复用现有记忆管理面板
  与 `update_status`）。

兼容与灰度：

- 旧卡无新字段时 `from_dict` 默认值兼容，不迁移即可读。
- 配置开关 `candidate_pipeline_enabled`，默认关闭，验证后开启。

验收标准：

- candidate 生命周期全路径单测。
- 旧数据兼容：无新字段的 JSON 卡加载不报错。
- 记忆面板 pending 列表可见、可确认、可拒绝。

---

## 6. 预算策略（C1 简版 -> E2 演化）

C1 采用静态比例（落地时按实测调整）：

| 分区 | 默认占比 |
|------|----------|
| `system` | 10% |
| `task` + `execution_state` | 15% |
| `memory` | 10% |
| `knowledge` | 30% |
| `session_summary` | 10% |
| `recent_turns` | 20% |
| reserve | 5% |

E2（扩展点）：按任务类型（问答 / 报告 / 研究循环）动态倾斜；预算分配
本身写入 trace，便于消融实验。

---

## 7. 非目标

- 不做自动自我改进 / 自动实验（P10/P11 已取消，保持取消）。
- 不引入外部框架，沿用「先直接 API 调用」原则。
- 不改 DAG 执行语义，不加新 Agent 类别。
- 不重构 RAG 内部——KnowledgeBase / EvidenceRetriever 只是被 selector
  消费的数据源。

---

## 8. 演化机制（本文件如何演化）

- 每阶段落地：更新第 0 节状态表 -> 在附录 A 追加决策记录 -> 原子提交
  （实施提交用 `C:` 前缀，纯文档用 `docs:` 前缀）。
- 规格变更（分区表、数据模型、状态机）必须先改本文件再动代码。
- 验收失败需要缩水时，必须在决策记录写明缩小后的范围，不允许静默降级。
- tag 规则：C1 + C2 完成 -> `v0.7.0-context-native`；C3 完成 ->
  `v0.8.0-memory-pipeline`。

---

## 附录 A — 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-09-03 | 立项本 spec，作为 P9 冻结后首个演化方向，不属于 P10/P11 复活 | Context / Memory 是 runtime 闭环缺口，与已取消的「自我改进」性质不同 |
| 2026-09-03 | C1 采用 shadow 迁移而非直接切换 | 308 个回归测试是资产，行为等价优先于架构洁癖 |
| 2026-09-03 | C2 触发条件定为 Token Delta / Semantic Event / Before Compaction | 每轮提取成本高且不稳定；压缩前必须兜底 |
| 2026-09-03 | C3 高置信自动确认 + 低置信面板人工确认 | 与现有记忆管理面板能力衔接，避免候选无限堆积 |
| 2026-09-03 | C1 初始装配切到 `ContextCompiler`，旧装配保留为关闭开关下的兼容路径 | 先覆盖最高风险的新任务入口；HierarchicalMemory 继续作为压缩后端 |
| 2026-09-03 | C2 先落 SQLite 游标、稳定 message id、三类触发和确定性结构化摘要 | 满足幂等、压缩前兜底与崩溃续传；LLM 语义摘要留到有真实轨迹评估后再增强 |
| 2026-09-03 | DAG 节点失败/超时也计入节点与全局预算 | 回归发现失败重试路径未扣预算，可能导致无限 replan；该修复是 C 系列回归的一部分 |
