# PaperWise Memory & Proactive Intelligence V2 Spec

> 版本：V2.0  
> 目标：把当前独立的 UserMemory / KnowledgeBase / HierarchicalMemory / PaperRecommender 升级为一个围绕“Research State”双向闭环的智能记忆 + 主动推荐系统。  
> 范围：以当前已有实现为基础，提出可渐进落地的架构 spec，而非一次性推翻重写。

---

## 1. 设计原则

| 旧认知 | V2 认知 | 含义 |
|--------|---------|------|
| Memory = Database | Memory = Agent Long-Term State | 记忆是 Agent 后续推理、规划和决策的状态输入，不只是可检索的记录。 |
| Recommendation = Search | Recommendation = Proactive Decision | 推荐不是简单返回相似论文，而是判断“当前是否有值得主动帮用户做的事”。 |
| DAG = Execution | DAG = Action / Reasoning Process | DAG 执行过程中会不断产生事件、发现缺口，这些要回写记忆。 |

最终闭环：

```
User  →  Conversation  →  Memory / ResearchState
                              ↓
                    Context Engine（按需召回）
                              ↓
                       Dynamic DAG
                              ↓
                    Execution + Events
                              ↓
              Findings / Gaps / New Topics
                              ↓
                    Proactive Engine
                              ↓
                  Recommendation + Explanation
                              ↓
                      User Feedback
                              ↓
                       Memory Update
```

---

## 2. 记忆分层（Memory Taxonomy）

当前实现映射：

| 当前模块 | V2 层级 | 作用 |
|----------|---------|------|
| `UserMemory` | Profile Memory + Semantic Memory | 长期稳定的用户画像、偏好、事实。 |
| `KnowledgeBase` | Semantic Memory | 已解析论文、图表、公式等可检索知识；未来扩展 Knowledge Graph。 |
| `HierarchicalMemory` | Working Memory | 当前会话的上下文压缩（recent / working / long-term summaries）。 |
| — | Episodic Memory | 记录“用户和 Agent 一起完成了什么任务”。新增。 |
| — | Procedural Memory | 记录“用户喜欢和 Agent 怎样协作”。新增。 |
| — | ResearchState | 连接记忆与 DAG 的实时研究状态。新增核心。 |

### 2.1 Profile Memory

描述用户是谁，长期稳定。

```python
@dataclass
class ProfileMemory:
    memory_id: str
    user_id: str
    key: str                 # e.g. "research_domains", "output_preference"
    value: Any
    confidence: float        # 0.0 ~ 1.0
    source: MemorySource     # conversation | explicit | inference | feedback
    status: MemoryStatus     # active | stale | conflicting | archived
    created_at: datetime
    updated_at: datetime
    last_confirmed_at: Optional[datetime]
```

- 支持 key 重复：同一 key 可有多条不同来源/置信度的记忆，conflict detection 后选出 active。
- 生命周期：根据反馈、recency、staleness 自动调整 confidence 和 status。

### 2.2 Semantic Memory

- 已解析论文内容：`KnowledgeBase` 的 chunks + metadata。
- 可扩展为轻量 Knowledge Graph：实体（方法、数据集、指标）和关系（uses/evaluates-on/improves）。

### 2.3 Episodic Memory

记录“一次有意义的任务”。不是每一轮对话，而是“分析某篇论文”“生成 PPT”“完成一次文献对比”。

```python
@dataclass
class Episode:
    episode_id: str
    user_id: str
    session_id: Optional[str]
    task_type: str              # paper_analysis | comparison | report | ppt | qa
    goal: str
    entities: list[str]         # 涉及论文、方法、项目
    actions: list[str]          # Agent 执行的关键节点
    findings: list[str]
    decisions: list[str]
    unresolved_questions: list[str]
    outcome: str                # completed | partial | abandoned
    artifacts: list[str]
    created_at: datetime
```

Episodic Memory 让 Agent 能回答：
- “我上周分析过哪篇类似论文？”
- “上次比较时我关注了哪些指标？”

### 2.4 Procedural Memory

记录用户与 Agent 的协作策略，例如：
- 论文分析习惯：先方法 → 再创新点 → 再实验 → 最后局限。
- PPT 偏好：图多字少。
- 对比任务：更关注 ablation 和实现细节。

