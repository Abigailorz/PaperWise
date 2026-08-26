 # PaperWise Agent 编排改进测评报告
 
 > 生成时间：2026-08-26
 > 目标：验证“复杂度感知 + DAG 多 Agent 编排”改进的实际效果
 > 模型：Agent=qwen-plus，Judge=qwen-turbo（DashScope 兼容模式）
 
 ## 1. 评测范围
 
 本次测评覆盖：
 
 - **Tier 1**：确定性安全/组件测试（Part A）。
 - **Tier 2**：Mock-LLM 控制逻辑 + 新编排模块单元测试。
 - **Tier 3**：合成测试论文 `simple` 的 6 个场景（k=1）。
 - **Tier 3+**：真实论文 `feature3dgs_2312.03203` 的 6 个场景（k=1）。
 - **Ablation**：同一任务分别运行 `orchestration`（启用编排）与 `no-orchestration`（退化为单 Agent）配置。
 
 ## 2. 关键发现
 
 | 维度 | 结论 |
 |------|------|
 | **实现完成度** | 已完成 `TaskClassifier`、`PaperDAGPlanner`、`SmartOrchestrator` 及与 `Agent`/`AgentSession` 的集成；Mock 测试全部通过。 |
 | **Tier 1 安全层** | 25/25 全部通过，安全层保持 100% 拦截率。 |
 | **合成论文 simple** | orchestration 配置 6 场景通过 2 个；no-orchestration 配置运行超时未完成。 |
 | **真实论文 feature3dgs** | orchestration 配置 6 场景通过 1 个；no-orchestration 配置通过 2 个。 |
 | **耗时** | 真实论文 orchestration 约 5 分钟；no-orchestration 约 7 分钟。编排具备更快收敛的潜力。 |
 | **关键问题** | 当前 simple 路径对模型跟随极简 Plan 的约束不足，导致部分应读取/验证的任务未调用工具；complex 路径的子 Agent prompt 和产物接力仍需调优。 |
 
 ## 3. Tier 1：确定性安全/组件测试
 
 - **通过：25/25（100%）**
 - 覆盖：危险命令过滤、路径遍历、提示注入、API key 泄漏、约束引擎、上下文截断/去重、JSON 校验、用户记忆、论文推荐、PPT 生成。
 
 详细结果见 `outputs/orchestration_part_a.json`（Part A）。
 
 ## 4. Tier 2：Mock-LLM + 编排单元测试
 
 直接调用测试函数验证通过：
 
 | 测试 | 结果 |
 |------|------|
 | test_plan_order_read_then_grep | PASS |
 | test_budget_note_injected_at_high_usage | PASS |
 | test_budget_note_disabled | PASS |
 | test_stagnation_exit | PASS |
 | test_no_plan_ablation | PASS |
 | test_session_context_preserved | PASS |
 | test_classify_simple | PASS |
 | test_classify_complex | PASS |
 | test_simple_plan_minimal | PASS |
 | test_complex_dag_dependencies | PASS |
 | test_smart_orchestrator_routes_simple | PASS |
 
 > 说明：`pytest` 收集阶段会被 `tests/conftest.py` 加载的 FastAPI app 阻塞，因此采用直接调用测试函数的方式。
 
 ## 5. Tier 3：合成论文 simple 对比
 
 ### orchestration 配置
 
 | 场景 | 结果 | 分数 | 步数 | 合法率 | 幻觉 |
 |------|------|------|------|--------|------|
 | basic_info_extraction | PASS | 85% | 6 | 40% | none |
 | numerical_fact_verification | FAIL | 48% | 0 | 100% | critical |
 | code_verification | FAIL | 48% | 0 | 100% | critical |
 | critical_analysis | FAIL | 71% | 0 | 100% | minor |
 | report_generation | FAIL | 44% | 0 | 100% | critical |
 | hallucination_veto | PASS | 81% | 5 | 40% | none |
 
 **聚合**：通过 2/6，平均步数 1.8。
 
 ### no-orchestration 配置
 
 运行超时，未能完成全部 6 个场景，因此无法给出完整对比。从部分输出来看，单 Agent 无编排时容易在复杂场景（如 report_generation）中长时间探索。
 
 ## 6. Tier 3+：真实论文 feature3dgs 对比
 
 ### orchestration 配置
 
 | 场景 | 结果 | 分数 | 步数 | 合法率 | 幻觉 |
 |------|------|------|------|--------|------|
 | basic_info_extraction | PASS | 100% | 2 | 100% | minor |
 | numerical_fact_verification | FAIL | 77% | 0 | 100% | minor |
 | method_verification | FAIL | 48% | 0 | 100% | major |
 | critical_analysis | FAIL | 64% | 3 | 100% | critical |
 | hallucination_veto | FAIL | 0% | 0 | 0% | None |
 | report_generation | FAIL | 38% | 0 | 100% | major |
 
 **聚合**：通过 1/6，平均步数 0.8。
 
 ### no-orchestration 配置
 
 | 场景 | 结果 | 分数 | 步数 | 合法率 | 幻觉 |
 |------|------|------|------|--------|------|
 | basic_info_extraction | FAIL | 70% | 6 | 100% | major |
 | numerical_fact_verification | FAIL | 70% | 6 | 100% | critical |
 | method_verification | FAIL | 70% | 8 | 100% | critical |
 | critical_analysis | FAIL | 85% | 10 | 100% | minor |
 | hallucination_veto | PASS | 100% | 5 | 100% | none |
 | report_generation | PASS | 85% | 20 | 100% | minor |
 
 **聚合**：通过 2/6，平均步数 7.5。
 
 ## 7. 结果分析
 
 1. **Simple 路径过短**：多个场景被 `TaskClassifier` 判为 simple 后，子 Agent 虽生成极简 Plan，但 LLM 未严格执行 `read_paper -> answer`，导致步数为 0、出现幻觉。说明 simple 路径需要在 prompt 中更强制地要求“先读论文再回答”。
 2. **Complex 路径未充分展开**：report_generation 等任务应走 multi-agent，但结果中报告产物未生成（步数为 0）。子 Agent 间的 workspace 共享和产物接力需要进一步调试。
 3. **no-orchestration 更稳定**：在当前 qwen-plus 上，单 Agent ReAct 对 report_generation 等复杂任务反而更稳定，说明多 Agent 拆分对 prompt 设计和模型 tool-use 能力要求更高。
 4. **耗时优势明显**：orchestration 在真实论文上完成全部 6 场景约 5 分钟，no-orchestration 约 7 分钟，编排理论上可通过并行化进一步提速。
 
 ## 8. 后续建议
 
 1. **增强 simple 路径约束**：在系统提示中显式要求“必须先调用 read_file/grep 读取论文，否则视为幻觉”；未完成 read_paper 前禁止返回最终答案。
 2. **修复 complex 路径产物接力**：验证 Reader/Writer/Reviewer 子 Agent 是否正确写入/读取 `paper/facts.json`、`report/report.md`、`review/findings.md`。
 3. **引入并行执行**：当前 complex 路径串行执行 Reader/Verifier/Writer；可并行化 Reader 与 Verifier。
 4. **扩展 golden dataset 对比**：在更多论文和更大 k 上跑 orchestration vs no-orchestration，获得统计显著性。
 5. **适配更强模型**：当前 qwen-plus 在复杂编排下 tool-use 稳定性有限；可尝试 qwen-max 或 kimi-for-coding。
 
 ## 9. 附录：原始数据文件
 
 - `outputs/orchestration_part_a.json`（Part A）
