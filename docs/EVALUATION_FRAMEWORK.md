# PaperWise 评测框架设计（Evaluation Framework）

> 目标：为 PaperWise 建立一套可展示、可复现、可对比的 agent 评测体系，支撑简历项目中的「效果评测展示」环节。
> 版本：v0.1
> 最后更新：2026-08-18

## 目录

1. [背景：从 SOTA Agent 中学到什么](#背景从-sota-agent-中学到什么)
2. [PaperWise 当前差距](#paperwise-当前差距)
3. [四级评测体系](#四级评测体系)
4. [与现有 Harness / Benchmark 的关系](#与现有-harness--benchmark-的关系)
5. [Ablation 对照设计](#ablation-对照设计)
6. [指标与输出格式](#指标与输出格式)
7. [落地路线图](#落地路线图)
8. [附录：参考资源](#附录参考资源)

---

## 背景：从 SOTA Agent 中学到什么

### 1. OpenAI Codex：Agent-First 工程范式

OpenAI Codex 团队在 2026 年 2 月分享的 *Harness Engineering* 中提出：

- **代码仓库要对 agent 可读**：不要把全部知识塞进一个巨大的 `AGENTS.md`，而是把架构、设计原则、执行计划结构化地放在 `docs/`、`ARCHITECTURE.md`、执行计划里，实现「渐进式披露」（progressive disclosure）。
- **用机械规则维持架构一致**：自定义 linter、结构测试、CI 检查来强制约束，而不是依赖 LLM 自觉。
- **把反馈回路编码到仓库**：让 agent 自己审 PR、修复 lint、定期清理技术债务，形成「垃圾回收」机制。
- **人类角色转变**：从写代码转向设计环境、明确意图、构建反馈回路。

### 2. Anthropic：Building Effective Agents

Anthropic 在 *Building effective agents* 中区分了两类系统：

- **Workflow**：LLM 和工具通过预定义代码路径编排，适合确定性任务。
- **Agent**：LLM 动态 directing 自身流程和工具使用，适合需要灵活决策的复杂任务。

核心建议：

- 从最简单方案开始，只在必要时增加复杂度。
- 基础构建块是 **augmented LLM**：检索、工具、记忆。
- 常见 workflow 模式：prompt chaining、routing、parallelization。

### 3. Anthropic：Demystifying Evals for AI Agents

在 *Demystifying evals for AI agents* 中，Anthropic 给出 agent 评测的标准结构：

| 概念 | 含义 |
|------|------|
| **Task** | 单个测试，含输入和成功标准 |
| **Trial** | 一次尝试，通常每个 task 跑多次 |
| **Grader** | 评分逻辑，可包含多个 assertion |
| **Transcript / Trajectory** | 完整执行记录：输出、工具调用、推理过程 |
| **Outcome** | 环境最终状态，而非 agent 说了什么 |
| **Evaluation Harness** | 端到端运行评测的基础设施 |
| **Agent Harness / Scaffold** | 让模型成为 agent 的系统：输入处理、工具编排 |
| **Evaluation Suite** | 面向特定能力或行为的一组 task |

三种 grader 类型：

- **Code-based**：字符串匹配、单元测试、静态分析、状态检查、工具调用检查。优点：快、客观、可复现。
- **Model-based**：rubric 打分、自然语言断言、对比评测、多 judge 共识。优点：灵活、可处理开放题。
- **Human**：专家标注、A/B 测试、inter-annotator agreement。优点：黄金标准，用于校准 model-based grader。

两类 eval：

- **Capability eval**：回答「agent 能做什么？」，初始通过率低，用于爬坡。
- **Regression eval**：回答「agent 是否还能做以前能做的事？」，应保持近 100%。

非确定性处理：

- 同一 task 多次运行，使用 **pass@k**（k 次中至少一次成功）和 **pass^k**（连续 k 次都成功）。
- 区分「能力上限」与「可靠性」。

---

## PaperWise 当前差距

结合 SOTA 经验，PaperWise 目前还存在以下「toy」特征：

| 维度 | SOTA 做法 | PaperWise 现状 |
|------|-----------|----------------|
| 控制逻辑 | 统一、可 ablation、可测试 | 刚抽到 `AgentLoopMixin`，但 `Agent`/`AgentSession` 仍部分重复 |
| 环境约束 | 机械规则（linter/结构测试） | 主要依赖 prompt，缺少架构级自动检查 |
| 反馈回路 | agent 自审、多 agent 交叉审、回归测试 | 仅有单次 Judge review，无系统化反馈 |
| Workflow vs Agent | 明确区分固定 workflow 与动态 agent | 两者混合，界限不清 |
| 知识管理 | 仓库即记录系统，版本化、可查询 | 有 memory/KB，但未形成 agent 可读的知识库 |
| 评测 | 分层、多 grader、可 ablation、可对比 | 已有 Part A/B，但缺少统一 harness、ablation、横向对比 |

---

## 四级评测体系

为把 PaperWise 从「能跑 demo」提升到「可量化、可对比」的简历项目，建议建立四级评测体系。

### Tier 1：Deterministic Component Tests（无 LLM，秒级）

**目标**：保护基础能力不回归，每次 commit 运行。

覆盖内容：

- **安全约束**：路径遍历、危险命令过滤、API key 泄漏检测
- **工具调用校验**：参数 schema 校验、风险等级、调用次数上限
- **Context Manager**：截断、去重、token 估算
- **Plan 状态机**：`Plan.add` / `mark_done` / `progress` 正确性
- **生成器**：PPT / report 在 mock 数据下产出有效文件
- **Memory / KB**：跨 session 召回、RAG 召回率

对应代码：

- `测评/scripts/run_evaluation.py --part a`
- `tests/` 中相关单元测试

**通过标准**：≥ 95%（核心 safety / component 应保持近 100%）。

### Tier 2：Capability Unit Tests with Mock LLM（秒级到分钟级）

**目标**：在不调用真实 API 的情况下，验证 agent 控制逻辑正确。

测试场景：

- 按 plan 顺序调用 `read_file` / `grep` / `write_file`
- budget usage > 70% 时是否注入 budget note
- stagnation 检测是否触发退出
- Judge review 失败时是否能继续修正
- `AgentSession` 多轮对话上下文不丢失
- Plan task 完成后 `_verify_completion` 正确触发

实现方式：

- 预置 `MockLLMClient`，返回固定响应序列
- 检查最终 state、tool stats、exit reason

对应代码：

- `tests/test_integration/test_e2e_paper.py`
- 新增 `tests/test_agent_loop.py`

### Tier 3：Golden Paper Capability Eval（真实 API，可控成本）

**目标**：在真实学术论文上验证 agent 的端到端能力。

数据集：

- `测评/results/golden/golden_feature3dgs_2312.03203.json`
- `测评/results/golden/golden_langsplat_2312.16084.json`
- `测评/results/golden/golden_gaussaingrouping_2312.00732.json`

场景（每个 paper 6 个）：

1. `basic_info_extraction`
2. `numerical_fact_verification`
3. `method_verification`
4. `critical_analysis`
5. `hallucination_veto`
6. `report_generation`

Grader 设计：

| Grader | 类型 | 具体做法 |
|--------|------|----------|
| 输出文件检查 | Code-based | `report/report.md` 等文件存在且长度达标 |
| 关键字符串命中 | Code-based | 检查 golden answer 中的关键词是否出现 |
| 代码可执行性 | Code-based | `code_interpreter` 执行的代码能返回预期结果 |
| Rubric 打分 | Model-based | Kimi K3 按预定义 rubric 对回答/报告打分 |
| Grounded Fact Quality | Model-based | 分别打分 factual accuracy、evidence grounding、answer scope，并记录 unsupported claims |
| 工具调用检查 | Transcript | 是否调用了期望工具、是否有重复调用 |
| 效率指标 | Transcript | steps、tokens、legal tool rate |

对应代码：

- `测评/scripts/run_real_evaluation.py --part b`
- 新增 `paperwise/evaluation/graders/`

### Tier 4：Ablation + 横向对比（论文核心卖点）

**目标**：证明 PaperWise 设计的有效性。

对照配置：

| 配置 | 说明 |
|------|------|
| `paperwise-full` | 当前完整系统：Plan + Budget + Judge + HierarchicalMemory |
| `paperwise-no-plan` | 移除显式 Plan，让 LLM 自己推断 TODO |
| `paperwise-no-budget` | 移除 budget-aware 提示 |
| `paperwise-no-judge` | 移除 Judge review |
| `paperwise-no-memory` | 移除 HierarchicalMemory 压缩 |
| `baseline-react` | 最基础 ReAct，无 plan/budget/judge/memory |

在同一套 golden tasks 上跑同样 k 次，对比：

- 通过率（Pass@k / pass^k）
- 平均 steps / tokens / 耗时
- 幻觉率
- 报告 rubric 分数

这是简历中最能体现「设计价值」的实验。

---

## 与现有 Harness / Benchmark 的关系

| 外部 Harness / Benchmark | 评测对象 | PaperWise 的对照方式 |
|----------------------------|----------|------------------------|
| **AgentHarness (Apodex)** | 深度研究 agent 的 BrowseComp、DeepSearchQA、HLE-Text | 形式类似，但 PaperWise 聚焦学术 paper reading，用论文 golden tasks 替代开放网页任务 |
| **SWE-bench** | 真实 GitHub issue + 单元测试 | 借鉴其「确定性测试」思想，但任务不是 coding，而是报告/分析产出 |
| **τ-Bench / GAIA** | 多轮对话 + 环境状态检查 | `AgentSession` 的 chat 模式可向此对齐，但不是核心卖点 |
| **WebArena / OSWorld** | GUI / 浏览器 / OS 任务 | 不适用 |
| **AgentBench** | 多环境通用 agent 能力 | 可对比 tool-use、reasoning 维度，但领域不同 |

**PaperWise 的差异化优势**：

1. **领域聚焦**：真实学术论文 + 人类标注 golden answers，避免开放域评测的不可控。
2. **多维度评估**：不只测「答对没」，还测幻觉、引用、报告质量、token 效率。
3. **内置 ablation**：Plan / Budget / Judge / Memory 都是可开关的模块，天然适合做对照实验。
4. **工程化链路**：从 component test 到 real-paper eval 形成完整闭环。

---

## Ablation 对照设计

### 开关设计

在 `AgentConfig` 或 settings 中增加布尔开关：

```python
@dataclass
class AgentConfig:
    ...
    enable_plan: bool = True
    enable_budget_note: bool = True
    enable_judge_review: bool = True
    enable_hierarchical_memory: bool = True
```

`AgentLoopMixin` 中根据开关决定是否调用对应逻辑。

### 实验设计

- 每个配置在 3 篇 paper × 6 个 scenario 上运行，k=3（至少）。
- 如果成本敏感，可先做 k=1 的 smoke test，再对关键 scenario 跑 k=3。
- 输出对比表格和图表。

### 预期结论（可验证）

| 假设 | 验证方式 |
|------|----------|
| 显式 Plan 能提升任务完成率 | 对比 `paperwise-full` vs `paperwise-no-plan` |
| Budget-aware 能减少冗余探索 | 对比 steps / tokens、是否在 budget 耗尽前收敛 |
| Judge review 能降低事实错误 | 对比 factual accuracy 与 unsupported claim 分离统计 |
| HierarchicalMemory 能在长 paper 上保持 recall | 对比长论文场景下的内容覆盖率 |

---

## 指标与输出格式

### 评测指标口径（v2）

六个场景不再只被理解成六次 PASS/FAIL，而是按三个维度重组：

| 维度 | 场景 / 指标 | 判定原则 |
|------|--------------|----------|
| **能力维度** | `basic_info_extraction`、`method_verification`、`critical_analysis` | Agent 能否完成定位、解释和推理任务；`critical_analysis` 单独看 reasoning quality |
| **质量维度** | `numerical_fact_verification`、`hallucination_veto`、`report_generation` 的报告质量 | 数值必须真实且有证据；正确拒答和拒答后过度展开分开计分 |
| **工程维度** | `completed`、`timeout`、steps、tokens、legal tool rate、efficiency | 长任务超时不等于产出为 0；已生成的报告文件必须参与评分 |

`Grounded Fact Quality` 把旧的单一 hallucination veto 拆成四个信号：

```json
{
  "factual_accuracy": 0.95,
  "evidence_grounding": 1.0,
  "scope_compliance": 0.75,
  "unsupported_claims": 1
}
```

关键规则：只有 `factual_error` 才能触发事实否决；`scope_violation` 只降低
scope compliance，不再把论文中真实存在的补充细节误判成幻觉。这样可以避免
“Agent 越少说话分数越高”的伪优化。

`hallucination_veto` 也采用同样规则：正确说出“论文未报告该指标”是事实
正确；若随后列出额外指标，只会降低 scope compliance，不应因为额外展开而
判成伪造目标指标。

`report_generation` 单独保留 quality 与 efficiency：

```json
{
  "quality": 0.86,
  "efficiency": 0.42,
  "completed": false,
  "timeout": true,
  "artifact_chars": 4200
}
```

超时时评分器会读取 `report/report.md` 和 `report/sections/*.md`，对已经落盘
的中间产物继续评 quality；timeout 只进入 engineering 维度，不再自动把 score
置为 0。

### 单次 trial 指标

```json
{
  "scenario": "basic_info_extraction",
  "paper_id": "feature3dgs_2312.03203",
  "passed": true,
  "score": 0.75,
  "quality": 0.86,
  "efficiency": 0.72,
  "completed": true,
  "timeout": false,
  "steps": 5,
  "tokens_used": 3200,
  "duration": 18.5,
  "legal_rate": 1.0,
  "rubric": 2.5,
  "hallucination": {"severity": "none", "unsupported_claim_count": 0, "scope_compliance": 1.0},
  "fact_quality": {
    "factual_accuracy": 0.95,
    "evidence_grounding": 1.0,
    "scope_compliance": 1.0,
    "unsupported_claim_count": 0
  },
  "details": [...],
  "errors": []
}
```

### 聚合指标

```json
{
  "paper_id": "feature3dgs_2312.03203",
  "model": "deepseek-v4-flash",
  "judge": "kimi-k3",
  "k": 3,
  "total_runs": 18,
  "passed": 14,
  "success_rate": 0.7778,
  "pass_at_k": 0.9500,
  "pass_consecutive_k": 0.6000,
  "avg_steps": 8.2,
  "avg_tokens": 5200,
  "avg_duration": 42.1,
  "avg_legal_rate": 0.96,
  "avg_rubric": 2.3,
  "quality_engineering_dimensions": {
    "ability_score": 0.7778,
    "quality_score": 0.82,
    "engineering_score": 0.71,
    "scope_violation_rate": 0.22,
    "unsupported_claims_per_run": 0.6
  },
  "per_scenario": {...}
}
```

### 可视化建议

- 各配置 Pass@k 对比柱状图
- 各配置 avg tokens / steps 对比图
- factual accuracy / grounding / scope compliance 雷达图（按 scenario）
- 报告 rubric 分数分布

---

## 落地路线图

### Phase 1：基础设施（已完成 / 进行中）

- [x] 合并 `Agent` / `AgentSession` 控制逻辑到 `AgentLoopMixin`
- [x] 修复 `run_evaluation.py` PPT 生成引用
- [x] 修复 `parse_papers.py` / `run_real_evaluation.py` 路径
- [ ] 统一评测入口：`paperwise eval` 或 `python -m 测评.scripts.eval`
- [x] 抽象 Grader 接口：`CodeGrader`、`RubricGrader`、`GroundedFactGrader`、`TranscriptMetrics`

### Phase 2：可重复真实论文评测

- [ ] 标准化 golden dataset schema
- [x] 实现 Tier 3 多 grader 流水线
- [ ] 生成标准 JSON / Markdown report
- [ ] 支持 `--config full|no-plan|no-budget|no-judge|no-memory|baseline`

### Phase 3：Ablation 与对比

- [ ] 实现 ablation 开关
- [ ] 在 3 篇 paper、6 个 scenario 上跑 k=3
- [ ] 输出对比报告，含图表
- [ ] 与 baseline ReAct 做显著性对比

### Phase 4：简历展示

- [ ] 整理成「Benchmark Results」页面 / README 章节
- [ ] 提供一键复现命令
- [ ] 附关键结论和折线图

---

## 附录：参考资源

1. **OpenAI — Harness engineering: leveraging Codex in an agent-first world**（2026-02-11）
   - 核心：agent-readable codebase、渐进式披露、机械规则、反馈回路。
2. **Anthropic — Building effective agents**（2024-12-19）
   - 核心：workflow vs agent、augmented LLM、简单可组合模式。
3. **Anthropic — Demystifying evals for AI agents**（2026-01-09）
   - 核心：task/trial/grader/transcript/outcome、三类 grader、capability vs regression eval、pass@k/pass^k。
4. **ApodexAI / AgentHarness**
   - GitHub: https://github.com/ApodexAI/AgentHarness
   - 核心：Deep-research benchmark harness，覆盖 BrowseComp、DeepSearchQA、HLE-Text。
5. **SWE-bench Verified**
   - 核心：coding agent 标准 benchmark，用真实 GitHub issue + 单元测试评分。
6. **τ-Bench / GAIA / WebArena / OSWorld**
   - 核心：多轮对话、浏览器/OS 环境下的 agent 评测。

---

> 本文件为设计文档，后续实现将基于本文档逐步推进。真实 API 评测仅在功能验证通过后小范围运行，避免不必要的成本。
