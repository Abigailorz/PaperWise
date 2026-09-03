# PaperWise — AI 学术论文智能解读系统 v0.7.0

基于《深入理解 AI Agent：设计原理与工程实践》全书知识体系构建。

## 快速开始

```bash
cd PaperWise
source .venv/Scripts/activate

# 1. 配置 API
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY

# 2. 启动 Web 界面（推荐）
python -m paperwise.api.server
# → http://localhost:8000

# 3. 或使用 CLI
paperwise parse paper.pdf                      # 解析论文
paperwise analyze paper.pdf --model deepseek-chat  # AI 深度分析
paperwise generate pptx workspace/{paper}/     # 生成 PPT
paperwise evaluate report.md workspace/{paper}/ # 评估质量

# 4. 运行测试
pytest tests/ -v                               # 311 个单元/集成测试（另有 4 个 e2e 真实 LLM 测试）
python tests/run_agent_tests.py                # Agent 能力测试
```

## Web 界面功能

打开 `http://localhost:8000` 后：

1. **上传 PDF** — 拖拽或点击上传论文
2. **对话分析** — Agent 自动解析后，你可以随意提问：
   - "这篇论文的核心创新是什么？"
   - "详细解释方法部分"
   - "实验设计有什么不足？"
   - "帮我生成一份完整报告"
   - "再详细一点" — Agent 在上文基础上加深
3. **生成 PPT** — 点击按钮自动生成学术演示文稿
4. **编辑报告** — 点击"编辑"修改报告内容
5. **Agent 记住你** — 偏好和研究方向自动保存
6. **记忆管理** — 侧边栏"记忆"面板可查看/删除用户记忆卡
7. **章节编辑** — 直接编辑报告章节，保存后重新生成 PPT
8. **arXiv 摄入** — `POST /api/sessions/{sid}/arxiv` 粘贴 arXiv 链接即可解析
9. **主动提醒** — 定时器到期自动注入 Agent 上下文并广播
10. **评估 Dashboard** — 打开 `http://localhost:8000/dashboard` 查看 Pass@k 趋势
11. **主动论文推荐** — 从记忆自动学习兴趣画像（无需手动填研究方向），据此检索 arXiv 新论文，横幅一键"解读这篇"；每日定时推送
12. **Research Graph** — Evidence / Claim / Method / Opportunity 实体化，跨任务持久累积研究状态
13. **跨论文分析（P9）** — 多论文库的方法对比 / 矛盾 / 互补自动发现，报告与 PPT 自动注入跨论文章节

## CLI 命令

```bash
# 解析 PDF
paperwise parse attention_is_all_you_need.pdf
paperwise parse paper.pdf --output-dir ./my_output/

# AI 深度分析
paperwise analyze paper.pdf
paperwise analyze paper.pdf --model deepseek-chat --provider openai_compatible
paperwise analyze paper.pdf --model moonshot-v1-auto --provider moonshot

# 生成 PPT
paperwise generate pptx workspace/attention_is_all_you_need/

# 评估质量
paperwise evaluate workspace/{paper}/report/report.md workspace/{paper}/
paperwise fetch-arxiv 2401.12345                  # 下载 arXiv 论文
paperwise pipeline paper.pdf                      # 端到端流水线（含对抗式审核+修订）
```

## 项目结构

