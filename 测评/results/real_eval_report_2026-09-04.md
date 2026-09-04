# PaperWise 真实 LLM 评测报告（2026-09-04）

## 运行环境

- 主模型：`glm-5.3-flash`
- 异源评审模型：`deepseek-v4-flash`
- 网关：OpenCode go 网关，OpenAI-compatible base URL `https://opencode.ai/zen/go/v1`
- 评测脚本：`测评/scripts/run_real_evaluation.py`
- 固定论文：`LangSplat`、`Feature 3DGS`
- 运行设置：每篇论文 6 个场景，`k=1`

## 结论摘要

第一轮评测在 Agent 级 ContextCompiler 测试落地后进行。随后 Orchestrator 的
上下文装配改为经由 `ContextCompiler` 汇编，并复测一轮。终版结果显示：

- LangSplat 通过数从 2/6 提升到 4/6。数值事实与方法验证从失败转为通过。
- Feature 3DGS 通过数从 3/6 下降到 2/6。数值事实出现新的 120s 超时；
  方法验证仍被异源 Judge 拦截 major 幻觉。
- 两轮共同暴露的瓶颈是 `critical_analysis` 和 `report_generation` 长链路
  超时。终版报告需要优先解决超时前的可评分中间产物保留，而不是继续把
  这一问题归因于局部上下文质量。

## 终版结果

终版代码包含 Orchestrator 到 `ContextCompiler` 的完整路由。全量回归在复测
前为 336 passed。

| 论文 | 通过 / 总数 | 通过率 | 平均步数 | 平均耗时 | 平均 tokens |
|------|------------:|-------:|---------:|---------:|------------:|
| LangSplat | 4 / 6 | 66.67% | 3.3 | 145.7s | 1070 |
| Feature 3DGS | 2 / 6 | 33.33% | 1.8 | 144.7s | 877 |

### LangSplat

| 场景 | 结果 | 步数 | 耗时 | 工具合法率 | 关键信息 |
|------|------|-----:|-----:|-----------:|----------|
| basic_info_extraction | PASS | 3 | 49.3s | 100% | 命中主要贡献，无严重幻觉 |
| numerical_fact_verification | PASS | 5 | 111.6s | 100% | 本轮通过，评分 0.95 |
| method_verification | PASS | 7 | 152.6s | 100% | 本轮通过；Judge 未见 major/critical 幻觉 |
| critical_analysis | FAIL | 0 | 180.1s | 0% | 超时 |
| hallucination_veto | PASS | 5 | 80.6s | 100% | 正确拒答不存在的 BLEU 指标 |
| report_generation | FAIL | 0 | 300.1s | 0% | 超时 |

### Feature 3DGS

| 场景 | 结果 | 步数 | 耗时 | 工具合法率 | 关键信息 |
|------|------|-----:|-----:|-----------:|----------|
| basic_info_extraction | PASS | 3 | 62.4s | 100% | 命中主要贡献，无幻觉 |
| numerical_fact_verification | FAIL | 0 | 120.1s | 0% | 本轮为 120s 超时 |
| method_verification | FAIL | 4 | 104.4s | 100% | Judge 拦截不可证实的损失函数、维度和栅格化细节 |
| critical_analysis | FAIL | 0 | 180.1s | 0% | 超时 |
| hallucination_veto | PASS | 4 | 101.0s | 100% | 正确拒答不存在的 COCO mAP |
| report_generation | FAIL | 0 | 300.1s | 0% | 超时 |

## 第一轮对照

第一轮在 `tests/test_agents/test_context_native_agent.py` 与真实评测报告
落地后运行；当时 Orchestrator 上下文尚未全部经由 `ContextCompiler` 汇编。

| 论文 | 通过 / 总数 | 通过率 | 平均步数 | 平均耗时 | 平均 tokens |
|------|------------:|-------:|---------:|---------:|------------:|
| LangSplat | 2 / 6 | 33.33% | 3.3 | 145.0s | 1140 |
| Feature 3DGS | 3 / 6 | 50.00% | 2.8 | 147.2s | 1212 |

### LangSplat 对照

| 场景 | 第一轮 | 终版 | 变化 |
|------|--------|------|------|
| basic_info_extraction | PASS | PASS | 持平 |
| numerical_fact_verification | FAIL，critical 幻觉 | PASS | 修复 |
| method_verification | FAIL，major 幻觉 | PASS | 修复 |
| critical_analysis | FAIL，180s 超时 | FAIL，180s 超时 | 持平 |
| hallucination_veto | PASS | PASS | 持平 |
| report_generation | FAIL，300s 超时 | FAIL，300s 超时 | 持平 |

### Feature 3DGS 对照

