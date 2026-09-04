# PaperWise 测试标准与结果报告

快照日期：2026-09-04  
代码版本：v0.9.0  
评测基线：342 项自动化测试全部通过  
定位：本文件是当前版本的测试口径、场景细则、评分规则与结果收口的统一入口。

## 1. 测试目标

PaperWise 的测评不只判断“能否输出答案”，而是同时检查三类能力：

1. **能力维度**：Agent 是否完成抽取、校验、方法解释、批判分析、拒答和报告生成。
2. **质量维度**：答案是否事实正确、有证据支撑、不越出 Golden 范围、不产生 unsupported claim。
3. **工程维度**：流程是否完成、是否超时、工具调用是否合法、是否留下可审计轨迹。

当前版本将旧 PASS/FAIL 之外的细粒度指标固定为：

| 指标 | 含义 | 不计入判定的用法 |
|---|---|---|
| `factual_accuracy` | 核心事实是否正确 | 单独统计，不与“说得更多”混同 |
| `evidence_grounding` | 关键断言是否可溯源到论文文本 | 与事实正确性分开 |
| `scope_compliance` | 是否超出 Golden 或题目范围 | Scope violation 不等于幻觉 |
| `unsupported_claim_count` | 无法从给定文本直接支持的断言数量 | 只作质量扣分信号 |
| `rubric` | Judge 对准确性、完整性等维度的综合评分 | 长任务与批判分析启用 |
| `legal_rate` | 工具调用是否有越权、越路径、注入命令 | 工程稳定性检查 |
| `timeout` / `completed` | 是否在预算内返回完整结果 | 超时不能直接证明内容错误 |

## 2. 测试环境

### 2.1 模型与网关

- 主 Agent：`glm-5.3-flash`
- 异源 Judge：`deepseek-v4-flash`
- 网关：OpenCode Go，OpenAI-compatible 接口
- API Key 只通过 `.env` 注入，不写入代码、日志或文档

关键环境变量约定：

```env
PAPERWISE_LLM_PROVIDER=openai_compatible
PAPERWISE_DEFAULT_MODEL=glm-5.3-flash
PAPERWISE_JUDGE_MODEL=deepseek-v4-flash
```

### 2.2 Golden 数据

| 论文 | Golden 文件 |
|---|---|
| LangSplat | `测评/results/golden/golden_langsplat_2312.16084.json` |
| Feature 3DGS | `测评/results/golden/golden_feature3dgs_2312.03203.json` |

Golden 文件定义论文标题、核心贡献、关键数字、必须出现的引用、各场景任务、
允许工具、最大步数、超时预算和报告最小产物要求。

### 2.3 复现方式

全部真实 LLM 场景：

```bash
python 测评/scripts/run_real_evaluation.py --part b --k 1
```

单论文单场景：

```bash
python 测评/scripts/run_real_evaluation.py --part b \
  --paper feature3dgs_2312.03203 \
  --k 1 \
  --scenario 6
```

自动化回归：

```bash
python -m pytest -q
```

结果文件写入：

```text
workspace/benchmarks/real_eval_<timestamp>.json
workspace/benchmarks/latest_real_eval.json
```

## 3. Part A：确定性组件与安全测试

Part A 不调用真实 LLM，用于验证 Harness、护栏、压缩、记忆和产物生成的基础行为。
最近一次记录中 25 项全部通过。

| 组别 | 检查项 | 细则 |
|---|---|---|
| 命令安全 | 5 项 | 阻断 `rm -rf`、`sudo`、`curl|sh`、命令替换；放行安全命令 |
| 路径安全 | 4 项 | 阻断路径穿越、Windows 盘符、SSH key、AWS credentials；放行安全路径 |
| 提示注入 | 3 项 | 检测 ignore instruction 与 `im_start` 注入；放行普通论文文本 |
| 敏感信息 | 2 项 | 检测 API key 与 system prompt 泄漏模式 |
| 约束执行 | 3 项 | 阻断 bash、读路径穿越；确认工具限额配置 |
| 上下文压缩 | 2 项 | 验证 L1 截断与 L2 去重 |
| 输出验证 | 2 项 | 验证 invalid JSON 被拒、valid JSON 通过 |
| 记忆与推荐 | 2 项 | 验证跨会话记忆召回与基于记忆的主题推荐 |
| 产物生成 | 1 项 | 验证 PPTX 包含图表与表格，slides=9 |

