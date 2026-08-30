# PaperWise P4 — Research Opportunity Engine 架构设计

> 版本：v0.1 设计稿（编码前边界定义）
> 前置阅读：`docs/PaperWise-Roadmap-v0.5-Implementation-Spec.md`（演进主线与迭代记录）
> 状态：**设计评审阶段，尚未编码**

本文档回答一个核心问题：P4 如何做到"研究机会探测"，而不是"又一个更复杂的论文推荐器"。

---

## 1. 定位：P4 不是什么

**P4 不是**：

```text
每天 → arXiv → 找相关论文 → 推送
```

**P4 是**：

```text
DAG 执行过程
  → 产生 Evidence / Findings / Conflicts / Gaps
  → Opportunity Detection
  → Evidence Verification（不能凭空声称机会）
  → Opportunity（结构化领域对象）
  → Action Planner（进入 Dynamic DAG）
```

关键转变：**DAG Execution 本身就是 Opportunity Engine 的信息源**。
Recommendation 只是 Opportunity 的输出形式之一，不是 Opportunity Engine 本身。

---

## 2. 核心领域对象

### 2.1 ResearchOpportunity（新增，一级领域对象）

```python
@dataclass
class ResearchOpportunity:
    opportunity_id: str
    user_id: str
    session_id: Optional[str]

    type: OpportunityType           # 见 2.2
    title: str
    description: str

    evidence: list[EvidenceRef]     # 支持该机会的证据引用，必填
    confidence: float               # 证据强度
    importance: float               # 对当前研究的价值
    novelty: float                  # 是否已知/重复

    related_entities: list[str]     # 涉及的 paper / method / claim
    suggested_actions: list[str]    # 可执行的 Action 类型

    status: OpportunityStatus       # pending | acting | acted | dismissed | expired
    created_at: str
```

### 2.2 OpportunityType（第一版只做 4 种）

| 类型 | 定义 | 例子 |
|------|------|------|
| `KnowledgeGap` | 当前研究需要某知识，用户/KB 中缺失 | 论文用了 DINOv2，用户研究上下文中没有相关基础 |
| `MissingEvidence` | Claim 存在，但检索到的证据不足以支持 | 作者声称优于 baseline，但实验数据不足以支撑 |
| `Contradiction` | 两处来源的 Claim 冲突 | Paper A 说 X，Paper B 说 NOT-X；或用户假设与新证据冲突 |
| `MethodComplementarity` | 两个方法可能互补 | 用户的 semantic pruning + 论文的 uncertainty estimation |

**明确不做**（第一版）：TrendDetection、ReplicationOpportunity、DatasetDiscovery 等——
类型膨胀会让规则与评测失焦。

### 2.3 OpportunityStatus 与生命周期

```text
detected → pending ──→ acting（Action DAG 执行中）──→ acted
                │                                        ↓
                ├──→ dismissed（用户/策略拒绝）      结果回写 confidence
                └──→ expired（cooldown 内未被处理）
```

---

## 3. 架构与模块布局

```text
src/paperwise/opportunity/
├── models.py       # ResearchOpportunity / OpportunityType / OpportunityStatus / EvidenceRef
├── detector.py     # OpportunityDetector：从 Trace + ResearchState 提取候选
├── rules.py        # 4 种类型的确定性检测规则（Mechanism over prompt）
├── evidence.py     # Evidence verification：机会必须有证据支撑
└── scorer.py       # confidence / importance / novelty 打分与排序
```

### 3.1 数据流

```text
                DAG
                 │
                 ▼
               Trace（P0）
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
    Findings   Evidence   Errors / Gaps
       │         │         │
       └─────────┼─────────┘
                 ▼
      OpportunityDetector（rules.py）
                 ▼
        Candidate Opportunities
                 ▼
      Evidence Verification（evidence.py）
                 ▼
      Opportunity Scorer（scorer.py）
                 ▼
        ResearchOpportunity[]
                 ▼
        Action Planner（P4 第二阶段，进 Dynamic DAG）
```

### 3.2 与现有模块的关系

| 现有模块 | 在 P4 中的角色 | 是否改动 |
|----------|----------------|----------|
| P0 Trace / TraceStore | 机会检测的**输入**（claims、evidence、findings、reviewer issues） | 不改，读取 |
| ResearchState | 机会落点：新增 `opportunities: list[ResearchOpportunity]` | 扩展字段 |
| ProactiveEngine | 降级为 Opportunity 的**一种 Action 执行器**（推论文） | 保留，收窄职责 |
| PaperRecommender | arXiv/KB 候选获取逻辑被 Action Planner 复用 | 迁移部分逻辑 |
| KnowledgeBase | Evidence Verification 的检索后端（`search` / `search_chunks` 已具备） | 不改，复用 |
| Dynamic DAG | Opportunity Action 的执行机制（P2 收尾成果的直接复用） | 不改，调用 |

---

## 4. Gap Analysis（编码前必答的 10 个问题）

> 结论先行：**当前基础设施足够启动 P4 第一阶段（Opportunity Detection），
> 但 Claim/Evidence 的结构化程度是最大短板——它决定检测质量上限。**

1. **当前 DAG Event 是否足够支持 Opportunity Detection？**
   基本够。Trace 已记录 NODE_START/END/FAILED、STEP、REPLAN、RESULT。
   缺：事件 payload 中 claims/evidence 是自由文本，无结构化字段。
   → 对策：第一阶段从 `findings`（已有 `Finding.claim/evidence/confidence`）和
   reviewer `findings.json`（已有 flagged_claims 结构）提取，不依赖 Trace payload 增强。

