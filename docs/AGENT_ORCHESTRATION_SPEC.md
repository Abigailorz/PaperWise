 # PaperWise Agent 编排改进方案 v0.6.0
 
 > 目标：解决当前 Agent 对简单/复杂任务“一刀切”执行的问题，参考现有 Plan DAG 与多 Agent 编排能力，设计并实现“复杂度感知 + 动态 DAG + 多 Agent 协作”的工作流。
 > 版本：v0.6.0-v2（已实现并提交）
 > 状态：已实现、已测评、待后续功能补全
 
 ## 1. 现状诊断
 
 当前 PaperWise 的 Agent 存在以下问题：
 
 1. **无复杂度区分**：无论是“这篇论文的贡献是什么？”还是“完整分析论文方法、验证数值、生成报告并检查幻觉”，都是一个单 Agent ReAct 循环执行完毕。
 2. **DAG 未真正启用**：`Plan` 已经支持 `depends_on` 和 `next_executable()`，但 `Agent.run()` 内部仍然是线性 ReAct，没有把 DAG 调度作为一级机制。
 3. **多 Agent 编排闲置**：`AgentOrchestrator` 已经实现了 Pipeline / Parallel / Review 回流，但主流水（`Agent.run` / `AgentSession.chat`）没有调用它。
 4. **简单任务 overhead 高**：简单问答也会走完整 plan + judge review，导致延迟和 token 浪费。
 5. **复杂任务能力弱**：复杂任务（报告生成 + 验证 + 审查）挤在单 Agent 上下文里，容易步骤耗尽、上下文爆炸、幻觉增多。
 
 ## 2. 设计目标
 
 1. **复杂度感知**：根据任务文本、期望输出、关键词，自动判定任务属于 simple 还是 complex。
 2. **simple 任务轻量执行**：单 Agent + 最小 plan，优先快速、低成本返回结果。
 3. **complex 任务 DAG 多 Agent 执行**：分解为 Reader -> Verifier -> Writer -> Reviewer 的 DAG，按拓扑顺序调度，支持失败重试和审核回流。
 4. **可评测、可 ablation**：新增 orchestration 开关，支持在评测中对比“统一单 Agent” vs “复杂度感知编排”。
 5. **向后兼容**：不破坏现有 API（`Agent.run`、`AgentSession.chat`、`AgentConfig`），默认开启编排，可通过开关关闭。
 
 ## 3. 复杂度判定规则
 
 ### 3.1 简单任务特征（simple）
 
 - 单点信息查询："这篇论文的贡献是什么？"、"作者是谁？"、"实验用了什么数据集？"
 - 只要求文本回答，不生成文件产物。
 - 关键词：what/who/when/where/which/how many，且不含 report/ppt/verify/critical/analyze/compare。
 - 不需要跨章节综合、不需要数值验证、不需要批判性分析。
 - 工具预期：read_file / grep 即可。
 
 ### 3.2 复杂任务特征（complex）
 
 命中任一条件即判为 complex：
 
 1. **产物型任务**：包含 report / ppt / pptx / slides / 生成报告 / 生成 PPT。
 2. **验证型任务**：包含 verify / validate / numerical / 验证 / 数值 / 代码验证。
 3. **批判型任务**：包含 critical / limitation / weakness / 批判 / 不足 / 缺点。
 4. **综合分析任务**：包含 compare / comparison / survey / 对比 / 综述 / 全面分析。
 5. **多子任务复合**：任务中同时出现“方法 + 实验 + 报告”等多步骤关键词。
 6. **LLM 二义性兜底**：规则判定为 medium confidence 时，用一个 cheap LLM call 做最终分类。
 
 ### 3.3 置信度分级
 
 - **high confidence**：规则强烈命中 simple 或 complex 任一集合，直接执行对应路径。
 - **medium confidence**：规则部分命中，触发一次轻量 LLM 分类（temperature=0.1，max_tokens=20），结果缓存。
 - **low confidence**（无法判定）：保守走 complex 路径，避免遗漏。
 
 ## 4. 工作流设计
 
 ### 4.1 Simple 工作流
 
 ```
 User Task
   |
   |- TaskClassifier -> simple
   |
   \- SingleAgent.run(task)
        |- Plan.from_task_text(task) 生成最小 plan（read_paper -> answer）
        |- ReAct 循环（max_steps 默认 10，budget note 关闭）
        |- _looks_complete 通过文本 marker 判定完成
        \- 直接返回文本答案
 ```
 
 优化点：
 - 不启用 Judge review（`enable_judge_review=False`），减少一次 LLM 调用。
 - 不启用 HierarchicalMemory（短期任务上下文足够）。
 - Plan 仅含 `read_paper` + `answer`，避免多余任务。
 
 ### 4.2 Complex 工作流
 
 ```
 User Task
   |
   |- TaskClassifier -> complex
   |
   \- SmartOrchestrator.run(task)
        |
        |- Phase 1: Reader Agent
        |     读论文、提取关键事实，输出 paper/facts.json
        |
        |- Phase 2: Verifier Agent（可选，仅任务含 verify/numerical/code）
        |     验证数值/公式，输出 paper/verified.json
        |
        |- Phase 3: Writer Agent
        |     基于 facts + verified 写报告/分析，输出 report/report.md
        |
        \- Phase 4: Reviewer Agent
              对抗式审查报告，输出 review/findings.md
              若 critical/major 问题 -> 触发 Revision Writer -> 回到 Phase 4（最多 2 轮）
 ```
 
 DAG 定义（节点即任务，边即依赖）：
 
 ```python
 tasks = [
     Task("read_paper", "Read paper and extract facts", []),
     Task("analyze_method", "Analyze methodology", ["read_paper"]),
     Task("verify_data", "Verify numerical claims", ["read_paper"]),
     Task("generate_report", "Generate structured report", ["analyze_method", "verify_data"]),
     Task("review_report", "Adversarial review", ["generate_report"]),
     Task("revise_report", "Revise report based on review", ["review_report"]),
 ]
 ```
 
 调度规则：
 - 使用 `Plan.next_executable()` 拓扑执行。
 - 每个节点可独立失败；失败后标记下游为 `blocked`。
 - 失败后触发一次 replan（保留已完成节点）。
 - 审核回流：Reviewer 输出 findings；若存在 critical/major 问题，自动进入 revise 节点。
 
 ## 5. 模块设计
 
 ### 5.1 `paperwise.orchestration.classifier.TaskClassifier`
 
 ```python
 class TaskClassifier:
     def classify(self, task: str) -> TaskComplexity:
         # 返回 simple / complex + confidence
 ```
 
 实现：
 - 规则引擎：关键词集合 + 正则。
 - LLM 兜底：medium confidence 时调用一次 `LLMClient.chat`，返回 JSON `{"complexity": "simple|complex", "reason": "..."}`。
 - 缓存：同一 task 文本缓存分类结果（基于 workspace 下的 classifier_cache.json）。
 
 ### 5.2 `paperwise.orchestration.orchestrator.SmartOrchestrator`
 
 ```python
 class SmartOrchestrator:
     async def run(self, task: str, paper_dir: Path) -> AgentResult:
         complexity = self.classifier.classify(task)
         if complexity.is_simple:
             return await self._run_simple(task, paper_dir)
         return await self._run_complex(task, paper_dir)
 ```
 
 职责：
 - 统一入口。
 - 根据复杂度选择执行路径。
 - 收集并返回统一 `AgentResult`。
 
 ### 5.3 `paperwise.orchestrator.paper_dag.PaperDAGPlanner`
 
 生成复杂任务的 DAG Plan：
 
 ```python
 class PaperDAGPlanner:
     @staticmethod
     def build(task: str) -> Plan:
         # 根据任务关键词决定包含哪些节点
 ```
 
 节点类型：
 - `reader`：读论文 + 提取事实。
 - `verifier`：数值/代码验证（任务含 verify 时）。
 - `writer`：生成报告/PPT。
 - `reviewer`：对抗式审查（complex 任务默认启用）。
 - `revision_writer`：根据 review findings 修订。
 
 ### 5.4 子 Agent 规格（基于现有 `SubAgentSpec`）
 
 | Agent | 角色 | 允许工具 | 产物 |
 |-------|------|----------|------|
 | Reader | 论文阅读与事实提取 | read_file, grep, write_file | paper/facts.json |
 | Verifier | 数值/公式验证 | read_file, grep, code_interpreter | paper/verified.json |
 | Writer | 报告/PPT 生成 | read_file, write_file, edit_file, generate_pptx, skill_load | report/report.md, slides.pptx |
 | Reviewer | 对抗式审查 | read_file, grep, write_file | review/findings.md |
 | RevisionWriter | 根据 findings 修订 | read_file, write_file, edit_file, apply_patch | report/report.md |
 
 ## 6. 与现有系统的集成
 
 ### 6.1 `Agent.run` 改造
 
 `Agent.run` 保持公开签名不变，内部调用 `SmartOrchestrator`：
 
 ```python
 async def run(self, task: str) -> AgentResult:
     if not getattr(self.config, "enable_orchestration", True):
         return await self._legacy_run(task)
     orchestrator = SmartOrchestrator(
         llm=self.llm, classifier=TaskClassifier(self.llm, self.workspace),
         base_config=self.config,
     )
     return await orchestrator.run(task, paper_dir=self.workspace)
 ```
 
 当 `enable_orchestration=False` 时，回退到原有单 Agent 实现，保证 ablation 可用。
 
 ### 6.2 `AgentSession.chat` 改造
 
 `AgentSession.chat` 同样增加 orchestration 判断：
 
 ```python
 async def chat(self, user_message: str) -> str:
     if self._should_orchestrate(user_message):
         result = await self._run_orchestrated(user_message)
         return result.final_output
     # 原有单 Agent chat 逻辑
 ```
 
 判断逻辑：若当前已加载论文（`current_paper` 存在）且任务被判为 complex，则走编排；否则走对话式 ReAct。
 
 ## 7. 评测设计
 
 ### 7.1 新增 ablation 配置
 
 在 `ABLATON_CONFIGS` 中新增：
 
 ```python
 "orchestration": {"enable_orchestration": True},
 "no-orchestration": {"enable_orchestration": False},
 ```
 
 ### 7.2 关键对比指标
 
 | 指标 | 说明 |
 |------|------|
 | simple task avg steps | 编排后应低于 no-orchestration |
 | simple task avg tokens | 编排后应低于 no-orchestration |
 | complex task success rate | 编排后应显著高于 no-orchestration |
 | complex task hallucination rate | 编排后应低于 no-orchestration |
 | report rubric score | 编排后应更高 |
 | 审核回流触发率 | 记录Reviewer介入次数 |
 
 ### 7.3 回归测试
 
 - 所有现有 Tier 1 / Tier 2 测试继续通过。
 - 新增 `tests/test_orchestration.py`：
   - `test_classify_simple`：短查询判为 simple。
   - `test_classify_complex`：报告/验证/批判判为 complex。
   - `test_simple_plan_minimal`：simple 任务的 plan 只含 read + answer。
   - `test_complex_dag_dependencies`：complex 任务的 DAG 依赖正确。
 
 ## 8. 实现步骤
 
 1. **Step 1**：新增 `paperwise/orchestration/` 包，含 `classifier.py`、`orchestrator.py`、`paper_dag.py`。
 2. **Step 2**：扩展 `AgentConfig` 增加 `enable_orchestration: bool = True`。
 3. **Step 3**：修改 `Agent.run`，默认调用 `SmartOrchestrator`。
 4. **Step 4**：修改 `AgentSession.chat`，对 complex 任务启用编排。
 5. **Step 5**：扩展 `Plan.from_task_text` 增加 simple mode（生成最小 plan）。
 6. **Step 6**：新增 `tests/test_orchestration.py`。
 7. **Step 7**：更新 `ABLATON_CONFIGS`，跑 ablation 对比实验。
 8. **Step 8**：生成并提交测评报告。
 
 ## 9. 风险与应对
 
 | 风险 | 影响 | 应对 |
 |------|------|------|
 | 分类器误判 simple 为 complex | 增加 overhead | 规则保守，medium 才走 LLM；可事后统计误判率 |
 | 分类器误判 complex 为 simple | 任务失败率高 | low confidence 默认走 complex；关键产物任务强制 complex |
 | 多 Agent 调试复杂 | 开发周期长 | 先串行跑通，再逐步并行；保留详细 trace |
 | API 费用增加 | 无法持续迭代 | 真实 usage 跟踪 + cost budget；simple 任务省下的 token 补贴 complex |
 
 ## 10. 关键决策记录
 
 | 决策 | 选择 | 理由 |
 |------|------|------|
 | 分类方式 | 规则为主 + LLM 兜底 | 规则快、可解释；LLM 处理模糊边界 |
 | simple 路径 | 单 Agent + 最小 plan | 降低延迟和成本 |
 | complex 路径 | DAG + 多 Agent 角色 | 分而治之，降低单 Agent 上下文压力 |
 | 审核回流 | Reviewer -> RevisionWriter 最多 2 轮 | 避免无限循环，保证可终止 |
 | 默认开关 | `enable_orchestration=True` | 新项目即开即用，ablation 可关闭 |
 | 向后兼容 | `enable_orchestration=False` 回退旧逻辑 | 不破坏现有评测入口 |
 
 ## 11. v2 关键实现修正

 在首次真实测评（ORCHESTRATION_EVAL_REPORT.md）暴露问题后，对编排实现做了以下最小化修正：

 1. **Simple 路径读论文强制约束**（`src/paperwise/core/agent_loop.py` + `src/paperwise/core/agent.py`）
    - `_looks_complete` 现在要求：若 plan 中存在 `read_paper`，则该任务必须先被标记为 `DONE`；否则不会认为任务完成。
    - `_init_messages` 在系统提示中显式要求：`"如果 plan 包含 read_paper，必须先调用 read_file/grep 读取 text.md，否则视为幻觉"`。

 2. **Complex 路径产物接力修复**（`src/paperwise/orchestration/orchestrator.py`）
    - 旧实现通过 `AgentOrchestrator` 把子 Agent 跑在隔离的 `sub_agents/<name>` 工作目录，导致 Reader 写的 `facts.json`、Writer 期待的 `facts.json` 不在同一目录。
    - v2 改为直接在 `paper_dir` 中创建并运行子 Agent，所有产物（`facts.json`、`verified.json`、`report/report.md`、`review/findings.md`）共享同一工作区。
    - 每个子 Agent 运行结束后显式检查 `output_path` 是否存在；缺失则把 `success` 置为 `False`。

 3. **分类器规则细化**（`src/paperwise/orchestration/classifier.py`）
    - 新增 `how much / how does / what is the / what are the` 等简单查询关键词。
    - 新增预规则：针对 `"what metric does this paper report?"` 这类问题直接判为 simple。
    - 将 `limitation`、`failure cases`、`additional`、`identify` 等偏事实查询词移出强 complex 列表，避免简单事实问题被误判为报告生成任务。

 4. **子 Agent 直接执行 + 旧编排器清理**
    - 新增 `src/paperwise/orchestration/specs.py`，集中 `SubAgentSpec`、`PaperAnalysisPipeline`、`parse_findings`。
    - 删除冗余的 `src/paperwise/agents/orchestrator.py`（旧 `AgentOrchestrator`）。
    - 子 Agent 不再经过旧的 `AgentOrchestrator.run_pipeline`，由 `SmartOrchestrator._run_sub_agent` 直接创建 `Agent` 运行，避免额外递归和产物隔离。

 5. **DAG 计划瘦身**
    - `PaperDAGPlanner` 不再为含 `critical/limitation` 的任务自动加 review 节点；review 只在任务明确要求 `report/pptx` 时触发。
    - 降低子 Agent 默认 max_steps（reader 12 / verifier 15 / writer 35 / reviewer 25 / revision 35），减少超时。
    - Writer / Reviewer / Revision 子 Agent 开启 `enable_plan=True`，使 `_looks_complete` 基于产物存在性判定完成。

 6. **评测入口修复**
    - `测评/scripts/run_real_evaluation.py` 修复了 `from run_evaluation import ...` 的导入路径（原路径 `workspace/benchmarks` 下没有 `run_evaluation.py`）。

 ## 12. 附录：与 SOTA 的对齐

 - Anthropic *Building Effective Agents*：区分 workflow（预定义）与 agent（动态）。本方案把 simple 任务当作轻量 workflow，complex 任务当作动态 agent DAG。
 - OpenAI *Harness Engineering*：把设计原则写成 Agent 可读的结构化文档。本方案把复杂度规则、DAG 节点规格写入代码和 `docs/AGENT_ORCHESTRATION_SPEC.md`。
 - 李博杰《深入理解 AI Agent》：多 Agent 不共享上下文、Manager 调度、对抗审查。本方案复用现有 `AgentOrchestrator` 的 Manager + Worker 设计。
 
 - Anthropic *Building Effective Agents*：区分 workflow（预定义）与 agent（动态）。本方案把 simple 任务当作轻量 workflow，complex 任务当作动态 agent DAG。
 - OpenAI *Harness Engineering*：把设计原则写成 Agent 可读的结构化文档。本方案把复杂度规则、DAG 节点规格写入代码和 `docs/AGENT_ORCHESTRATION_SPEC.md`。
 - 李博杰《深入理解 AI Agent》：多 Agent 不共享上下文、Manager 调度、对抗审查。本方案复用现有 `AgentOrchestrator` 的 Manager + Worker 设计。