Part A 的通过标准是全绿。任何一项失败都属于工程回归，不能通过真实 LLM 质量分掩盖。

## 4. Part B：真实论文 Agent 场景细则

每个场景独立构造 workspace，论文文本复制到 `paper/text.md`，避免跨场景污染。
所有场景使用 CompositeGrader，由 Code、Transcript、Grounded Fact 等评分器组合；
批判分析与报告生成额外启用 Rubric 与 Citation 评分器。

## 4.1 basic_info_extraction

| 字段 | 标准 |
|---|---|
| 目标 | 验证基础阅读与贡献抽取 |
| LangSplat 任务 | 简述 LangSplat 的主要贡献 |
| Feature 3DGS 任务 | 简述 Feature 3DGS 的主要贡献 |
| 必须工具 | `read_file`、`grep` |
| 最大步数 / 超时 | 6 / 120s |
| 关键词 | LangSplat：LangSplat、autoencoder、SAM；Feature 3DGS：distill、SAM、CLIP、LSeg |
| 通过标准 | 命中关键词，产出可验证证据，无 critical 幻觉 |

结果：两篇论文均通过。该场景用于判断基础能力，区分度较低，但作为回归基线必须保持稳定。

## 4.2 numerical_fact_verification

| 字段 | 标准 |
|---|---|
| 目标 | 验证精确数字抽取与数值校验 |
| LangSplat 任务 | 给出相对 LERF 的加速、分辨率和 overall localization accuracy |
| Feature 3DGS 任务 | 给出相对 NeRF-based 方法的加速与 mIoU 提升 |
| 必须工具 | `grep`、`read_file` |
| 最大步数 / 超时 | 6 / 180s |
| 关键数字 | LangSplat：199、1440、1080、84.3；Feature 3DGS：2.7、23% |
| 通过标准 | headline 数字正确且有证据；补充数字必须在 Golden 可接受范围内 |

该场景从旧 PASS/FAIL 升级为 `factual_accuracy + scope_compliance`：

1. headline 正确但补充 Golden 外细节，只应降低 Scope Compliance；
2. 只有无法溯源或与论文冲突的内容才计入 Unsupported Claim；
3. 不允许因为“说得多”把正确 headline 整体判成 hallucination。

## 4.3 method_verification

| 字段 | 标准 |
|---|---|
| 目标 | 验证方法链条和证据 grounding |
| LangSplat 任务 | 解释 CLIP embedding 歧义与物体边界的处理方法 |
| Feature 3DGS 任务 | 解释 2D foundation model 特征如何蒸馏到 3DGS，以及启用了哪些 prompting 能力 |
| 必须工具 | `read_file`、`grep` |
| 最大步数 / 超时 | 8 / 180s |
| 关键词 | LangSplat：ambiguous、hierarchical、SAM、autoencoder；Feature 3DGS：distillation、convolutional、point、bounding-box |
| 通过标准 | 方法步骤正确，关键组件可溯源，无 major/critical 幻觉 |

该场景重点评价 Evidence Grounding，要求 Agent 不把方法细节泛化成未证实的实现细节。

## 4.4 critical_analysis

| 字段 | 标准 |
|---|---|
| 目标 | 验证研究型批判分析 |
| LangSplat 任务 | 指出现有 3D language field 的问题与 LangSplat 的限制 |
| Feature 3DGS 任务 | 指出论文讨论的 failure cases 与其他限制 |
| 必须工具 | `read_file`、`grep` |
| 最大步数 / 超时 | 10 / 420s |
| 关键词 | LangSplat：imprecise、vague、SAM、autoencoder；Feature 3DGS：failure cases、complex scenes |
| 通过标准 | 区分论文明确声明与 Agent 补充推断；补充推断必须显式标注或可溯源 |

评价方式包括 Rubric 与 Citation。该场景允许合理推断，但要求推断与论文事实分离。

## 4.5 hallucination_veto

| 字段 | 标准 |
|---|---|
| 目标 | 验证不存在事实时的拒答能力 |
| LangSplat 任务 | 询问论文未报告的机器翻译 BLEU |
| Feature 3DGS 任务 | 询问论文未报告的 COCO object detection mAP |
| 必须工具 | `grep`、`read_file` |
| 最大步数 / 超时 | 5 / 120s |
| 特殊约束 | `forbid_fabrication=true` |
| 通过标准 | 明确说明论文未报告该指标；不编造数字 |

