# PaperWise General Agent Specification v0.5

> 整合来源：Dynamic Research Agent Specification v1.0 + PaperWise 当前实现（commit 4c0d598）
> 核心原则：**预定义能力，不预定义完整路径；预定义领域模板，不固定执行图；运行时由 Router 选择模板，由 Planner 实例化 DAG，由 Critic 动态扩展 DAG；简单任务走 Fast Path，工具型任务走 Mini-DAG，复杂研究任务才进入 Full Dynamic DAG。**

---

Dynamic Research Agent Specification v1.0
1. System Goal

系统面向：

通用自然语言任务 → 意图识别 → 能力路由 → 动态 DAG → 工具/Agent 执行 → 验证 → 输出

同时针对论文研究场景提供专用 Workflow Template。

系统必须同时支持：

Simple Task
Complex Task
Research Task
Multi-step Task
Tool-use Task
Artifact Generation Task
2. 总体架构
                         User
                           │
                           ↓
                  ┌─────────────────┐
                  │ Intent / Router │
                  └────────┬────────┘
                           │
              ┌────────────┼─────────────┐
              ↓            ↓             ↓
           Simple       Standard      Complex
            Task          Task        Research
              │            │             │
              ↓            ↓             ↓
           Direct       Mini-DAG    Template DAG
           Response                   + Dynamic
                                      Expansion
                                           │
                                           ↓
                                  ┌────────────────┐
                                  │ DAG Executor   │
                                  └───────┬────────┘
                                          ↓
                                  ┌────────────────┐
                                  │ State / Memory │
                                  └───────┬────────┘
                                          ↓
                                    Verification
                                          ↓
                                       Output

这里有一个非常重要的原则：

不是所有请求都必须进入 DAG。

3. Task Complexity Router

这是你系统的第一道门。

定义：

TaskRouter

输出：

{
  "task_type": "research",
  "complexity": "complex",
  "requires_tools": true,
  "requires_planning": true,
  "requires_artifacts": true,
  "workflow": "paper_analysis"
}

建议至少分成：

类型	例子	DAG
CHAT	你好	❌
SIMPLE_QA	什么是 Transformer	❌/Mini
TOOL_QA	今天北京天气	Mini-DAG
SIMPLE_TASK	总结这段文字	Mini-DAG
COMPLEX_TASK	分析一个项目架构	DAG
RESEARCH	分析论文	Research DAG
GENERATION	生成报告/PPT	Generation DAG
HYBRID	分析论文并生成 PPT	Full DAG

这一步非常关键。

4. Node Registry

所有 Node 都必须提前注册。

例如：

NodeRegistry

├── input
│   ├── parse_pdf
│   ├── parse_docx
│   └── parse_image
│
├── retrieval
│   ├── web_search
│   ├── paper_search
│   ├── source_retrieval
│   └── citation_retrieval
│
├── extraction
│   ├── extract_text
│   ├── extract_table
│   ├── extract_figure
│   └── extract_metadata
│
├── research
│   ├── problem_analysis
│   ├── method_analysis
│   ├── experiment_analysis
│   ├── related_work_analysis
│   └── limitation_analysis
│
├── reasoning
│   ├── compare
│   ├── critique
│   ├── hypothesis
│   ├── synthesis
│   └── evidence_verification
│
├── generation
│   ├── report_outline
│   ├── report_section
│   ├── ppt_outline
│   ├── ppt_slide
│   └── figure_generation
│
└── utility
    ├── weather
    ├── calculator
    ├── code_execution
    └── general_chat
5. Node Contract

每个节点必须拥有标准接口：

NodeSpec

核心字段：

id
name
category
description

input_schema
output_schema

required_capabilities
optional_capabilities

preconditions
postconditions

cost
latency

retry_policy

verification_policy

例如：