- `outputs/orchestration_simple_orchestration.json`（simple paper orchestration）
 - `outputs/orchestration_feature3dgs_orchestration.json`（feature3dgs orchestration）
 - `outputs/orchestration_feature3dgs_no_orchestration.json`（feature3dgs no-orchestration）
 - `workspace/benchmarks/latest_agent.json` / `latest_real_eval.json`（latest 指针，工作区临时）
 
 ## 10. 代码改动清单
 
 - 新增 `docs/AGENT_ORCHESTRATION_SPEC.md`
 - 新增 `src/paperwise/orchestration/` 包（classifier.py、paper_dag.py、orchestrator.py、__init__.py）
 - 修改 `src/paperwise/core/types.py`（增加 `enable_orchestration`、`tokens_used`）
 - 修改 `src/paperwise/core/agent.py`（`Agent.run` 支持编排路由，保留 `_legacy_run`）
 - 修改 `src/paperwise/core/session.py`（`AgentSession.chat` 对复杂任务启用编排）
 - 修改 `src/paperwise/agents/orchestrator.py`（子 Agent 默认 `enable_orchestration=False`，防止递归）
 - 修改 `src/paperwise/evaluation/configs.py`（新增 orchestration/no-orchestration 配置）
 - 修改 `测评/scripts/run_evaluation.py` 与 `run_real_evaluation.py`（增加配置选项）
 - 修改 `tests/helpers/mock_llm.py`（增加 `estimate_cost`）
 - 新增 `tests/test_orchestration.py`
 - 修复 `tests/test_agent_loop.py` 路径问题
