# PaperWise 真实 LLM 评测报告（2026-09-04）

## 运行环境

- 主模型：`glm-5.3-flash`
- 异源评审模型：`deepseek-v4-flash`
- 网关：OpenCode go 网关，OpenAI-compatible base URL `https://opencode.ai/zen/go/v1`
- 评测脚本：`测评/scripts/run_real_evaluation.py`
- 固定论文：`LangSplat`、`Feature 3DGS`
- 运行设置：每篇论文 6 个场景，`k=1`

## 结果概览

| 论文 | 通过 / 总数 | 通过率 | 平均步数 | 平均耗时 | 平均 tokens |
|------|------------:|-------:|---------:|---------:|------------:|
| LangSplat | 2 / 6 | 33.33% | 3.3 | 145.0s | 1140 |
| Feature 3DGS | 3 / 6 | 50.00% | 2.8 | 147.2s | 1212 |

## 分场景结果

### LangSplat

| 场景 | 结果 | 步数 | 耗时 | 工具合法率 | 关键信息 |
|------|------|-----:|-----:|-----------:|----------|
| basic_info_extraction | PASS | 3 | 55.1s | 100% | 命中主要贡献，无严重幻觉 |
| numerical_fact_verification | FAIL | 5 | 99.7s | 100% | 幻觉评审判定 critical |
| method_verification | FAIL | 7 | 157.2s | 100% | 幻觉评审判定 major |
| critical_analysis | FAIL | 0 | 180.1s | 0% | 超时 |
| hallucination_veto | PASS | 5 | 77.9s | 100% | 正确拒答不存在的 BLEU 指标 |
| report_generation | FAIL | 0 | 300.1s | 0% | 超时 |

### Feature 3DGS

| 场景 | 结果 | 步数 | 耗时 | 工具合法率 | 关键信息 |
|------|------|-----:|-----:|-----------:|----------|
| basic_info_extraction | PASS | 3 | 60.6s | 100% | 命中主要贡献，无幻觉 |
| numerical_fact_verification | FAIL | 5 | 119.1s | 100% | 幻觉评审判定 major |
| method_verification | PASS | 5 | 144.8s | 100% | 蒸馏机制与 prompting 能力描述合格 |
| critical_analysis | FAIL | 0 | 180.1s | 0% | 超时 |
| hallucination_veto | PASS | 4 | 78.7s | 100% | 正确拒答不存在的 COCO mAP |
| report_generation | FAIL | 0 | 300.1s | 0% | 超时 |

## 主要观察

1. **基础事实与幻觉拒答稳定**：两篇论文的 `basic_info_extraction` 和
   `hallucination_veto` 都通过，说明 Agent 的定向读取、检索和“不存在就
   不编造”的基础行为可用。
2. **数值细节是主要失败点**：主模型在给出 headline 数值之外，还补充了
   表格级细节。这些细节在评审判例中被判定为不可证实，说明输出约束应从
   “有引用”进一步收紧到“只有被检索片段直接支持的数值才能输出”。
3. **长链路收尾是第二个失败点**：`critical_analysis` 和
   `report_generation` 连续在 180s / 300s 超时，且记录中的步数为 0，
   表示任务没有在超时前形成可评分的最终输出。这一现象在两篇论文上重复，
   不是单次偶发。
4. **异源评审有效**：`deepseek-v4-flash` 能拦截主模型的不可证实细节，
   同时没有误拦基础问答和拒答场景。

## 原始结果

- LangSplat：`workspace/benchmarks/real_eval_1788452848.json`
- Feature 3DGS：`workspace/benchmarks/real_eval_1788454042.json`

## 下一轮建议

1. 对数值问题增加“数字必须来自当前工具片段”的硬约束，并在最终输出前做
   数值一致性校验。
2. 对报告/分析类长任务拆分中间产物，允许超时前保留可评分的部分结果。
3. 为 `critical_analysis` 和 `report_generation` 分别做超时消融，确认瓶颈
   在模型生成速度、编排步数，还是验证/评分前的收尾流程。