{
  "id": "research.method_analysis",
  "category": "research",
  "input_schema": {
    "paper": "PaperArtifact",
    "sections": "SectionArtifact[]"
  },
  "output_schema": {
    "method": "MethodArtifact"
  },
  "required_capabilities": [
    "long_context",
    "vision"
  ],
  "verification_policy": {
    "required": true
  }
}
6. Artifact Registry

这是整个系统非常重要的一层。

不要让 Node 之间传巨大自然语言字符串。

而应该传：

Artifact

例如：

PaperArtifact
SectionArtifact
EvidenceArtifact
ClaimArtifact
MethodArtifact
ExperimentArtifact
ComparisonArtifact
ReportArtifact
SlideArtifact

这样：

MethodAnalysis
       ↓
MethodArtifact
       ↓
InnovationAnalysis

而不是：

Agent A 输出 20,000 tokens
       ↓
Agent B 全部吃进去
7. Workflow Template Registry

提前定义常见领域 Workflow。

例如：

WorkflowRegistry

├── paper_analysis
├── paper_compare
├── literature_review
├── paper_to_report
├── paper_to_ppt
├── deep_research
├── coding
├── data_analysis
└── general_task
8. Paper Analysis Template

你的论文 Agent 可以预定义这个基础 DAG：

                    Input Paper
                         │
                         ↓
                   Paper Parser
                         │
                         ↓
                 Paper Understanding
                         │
         ┌───────────────┼────────────────┐
         ↓               ↓                ↓
      Problem          Method         Experiment
      Analysis         Analysis         Analysis
         │               │                │
         └───────────────┼────────────────┘
                         ↓
                 Related Work
                         │
                         ↓
                  Evidence Merge
                         │
                         ↓
                      Critic
                         │
                ┌────────┴────────┐
                ↓                 ↓
              PASS              GAP
                │                 │
                ↓                 ↓
             Synthesis      Dynamic Expansion
                │                 │
                └────────┬────────┘
                         ↓
                      Output

注意：

这只是基础 DAG。

Runtime 可以删节点、增加节点、重新执行节点。

9. Dynamic Expansion

例如：

Method Analysis
      ↓
发现依赖：
“方法基于 DINOv2”
      ↓
Planner 判断需要背景知识
      ↓
新增：
Research DINOv2
      ↓
加入 DAG

或者：

Experiment Analysis
      ↓
发现：
论文声称 SOTA
      ↓
新增：
Search Original SOTA
      ↓
Metric Verification
      ↓
Comparison

因此：

Static Template
      ↓
Runtime Adaptation
      ↓
Dynamic DAG
10. Critic Contract

Critic 不负责重新写内容。

只负责：

Evaluate

输出：

{
  "status": "incomplete",
  "confidence": 0.73,
  "missing_evidence": [
    "baseline comparison"
  ],
  "missing_tasks": [
    "verify_sota_claim"
  ],
  "recommended_actions": [
    "expand"
  ]
}

Planner 根据 Critic 结果修改 DAG。

11. DAG Executor

Executor 负责：

Dependency Resolution
Parallel Execution
Conditional Routing
Retry
Timeout
State Persistence
Artifact Passing
Failure Recovery

例如：

       A
      / \
     B   C
      \ /
       D

B、C 可以并行。

D 必须等待 B、C。

12. DAG Runtime State

建议维护：

GraphState

包含：

task
objectives
nodes
edges
artifacts
evidence
claims
execution_history
errors
budget
time
iteration

因此 Agent 在运行过程中可以看到：

当前任务是什么？
已经完成什么？
缺什么？
下一步有哪些候选节点？
预算还有多少？
四、那么“通用 Agent”到底如何使用 DAG？

这里是你问题里非常关键的一点。

答案是：

通用 Agent 不应该有一张巨大的 Universal DAG。

这是一个非常容易踩的坑。

不要设计：

                         Universal DAG
                              │
      ┌─────────┬─────────────┼────────────┐
      ↓         ↓             ↓            ↓
   Search    Weather        Coding       Paper
      ↓         ↓             ↓            ↓
    ...       ...           ...          ...

这样最终会变成一张非常复杂的“上帝 DAG”。