```python
@dataclass
class ProceduralPattern:
    pattern_id: str
    user_id: str
    task_type: str
    context_signature: dict      # 触发条件
    preferred_steps: list[str]  # 用户偏好的执行顺序
    preferences: dict            # 每步偏好参数
    success_rate: float          # 历史成功率
    last_used: datetime
```

### 2.5 Working Memory / Context Engine

`HierarchicalMemory` 继续承担会话级 Working Memory。V2 增强：
- 显式保存当前 `ResearchState` 的快照。
- 上下文组装时，除了 recent/working/long-term summaries，还要召回相关的 Profile/Episode/Procedure。

---

## 3. ResearchState：连接记忆与 DAG 的核心桥

```python
@dataclass
class ResearchState:
    state_id: str
    user_id: str
    session_id: Optional[str]

    # 当前任务
    current_task: str
    intent: TaskIntent          # simple_qa | analysis | comparison | report | ppt | verify | open_ended
    complexity: TaskComplexity  # simple | medium | complex

    # 当前论文/项目
    current_paper: Optional[str]
    related_papers: list[str]

    # 已知与未知
    findings: list[Finding]              # 已确认发现
    gaps: list[KnowledgeGap]             # 知识缺口
    unresolved_questions: list[str]      # 未解问题
    next_steps: list[str]                # 建议下一步

    # 执行状态
    dag_status: DAGStatus                # running | paused | completed | failed | budget_exhausted
    completed_nodes: list[str]
    failed_nodes: list[str]

    # 置信与时间
    confidence: float
    updated_at: datetime
```

`ResearchState` 由 `SmartOrchestrator` 在执行过程中维护；每个 DAG 节点结束后，节点结果会更新 ResearchState。推荐系统读取 ResearchState 判断是否存在主动机会。

---

## 4. Context Engine：任务感知的记忆召回

### 4.1 目标

回答：“当前这个任务，应该想起什么？”

### 4.2 召回流程

1. **解析当前意图**：从 `ResearchState` 获取 `intent`、`current_task`、`current_paper`。
2. **分层召回**：
   - Profile：用户的 `research_domains`、`output_preference` 等。
   - Episode：历史上相似任务/相似论文的 Episode。
   - Procedural：当前 `task_type` 匹配的协作模式。
   - Semantic：`KnowledgeBase` 中关于当前论文的相关 chunks。
   - Working：最近几轮对话和 working summary。
3. **重排与组装**：
   - 用 Cross-Encoder 或轻量 LLM 对召回记忆做相关性打分。
   - 组装成 `ContextPackage` 注入 system prompt。

### 4.3 输出格式

```xml
<context>
  <profile>...</profile>
  <episodes>...</episodes>
  <procedures>...</procedures>
  <paper_context>...</paper_context>
  <working_memory>...</working_memory>
</context>
```

---

## 5. Proactive Engine：从“搜索”到“主动决策”

### 5.1 触发源（Triggers）

| 触发类型 | 示例 | 当前实现 |
|----------|------|----------|
| Event-based | DAG 执行发现 knowledge gap | 未实现 |
| Goal-based | 用户 stated goal 完成到 80% | 未实现 |
| Gap-based | ResearchState.gaps 非空 | 未实现 |
| Context-based | 用户换到新方向，与长期兴趣产生张力 | 未实现 |
| Time-based | 每日/每会话首次推荐 | 已实现 `_recommend_loop` |
| Upload-based | 上传论文后 5 分钟 | 已实现 `_maybe_push_recommendations` |

V2 重点补充 Event/Goal/Gap/Context 触发，降低对纯时间触发的依赖。

### 5.2 候选源（Candidate Sources）

- arXiv 最新论文（已有）。
- 本地 `KnowledgeBase` 中相关但未深入阅读的论文。
- 历史 `Episode` 中提到的相关论文。
- 用户长期兴趣对应的 benchmark / survey。
- 当前 DAG 执行中发现的相关方法/数据集对应的论文。

### 5.3 排序模型（Context-aware Ranking）

从单一关键词匹配升级为多维度 score：

```
score = w1 * relevance_to_research_state
      + w2 * relevance_to_profile
      + w3 * novelty_vs_episodes
      + w4 * urgency_of_gap
      + w5 * user_feedback_bias
      - w6 * recent_exposure
```

