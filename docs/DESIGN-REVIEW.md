# PaperWise Agent 设计评审与改进路线图 v0.4.1

> 评审时间：2026-08-12
> 评审对象：PaperWise v0.4.1 的 Agent 架构（ReAct 循环 / Harness / 记忆与 RAG / 多 Agent 编排）

---

## 1. 评审结论摘要

PaperWise 的 Agent 设计已经具备一个生产级系统应有的骨架：

- **真实而非演示**：ReAct 循环、5 层压缩、3 层护栏、真实子 Agent、真实进程管理均已落地
- **记忆与 RAG 管线完整**：Dense/Sparse/RRF/Rerank/HyDE/RAPTOR/GraphRAG 全部可运行
- **可观测性好**：轨迹、状态栏、评估框架一应俱全

主要短板不在"有没有"，而在**闭环是否闭合**：

1. 审核 Agent 的发现不会回流修正报告（验证结果被"看了一眼"就结束）
2. 评估框架没有接入日常测试流程（Pass@k 存在但没人跑 k 次）
3. 对话场景的上下文生命周期管理不完整（本次已修复 token 预算跟踪）
4. 多 Agent 的信息流只有文件交换，没有消息回传和冲突处理

按"先保可靠性、再扩能力、最后产品化"的顺序，改进分为 P0 / P1 / P2 三档。

---

## 2. P0 — 可靠性闭环（建议优先实施）

### 2.1 审核结果回流：revise-until-pass 循环 — ✅ 已实现（2026-08-12）

**原现状**：`agents/orchestrator.py` 的 Pipeline 是 Analyst → Writer → Reviewer
串行执行。Reviewer 把发现写入 `review/findings.md` 后流程就结束了，
报告不会根据审核意见修改；HallucinationDetector 的"一票否决"也没有接入主流程。

**已实现**：
1. `parse_findings()` 解析 findings.md（verdict + 严重度统计）
2. `get_revision_spec()` 修订 Agent（最小改动原则 + 修订日志）
3. `run_paper_analysis()` 闭环：Analyst → Writer → Reviewer → Revision（≤3 轮）
4. 审查记录落盘 `review/review_record.json`
5. CLI 新增 `paperwise pipeline <pdf>` 一键执行

**涉及文件**：`agents/orchestrator.py`、`cli/app.py`

**收益**：把"生成质量"从概率问题变成工程约束，幻觉报告不会流向用户。

### 2.2 评估落地日常化 — ✅ 已实现（2026-08-12）

**现状**：`evaluation/benchmark.py` 有完整的 PassKEvaluator，但
`tests/run_agent_tests.py` 仍是单次运行，没有 k 次重复、没有 Pass@k/Pass^k 输出、
没有基准数据集（目前只有 1 篇测试论文、5 个场景）。

**已实现**：
1. `run_agent_tests.py` 支持 `--k N` 重复 + 每场景/总体的 Pass@k、Pass^k
2. 结果 JSON 增加 `pass_at_k` / `overall` 字段，落盘 `workspace/test_runs/`
3. 新增 CV 领域测试论文（SegNet-Lite）与 ground truth，`--paper cv` 可选

**涉及文件**：`tests/run_agent_tests.py`、`tests/test_data/`

**待办**：接入 CI，每次改动跑一遍能力基线（k=3 起步）。

### 2.3 Web 会话恢复接入 — ✅ 已实现（2026-08-12）

**现状**：`AgentSession.load()` 已实现，但 `api/server.py` 的 `sessions` 是内存字典，
服务重启后所有会话丢失。

**已实现**：`GET /api/sessions` 列出历史会话；`_ensure_session` 优先从磁盘
惰性恢复；前端侧边栏展示历史会话并可点击恢复（含消息历史）。
顺带修复了 `AgentSession.session_id` 与 API sid 不一致导致的恢复失效 bug。

### 2.4 记忆整合自动化 — ✅ 已实现（2026-08-12）

**现状**：记忆写入（LLM 提取 + 去重 + 冲突检测）完整，但缺少周期性的
压缩/整合/遗忘流程——Advanced JSON Cards 只增不减。

**已实现**：`UserMemory.consolidate()`（清理过期 → 合并重复 → 类别规模控制）
+ `maybe_consolidate()`（7 天间隔自动跳过）；Web/CLI 启动时自动触发。

---

## 3. P1 — 能力扩展

### 3.1 真正的 Agent 间消息总线 — ✅ 已实现（2026-08-12）

**现状**：`send_message_to_agent` 只是触发回调打印，消息不会真正到达其他 Agent。

**已实现**：`core/bus.py` AgentBus（register/send/receive/pending），
`send_message_to_agent` 真实投递到目标邮箱，子 Agent 通过
`receive_message` 工具读取；orchestrator 与 spawn_subagent 均自动注册邮箱。
文件系统仍作为产物交换通道，消息只承载控制信号。

### 3.2 主动调度 — ⏳ 待实现

**现状**：`set_timer` / `monitor_shell` 是 Agent 侧的"一次性"工具，
没有系统级守护把它们转成持续的 Agent 输入。

**方案**：`Scheduler` 后台任务（asyncio）持有定时器与监控项，
到期时向活跃 Session 注入事件消息；这为"论文更新提醒、引用告警"等
主动服务（spec S6.4）打基础。