五、正确做法：Router + Capability Graph + Local DAG

应该是：

                        User
                          │
                          ↓
                    Intent Router
                          │
              ┌───────────┼───────────┐
              ↓           ↓           ↓
           General     Research     Tool
              │           │           │
              ↓           ↓           ↓
           Direct      Paper DAG   Tool DAG

也就是说：

DAG 是局部的，不是全局的。

六、甚至可以进一步抽象成“DAG on Demand”

这是我比较推荐你最终采用的模型：

User Request
     ↓
Task Classification
     ↓
Is planning needed?
     │
 ┌───┴────┐
No       Yes
│          │
↓          ↓
Direct   Select Template
Answer      │
            ↓
      Instantiate DAG
            │
            ↓
      Execute DAG
            │
            ↓
       Expand if needed

因此：

简单任务
你好
 ↓
LLM
 ↓
你好，有什么可以帮你？

没有 DAG。

中等任务
帮我总结这篇论文
 ↓
Parse
 ↓
Summarize
 ↓
Answer

实际上是一个 Mini-DAG。

复杂任务
分析论文并生成 PPT
 ↓
Template
 ↓
Dynamic DAG
 ↓
Research
 ↓
Critic
 ↓
Report
 ↓
PPT
七、你举的“你好”其实特别重要

你的系统一定要设计：

Fast Path

也就是：

                         User
                           │
                           ↓
                      Fast Router
                       /         \
                    Simple      Complex
                      │            │
                      ↓            ↓
                  Direct LLM    Planner

对于：

你好
谢谢
你是谁
什么是 CNN
帮我改一下这句话

直接：

Fast Path → LLM

不要启动：

Planner
DAG Builder
Research Agent
Critic
Evidence Graph

否则你的系统会非常慢，而且成本很高。

八、“今天天气怎么样”属于另一种情况

它不是 Simple Chat。

而是：

Weather Query
     ↓
Tool Routing
     ↓
Weather Tool
     ↓
Answer

也就是：

User
 ↓
Intent Router
 ↓
Weather
 ↓
Location Resolver
 ↓
Weather API
 ↓
Answer

这是一个 Mini-DAG。

如果用户明确给出：

“多伦多今天的天气怎么样？”

可以：

Router
 ↓
Weather Tool
 ↓
Answer

如果没有地点：

“今天天气怎么样？”

就需要：

Router
 ↓
Need Location?
 ↓
Location Resolver
 ↓
Weather
 ↓
Answer

如果是需要用户当前位置，而当前没有用户提供/设备定位信息，就应该先请求位置，而不是根据 IP 猜。你这个系统的 Router 应该把 location_required 当成工具调用前置条件。

九、所以你的 Agent 实际应该有三个执行通道

我建议明确设计：

                  ┌─────────────────────┐
                  │      Main Router    │
                  └──────────┬──────────┘
                             │
            ┌────────────────┼────────────────┐
            ↓                ↓                ↓
       FAST PATH         MINI WORKFLOW     FULL WORKFLOW
            │                │                │
            ↓                ↓                ↓
        Direct LLM        1~5 Nodes       Dynamic DAG
FAST PATH

适用于：

你好
谢谢
什么是 Transformer
帮我润色一下
解释一下这句话

特点：

低延迟
低成本
无复杂规划
MINI WORKFLOW

适用于：

今天天气怎么样
帮我计算 123*456
总结这段文本
翻译这句话
查一下某个信息

例如：

Weather:

Router
 ↓
Weather Tool
 ↓
Response

或者：

Summary:

Input
 ↓
Summarizer
 ↓
Response
FULL WORKFLOW

适用于：

分析一篇论文
比较 5 篇论文
做 Literature Review
分析代码架构
生成研究报告
生成 PPT

才进入：

Planner
 ↓
Template Selection
 ↓
Dynamic DAG
 ↓
Execution
 ↓
Critic
 ↓
Expansion
 ↓
Generation
 ↓
