# PaperWise 完整架构文档 v0.4.1

> 基于《深入理解 AI Agent：设计原理与工程实践》（李博杰 著, v1.4）全书知识体系
> 审计达标：CRITICAL 0 降级 / HIGH 17/20 已修复 / MEDIUM 10/16 已修复 / LOW 2/8 已修复

> 注意：本文为历史架构文档，部分内容（推荐器、记忆去重、异源 Judge、报告组装等）已有更新。
> 最新、最完整的解析见 [项目全景解析.md](./项目全景解析.md)。

---

## 1. 系统总览

PaperWise 是一个多 Agent 协作的学术论文智能解读系统。输入 PDF 论文，自动生成深度解读报告和学术演示文稿，支持对话式交互。

### 1.1 核心公式

```
PaperWise Agent = Model + Harness + Environment

Model:       DeepSeek / Kimi K3 / OpenAI / 任意 OpenAI 兼容 API
Harness:     上下文管理(5层压缩) + 工具接口(17工具五类) + 约束/验证/纠正
Environment: 文件系统 + 知识库(RAG+RAPTOR+GraphRAG) + Web API + WebSocket
```

### 1.2 技术指标

| 指标 | 值 |
|------|-----|
| Python 文件 | 56 个 |
| 代码行数 | ~9,000 行 |
| 工具数量 | 17 个（五类全覆盖） |
| Agent Skills | 3 个（学术阅读/报告生成/验证） |
| 单元测试 | 60 个 |
| 支持 LLM | DeepSeek / Kimi K3 / OpenAI / 任意兼容 API |
| CRITICAL 降级 | 0（全部修复） |

---

## 2. 分层架构

```
┌──────────────────────────────────────────────────────┐
│ 展示层    CLI (Typer+Rich) / Web Chat UI / API (FastAPI+WS) │
├──────────────────────────────────────────────────────┤
│ 会话层    AgentSession (对话式, 持久化, 有记忆)        │
│           ├─ 跨轮上下文保留                             │
│           ├─ LLM 驱动的记忆提取                          │
│           └─ Session 完整恢复                            │
├──────────────────────────────────────────────────────┤
│ 编排层    AgentOrchestrator (Manager+Worker 不共享上下文)│
│           ├─ Pipeline (Analyst→Writer→Reviewer)         │
│           ├─ Parallel (报告+PPT 并行)                    │
│           └─ Adversarial Review (对抗式审查)             │
├──────────────────────────────────────────────────────┤
│ Agent层   Agent (增强 ReAct 循环)                       │
│           ├─ Budget-Aware 执行策略                       │
│           ├─ Plan-then-Execute 规划先行                  │
│           ├─ 过早终止检测 (Proposer-Reviewer)            │
│           ├─ 7 种退出条件                                │
│           └─ 流式思考 + 缓冲推送                         │
├──────────────────────────────────────────────────────┤
│ Harness层 上下文管理 │ 约束引擎 │ 状态栏 │ 纠正器 │ 验证器 │
│           ├─ 5层压缩 (截断→噪声→微压缩→归档→LLM)        │
│           ├─ 3层护栏 (输入/执行/输出)                    │
│           ├─ 自动 TODO 推断                              │
│           └─ 熔断器 + 指数退避重试                       │
├──────────────────────────────────────────────────────┤
│ 工具层    17个工具 / 5个类别                             │
│           感知: read_file, glob, grep                    │
│           执行: write/edit/code_interpreter/bash/request │
│           技能: skill_list, skill_load, discover_tool    │
│           协作: spawn_subagent, send_message             │
│           事件: set_timer, monitor_shell                 │
│           沟通: ask_user, notify_user                    │
├──────────────────────────────────────────────────────┤
│ 能力层    LLM客户端 │ PDF解析 │ RAG知识库 │ 用户记忆 │ 进化引擎 │ 调度器 │
│           ├─ OpenAI 兼容 + 流式                          │
│           ├─ PyMuPDF (文本/图表/表格/公式)               │
│           ├─ Dense+Sparse+RRF+Rerank+HyDE+RAPTOR+GraphRAG │
│           ├─ Advanced JSON Cards + LLM提取 + 去重合并    │
│           ├─ 轨迹→模式→部署闭环                          │
│           ├─ 主动调度器 (定时器/监控事件 → 会话注入)      │
│           └─ 论文推荐器 (arXiv API/列表页/语义学者)      │
└──────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 3.1 Agent 核心引擎 (`core/agent.py` ~420行)

增强 ReAct 循环，远超基础实现：

```
ReAct 循环 = 思考 → 行动 → 观察 → 重复

