# PaperWise 测评文件夹

本文件夹用于记录 PaperWise Agent 的测评设计与结果，目标是回答三个问题：

1. **测什么、怎么测、为什么这么测** → 见 [测试设计.md](./测试设计.md)
2. **当前 Agent 实际跑出来的结果** → 见 [测试结果.md](./测试结果.md)
3. **要不要做“消融实验”来证明组件价值，是否合理** → 见 [消融分析.md](./消融分析.md)
4. **消融实验设计与结果** → 见 [消融实验设计.md](./消融实验设计.md) 和 [消融实验结果.md](./消融实验结果.md)

## 目录结构

```text
测评/
├── README.md        # 本文件
├── 测试设计.md       # 详细测评设计（方法 + 依据）
├── 测试结果.md       # 完整运行当前 Agent 的结果
├── 消融分析.md       # 消融是否合理的分析
├── 消融实验设计.md    # 消融实验的完整设计
├── 消融实验结果.md    # 消融实验结果与分析
├── results/         # 原始结果 JSON（可机读）
└── scripts/         # 复现脚本
```

## 统一入口

新增四级评测统一入口：

```bash
# 查看支持的 Agent 配置变体
python 测评/scripts/eval.py --list-configs

# Tier 1：确定性安全/组件测试
python 测评/scripts/eval.py --tier 1

# Tier 2：Mock-LLM Agent 控制逻辑测试
python 测评/scripts/eval.py --tier 2

# Tier 3：真实论文能力评测（k=1，config=full）
python 测评/scripts/eval.py --tier 3 --paper feature3dgs_2312.03203 --k 1

# Tier 4：消融实验
python 测评/scripts/eval.py --tier 4
```

实现细节：

- `AgentConfig` 新增 `enable_plan / enable_budget_note / enable_judge_review / enable_hierarchical_memory` 开关，用于 Tier 3/4 的消融配置。
- `src/paperwise/evaluation/graders.py` 抽象了 `CodeGrader / RubricGrader / HallucinationGrader / TranscriptMetrics / CompositeGrader`。
- `tests/test_agent_loop.py` 使用 `MockLLMClient` 对预算提示、stagnation、plan ablation、session 上下文等做确定性验证。



## 高级评测脚本

除了 `eval.py` 四级入口，还有以下高级脚本：

```bash
# 完整 Ablation 对比（包含统计显著性分析）
python 测评/scripts/run_ablation_study.py --paper all --k 3

# 模型替换实验（区分模型 vs Harness 瓶颈）
python 测评/scripts/run_model_swap.py --paper all --k 3     --models deepseek-v4-flash claude-sonnet-4-20250514 gpt-4o
```

## 实现细节补充

- `src/paperwise/evaluation/stats.py` 提供标准误、置信区间、显著性检验、配对 bootstrap 等统计分析工具。
- `测评/scripts/run_ablation_study.py` 在 3 篇论文上跑全部 ablation 配置并生成对比报告。
- `测评/scripts/run_model_swap.py` 在固定 Harness 的情况下更换多个模型，用于定位瓶颈是模型还是 Harness。
- `测评/scripts/download_papers.py` 和 `parse_papers.py` 已扩展支持 5 篇语义 3DGS 相关论文。

## 一句话结论

- 测评分为两部分：**Part A（确定性安全/组件测试）** 和 **Part B（真实论文上的 LLM Agent 能力测试）**。
- 核心思想：先用**确定性检查**把安全与组件逻辑隔离验证，再用**真实论文 + 人工金标**衡量 Agent 的真实能力。
- 消融实验**合理且值得做**，但对 LLM 系统需要更谨慎的设计（多次重复、控制变量、优先确定性指标）。
- 已完成的确定性消融证明：**安全层 100% 拦截、记忆是推荐的必要条件、论文信号学习把召回从 83% 提到 100%**；同时发现并修复了一个记忆去重 bug，并暴露了推荐评分的精度问题。