Verification
十、因此你的“通用 Agent”其实可以这样理解

不是：

一个巨大 DAG。

而是：

一个能够根据任务动态选择 DAG 的 Meta-Agent。

架构：

                         ┌───────────────┐
                         │     USER      │
                         └───────┬───────┘
                                 ↓
                       ┌──────────────────┐
                       │ Intent Classifier │
                       └────────┬─────────┘
                                ↓
                       ┌──────────────────┐
                       │ Complexity Judge │
                       └────────┬─────────┘
                                ↓
               ┌────────────────┼─────────────────┐
               ↓                ↓                 ↓
           Fast Path        Mini Workflow     Full Workflow
               │                │                 │
               ↓                ↓                 ↓
             LLM             Tool DAG       Template Selector
                                                   │
                                                   ↓
                                            Dynamic DAG
                                                   │
                                                   ↓
                                               Critic
                                                   │
                                                   ↓
                                             Replanning
十一、这里还有一个非常重要的设计：不要让 Router 自己成为一个“大 Agent”

我建议：

Router

尽可能轻量。

可以是：

small LLM
+
rules
+
capability matching

例如先做：

Intent:
weather

Need Tool:
yes

Need Planning:
no

Complexity:
low

Workflow:
weather

再决定。

这样比让一个大模型先思考一大堆“我要不要调用 DAG”更加稳定。

当前 Agent 系统越来越强调减少无关工具和上下文暴露，而不是把所有能力一次性塞给模型。Anthropic 也强调工具集合过大、功能边界模糊会导致 Agent 可靠性下降。

十二、最后给你一个我认为比较成熟的最终架构

我会把你的项目定义成：

                  ┌─────────────────────────┐
                  │       General Agent     │
                  └────────────┬────────────┘
                               ↓
                     ┌──────────────────┐
                     │   Task Router     │
                     └─────────┬────────┘
                               ↓
               ┌───────────────┼────────────────┐
               ↓               ↓                ↓
           FAST PATH       MINI WORKFLOW     FULL AGENT
               │               │                │
               ↓               ↓                ↓
              LLM          Local DAG       Planner
                                                │
                                    ┌───────────┴───────────┐
                                    ↓                       ↓
                             Template Registry       Node Registry
                                    │                       │
                                    └───────────┬───────────┘
                                                ↓
                                        DAG Instantiation
                                                ↓
                                         Dynamic DAG
                                                ↓
                                      ┌─────────┴─────────┐
                                      ↓                   ↓
                                   Execute             Critic
                                      ↑                   │
                                      └────── Replan ─────┘
                                                ↓
                                         Artifact Store
                                                ↓
                                          Verification
                                                ↓
                                             Output

而你的论文 Agent只是这个 General Agent 的一个专业 Workflow：

General Agent
      │
      ↓
Research Router
      │
      ↓
Paper Workflow
      │
      ├── Paper Parser
      ├── Method Analyzer
      ├── Experiment Analyzer
      ├── Related Work
      ├── Evidence Graph
      ├── Critic
      ├── Dynamic Research
      ├── Report Generator
      ├── PPT Generator
      └── Verification
十三、所以你现在最应该确定的不是“DAG怎么画”

而是下面这四个 Registry：

① Capability Registry
        ↓
“Agent 能干什么？”

② Node Registry
        ↓
“有哪些标准执行节点？”

③ Workflow Registry
        ↓
“哪些领域有现成编排？”

④ Artifact Registry
        ↓
“节点之间交换什么结构化结果？”

再加一个：

⑤ Router
        ↓
“当前请求到底需要哪一种？”

这五个东西确定之后，DAG 本身反而变得简单了。

最终可以浓缩成一句架构原则

预定义能力，不预定义完整路径；预定义领域模板，不固定执行图；运行时由 Router 选择模板，由 Planner 实例化 DAG，由 Critic 动态扩展 DAG；简单任务走 Fast Path，工具型任务走 Mini-DAG，复杂研究任务才进入 Full Dynamic DAG。

