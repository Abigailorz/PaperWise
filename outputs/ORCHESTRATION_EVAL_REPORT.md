 # PaperWise Agent 编排改进测评报告
 
 > 生成时间：2026-08-27
 > 目标：验证“复杂度感知 + DAG 多 Agent 编排”v2 改进的实际效果
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
 | **实现完成度** | 已完成 `TaskClassifier`、`PaperDAGPlanner`、`SmartOrchestrator` 并清理旧 `AgentOrchestrator`；Mock 测试与 Part A 回归全部通过。 |
 | **Tier 1 安全层** | 25/25 全部通过，安全层保持 100% 拦截率。 |
 | **真实论文 feature3dgs（v2）** | orchestration 配置 6 场景通过 3 个；no-orchestration 配置通过 3 个。 |
 | **耗时** | 真实论文 orchestration 总耗时约 4 分钟（avg 38.6s/场景），no-orchestration 总耗时约 6 分钟。编排更快。 |
 | **关键改进** | simple 路径强制“先读 paper 再回答”；complex 路径子 Agent 在共享 `paper_dir` 中运行，产物接力正常；分类器不再把数值/方法事实查询误判为 report 任务。 |
 | **关键问题** | method_verification 仍存在关键幻觉；report_generation 生成报告但分数 72%，minor 幻觉；critical_analysis 被降级为 simple 后仍 major 幻觉。 |
 
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
 
 ## 6. Tier 3+：真实论文 feature3dgs 对比（v2）

 ### orchestration 配置

 | 场景 | 结果 | 分数 | 步数 | 合法率 | 幻觉 |
 |------|------|------|------|--------|------|
 | basic_info_extraction | PASS | 100% | 2 | 100% | none |
 | numerical_fact_verification | PASS | 100% | 4 | 100% | none |
 | method_verification | FAIL | 70% | 1 | 100% | critical |
 | critical_analysis | FAIL | 63% | 4 | 100% | major |
 | hallucination_veto | PASS | 100% | 2 | 100% | none |
 | report_generation | FAIL | 72% | 0 | 100% | minor |

 **聚合**：通过 3/6，平均步数 2.2，平均耗时 38.6s，平均 token 998。

 ### no-orchestration 配置（同期基线）

 | 场景 | 结果 | 分数 | 步数 | 合法率 | 幻觉 |
 |------|------|------|------|--------|------|
 | basic_info_extraction | PASS | 100% | 2 | 100% | none |
 | numerical_fact_verification | FAIL | 70% | 3 | 100% | major |
 | method_verification | FAIL | 70% | 6 | 100% | critical |
 | critical_analysis | PASS | 87% | 5 | 100% | minor |
 | hallucination_veto | PASS | 100% | 5 | 100% | minor |
 | report_generation | FAIL | 85% | 20 | 100% | minor |

 **聚合**：通过 3/6，平均步数 6.8。
 
 ## 7. 结果分析（v2）

 1. **Simple 路径约束生效**：v2 通过 `_looks_complete` 和系统提示强制“read_paper 完成前不得返回最终答案”，basic / numerical / hallucination_veto 均成功读取 paper 并给出正确答案； hallucination_veto 0 步问题消失。
 2. **Complex 路径产物接力修复**：report_generation 成功生成 `report/report.md`（5030+ 字节）和 sections，分数从 38% 提升到 72%，但仍存在 minor 幻觉，说明 writer 提示和事实校验还有提升空间。
 3. **分类器仍需调优**：method_verification 和 critical_analysis 被 v2 分类器识别为 simple，虽避免超时，但模型在方法细节和“额外局限”推理上产生 major/critical 幻觉。说明这类半综合任务若完全走 simple，模型容易过度推断；若走 complex，又可能因超时失败。
 4. **与 no-orchestration 基本持平**：v2 orchestration 与 no-orchestration 均为 3/6。Orchestration 更快且成功处理数值验证；no-orchestration 在 critical/report 上表现略好。说明当前 qwen-plus 的 tool-use 稳定性下，多 Agent 拆分的收益尚未完全释放，需在 writer prompt、review 效率和 stronger model 上继续优化。
 
 ## 8. 后续建议

 1. **提升 method/critical 类半综合任务质量**：给 simple 路径增加“必须引用具体行号 / 不确定就省略”的 prompt 约束；或为此类任务引入轻量级 verifier 子 Agent，但不强制生成完整报告。
 2. **优化 report writer**：在 writer system prompt 中显式要求“只写 paper 中能找到的事实，不确定的内容标 unverified”；缩短 reader 提取范围，避免无关事实稀释报告。
 3. **引入并行执行**：当前 complex 路径串行执行 Reader/Verifier/Writer；可并行化 Reader 与 Verifier，进一步降低延迟。
 4. **扩展 golden dataset 对比**：在更多论文和更大 k 上跑 orchestration vs no-orchestration，获得统计显著性。
 5. **适配更强模型**：当前 qwen-plus 在复杂编排下 tool-use 稳定性有限；可尝试 qwen-max 或 kimi-for-coding。
 
 ## 9. 代码改动清单（v2）

 - 修改 `src/paperwise/core/agent_loop.py`：`_looks_complete` 强制 read_paper 完成。
 - 修改 `src/paperwise/core/agent.py`：`_init_messages` 增加读 paper 优先指令。
 - 修改 `src/paperwise/orchestration/classifier.py`：细化 simple/complex 规则，避免事实查询误判。
 - 修改 `src/paperwise/orchestration/paper_dag.py`：减少无必要的 review 节点。
 - 重写 `src/paperwise/orchestration/orchestrator.py`：子 Agent 在共享 `paper_dir` 运行，产物接力 + 显式检查。
 - 新增 `src/paperwise/orchestration/specs.py`：集中 SubAgentSpec、PaperAnalysisPipeline、parse_findings。
 - 删除 `src/paperwise/agents/orchestrator.py`：移除冗余旧编排器。
 - 修改 `tests/test_agents/test_orchestrator.py`：指向新的 `orchestration.specs`。
 - 修改 `测评/scripts/run_real_evaluation.py`：修复 run_evaluation 导入路径。
 - 更新 `docs/AGENT_ORCHESTRATION_SPEC.md`：补充 v2 实现修正。

 ## 10. 附录：原始数据文件
 - `outputs/orchestration_part_a.json`（Part A，v2 重新通过）
 - `workspace/benchmarks/real_eval_1787761093.json`（orchestration v2）
 - `workspace/benchmarks/real_eval_1787761533.json`（no-orchestration v2 基线）
 
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