- `relevance_to_research_state`：与 current task / gaps / current paper 的语义相关度。
- `relevance_to_profile`：与用户长期兴趣的匹配度。
- `novelty_vs_episodes`：与用户已看过论文的去重/新颖度。
- `urgency_of_gap`：是否能填补当前 ResearchState.gaps。
- `user_feedback_bias`：历史点击/收藏的加权。
- `recent_exposure`：避免反复推同一篇。

### 5.4 Policy 与解释

Policy 决定是否真的推送：
- 分数阈值。
- 同一会话/同一天节流窗口。
- 用户当前是否处于 focus mode（避免打扰）。
- 推荐数量上限。

Explanation：每条推荐附带一句话理由，例如：
> “你正在分析 3D Gaussian Splatting 的加速方法；这篇新论文提出了与你当前方法互补的稀疏采样策略，可能填补你在 `rasterization gap` 里的疑问。”

---

## 6. 与 SmartOrchestrator / DAGExecutor 的集成

### 6.1 DAG → ResearchState

在 `DAGExecutor._process_result` 中，节点成功后调用 `ResearchStateUpdater`：
- Reader → 更新 `findings`。
- Verifier → 更新 `gaps` 或关闭已有 gap。
- Reviewer → 新增 `unresolved_questions`。
- Replan → 新增 `next_steps`。

### 6.2 ResearchState → ProactiveEngine

`ProactiveEngine` 监听 `ResearchState` 变化：
- `gaps` 新增 → 触发候选召回。
- `intent` 变为 `open_ended` → 主动询问用户是否需要文献综述。
- `outcome=completed` 且与历史 Episode 相似 → 提醒用户“是否需要把这次分析 extend 到上次未完成的比较”。

### 6.3 推荐 → 会话

沿用现有 `_push_recommendations_for_session` 机制，但内容从单一 arXiv 结果升级为：
- 论文列表。
- 推荐理由（explanation）。
- 对应的知识缺口（linked gap）。
- 建议的下一步操作（例如：加入对比列表、直接分析、添加到阅读列表）。

---

## 7. 记忆生命周期（Lifecycle）

### 7.1 写入流程

```
Extract（LLM 抽取） → Gate（置信度/来源校验） → Validate（冲突检测） → Store（写入对应层级）
```

### 7.2 衰减与刷新

| 因素 | 影响 |
|------|------|
| confidence | 高置信度记忆更持久，低置信度更快衰减。 |
| recency | 越新的记忆权重越高。 |
| staleness | 用户长期未确认的记忆标记为 stale。 |
| feedback | 用户点赞/收藏提升权重；忽略/拒绝降低权重。 |

`UserMemory.consolidate()` 已具备基础合并能力，V2 加入上述多因子生命周期评分。

---

## 8. 反馈闭环

| 反馈类型 | 来源 | 用途 |
|----------|------|------|
| 显式 | 用户点击“有用/无用”、收藏、添加到列表 | 更新 Profile/记忆权重，调整排序模型 bias。 |
| 隐式 | 用户是否打开链接、是否追问、是否执行推荐操作 | 更新 Episode 和反馈信号。 |
| 执行反馈 | DAG 节点成功/失败、Replan 次数 | 更新 Procedural Memory 的成功率。 |

---

## 9. 实现阶段（渐进落地）

### Phase 1：重构记忆数据模型
- 将 `MemoryCard` 升级为支持 `source/confidence/status/lifecycle` 的 `ProfileMemory`。
- 新增 `EpisodicMemory` + `Episode`。
- 新增 `ProceduralMemory` + `ProceduralPattern`。
- 新增 `ResearchState` dataclass，并在 `SmartOrchestrator` 中维护。

### Phase 2：Context Engine
- 实现 `ContextEngine.retrieve(research_state)`，统一召回 Profile/Episode/Procedure/KB/Working Memory。
- 替换当前 `HierarchicalMemory.to_messages()` 的硬编码拼接，改为按任务重排组装。

### Phase 3：Proactive Engine
- 新增 `ProactiveEngine`：监听 ResearchState 变化、生成候选、排序、应用 Policy、生成 explanation。
- 扩展推荐触发源：Event / Goal / Gap / Context，保留现有 Time/Upload 触发。
- 候选源扩展到 KB 和 Episode，不只 arXiv。