增强特性:
├─ Budget-Aware: 根据剩余 token/步数动态调整策略
│    <50%: 正常探索
│   50-80%: 聚焦关键部分
│   >80%: 紧急输出
├─ Plan-then-Execute: 先写计划到 analysis/plan.md
├─ 过早终止检测: 连续 2 次 text → 验证产物是否真实存在
├─ 7 种退出条件: max_steps, token_budget, circuit_breaker,
│                time_budget, consecutive_errors, natural_end, interrupt
├─ 流式缓冲: 文本累积 0.5s 或遇到断点才推送（防洪水）
└─ 配置外置: 所有阈值从 Settings 读取
```

### 3.2 Harness 工程层 (`harness/` ~700行)

**ContextManager** — 完整 5 层压缩：

| 层 | 名称 | 策略 |
|----|------|------|
| L1 | 工具结果预算 | 8K 字符智能截断，保留首尾+中间摘要 |
| L2 | 噪声删除 | 重复检测 + 低密度内容过滤 |
| L3 | API 微压缩 | 空白精简 + 超长行截断（发 API 前） |
| L4 | 归档摘要 | git log 风格逐轮结构化记录 |
| L5 | LLM 全量压缩 | LLM 驱动的完整上下文压缩 |

**ConstraintEngine** — 三层护栏：

```
输入侧: 注入模式检测（chat template/伪指令/角色劫持）
执行侧: 工具风险评级 + 路径安全 + 命令模式检测
输出侧: API key 泄露检测 + 提示词泄露检测
```

**StatusBar** — 自动 TODO 推断：

```
从 Agent 消息历史中自动推断 TODO:
  read_file(text.md)  → "Read and understand paper"  [done]
  grep                → "Search paper for key info"   [done]
  write_file(report/*) → "Generate analysis report"   [in_progress]
  code_interpreter    → "Verify numerical claims"     [done]
```

### 3.3 工具系统 (`tools/` ~1,300行)

17 个工具，MCP 兼容，五类全覆盖：

| 类别 | 工具 | 风险 | 实现质量 |
|------|------|------|----------|
| 感知 | read_file | LOW | 带行号，支持 offset/limit |
| 感知 | glob | LOW | 完整 glob 语法 |
| 感知 | grep | LOW | 正则 + 上下文行 |
| 执行 | write_file | MEDIUM | 创建/覆盖 |
| 执行 | edit_file | MEDIUM | 精确字符串替换（唯一匹配） |
| 执行 | code_interpreter | MEDIUM | subprocess 隔离，30s 超时 |
| 执行 | bash | MEDIUM | 正则安全检测，exec 参数列表 |
| 执行 | request_file_access | MEDIUM | 白名单外路径授权申请（读/写） |
| 技能 | skill_list | LOW | 渐进披露第一层：技能目录 |
| 技能 | skill_load | LOW | 加载完整 SKILL.md 指令 |
| 技能 | discover_tool | LOW | **动态工具发现**：按关键词返回完整定义 |
| 协作 | spawn_subagent | MEDIUM | **真实 Agent 创建**（非桩） |
| 协作 | send_message_to_agent | LOW | 多 Agent 消息传递 |
| 事件 | set_timer | LOW | 异步回调 |
| 事件 | monitor_shell | MEDIUM | **真实进程管理**（非桩） |
| 沟通 | ask_user | LOW | 结构化选项提问 |
| 沟通 | notify_user | LOW | 四级通知（info/success/warning/error） |

### 3.4 RAG 知识库 (`memory/knowledge_base.py` ~850行)

完整检索管线（对应书中第 3 章全部技术点）：

```
Query
  ├─ 1. Context-Aware 增强 (LLM 驱动查询改写)
  ├─ 2. HyDE 查询扩展 (LLM 生成假设文档)
  ├─ 3. RRF 混合检索
  │     ├─ Dense: sentence-transformers / API embeddings / TF-IDF
  │     └─ Sparse: BM25 (k1=1.5, b=0.75)
  ├─ 4. Cross-Encoder 重排序 (LLM-as-Reranker)
  ├─ 5. RAPTOR 层次索引 (LLM 聚类 + 摘要树)
  ├─ 6. GraphRAG 知识图谱 (LLM 实体关系抽取)
  └─ 7. 多模态检索 (图片/表格/公式索引)
```

| 嵌入模式 | 优先级 | 说明 |
|----------|--------|------|
| 本地 sentence-transformers | 1 | all-MiniLM-L6-v2, 384维（需下载一次） |
| API embeddings | 2 | OpenAI 兼容 /v1/embeddings（需配置 key） |
| TF-IDF 降级 | 3 | 纯本地，无外部依赖 |

### 3.5 用户记忆 (`memory/user_memory.py` ~280行)

```
Advanced JSON Cards:
  card_id         → 唯一标识
  category        → preference|fact|relationship|experience|knowledge
  data            → 结构化键值对
  backstory       → 来源背景（为什么记住）
  confidence      → 可信度 0.0-1.0
  version         → 更新版本号

特性:
  ├─ LLM 驱动提取 (extract_from_conversation)
  ├─ 自动去重合并 (同 key 更新而非新增)
  ├─ 冲突检测 (同类别同 key 不同 value)
  ├─ 上下文注入 (结构化 XML 注入 system prompt)
  └─ 损坏恢复 (备份 + 逐条加载)
```

### 3.6 持续进化 (`evolution.py` ~270行)

```
闭环: 轨迹 → 评估 → 模式发现 → 部署 → 验证

四种更新载体:
  ├─ 知识 (→ learned_knowledge.md + 项目根目录)
  ├─ 指令 (→ SKILL.md 文件自动更新)
  ├─ 程序 (→ harness_improvements.json)
  └─ 参数 (→ 需外部训练)

部署现已连接回实际 Agent 行为文件（不再写入死文件）
```

### 3.7 评估体系 (`evaluation/` ~200行)

```
RubricEvaluator:     4 维评分 (accuracy/completeness/insight/evidence)
HallucinationDetector: 3 类检测 (numerical/methodological/finding)
PassKEvaluator:      Pass@k + Pass^k 双指标
AblationTester:      逐一关闭组件测量贡献

安全: 错误时 passed=False（不再静默通过）
```

### 3.8 多 Agent 编排 (`agents/orchestrator.py` ~260行)

```
AgentOrchestrator:
  ├─ Pipeline (顺序): Analyst → Writer → Reviewer
  ├─ Parallel (并行): 报告 + PPT 同时生成
  └─ Adversarial Review (对抗): 独立 Agent 假设报告有错，严格审查

PaperAnalysisPipeline:
  ├─ get_analyst_spec()      → 深度论文分析
  ├─ get_report_writer_spec() → 结构化报告生成
  ├─ get_reviewer_spec()     → 质量审查
  └─ get_adversarial_reviewer_spec() → 对抗式审查（新）
```

### 3.9 对话式 Session (`core/session.py` ~410行)

```
AgentSession:
  ├─ chat(message) → 返回回复（上下文跨轮保留）
  ├─ handle_file_upload(pdf) → 解析论文 + 注入上下文
  ├─ LLM 记忆提取 (_auto_remember)
  ├─ KB 关联搜索 (find_related_papers)
  ├─ Session 完整持久化 (state.json)
  └─ Session 完整恢复 (load)

与流水线 Agent 的本质区别:
  流水线: upload → analyze → done (一次性)
  对话式: chat("帮我分析创新点") → chat("再详细点") → chat("生成报告") (持续)
```

### 3.10 经验学习层 (`learning/`)

```
LearningSignalGenerator:  Reviewer findings / AgentTrace → 结构化学习信号
                           (hallucination / quality_gap / omission / node_failure /
                            planning_failure / instability / success)
FailurePatternExtractor:  跨 trace 聚合失败模式（min_occurrences 阈值过滤噪声）
StrategyLibrary:          策略库（plan_hints / avoid / success_rate 滚动更新）
                           └─ 落盘 workspace/.paperwise/{user}/strategies/

闭环: Execute → Review → learn_from_review → StrategyLibrary
                                          → apply_strategies_to_plan → 下次规划
```

---

## 4. 数据流图

```
用户 (CLI / Web Chat)
  │
  ├─ POST /api/sessions/{id}/upload → PDFParser → workspace/{id}/
  ├─ POST /api/sessions/{id}/chat  → AgentSession.chat()
  │     │
  │     ├─ memory.to_context_string() → 注入系统提示词
  │     ├─ kb.add_conversation_turn()  → 更新上下文感知检索
  │     ├─ Agent.run() (ReAct 循环)
  │     │   ├─ pre_llm: StatusBar + Budget + Loop Detect
  │     │   ├─ chat_stream: LLM API (流式 + 缓冲)
  │     │   ├─ post_llm: Token Track + Early Term
  │     │   ├─ execute_tool: ConstraintEngine.check()
  │     │   └─ check_exit: 7 种条件
  │     │
  │     └─ _auto_remember():
  │         ├─ LLM 提取记忆 → UserMemory
  │         └─ KB 关联搜索 → find_related_papers
  │
  └─ POST /api/generate/pptx → PPTXGenerator → slides.pptx
```

---

## 5. 配置体系

### 5.1 Settings 完整列表

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `PAPERWISE_LLM_PROVIDER` | deepseek | LLM 提供商 |
| `PAPERWISE_DEFAULT_MODEL` | deepseek-chat | 默认模型 |
| `PAPERWISE_MAX_STEPS` | 25 | Agent 最大步数 |
| `PAPERWISE_TOKEN_BUDGET` | 180000 | Token 预算 |
| `PAPERWISE_TEMPERATURE` | 0.3 | LLM 温度 |
| `PAPERWISE_TIME_BUDGET` | 1800 | 时间预算 (秒) |
| `PAPERWISE_EARLY_TERM_THRESHOLD` | 2 | 过早终止阈值 |
| `PAPERWISE_MAX_CONSECUTIVE_ERRORS` | 5 | 熔断器阈值 |
| `PAPERWISE_MAX_RETRIES` | 3 | 最大重试 |
| `PAPERWISE_COMPRESSION_TRIGGER` | 0.85 | 压缩触发点 |
| `PAPERWISE_TOOL_OUTPUT_MAX_CHARS` | 8000 | 工具输出截断 |
| `PAPERWISE_ARCHIVE_WINDOW` | 20 | 归档窗口 |
| `PAPERWISE_TRAJECTORY_MAX` | 100 | 轨迹保留数 |
| `PAPERWISE_EMBEDDING_API_KEY` | (空) | 嵌入 API Key（可选） |
| `PAPERWISE_EMBEDDING_BASE_URL` | api.openai.com | 嵌入 API URL |
| `PAPERWISE_EMBEDDING_MODEL` | text-embedding-3-small | 嵌入模型 |

### 5.2 .env 示例

```bash
# 必填
PAPERWISE_LLM_PROVIDER=openai_compatible
PAPERWISE_DEFAULT_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 可选 — RAG 语义检索质量提升
PAPERWISE_EMBEDDING_API_KEY=sk-your-key
PAPERWISE_EMBEDDING_BASE_URL=https://api.openai.com/v1
PAPERWISE_EMBEDDING_MODEL=text-embedding-3-small
```

---

## 6. 质量审计状态

| 严重级别 | 总计 | 已修复 | 剩余 | 状态 |
|----------|------|--------|------|------|
| CRITICAL | 12 | 12 | 0 | ✅ 全部清除 |
| HIGH | 20 | 17 | 3 | 核心已修复 |
| MEDIUM | 16 | 10 | 6 | 持续优化中 |
| LOW | 8 | 2 | 6 | 低优先级 |

**CRITICAL 全部清除** — 无安全漏洞、无假实现、无核心功能缺失。

> 完整审计清单见 `docs/AUDIT.md`；改进路线图见 `docs/DESIGN-REVIEW.md`
