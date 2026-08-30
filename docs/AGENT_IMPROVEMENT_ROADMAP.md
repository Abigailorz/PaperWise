 # PaperWise Agent 核心改进方案

 > 版本：v0.5.0 设计稿  
 > 目标：让 Agent 从“能跑通的学术原型”升级为“可展示、可对比”的生产级论文解读 Agent  
 > 参考：CODEX / Claude Code / Anthropic Building Effective Agents / AgentHarness

 本文档是 `docs/EVALUATION_FRAMEWORK.md` 与 `docs/DESIGN-REVIEW.md` 的延续，聚焦 **Agent 本身的能力缺陷** 与 **可落地的改进路径**。阅读前建议先看：
 - [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)：系统分层。
 - [`docs/EVALUATION_FRAMEWORK.md`](./EVALUATION_FRAMEWORK.md)：四级评测体系。
 - [`docs/DESIGN-REVIEW.md`](./DESIGN-REVIEW.md)：上一轮设计评审与已修复项。

 ---

 ## 一、当前 Agent 的能力定位

 ### 1.1 已经做得不错的地方

 | 维度 | 现状 | 评价 |
 |------|------|------|
 | 安全约束 | `ConstraintEngine` + `security.py` 拦截危险路径/命令/注入/API 泄露 | 达到可用水准 |
 | 上下文压缩 | 5 层压缩（L1 截断 / L2 去噪 / L3 微压缩 / L4 归档 / L5 LLM 摘要） | 有想法，实现还粗 |
 | 显式 Plan | 用代码而非 LLM 推断 TODO，避免模型编造任务 | 方向对，但太静态 |
 | 评测框架 | 四级评测 + golden dataset + ablation + grader | 骨架完整 |
 | 异源 Judge | 主模型与 Judge 分离，避免同源性偏见 | 已落地 |

 ### 1.2 与 Codex / Claude Code 的差距（一句话版）

 | 维度 | PaperWise | Codex / Claude Code |
 |------|-----------|---------------------|
 | 规划 | 关键词硬编码 Plan，不会 replan | 动态规划 + 子任务分解 + replan |
 | 检索 | 一次读全文进上下文 | 按需 `grep`/`view` 片段 |
 | 编辑 | 全量 `write_file`/`edit_file` | `apply_patch` 精确 diff |
 | 完成判定 | 关键词 + 文件存在性 | 输出质量 + 用户意图匹配 |
 | 并行 | 串行单工具 | 并行工具 + 多 Agent |
 | 引用溯源 | 无 | 每句 claims 要求可追溯到源文件 |
 | 人机协同 | 无审批直接执行 bash | 危险操作需用户确认 |

 当前 PaperWise Agent 大约处于 **“有护栏的 ReAct demo”** 阶段；距离能稳定处理真实论文、可写进简历的“效果评测展示”，还差 **动态规划、检索式上下文、精确编辑、引用溯源、多 Agent 协作** 这几块。

 ---

 ## 二、核心设计原则

 1. **Agent-readable codebase**：把设计原则、工具 schema、评测标准写成 Agent 能读取的结构化文档，而不是只存在于代码注释。
 2. **Mechanism over prompt**：尽量少用 prompt 约束，用代码机制约束（Plan DAG、文件锁、引用校验、apply_patch）。
 3. **Progressive disclosure**：复杂能力（RAPTOR/GraphRAG/多 Agent）只在需要时暴露，默认保持简单 ReAct。
 4. **Outcome over output**：评测看“环境最终状态/答案是否正确”，而不是看 Agent 说了什么。
 5. **Feedback loop**：Reviewer 的发现必须回写到 Agent 并触发 revise，形成闭环。

 ---

 ## 三、待改进问题与对应方案

 ### 3.1 规划系统：从静态关键词到动态 DAG

 #### 问题

 - [`Plan.from_task_text()`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\plan.py) 仅根据用户输入里的关键词生成固定计划：
   - `report` -> analyze_method -> generate_report
   - `ppt` -> generate_pptx
 - 不读论文结构，不会根据论文实际章节调整。
 - 某步失败不会 replan，只会继续按原列表执行。
 - 没有子任务分解，复杂任务（“完整解读 + 验证 + 报告 + PPT”）一次性塞进一个 Agent。

 #### 方案

 1. **两阶段规划**
    - **阶段 A（骨架规划）**：仍用关键词/规则快速生成初始 Plan，降低 LLM 调用成本。
    - **阶段 B（细化规划）**：在 `read_paper` 之后，用 LLM 读取论文目录/章节标题，生成与论文结构匹配的子任务 DAG。
 2. **Plan DAG 化**
    - `Task` 增加 `depends_on`（已有），但扩展为支持 AND/OR 依赖。
    - 引入 `next_executable()` 调度器，按拓扑顺序执行；前置任务失败时自动标记下游为 `blocked` 并触发 replan。
 3. **失败 Replan**
    - 当某任务连续失败 2 次或超时，调用 `ReplanAgent` 用 LLM 分析失败原因并返回新 Plan。
    - 新 Plan 必须保留已完成任务，避免重复劳动。
 4. **子任务分解**
    - 复杂任务拆成 `WorkerAgent`：
      - `ReaderAgent`：负责读论文、提取关键事实。
      - `VerifierAgent`：负责数值/代码验证。
      - `WriterAgent`：负责写报告。
      - `ReviewerAgent`：负责审查并输出 findings。
    - 由 `OrchestratorAgent` 按 DAG 调度，子 Agent 之间通过 `AgentBus` 传递结果。

 #### 涉及文件

 - [`src/paperwise/core/plan.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\plan.py)：扩展 DAG 与 replan。
 - [`src/paperwise/agents/orchestrator.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\agents\orchestrator.py)：实现调度与 WorkerAgent 创建。
 - [`src/paperwise/core/bus.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\bus.py)：子 Agent 消息传递。

 ---

 ### 3.2 上下文管理：从“压全文”到“按需检索”

 #### 问题

 - [`read_file`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\tools\file_tools.py) 默认可能读出整篇论文，导致 messages 膨胀。
 - `ContextManager` 的 L1-L5 都是“压缩已有 messages”，而非“只取相关片段”。
 - 论文里的图表/公式无法被多模态理解。

 #### 方案

 1. **论文分块索引**
    - PDF 解析后，把 `text.md` 按章节/段落切 chunk，每个 chunk 保留：
      - `chunk_id`、`paper_id`、`section_title`、`start_line`、`end_line`、`text`、`is_table`、`is_figure`、`caption`。
    - 用本地 `sentence-transformers`（all-MiniLM-L6-v2）或 API embedding 建向量索引，落盘 `workspace/{paper_id}/index/`。
 2. **检索优先的 read_file**
    - `read_file` 支持 `offset` / `limit`，默认只读 500 行以内；超过长度时自动提示使用 `grep` / `search_knowledge_base`。
    - 新增 `search_paper(query, top_k=5)` 工具，返回最相关 chunk 的摘要与行号，Agent 再按需 `read_file`。
 3. **图表/公式多模态描述**
    - PDF 解析阶段用 VLM（如 Qwen-VL、CLIP）为每个 figure/table 生成一段语义描述，存入索引。
    - Agent 提问涉及图表时，先检索这些描述，再决定是否要看原图。
 4. **压缩策略降级**
    - 当上下文接近上限时，优先丢弃“已检索到答案的冗余 chunk”，而不是对所有 messages 做 LLM 摘要；LLM 摘要只在检索仍不够时触发。

 #### 涉及文件

 - [`src/paperwise/parsers/pdf_parser.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\parsers\pdf_parser.py)：chunk 化与索引。
 - [`src/paperwise/memory/knowledge_base.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\memory\knowledge_base.py)：接入 paper-specific 检索。
 - [`src/paperwise/tools/search_tools.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\tools\search_tools.py)：新增 `search_paper`。
 - [`src/paperwise/harness/context.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\harness\context.py)：压缩前先去重/去冗余 chunk。

 ---

 ### 3.3 编辑工具：引入 `apply_patch` 精确编辑

 #### 问题

 - 当前 `write_file`/`edit_file` 容易破坏代码/ Markdown 结构，且难以验证是否改对。
 - 评测中 `report_generation` 场景经常因为 Agent 写报告的方式不对而失败。

 #### 方案

 1. **新增 `apply_patch` 工具**
    - schema：
      ```json
      {
        "path": "report/sections/intro.md",
        "patch": "*** Begin Patch\n...\n*** End Patch"
      }
      ```
    - 工具内部先校验 patch 语法，失败时返回错误而非直接写盘。
 2. **报告生成走 apply_patch**
    - 规划任务 `generate_report` 拆成：
      1. `write_file` 创建 `report/outline.json`。
      2. 对每个 section 用 `apply_patch` 追加/修改。
      3. 最后 `write_file` 组装 `report/report.md`。
 3. **编辑后校验**
    - 每次 `apply_patch` / `edit_file` 后，用 `OutputVerifier` 检查文件是否仍是合法 Markdown / JSON。

 #### 涉及文件

 - [`src/paperwise/tools/file_tools.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\tools\file_tools.py)：新增 `ApplyPatchTool`。
 - [`src/paperwise/generators/report.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\generators\report.py)：支持 section-by-section 生成。
 - [`src/paperwise/harness/verification.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\harness\verification.py)：新增 Markdown/JSON 合法性校验。

 ---

 ### 3.4 完成判定：从关键词到“意图 + 证据 + 输出”三重检查

 #### 问题

 - [`_looks_complete()`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent_loop.py) 只看关键词（如 `report has been generated`）。
 - [`_verify_completion()`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent_loop.py) 只检查文件存在和字数。
 - 这导致 Agent 可能在根本没读论文的情况下宣布完成（我们在最小验证中已经看到 steps=0 的情况，虽然那次 PASS 是侥幸）。

 #### 方案

 1. **意图匹配检查**
    - 用 cheap LLM 判断 Agent 的最终输出是否回答了原始任务；输出布尔值 + 简要理由。
 2. **证据引用检查**
    - 要求最终报告中的关键 claim 必须带 `[source: text.md Lxxx-Lyyy]` 格式。
    - 用程序校验这些 source 是否真实存在于论文中。
 3. **输出产物检查**
    - 根据 Plan 中标记为 `required_output` 的任务，检查对应文件是否存在且非空。
    - 例如 `generate_report` 必须产出 `report/report.md`，`generate_pptx` 必须产出 `.pptx`。
 4. **失败反馈**
    - 任何检查失败，都返回结构化 `<verification_result>`，告诉 Agent 具体缺什么（缺引用 / 缺文件 / 答非所问），而不是让它自由发挥。

 #### 涉及文件

 - [`src/paperwise/core/agent_loop.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent_loop.py)：重写 `_looks_complete` / `_verify_completion`。
 - [`src/paperwise/evaluation/graders.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\evaluation\graders.py)：新增 `CitationGrader`。

 ---

 ### 3.5 幻觉检测：从 Judge 打分到“可验证的引用链”

 #### 问题

 - [`HallucinationGrader`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\evaluation\graders.py) 依赖 Judge LLM 判断，成本高、误判多。
 - Agent 本身没有机制保证每句话都有出处。

 #### 方案

 1. **引用强制化**
    - 系统 prompt 增加规则：
      ```
      每提出一个事实性断言，必须在括号内标注 [source: text.md Lxxx-Lyyy]。
      如果无法找到出处，必须写 [source: not reported in paper]。
      ```
 2. **程序化幻觉检测**
    - 提取输出中所有 `[source: text.md Lxxx-Lyyy]`。
    - 校验行号范围是否真实存在；用 fuzzy match 判断引文内容是否确实支持该 claim。
    - 未标注来源的数值/方法/结论直接判为 `major` 幻觉。
 3. **Judge 兜底**
    - 程序化检测通过后，再用 Judge 做高阶语义判断（如“是否曲解了论文含义”）。
    - 这样 Judge 调用次数大幅减少，只在“疑难杂症”时使用。

 #### 涉及文件

 - [`src/paperwise/core/agent.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent.py)：系统 prompt 增加引用规则。
 - [`src/paperwise/evaluation/__init__.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\evaluation\__init__.py)：增强 `HallucinationDetector` 的引用解析能力。
 - [`src/paperwise/evaluation/graders.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\evaluation\graders.py)：新增 `CitationGrader`。

 ---

 ### 3.6 多 Agent 协作与并行工具调用

 #### 问题

 - 当前 Agent 串行执行 tool call；`SpawnSubAgentTool` 注册了但几乎未在主流水中使用。
 - 论文分析天然适合并行：读摘要、读方法、读实验、验证数据可以同时进行。

 #### 方案

 1. **并行工具调用**
    - 在 `LLMClient._parse_response()` 中，如果模型返回多个 `tool_calls`，支持并行执行（受 `TOOL_CALL_LIMITS` 限制）。
 2. **Reader / Verifier / Writer / Reviewer 四角色**
    - `OrchestratorAgent` 读取 Plan，按需 spawn 子 Agent，每个子 Agent 只暴露必要工具。
    - `ReaderAgent`：只读论文，输出事实摘要。
    - `VerifierAgent`：调用 `code_interpreter`，验证数值。
    - `WriterAgent`：写报告。
    - `ReviewerAgent`：读报告 + 论文，输出 findings。
 3. **消息总线 + 文件锁**
    - 子 Agent 通过 `AgentBus` 发送结果；写文件前检查 `locks.json` 避免冲突（`DESIGN-REVIEW.md` P1 已实现文件锁，需接入主流水）。

 #### 涉及文件

 - [`src/paperwise/core/agent.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent.py)：支持并行 tool execution。
 - [`src/paperwise/agents/orchestrator.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\agents\orchestrator.py)：实现四角色编排。
 - [`src/paperwise/tools/locks.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\tools\locks.py)：接入写前锁检查。

 ---

 ### 3.7 Token 与成本估算精确化

 #### 问题

 - `HierarchicalMemory.estimate_token_usage()` 用 `chars // 3`，对中文/代码误差大。
 - 流式响应时 `usage` 标记为 `estimated`，预算判断不准确。

 #### 方案

 1. **按 provider/model 选择 tokenizer**
    - DeepSeek / Kimi / OpenAI 分别用对应 tokenizer；本地可用 `tiktoken` 兜底。
 2. **真实 usage 优先**
    - 非流式调用直接取 API 返回的 `usage`。
    - 流式调用尽量从 SDK 的 `usage` 事件获取；拿不到时再估算。
 3. **成本上限保护**
    - 新增 `PAPERWISE_COST_BUDGET_USD`，当估算成本超过阈值时强制停止并提示用户。

 #### 涉及文件

 - [`src/paperwise/core/llm_client.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\llm_client.py)：tokenizer 路由与 usage 精确化。
 - [`src/paperwise/config/settings.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\config\settings.py)：新增 `cost_budget`。

 ---

 ### 3.8 危险操作的人机回环审批

 #### 问题

 - `bash` / `request_file_access` 等工具一旦通过规则检查就直接执行，缺少二次确认。

 #### 方案

 1. **风险分级 + 审批**
    - LOW：直接执行。
    - MEDIUM：记录日志，默认执行但可审计。
    - HIGH：必须返回 `require_approval` 事件给 UI/API，等待用户确认后再执行。
 2. **审批事件标准化**
    - 事件格式：
      ```json
      {"type": "approval_required", "tool": "bash", "args": {...}, "reason": "..."}
      ```
    - Web UI 显示确认框；CLI/自动化测试可配置 `PAPERWISE_AUTO_APPROVE=1` 跳过。

 #### 涉及文件

 - [`src/paperwise/harness/constraints.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\harness\constraints.py)：增加风险等级判断。
 - [`src/paperwise/api/server.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\api\server.py)：新增 approval 事件接口。

 ---

 ### 3.9 去掉冗余能力

 #### 问题

 - `Agent` 与 `AgentSession` 控制逻辑已合并到 `AgentLoopMixin`，但仍是两个类。
 - 存在多条 PPT 生成路径：`generate_pptx` 工具、skill 流程、fallback slides。
 - CLI 模式按你的需求可以移除。

 #### 方案

 1. **Agent 退化为 AgentSession 的单轮模式**
    - 保留 `AgentSession` 作为唯一对外接口。
    - `Agent.run(task)` 内部可以封装成 `session.chat(task)` 的一次性调用，便于评测复用。
 2. **只保留最好的 PPT 能力**
    - 选择 `nature-paper2ppt` skill 作为主路径，`generate_pptx` 工具作为兜底。
    - 删除 `SlideDeckRenderer` 的重复 fallback 逻辑。
 3. **移除 CLI 入口**
    - 删除 `cli/app.py` 中相关命令，README 改为以 Web / API 为主。

 #### 涉及文件

 - [`src/paperwise/core/agent.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\core\agent.py)：可与 `AgentSession` 合并或作为薄封装。
 - [`src/paperwise/generators/slides.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\generators\slides.py) / [`src/paperwise/generators/pptx_skill.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\generators\pptx_skill.py)：能力合并。
 - [`src/paperwise/cli/app.py`](D:\HuaweiMoveData\Users\13970\Desktop\PaperWise\src\paperwise\cli\app.py)：移除或精简 CLI。

 ---

 ## 四、评测对齐：让简历上的结果站得住

 ### 4.1 向 AgentHarness 等 Benchmark 借鉴什么

 | 外部框架 | 值得借鉴的点 | 在 PaperWise 中的映射 |
 |----------|-------------|----------------------|
 | **AgentHarness** | 端到端 harness、DeepResearch 任务、可复现运行 | 把论文解读包装成固定 task，统一入口运行 |
 | **SWE-bench** | 真实 issue + 隐藏测试 + 确定性判定 | 用 golden answer + 代码执行结果判定，而非关键词 |
 | **GAIA / τ-Bench** | 多轮对话 + 环境状态检查 | `AgentSession` 的多轮 chat 模式 + 输出产物检查 |
 | **AgentBench** | 通用 tool-use / reasoning 维度 | 在 Part A 增加 tool-use 正确性测试 |

 ### 4.2 建议新增的评测维度

 1. **Citation Accuracy**：Agent 引用的行号是否真实、引文是否支持 claim。
 2. **Claim Verification**：用代码验证 Agent 对论文数值/公式的复述是否正确。
 3. **Out-of-Paper Refusal**：当问题超出论文范围时，Agent 是否拒绝回答而不是编造。
 4. **Tool Efficiency**：达到同样正确率下的平均 steps / tokens。
 5. **Regression Tests**：关键能力每次 commit 后必须仍通过，防止退化。

 ### 4.3 评测结果展示建议（简历可用）

 - **安全层**：100% 拦截危险命令 / 路径 / API 泄露（Part A 已接近）。
 - **控制逻辑**：Tier 2 mock-LLM 测试 100% 通过规划/budget/stagnation/judge 路径。
 - **真实论文能力**：在 3-5 篇 3DGS 论文上，完整配置 vs baseline 的 Pass@k、steps、tokens 对比。
 - **幻觉降低**：引入引用强制后，幻觉率从 X% 降到 Y%。
 - **模型无关性**：同任务在 Kimi / DeepSeek 上跑，区分模型能力与 Harness 能力瓶颈。

 ---

 ## 五、落地路线图

 ### P0：让 Agent 稳定可用（2-3 周）

 1. 统一 Agent 入口，去掉 CLI，合并/精简 PPT 路径。
 2. 修复 token 估算，支持真实 usage 与成本预算。
 3. 重写完成判定：意图匹配 + 证据引用检查 + 产物检查。
 4. 引入 `apply_patch` 工具，报告生成走 section-by-section。
 5. 系统 prompt 增加引用规则，新增 `CitationGrader`。
 6. 跑通最小验证：Part A + 单 paper 单 scenario + report_generation。

 **预期结果**：
 - `report_generation` 场景能稳定 PASS。
 - Agent 不再 steps=0 就宣布完成。
 - 评测 token 消耗下降 30% 以上。

 ### P1：提升质量与可对比性（3-4 周）

 1. 论文分块索引 + `search_paper` 检索工具。
 2. Plan DAG 化 + 失败 replan。
 3. Reader / Verifier / Writer / Reviewer 多 Agent 编排（先串行再并行）。
 4. 并行 tool calls + 文件锁接入主流程。
 5. 改进 Judge rubric，按维度打分。
 6. 扩展 golden dataset 到 5-6 篇论文，跑 `full` vs `baseline` 的 k=1 ablation。

 **预期结果**：
 - 完整配置明显优于 baseline（Pass@k 提升 >15%）。
 - 幻觉率显著下降。
 - 能生成可写进简历的对比图表。

 ### P2：工程化与扩展（4-6 周）

 1. 人机回环审批（HIGH 风险工具）。
 2. 图表/公式多模态描述。
 3. 接入 CI：每次 commit 跑 Tier 1 + Tier 2。
 4. 与外部 benchmark 对齐：把 PaperWise task 包装成 AgentHarness 可运行的任务包。
 5. 模型 swap 实验：Kimi / DeepSeek / Claude / GPT-4o 横向对比。

 **预期结果**：
 - 形成“本地快速测试 -> 真实论文验证 -> 多模型对比”的完整链路。
 - 项目具备作为简历亮点展示的条件。

 ---

 ## 六、关键决策记录

 | 决策 | 选择 | 理由 |
 |------|------|------|
 | Plan 生成 | 关键词快速骨架 + LLM 读论文后细化 | 平衡成本与质量 |
 | 上下文 | 检索式 chunk + 分层压缩兜底 | 避免一次性读全文 |
 | 编辑 | `apply_patch` 为主，`write_file` 为辅 | 精确、可验证 |
 | 幻觉 | 引用强制 + 程序校验 + Judge 兜底 | 减少 Judge 调用和误判 |
 | 多 Agent | 先串行角色再并行工具 | 降低调试复杂度 |
 | CLI | 移除 | 聚焦 Web/API/评测 |
 | 入口 | `AgentSession` 唯一化 | 减少重复代码 |

 ---

 ## 七、风险与应对

 | 风险 | 影响 | 应对 |
 |------|------|------|
 | LLM 不遵守引用格式 | 幻觉检测失效 | 程序失败后自动追加提示；多次失败则判定该轮失败 |
 | 论文 chunk 检索不准 | 答案遗漏 | chunk 保留章节标题；混合 dense + sparse + BM25 |
 | 多 Agent 调试复杂 | 开发周期拉长 | 先串行跑通，再逐步并行化 |
 | API 费用超预算 | 无法持续迭代 | 真实 usage 跟踪 + cost budget 硬限制 + 优先 mock-LLM 测试 |
 | 评测指标不敏感 | 无法体现改进 | 每次改一个变量跑 ablation，用 Pass@k + token 双指标 |

 ---

 ## 八、下一步建议

 1. **先不动多 Agent，先修 P0**：完成判定 + apply_patch + 引用规则 + 真实 usage。这是当前评测能稳定通过的前提。
 2. **P0 修完后跑一次完整最小验证**：Part A + feature3dgs 全部 6 scenario（k=1）+ report_generation 专门验证。
 3. **P0 验证通过后再进入 P1**：不要同时做多件事，否则 ablation 变量太多，无法判断哪个改进有效。
 4. **每完成一个 P0/P1 项就更新本文件并打勾**，形成可追溯的改进日志。

 > 本文档应作为活的 design doc，每次重大改动后更新“落地路线图”中的进度。

### 进度日志

 | 日期 | 迭代 | 状态 | 说明 |
 |------|------|------|------|
 | 2026-08-29 | P0 Trace 基础设施 | ✅ 完成 | trace 收集/存储/评估闭环，31 个测试 |
 | 2026-08-29 | P1 Memory → Decision | ✅ 完成 | ContextEngine 进入 Orchestrator，gaps 驱动 Plan |
 | 2026-08-29 | P2 动态 DAG Planner | ✅ 完成 | Capability Registry + DynamicDAGPlanner |
 | 2026-08-30 | P2 收尾：动态主路径 | ✅ 完成 | Dynamic DAG 默认开启；新增动态→可执行适配层（节点受控、组合动态）；静态 Plan 降级为 safety net |
 | 2026-08-30 | P3 经验学习 | ✅ 架构完成 / 🔶 效果待验证 | LearningSignal / FailurePattern / StrategyLibrary；修复 learn_procedure 与 _run_pptx_writer 两个存量 bug；**策略是否真正提升性能待真实任务验证** |
 | 2026-08-30 | 路线调整 | 📌 新方向 | 定位更新为"L3 骨架已成、向 L4 过渡"；插入 **P3.5 Learning Validation**（最高优先）；P4 重定位为 Research Opportunity Engine；新增 P4.5 Retrieval-native Paper Agent；详见实施 Spec 第 1、6 节 |
 | 2026-08-30 | P3.5 Learning Validation | ✅ 机制完成 / 🔶 证据待积累 | Strategy 验证字段（confidence/gain）+ StrategyEvaluator A/B 评测 + outcome 回写闭环；策略选择按验证置信度降权未验证项 |
 | 2026-08-30 | P4 架构设计 | 📐 设计稿落盘 | 编码前完成边界定义与 10 问 Gap Analysis；锁死 4 种机会类型 + 防递归五约束；详见 `docs/OPPORTUNITY_ENGINE_DESIGN.md` |

 > 详细接口与验收标准见 `docs/PaperWise-Roadmap-v0.5-Implementation-Spec.md`。