### Phase 4：反馈与闭环
- 在推荐消息中加入 feedback 入口（有用/无用/收藏）。
- 将反馈回写到 Profile/Episode 和排序模型 bias。
- 让 `consolidate()` 支持多因子生命周期评分。

### Phase 5：评估
- 离线：推荐点击率、gap 填补率、用户反馈分布。
- 在线：A/B 对比（纯 arXiv vs. Context-aware Proactive）。

---

## 10. 与当前代码的关键映射

| 新增/改动 | 影响文件 |
|-----------|----------|
| ProfileMemory 升级 | `src/paperwise/memory/user_memory.py` |
| EpisodicMemory | 新增 `src/paperwise/memory/episodic_memory.py` |
| ProceduralMemory | 新增 `src/paperwise/memory/procedural_memory.py` |
| ResearchState | 新增 `src/paperwise/memory/research_state.py` |
| Context Engine | 新增 `src/paperwise/memory/context_engine.py` |
| ProactiveEngine | 新增 `src/paperwise/memory/proactive_engine.py` |
| DAG → ResearchState | `src/paperwise/orchestration/dag_executor.py` |
| 推荐集成 | `src/paperwise/recommender.py`, `src/paperwise/api/server.py` |

---

## 11. 总结

V2 不是推翻现有 `UserMemory` / `KnowledgeBase` / `HierarchicalMemory`，而是把它们重新组织成：

1. **Memory Intelligence** —— Agent 该记住什么（Profile / Semantic / Episodic / Procedural + ResearchState）。
2. **Context Intelligence** —— 当前任务该想起什么（Context Engine）。
3. **Proactive Intelligence** —— 现在该主动帮用户做什么（Proactive Engine）。

三者通过 `ResearchState` 双向闭环：DAG 执行更新 ResearchState，ResearchState 驱动推荐，推荐反馈又更新记忆。这是把 PaperWise 从“论文 RAG + Agent”拉升到“长期研究助手”的关键架构。


## Implementation Status

| Component | Status | Files | Notes |
|---|---|---|---|
| Memory data model refactor | Done | src/paperwise/memory/user_memory.py, src/paperwise/memory/__init__.py | Added source, status, user_id, last_confirmed_at to MemoryCard; added update_status() and pply_feedback() helpers. |
| Episodic memory | Done | src/paperwise/memory/episodic_memory.py | Episode + EpisodicMemory with task/query support. |
| Procedural memory | Done | src/paperwise/memory/procedural_memory.py | ProceduralPattern + ProceduralMemory for learned workflows. |
| Research state manager | Done | src/paperwise/memory/research_state.py | Tracks ResearchState, KnowledgeGap, active/failed nodes. |
| Context engine | Done | src/paperwise/memory/context_engine.py | Assembles Profile, Episodic, Procedural, KB, and Working Memory into XML context block. |
| Proactive engine | Done | src/paperwise/memory/proactive_engine.py | Multi-source recommendations with scoring, policy/throttle, explanations, and feedback loop. |
| Orchestrator integration | Done | src/paperwise/orchestration/orchestrator.py, src/paperwise/orchestration/dag_executor.py | SmartOrchestrator initializes ResearchStateManager and ProactiveEngine; writes/updates ResearchState before/after DAG execution; infers knowledge gaps from failed nodes; stores proactive recommendations in status. |
| API integration | Done | src/paperwise/api/server.py | /api/recommend uses ProactiveEngine when a ResearchState exists. |
| Tests | Done | 	ests/test_memory_v2.py | 6/6 new tests pass; 87/87 existing non-e2e tests pass after fixing SmartOrchestrator regressions. |

### Known divergences / follow-ups

1. **Reviewer loop**: The current DAG uses a single review round followed by an external max_review_rounds loop. A fully dynamic reviewer-loop node inside the DAG is future work.
2. **E2E tests**: 	ests/test_integration/test_e2e_paper.py has two pre-existing assertion mismatches (steps <= 10 under orchestration, success is False under step-limit) and a judge-homogeneity environment check. These are not introduced by the memory V2 changes.
3. **Git push**: This commit is ready to push once a GitHub personal access token is available in the environment.