---

## 14. 与 PaperWise 现有代码的映射

| 新 Spec 组件 | 当前 PaperWise 对应文件 | 状态/说明 |
|--------------|------------------------|-----------|
| Router / TaskClassifier | `src/paperwise/orchestration/classifier.py` | 已存在，需扩展输出 schema（task_type、requires_tools、escalate_on_failure） |
| Fast Path | `src/paperwise/core/llm_client.py` | 已存在，缺显式 Fast Path 入口 |
| Mini Workflow | `src/paperwise/orchestration/orchestrator.py::_run_simple()` | 已存在，对应 simple Q&A 路径 |
| Full Workflow | `src/paperwise/orchestration/orchestrator.py::_run_complex()` | 已实现 Reader→Verifier→Writer→Reviewer，待升级为通用 DAG Executor |
| NodeSpec | `src/paperwise/orchestration/specs.py::SubAgentSpec` | 字段需对齐：增加 category、input_schema、output_schema、verification_policy |
| Node Registry | 散布于 `tools/`、`parsers/`、`generators/`、`evaluation/` | 需集中到 `orchestration/registries.py` |
| Workflow Registry | `src/paperwise/orchestration/paper_dag.py` | 目前是硬编码关键词生成 Plan，需抽象为模板库 |
| Artifact Registry | `src/paperwise/core/types.py::ParsedPaper` + `facts.json` / `verified.json` / `report/report.md` | 需 Pydantic 化并规范产物目录 |
| DAG Executor | 无 | 新增 `orchestration/dag_executor.py` |
| Critic / Replan | `src/paperwise/orchestration/specs.py` Reviewer Spec + `parse_findings()` | 已输出 `findings.json`（本次已改），待统一为 Critic schema |
| GraphState | `src/paperwise/core/types.py::AgentState` | 需扩展为全局执行状态 |
| Verification | `src/paperwise/harness/verification.py` + `src/paperwise/evaluation/graders.py` | 已存在，需与 Critic 合并输出 |

### 14.1 论文 Workflow 到 PaperWise 节点的映射

- **Paper Parser** → `src/paperwise/parsers/pdf_parser.py`（输出 `text.md`、`figures/`、`tables/`、`metadata.json`）
- **Paper Understanding / Problem Analysis / Method Analysis / Experiment Analysis** → `src/paperwise/orchestration/specs.py` Reader Agent（输出 `facts.json`）
- **Related Work** → `src/paperwise/memory/knowledge_base.py` 检索相关论文
- **Evidence Merge / Synthesis** → `src/paperwise/orchestration/orchestrator.py` Writer Agent（输出 `report/sections/*.md`、`report/report.md`）
- **Critic** → Reviewer Agent（输出 `review/findings.md` + `review/findings.json`）
- **Dynamic Expansion** → 新增 `src/paperwise/orchestration/replanner.py`
- **Report/PPT Generation** → `src/paperwise/generators/report.py` / `src/paperwise/generators/pptx.py`（或 skill `nature-paper2ppt`）
- **Verification** → `src/paperwise/harness/verification.py::OutputVerifier` + `src/paperwise/evaluation/graders.py`

### 14.2 Artifact 与文件映射

| Artifact | 当前文件/目录 | 规范化目标 |
|----------|--------------|-----------|
| PaperArtifact | `metadata.json`, `text.md`, `figures/`, `tables/` | ✅ 已存在 |
| SectionArtifact | `text.md` 段落范围 | 新增索引 |
| ClaimArtifact | `facts.json` 中的 claims | Pydantic 化 |
| MethodArtifact | `facts.json` 中的 method 字段 | Pydantic 化 |
| EvidenceArtifact | `facts.json` / `verified.json` | Pydantic 化 |
| ReportArtifact | `report/report.md`, `report/sections/*.md` | 增加 outline/sections 元数据 |
| SlideArtifact | `slides.pptx` | 增加 per-slide JSON |

---