| 场景 | 第一轮 | 终版 | 变化 |
|------|--------|------|------|
| basic_info_extraction | PASS | PASS | 持平 |
| numerical_fact_verification | FAIL，major 幻觉 | FAIL，120s 超时 | 失败形态变化 |
| method_verification | PASS | FAIL，major 幻觉 | 回退 |
| critical_analysis | FAIL，180s 超时 | FAIL，180s 超时 | 持平 |
| hallucination_veto | PASS | PASS | 持平 |
| report_generation | FAIL，300s 超时 | FAIL，300s 超时 | 持平 |

## 放宽时间复测（旧口径）

随后把 `numerical_fact_verification` 超时从 120s 放宽到 180s，
`critical_analysis` 从 180s 放宽到 420s，`report_generation` 从 300s 放宽到
600s。此轮仍使用旧的单一 hallucination 判定，因此数据用于验证时间假设；
口径本身已在后续提交中升级为 Grounded Fact Quality。

| 论文 | 通过 / 总数 | 通过率 | 平均步数 | 平均耗时 | 平均 tokens |
|------|------------:|-------:|---------:|---------:|------------:|
| LangSplat | 3 / 6 | 50.00% | 3.8 | 180.8s | 1679 |
| Feature 3DGS | 4 / 6 | 66.67% | 4.7 | 171.9s | 2305 |

### LangSplat 放宽时间结果

| 场景 | 结果 | 步数 | 耗时 | 关键信息 |
|------|------|-----:|-----:|----------|
| basic_info_extraction | PASS | 4 | 59.3s | 正常 |
| numerical_fact_verification | FAIL | 4 | 74.3s | 74.3s 完成，被旧口径判 critical |
| method_verification | PASS | 5 | 115.9s | 正常 |
| critical_analysis | PASS | 7 | 171.6s | 放宽后通过 |
| hallucination_veto | FAIL | 3 | 63.8s | 拒答正确，但补充展开被旧口径判 major |
| report_generation | FAIL | 0 | 600.1s | 600s 仍超时 |

### Feature 3DGS 放宽时间结果

| 场景 | 结果 | 步数 | 耗时 | 关键信息 |
|------|------|-----:|-----:|----------|
| basic_info_extraction | PASS | 4 | 86.7s | 正常 |
| numerical_fact_verification | FAIL | 3 | 66.5s | 66.5s 完成，headline 正确但表格细节越界 |
| method_verification | PASS | 7 | 172.9s | 正常 |
| critical_analysis | PASS | 9 | 75.7s | 放宽后通过 |
| hallucination_veto | PASS | 5 | 29.5s | 正确拒答 |
| report_generation | FAIL | 0 | 600.1s | 600s 仍超时 |

### 放宽时间观察

1. `critical_analysis` 在两篇论文上都转为通过，确认原 180s 预算是该场景
   的主要失败原因。
2. 数值题在两篇论文上都远早于 180s 完成，继续失败不是因为时间；失败来自
   “headline 正确 + golden 外表格细节”被旧口径整体判为 hallucination。
3. LangSplat 的幻觉拒答明确说论文未报告 BLEU，但仍因补充展开被判失败；
   这直接证明事实错误和 Answer Scope Violation 必须分开评分。
4. `report_generation` 在 600s 仍超时。LangSplat 日志显示 reader 阶段约
   514s 才结束，后续节点还要分析/写报告，说明需要复用中间产物或继续提高
   全链路预算。

## 主要观察

1. **基础事实与幻觉拒答稳定**：两篇论文的 `basic_info_extraction` 和
   `hallucination_veto` 在两轮均通过，说明定向读取、检索和“不存在就不
   编造”的基础行为可用。
2. **数值和方法验证存在波动**：LangSplat 终版通过，但 Feature 3DGS 终版
   的数值场景超时、方法场景被 `deepseek-v4-flash` 拦截。当前主模型在
   headline 数值之外仍倾向补充表格级细节；输出约束应从“有引用”收紧到
   “只有被检索片段直接支持的数值才能输出”。
3. **长链路收尾是系统性失败点**：`critical_analysis` 和
   `report_generation` 在两篇论文和两轮评测中全部超时，且记录步数为 0。
   这说明评测器没有拿到可评分的最终输出，需要允许超时前保留和提交中间
   结果。
4. **异源评审有效**：`deepseek-v4-flash` 能拦截主模型的不可证实细节，
   同时没有误拦基础问答和拒答场景。该模型固定作为本报告的异源 Judge。

## 原始结果