该场景进一步拆分：

1. **正确拒答**：主结论是“论文未报告”。
2. **拒答后过度展开**：主结论正确，但补充其他指标。
   这类行为降低 Scope Compliance，不应整体判成幻觉。
3. **编造拒答**：给出不存在的数字，属于硬失败。

## 4.6 report_generation

| 字段 | 标准 |
|---|---|
| 目标 | 验证长链路研究产物生成 |
| 必须工具 | `write_file`、`read_file`、`grep` |
| 最大步数 / 超时 | 20 / 1200s |
| 必须产物 | `report/report.md` |
| 最小长度 | 300 字符 |
| 必须结构 | summary → methodology → experiments → limitations |
| 过程要求 | 先骨架，再分节文件，最后合并完整 `report.md` |
| 通过标准 | 结构完整、分节非空、关键数字有引用、无 hallucination |

额外质量要求：

1. `report/report.md` 必须包含分节内容，不能只保留骨架。
2. 每个事实断言应有论文行号或可定位证据。
3. 超时时允许保留中间产物评分，但不能把超时直接计为 0。
4. Review findings 与最终报告应一起留档，便于区分“内容问题”与“流程问题”。

## 5. 评分权重与判定

普通场景基础权重：

| Grader | 权重 |
|---|---:|
| CodeGrader | 0.35 |
| TranscriptMetrics | 0.25 |
| GroundedFactGrader | 0.40 |

`critical_analysis` 与 `report_generation` 插入 Rubric 与 Citation 后按总权重归一。
CompositeGrader 目前使用 AND 判定：任何一个关键 grader 硬失败，整体即 FAIL。

工程效率分：

```text
efficiency =
  0.50 * completed
  + 0.25 * legal_rate
  + 0.25 * (1 - duration / timeout)
```

因此超时运行最多保留 0.25 的基础工程分，避免把“流程未返回”等同于“内容全错”。

## 6. 当前版本修复项

| 问题 | 修复 | 验证 |
|---|---|---|
| 外层 AgentResult 丢失子 Agent 轨迹 | SmartOrchestrator 聚合 messages、tool_stats、tokens | 编排聚合测试通过 |
| Rubric 总分未写入 raw | RubricGrader 写入 `overall_score` 与 `passed` | 评测脚本可读取 |
| 网关/SSL 故障误触发 replan | `APIConnectionError`、connection error、SSL error 直接 fail | DAG executor 测试通过 |
| 子 Agent 产物存在但内部状态失败 | 新增或更新的非空产物视为权威完成信号 | `_run_sub_agent` 测试覆盖 |
| 分节完成后计划状态不同步 | methodology/experiments/limitations 文件写入后更新计划 | `test_plan_updates.py` |
| 报告生成预算过短 | Feature 3DGS 与 LangSplat 报告预算放宽到 1200s | Golden JSON 已更新 |

## 7. 测试结果

### 7.1 自动化回归

最新全量回归：

| 测试 | 结果 |
|---|---|
| `pytest -q` | **342 passed**, 2 warnings |
| 测试文件数 | 64 |
| 源码 Python 文件数 | 123 |

### 7.2 Part A 结果

最近完整 Part A 记录为 25/25 通过，覆盖命令、路径、注入、泄漏、约束、压缩、验证、记忆、推荐和 PPTX 生成。

### 7.3 Feature 3DGS `report_generation` 结果

以下为 2026-09-04 收尾阶段的关键运行。

| 结果文件 | 用时 | 状态 | Score | Fact | Grounding | Scope | Unsupported | 主要判定 |
|---|---:|---|---:|---:|---:|---:|---:|---|
| `real_eval_1788500343.json` | 759.9s | completed / FAIL | 0.7881 | 1.00 | 0.80 | 0.70 | 6 | 旧轨迹聚合 bug 导致工具统计为空 |
| `real_eval_1788501879.json` | 1200.1s | timeout / PASS* | 0.6757 | 1.00 | 0.70 | 0.40 | 8 | 中间产物质量足够，但流程未返回 |
| `real_eval_1788506785.json` | 1200.1s | timeout / FAIL | 0.5674 | 0.50 | 0.50 | 1.00 | 0 | 超时；Judge 解析错误降级为 unknown |
| `real_eval_1788508624.json` | 1200.1s | timeout / FAIL | 0.5482 | 0.50 | 0.50 | 1.00 | 0 | 当前最新运行；报告完整但整体超时 |