### 3.3 工具自发现 — ✅ 已实现（2026-08-12）

**现状**：`ToolRegistry.get_catalog()` 只输出 200 token 目录，
Agent 无法按需获取某工具的完整 schema。

**已实现**：`discover_tool(query)` 工具——按关键词返回匹配工具的
完整定义（参数示例 + 边界说明），让 Agent 在任务中自行发现新能力。

### 3.4 RAPTOR / GraphRAG 索引持久化 — ✅ 已实现（2026-08-12）

**现状**：每次会话 `build_raptor_tree()` / `build_knowledge_graph()` 全量重建，
LLM 调用成本高，且只覆盖前 10 篇文档的前 2000 字符。

**已实现**：文档集签名缓存（sqlite），文档未变化时直接恢复摘要节点与
知识图谱，不再重复调用 LLM；顺带修复了 RAPTOR 聚类的两个潜伏 bug
（`_tokenize` 缺失、`asyncio.get_event_loop` 在 `asyncio.run` 后抛错）。

### 3.5 Windows 命令适配层 — ✅ 已实现（2026-08-12）

**现状**：bash 工具已增加 cmd 回退与 9009 提示（本次修复），
但 Agent 仍可能写出 `python3`、`ls | grep` 等跨平台不兼容命令。

**已实现**：`exec_tools.py` 命令预处理层——维护常见别名表
（python3→python、ls→dir 等），并给 Agent 的 bash 工具描述注入
"当前平台：Windows，避免使用 Unix 专属命令"的环境提示。

### 3.6 提示注入的 LLM 级审查 — ⏳ 待实现

**现状**：规则级检测（正则）覆盖常见注入模式，但对
"间接注入"（论文正文里委婉引导 Agent 的行为）无能为力。

**方案**：对高风险输入（论文全文、外部网页）增加轻量 LLM 分类器做
第二道审查（Sidecar）；规则优先拦截，LLM 兜底判断。

### 3.7 多 Agent 文件冲突检测 — ✅ 已实现（2026-08-12）

**现状**：并行 Worker 共享文件系统，无锁、无冲突检测。

**已实现**：文件写入前检查目标是否被其他 Agent 声明占用（`.locks.json`），
冲突时返回"文件被占用"并建议换名；为 `write_file` / `edit_file` 增加锁检查。
锁带 TTL，超时后可抢占（避免死锁）。

---

## 4. P2 — 产品化与体验

1. **Web UI 增强**：PPT 逐页预览/编辑、历史会话列表与恢复入口、记忆管理页
2. **arXiv 摄入**：上传页支持粘贴 arXiv URL，服务端下载 + 解析
3. **主动论文推荐**：落地 spec 中的 `daily_arxiv_check`，按用户研究方向推送论文
4. **用户系统/多租户**：用户画像与记忆按 user_id 隔离（数据模型已支持）
5. **多模态深度**：图表用视觉模型（如 Qwen-VL/CLIP）生成真正语义化描述并嵌入
6. **评估 Dashboard**：把 Pass@k / 幻觉率 / Rubric 均分可视化，追踪版本间变化

---

## 5. 设计原则自查

### 5.1 反馈闭环完整性

一个 Agent 系统是否可靠，取决于"验证结果是否必然影响产出"。
当前 reviewer 只输出不回流是最大的结构性缺口（P0-2.1）。

### 5.2 上下文生命周期

上下文不是"用完即弃"也不是"只增不改"：状态栏等瞬时信息应每轮重建
（本次已修复）；对话历史应随 token 预算自动压缩（token 跟踪已修复）；
真正需要长期保留的只应进入记忆层。

### 5.3 写路径与读路径分离

记忆的写（LLM 提取）已有保障，但缺"整合/遗忘"的写后治理；
RAG 的读（检索管线）很完整，但索引的写（构建/更新）成本过高。
两边的写路径都需要调度化。

### 5.4 信息流设计

"不共享上下文 + 共享文件系统"是正确的主线（隔离优于压缩），
但需要补充控制流（消息）与状态流（locks/progress）才能真正协作。

### 5.5 进化闭环的安全

EvolutionEngine 能"改自己"，但没有回归测试和回滚——这在生产中是危险的。
任何候选更新必须先过能力基线测试（P0-2.2 的副产品），再灰度部署。

---

## 6. 建议落地顺序

| 阶段 | 内容 | 状态 |
|------|------|------|
| A（可靠性） | revise-until-pass、评估落地、Web 会话恢复、记忆整合 | ✅ 已完成 |
| B（能力） | 消息总线、工具自发现、索引持久化、Windows 适配、文件锁 | ✅ 已完成 |
| B+（能力） | 主动调度器、LLM Sidecar 审查 | ⏳ 下一轮 |
| C（产品） | UI 增强、arXiv、用户系统、评估 Dashboard | ⏳ 后续 |

> 与 `docs/AUDIT.md` 的遗留项一一对应：P0 覆盖 HIGH-1/2/8/14，
> P1 覆盖 HIGH-3/4/5/6/7/10/12/13，P2 覆盖 MEDIUM/LOW 多数条目。