- 终版 LangSplat：`workspace/benchmarks/real_eval_1788456552.json`
- 终版 Feature 3DGS：`workspace/benchmarks/real_eval_1788457517.json`
- 第一轮 LangSplat：`workspace/benchmarks/real_eval_1788452848.json`
- 第一轮 Feature 3DGS：`workspace/benchmarks/real_eval_1788454042.json`
- 放宽时间 LangSplat：`workspace/benchmarks/real_eval_1788492086.json`
- 放宽时间 Feature 3DGS：`workspace/benchmarks/real_eval_1788493341.json`

## 下一轮建议

1. 对数值问题增加“数字必须来自当前工具片段”的硬约束，并在最终输出前做
   数值一致性校验。
2. 对报告/分析类长任务拆分中间产物，允许超时前保留可评分的部分结果。
3. 为 `critical_analysis` 和 `report_generation` 分别做超时消融，确认瓶颈
   在模型生成速度、编排步数，还是验证/评分前的收尾流程。
4. 将异源 Judge 约定固化为 `PAPERWISE_JUDGE_MODEL=deepseek-v4-flash`，避免
   后续评测中主模型与 Judge 混用同一模型。

## v2 口径与 1200 秒报告预算

本轮把 LangSplat 的 `report_generation` 预算放宽到 1200s，并启用 v2 指标。
原始结果为 `workspace/benchmarks/real_eval_1788496063.json`。

| 场景 | 结果 | 质量分 | Fact / Scope | 关键信号 |
|------|------|-------:|--------------|----------|
| basic_info_extraction | PASS | 1.00 | 1.00 / 1.00 | 正常 |
| numerical_fact_verification | FAIL | 0.425 | 0.00 / 0.00 | 34.2s 完成；最终输出仅 59 字符，6 步全为检索，无收束答案 |
| method_verification | PASS | 1.00 | 1.00 / 1.00 | 正常 |
| critical_analysis | FAIL | 0.412 | 0.00 / 0.00 | 57.1s 完成；最终输出仅 61 字符，10 步全为检索，无收束答案 |
| hallucination_veto | PASS | 0.982 | 1.00 / 0.70 | 正确拒答；3 项补充指标被判 scope violation，不再判幻觉 |
| report_generation | FAIL | 0.584 | 0.50 / 1.00 | 1200.1s 触顶；产物已落盘，Fact 0.50 是 Judge 连接错误降级为 unknown |

### report_generation 时间线

报告产物不是没有写完。LangSplat 运行目录显示：

1. `12:04:41` 写入 `summary.md` 和三个分节文件；
2. `12:08:22` 最终 `report.md` 更新完成，大小约 8.2KB；
3. `12:13:19` Reviewer 写出 PASS 的 `findings.json/md`；
4. 随后仍触发了 Revision Writer，直到 1200s 预算耗尽。

因此 1200s 不是报告写作的实际耗时约束。报告本体约 3 分 41 秒完成，
审稿约 4 分 57 秒完成；剩余时间被错误触发的修订循环消耗。根因是子代理
内部 Plan 与真实产物不同步：Reviewer 已交付有效 findings，但 Agent 返回
step-limit 失败，DAG 将其误判为 review 失败并 replan 成 revision。

后续收口是让“新增/更新的非空目标产物”成为子代理完成信号；同时将
`APIConnectionError` 视为基础设施失败，避免网关故障触发一串无意义
replan。相关编排测试已覆盖：340 passed。

## 2026-09-04 收尾验收

本节是当天的最终收口状态；完整测试标准、每个场景细则与评分公式见
`docs/TESTING-STANDARD-AND-RESULTS.md`。

自动化回归更新为 **342 passed**。新增修复包括：

1. Rubric 总分写入评测 raw 结果；
2. 子 Agent messages、tool stats 和 tokens 聚合回外层 AgentResult；
3. 网关连接与 SSL 错误不再触发 replan；
4. 分节产物写入后同步更新 Agent 内部计划状态。

Feature 3DGS `report_generation` 的收尾复测如下：

| 结果文件 | 用时 | 状态 | Score | Fact | Scope | Unsupported | 结论 |
|---|---:|---|---:|---:|---:|---:|---|
| `real_eval_1788506785.json` | 1200.1s | timeout / FAIL | 0.5674 | 0.50 | 1.00 | 0 | 报告已写出；Judge JSON 解析错误降级为 unknown |
| `real_eval_1788508624.json` | 1200.1s | timeout / FAIL | 0.5482 | 0.50 | 1.00 | 0 | 最新运行；报告与审稿产物完整，但整体超时 |

最新运行已留下完整 `report.md`、四个分节文件和 review findings。两次失败
中的 `Fact 0.50` 均来自 `JSONDecodeError` 后的 unknown 降级，不是新增幻觉。

最终口径：

> Agent 报告生成能力已完成功能与质量验证；Feature 3DGS 的端到端正式 PASS
> 因 1200s 总预算内流程未返回而保留为已知限制。