```
PaperWise/
├── src/paperwise/              ← 56 个 Python 文件 (~9000 行)
│   ├── core/                   # Agent 核心 (ReAct, LLM客户端, Session)
│   ├── harness/                # Harness 工程 (5层压缩, 3层护栏, 状态栏)
│   ├── tools/                  # 17 工具五类 (感知/执行/协作/事件/沟通)
│   ├── parsers/                # PDF 解析 (PyMuPDF)
│   ├── agents/                 # 多 Agent 编排 (Pipeline/Parallel/Adversarial)
│   ├── generators/             # 报告 + PPT 生成
│   ├── evaluation/             # Rubric + 幻觉检测 + Pass@k
│   ├── memory/                 # RAG (RAPTOR+GraphRAG) + 用户记忆
│   ├── skills/                 # 3 个 Agent Skills
│   ├── api/                    # FastAPI + WebSocket
│   ├── web/static/             # 聊天界面 (HTML/JS)
│   ├── cli/                    # CLI (Typer + Rich)
│   └── config/                 # 配置 (25 项可配置)
├── skills/                     # Skill 定义 (Markdown)
├── tests/                      # 311 单元/集成测试 + Agent 能力测试 + MCP 集成脚本
│   └── test_data/              # 测试数据集
├── 测评/                       # 测评设计 / 结果 / 金标 / 消融实验 / 复现脚本
├── docs/                       # 文档
│   ├── 项目全景解析.md           # 完整项目解析（推荐先读）
│   ├── 逐文件解读.md             # 每个文件的作用说明
│   ├── ARCHITECTURE.md         # 完整架构文档
│   ├── AUDIT.md                # 技术审计报告
│   ├── DESIGN-REVIEW.md        # Agent 设计评审与改进路线图
│   ├── PaperWise-SPEC.md       # 原始规格说明书
│   └── AI-Agents-in-Depth-zh-CN.pdf  # 理论基础
├── workspace/                  # 运行时数据
├── .env                        # API 配置
└── pyproject.toml              # 项目配置
```

## 核心架构

```
Agent = Model + Harness + Environment

Model:  DeepSeek / Kimi K3 / OpenAI (可切换)
Harness:
  ├── ContextManager    5层压缩 (截断→噪声→微压缩→归档→LLM)
  ├── ConstraintEngine  3层护栏 (输入/执行/输出)
  ├── StatusBar         自动 TODO 推断
  ├── Corrector         熔断器 + 指数退避
  └── Verifier          输出正确性校验

Agent Loop (增强 ReAct):
  ├── Budget-Aware 执行策略
  ├── Plan-then-Execute
  ├── 过早终止检测
  └── 7 种退出条件

RAG: Dense + Sparse + RRF + Cross-Encoder + HyDE + RAPTOR + GraphRAG
记忆: Advanced JSON Cards + LLM提取 + 自动去重 + 兴趣画像（主动推荐）
评估: Rubric + 幻觉检测（异源 Judge）+ Pass@k/Pass^k + 消融实验
```

## 技术对照（书中 20 项核心技术）

| # | 技术 | 书中章节 | 状态 |
|---|------|----------|------|
| 1 | ReAct 循环 + Budget-Aware | 1.1.5, 10.2 | ✅ |
| 2 | Harness 工程 5 要素 | 1.2 | ✅ |
| 3 | KV Cache 友好设计 | 2.3 | ✅ |
| 4 | 完整 5 层上下文压缩 | 2.7.4 | ✅ |
| 5 | Agent Skills 渐进披露 | 2.5 | ✅ |
| 6 | Agent 状态栏 + 自动 TODO | 2.6 | ✅ |
| 7 | 提示注入防护 | 2.4.7 | ✅ |
| 8 | 用户记忆 Advanced JSON Cards | 3.1.3 | ✅ |
| 9 | RAG 混合检索 + Rerank | 3.2.4 | ✅ |
| 10 | RAPTOR 层次索引 | 3.3.1 | ✅ |
| 11 | GraphRAG 知识图谱 | 3.3.1 | ✅ |
| 12 | Agentic RAG | 3.3.4 | ✅ |
| 13 | 五类工具全覆盖 | 4.1 | ✅ |
| 14 | MCP 兼容工具注册 | 4.3 | ✅ |
| 15 | 七核心 Coding Agent 工具 | 5.1.1 | ✅ |
| 16 | LLM-as-a-Judge + Rubric | 6.5 | ✅ |
| 17 | Pass@k / Pass^k | 6.2 | ✅ |
| 18 | 持续进化闭环 | 8.2-8.3 | ✅ |
| 19 | 多 Agent 协作 (Manager+Worker) | 10.1-10.4 | ✅ |
| 20 | 对抗式审查 | 10.4.3 | ✅ |

## 审计状态

| 严重级别 | 总计 | 已修复 | 剩余 |
|----------|------|--------|------|
| CRITICAL | 12 | **12** | **0** |
| HIGH | 20 | 17 | 3 |
| MEDIUM | 16 | 10 | 6 |
| LOW | 8 | 2 | 6 |

> 完整审计报告：`docs/AUDIT.md` | 架构详解：`docs/ARCHITECTURE.md` | 项目全景解析：`docs/项目全景解析.md` | 设计评审：`docs/DESIGN-REVIEW.md`