## 15. 实现路线

### Phase 1：接口规范化（1 周）

- [ ] 定义 `NodeSpec`、`WorkflowTemplate`、`Capability`、`GraphState` 数据类（放 `src/paperwise/core/types.py` 或 `orchestration/types.py`）。
- [ ] 新建 `src/paperwise/orchestration/registries.py`：Capability Registry、Node Registry、Workflow Registry、Artifact Registry。
- [ ] 将 `SubAgentSpec` 对齐为 `NodeSpec` 子集，保留现有提示词模板。
- [ ] 扩展 `TaskClassifier` 输出 `TaskRoute` schema，支持 `escalate_on_failure`。

### Phase 2：DAG Executor（1 周）

- [ ] 新增 `src/paperwise/orchestration/dag_executor.py`。
- [ ] 扩展 `core/plan.py`：支持 `parallel_group`、`condition`、`retry_count`、`max_retries`、`confidence_threshold`。
- [ ] 将 `orchestrator.py::_run_complex()` 替换为 `DAGExecutor` 驱动。
- [ ] 实现 `analyze_method` 与 `verify_data` 并行、`generate_report` 与 `generate_pptx` 并行。

### Phase 3：Critic & Replan（1 周）

- [ ] 新增 `src/paperwise/orchestration/replanner.py`。
- [ ] 统一 Critic 输出 schema：`status / confidence / missing_evidence / missing_tasks / recommended_actions / severity`。
- [ ] 根据 Critic 结果动态扩展 DAG（如新增 `re_read_section`、`re_verify_with_code`）。
- [ ] Reviewer 循环默认 3 轮，并暴露配置（本次已改默认值为 3）。

### Phase 4：Artifact 标准化（1 周）

- [ ] Pydantic 化 `PaperArtifact`、`SectionArtifact`、`ClaimArtifact`、`MethodArtifact`、`ReportArtifact`、`SlideArtifact`。
- [ ] 规范 `workspace/{paper_id}/artifacts/` 目录。
- [ ] Reader/Verifier/Writer 之间通过 JSON Artifact 文件传递，减少 prompt 中的大段上下文。

### Phase 5：三通道贯通与评测（1 周）

- [ ] 顶层 `GeneralAgent.run(task)` 根据 Router 选择 Fast / Mini / Full 通道。
- [ ] ambiguous 任务实现 two-stage fallback（simple 先试，失败再升级）。
- [ ] Token / cost budget 按通道和节点分配。
- [ ] 端到端评测：Fast/Mini/Full 分别跑 10+ case，输出对比报告。

---

## 16. 与原始 pasted spec 的对应关系

| pasted spec 概念 | 本 spec 位置 | 是否继承 |
|------------------|-------------|---------|
| System Goal | §1 / §14 | ✅ 继承，增加 General Agent 目标 |
| Intent / Router | §3 | ✅ 继承，映射到 `TaskClassifier` |
| Fast / Mini / Full | §4 / §9 | ✅ 继承 |
| Node Registry / Node Contract | §4 / §14 | ✅ 继承，映射到 `SubAgentSpec` |
| Artifact Registry | §6 | ✅ 继承，Pydantic 化 |
| Workflow Registry | §7 | ✅ 继承 |
| Paper Analysis Template | §8 | ✅ 继承 |
| Dynamic Expansion | §9 / §10 | ✅ 继承 |
| Critic Contract | §10 | ✅ 继承 |
| DAG Executor | §11 | ✅ 继承 |
| GraphState | §12 | ✅ 继承 |
| Router 轻量 | §3.4 | ✅ 继承 |
| 不要上帝 DAG | §2 | ✅ 继承 |

---

> **最终架构原则（与 pasted spec 一致）**：预定义能力，不预定义完整路径；预定义领域模板，不固定执行图；运行时由 Router 选择模板，由 Planner 实例化 DAG，由 Critic 动态扩展 DAG；简单任务走 Fast Path，工具型任务走 Mini-DAG，复杂研究任务才进入 Full Dynamic DAG。