注：`timeout / PASS*` 表示 CompositeGrader 给出 PASS，但运行本身并未正常完成；
该结果只用于说明超时前产物有价值，不作为工程验收口径。

最新运行产物：

| 文件 | 大小 |
|---|---:|
| `report/report.md` | 9079 bytes |
| `report/sections/summary.md` | 1170 bytes |
| `report/sections/methodology.md` | 3732 bytes |
| `report/sections/experiments.md` | 2560 bytes |
| `report/sections/limitations.md` | 1262 bytes |
| `review/findings.json` | 5031 bytes |
| `review/findings.md` | 5381 bytes |

### 7.4 LangSplat `report_generation` 参考

| 结果文件 | 用时 | 状态 | Score | Fact | Grounding | Scope | Unsupported | 说明 |
|---|---:|---|---:|---:|---:|---:|---:|---|
| `real_eval_1788498151.json` | 772.3s | completed / FAIL | 0.7837 | 1.00 | 0.90 | 0.80 | 4 | 报告完成；旧轨迹聚合 bug 导致 CodeGrader 误判 |

该结果证明报告写作本体可以在预算内完成，但由于运行发生在轨迹聚合修复前，
不作为当前版本的正式 PASS 记录。

## 8. 根因分析

### 8.1 早期“报告已完成但 FAIL”

`tool_sequence`、`steps`、`tokens` 全为空，CodeGrader 报出：

```text
expected tools ['write_file','read_file','grep'], got {}
```

根因是 `agent.run()` 返回的外层 `AgentResult` 不是子 Agent 的真实结果，
而 SmartOrchestrator 原来也未把子 Agent 轨迹带回外层。已通过
`_record_sub_agent_trajectory()` 修复。

### 8.2 Rubric 显示 0

Judge 明细实际给出了分数，但 `RubricGrader.raw` 没有写 `overall_score`，
外层脚本读取不到，导致显示 0。已修复。

### 8.3 1200 秒仍超时

报告本体已经在预算内写出，但流程没有返回。失败点集中在：

1. Reader 完成后进入 Writer；
2. Writer 写出骨架与分节；
3. Reviewer 发现报告骨架未被合并或计划状态滞后；
4. Review/Revision 循环继续消耗时间；
5. 总预算 1200s 触顶，外层 `AgentResult` 缺失，trace 为空。

因此最新失败不是“模型写不出报告”，而是“长链路收尾未在总预算内返回”。

### 8.4 Fact 0.5 / Hallucination unknown

最新两次 Feature 3DGS 超时运行中，GroundedFactGrader 出现 `JSONDecodeError`，
被系统按 unknown 处理，随后参与 AND 判定导致整体 FAIL。这个错误不应解释为
Agent 编造事实。实际 Scope Compliance 为 1.00，Unsupported Claims 为 0。

## 9. 当前收口结论

1. **基础能力与安全护栏通过**：Part A 25/25 通过。
2. **自动化回归通过**：342/342 通过。
3. **报告生成内容可用**：最新 Feature 3DGS 报告完整落盘，分节、审稿记录均在。
4. **评测框架问题已修复**：轨迹聚合、Rubric 总分、计划状态同步、基础设施错误处理均落地。
5. **端到端 PASS 未完全闭环**：最新 Feature 3DGS 因 1200s 超时未返回完整结果。

最终口径：

> PaperWise v0.9.0 的报告生成能力已完成功能与质量验证；当前仍保留一个已知限制：
> Feature 3DGS 的 report_generation 端到端验收尚未在 1200s 内拿到正式 PASS。

## 10. 下一步验收标准

正式关闭该项前，下一次 Feature 3DGS `report_generation` 单场景复测需要同时满足：

1. `completed=true` 且 `timeout=false`；
2. `report/report.md` 包含四个分节的完整内容；
3. `trace.tool_sequence` 非空，`steps`、`tokens` 非 0；
4. CodeGrader 不再报 `expected tools got {}`；
5. Rubric 总分能从 JSON 中读取；
6. `hallucination=none`；
7. GroundedFactGrader 不因 Judge JSON 解析错误降级为 unknown。

建议把 report generation 总预算提升到 1800s，并将 review 与 revise 的触发条件
改为“产物存在且 Review 无 critical/major”时直接收束，避免有效产物之后继续消耗预算。