2. **Trace 中有没有结构化的 Claim / Evidence / Finding / Limitation / Method / Experiment？**
   部分有。`ResearchState.findings`（claim + evidence + confidence）和
   reviewer `findings.json`（flagged_claims 带 severity）是现成的结构化来源。
   Limitation / Method / Experiment 散落在子 Agent 产物（facts.json / verified.json）中，
   无统一 schema。→ 第一阶段只用 findings + reviewer findings；结构化抽取留给 P4.5。

3. **ResearchState 是否能表达 Opportunity？**
   不能，需要扩展 `opportunities` 字段 + `add_opportunity()` / `dismiss_opportunity()`。
   改动小，兼容现有 `to_dict/from_dict`（新增字段带默认值）。

4. **KnowledgeBase 是否能支持 Evidence Verification？**
   能。`search(query, top_k)` 与 `search_chunks()` 已存在（hybrid dense+sparse）。
   MissingEvidence 的验证 = 对 claim 做 KB 检索，检查是否有 chunk 支撑。

5. **ProactiveEngine 哪些代码应该保留？**
   保留：`ProactivePolicy`（score_threshold / min_interval / quiet_hours /
   focus_mode_block——这正是 Opportunity 的 interrupt policy 雏形）、
   `_seen_ids` 去重、`record_feedback`。降级：`decide()` 中"从 topics 拉论文"的逻辑
   移入 Action Planner；ProactiveEngine 不再直接产出 Recommendation 作为主路径。

6. **PaperRecommender 哪些逻辑应该迁移？**
   迁移：`build_interest_profile`、`_clean_topics`、arXiv 拉取（`_fetch_arxiv` 链路）。
   这些属于"Action = 推荐论文"的执行细节，归 Action Planner。
   保留在 recommender.py 原位，由 Action Planner 调用，不做物理移动。

7. **Opportunity 是否应该成为新的 Domain Object？**
   应该。它是 P4 → P5 Research Graph 的衔接点，也是评测的对象（机会准确率）。
   不做成 Recommendation 的子类——两者生命周期与语义不同。

8. **Opportunity 产生后如何进入 Dynamic DAG？**
   Action Planner 把 Opportunity.suggested_actions 映射为节点 id，
   走 P2 收尾的 `to_executable_plan` 适配层，复用受控 handler。
   不新增"自由节点"。Action DAG 与原任务 DAG 同构，共享 executor。

9. **如何避免 Opportunity 无限递归触发 DAG？**（最关键）
   硬约束五件套：
   - **Opportunity Budget**：每次 DAG 执行最多产出 N 个机会（默认 3）
   - **Depth Limit**：Opportunity 触发的 Action DAG 不再级联产生新机会（depth=1 截止）
   - **Confidence Threshold**：confidence < 阈值（默认 0.5）的机会不触发 Action
   - **Cooldown + Deduplication**：同 (type, related_entities) 哈希在冷却期内不重复；
     `OpportunityStatus` 为 pending/acting 的同类机会不重复触发
   - **User Interrupt Policy**：复用 ProactivePolicy 的 quiet_hours / focus_mode；
     默认机会只记录为 pending，不主动打断，等用户下次相关研究时 surfaced

10. **如何进行 Opportunity Evaluation？**
    - 过程指标：机会去重率、cooldown 命中率、depth 截断次数（防失控的直接证据）
    - 质量指标：机会 precision（抽样人工/judge 判定"是否真的有价值"）、
      action 采纳率（pending → acted 比例）
    - 复用 P3.5 的 StrategyEvaluator 模式：同一 research state 下
      "开启 vs 关闭 Opportunity Detection" 的 A/B 对比

---

## 5. 三阶段落地

### Phase 1：Opportunity Detection（本迭代编码范围）
- `opportunity/models.py` + `rules.py` + `evidence.py` + `scorer.py`
- 4 种类型 + 五件防递归约束
- ResearchState 扩展 opportunities 字段
- 检测输入：findings + reviewer findings.json（暂不做 Trace payload 增强）
- **不做** Action 执行、不改推荐 UI；机会只落盘为 pending

### Phase 2：Action Planner
- Opportunity → suggested_actions → Dynamic DAG（复用 to_executable_plan）
- 结果回写 opportunity confidence（与 P3.5 record_outcome 同构）

### Phase 3：Proactive 升级
- ProactiveEngine 重构为 Opportunity 的一种输出通道
- pending 机会在用户下次相关研究时 surfaced，而非定时推送

---

## 6. 与 P4.5 的边界

Retrieval-native（chunk 化 + Evidence Pack）是 P4 质量的**放大器**：
机会检测的 evidence verification 依赖检索精度。
当前全文进上下文时，MissingEvidence 检测的召回会受限——
这被接受为 Phase 1 的已知限制，在 P4.5 解决，不阻塞 P4 Phase 1。

---

## 7. 明确的风险

| 风险 | 应对 |
|------|------|
| Opportunity 幻觉（LLM 凭空声称机会） | Evidence Verification 强制：无 evidence_refs 的机会直接丢弃 |
| 无限递归触发 | 五件硬约束（见 Gap 9），并有过程指标监控 |
| 类型膨胀失焦 | 第一版锁死 4 种类型 |
| 机会质量不可测 | Phase 1 落盘 pending + 评测指标先行，再开 Action |