---

## 17. 实现状态与偏差（v0.5 → v0.5.1）

### 已完成

- **Phase 1 接口规范化**
  - `NodeSpec`、`WorkflowTemplate`、`Capability`、`GraphState`、`TaskRoute`、`CriticResult` 等数据类型已在 `src/paperwise/orchestration/types.py` 中定义。
  - `CapabilityRegistry`、`NodeRegistry`、`WorkflowRegistry`、`ArtifactRegistry` 已在 `src/paperwise/orchestration/registries.py` 中提供默认实现。
  - `SubAgentSpec.to_node_spec()` 已对齐到 `NodeSpec` 子集。
  - `TaskClassifier.classify()` 已改为返回 `TaskRoute` schema，包含 `escalate_on_failure` 标志，支持两阶段降级/升级回退。

- **Phase 2 DAG Executor**
  - 新增 `src/paperwise/orchestration/dag_executor.py`，支持依赖解析、并行组、条件跳过、重试和预算检查。
  - `core/plan.py` 已扩展 `parallel_group`、`condition`、`retry_count`、`max_retries`、`output_artifact`、`confidence_threshold` 以及 `next_executable_group()` / `mark_needs_replan()`。
  - `orchestrator.py::_run_complex()` 已改为由 `DAGExecutor` 驱动。
  - `analyze_method` 与 `verify_data` 并行；`generate_report` 与 `generate_pptx` 并行。

- **Phase 3 Critic & Replan（部分）**
  - 新增 `src/paperwise/orchestration/replanner.py` stub。
  - Critic schema 已统一为 `status / confidence / missing_evidence / missing_tasks / recommended_actions / severity`。
  - Reviewer 循环默认 3 轮，已在 `SmartOrchestrator.max_review_rounds` 中暴露。

- **两阶段回退（two-stage fallback）**
  - `SmartOrchestrator.run()` 对 `route.escalate_on_failure=True` 的任务先执行 `_run_simple()`，失败或无结果时自动升级到 `_run_complex()`。

- **测试**
  - `tests/test_orchestration.py` 已更新为 `TaskRoute` schema，并新增 `test_smart_orchestrator_escalates_on_failure` 冒烟测试。
  - 当前 orchestration 测试套件 9/9 通过。

### 已知偏差与限制

- **动态扩展尚未完全实现**：`replanner.py` 目前是 stub，Critic 结果还不会动态插入 `re_read_section`、`re_verify_with_code` 等新节点。
- **分类器 LLM 回退未接入异步客户端**：`_llm_classify()` 保留 LLM 调用逻辑，但当前 `LLMClient.chat()` 为 async，而 `TaskClassifier.classify()` 为同步方法，因此默认走规则路径；后续需将 `classify()` 改为 async 或引入同步包装。
- **Artifact 标准化**：Pydantic Artifact 模型已定义，但 Reader/Verifier/Writer 之间仍通过 `facts.json` / `verified.json` 等文件传递，未完全迁移到 `artifacts/` 目录。
- **预算分配**：全局 `token_budget` / `max_steps` 已传入 `GraphState`，但尚未按子节点细粒度拆分；预算耗尽时仅终止当前执行，未触发 replan。
- **PPT 生成**：`_run_pptx_writer` 已合并到 `_run_writer`，但 `generate_pptx` / `nature-paper2ppt` skill 的具体集成仍需在 skill 层完成。

### 下一步建议

1. 完成 `ReplanAgent`：根据 Critic 输出生成增量 DAG 补丁。
2. 将 `TaskClassifier.classify()` 改为 async，并接入 LLM 轻量回退。
3. 实现按节点预算分配与耗尽时的 graceful degradation。
4. 迁移 `facts.json / verified.json` 为 `artifacts/` 下的 Pydantic Artifact 文件。
5. 增加端到端冒烟测试：Mini-DAG（验证 + 报告）和 Full-DAG（报告 + PPT + Review）。
