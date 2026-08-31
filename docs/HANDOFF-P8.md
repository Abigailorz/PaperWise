# PaperWise 交接文档 — P8 完成状态与新窗口工作指引

> 生成时间：2026-08-31 · 用途：新会话/新窗口接手时恢复完整上下文。
> 读完本文即可继续工作，无需翻阅旧对话。本文依据对代码库的逐项核实写成，
> 已修正此前交接摘要中的过时信息（见 §2.1）。

## 1. 项目概况

- **仓库**：`C:\Users\13970\Desktop\PaperWise`（唯一真实仓库；Codex 沙箱里的
  `Documents\Codex\...` 目录只是临时工作区，与项目无关）
- **分支**：`main`，HEAD = `05b0a6b`，工作树干净，已与 `origin/main` 同步
- **GitHub**：https://github.com/Abigailorz/PaperWise.git
- **运行环境**：Windows · Python 3.10 · 解释器 `.venv\Scripts\python.exe`
- **全量回归**：`pytest tests -q --ignore=tests/test_integration -p no:cacheprovider`
  （05b0a6b 上 291/291 通过）
- **LLM 配置**（本地 `.env`，密钥永不入库）：`PAPERWISE_DEFAULT_MODEL=glm-5.3-flash`、
  `OPENAI_BASE_URL=https://opencode.ai/zen/go/v1`、`OPENAI_API_KEY` 已配置
- **前端**：`http://localhost:8000/`（服务器可能仍在后台运行；重启前先杀旧进程，
  否则 Windows 报端口占用 10048）

## 2. 演进进度总览

P0–P8 全部完成并推送：

| 阶段 | 内容 | 提交 | 状态 |
|---|---|---|---|
| P0–P5 | 解析 / RAG / 报告 / PPT / 动态 DAG 主路径 | （早期提交） | ✅ |
| P6 | 全量 DAG LangSplat 评测 PASS | `d530280` | ✅ |
| P7 | Research Question 决策层 + 可审计行动 | `c1be58f` | ✅ |
| P8 | Research Loop：问题驱动行动 + 结果评估 | `d515bea` | ✅ |
| P8 修复 | 评估读 state 行动副本 + 行动前快照 evidence | `96ceddc` | ✅ |
| 前端修复 | 推理内容泄漏为正文的 bug（reasoning_delta 独立事件） | `05b0a6b` | ✅ |

### 2.1 P8 四个收尾缺口 — 已全部完成（重要更正）

此前交接摘要称以下 4 项"未修复"，本次会话已逐项核实源码：**全部已实现，
且包含在已推送的提交中**。新窗口不要重复实现。

1. **`new_question` 派生跟进问题** ✅ — `orchestration/orchestrator.py`（约 684 行起）：
   outcome == NEW_QUESTION 时确定性创建 `跟进验证：{原问题}`
   （importance × 0.8，继承 source_opportunities），经
   `StateEventType.RESEARCH_QUESTION_CREATED` 事件写入 state。
2. **`research_loop` 状态键** ✅ — `orchestration_status.json` 写入 `research_loop`
   汇总（active_questions / actions_planned / actions_executed /
   spawned_questions / evaluations），满足 P8 spec 验收标准 7。
3. **Narrative 输出 outcome** ✅ — `generators/narrative.py` 的
   `questions_summary` 已含 `outcome` 与 `evaluation_count` 字段。
4. **评测脚本行动开关** ✅ — `workspace/langsplat/eval_langsplat.py` 支持
   `--actions` 参数与 `P8_ACTIONS=1` 环境变量。
   **注意**：`workspace/` 被 gitignore，此改动仅存在于本地磁盘。

## 3. 架构速览（接手必读）

主链路：

```text
任务 → 路由 → 动态 DAG → Agent 执行 → Evidence/RAG → Review
     → Opportunity → Research Question → Action → Research Graph
     → Narrative → Report/PPT → Learning
```

P8 Research Loop（本轮核心）：

```text
Research Graph → Research Questions → Question Prioritization
     → Action Planning（确定性映射到 Dynamic DAG）
     → Evidence / RAG / Analysis
     → Outcome Evaluation（确定性，5 种 outcome）
     → 更新 Research Graph → 重新评估问题 → ↺
```

五种 outcome（`memory/outcome_evaluator.py`，纯确定性规则，LLM 不参与打分）：

| outcome | 判定条件 | 问题状态迁移 |
|---|---|---|
| resolved | 行动全部成功且新增 evidence | answered |
| partially_resolved | 行动成功但 evidence 无变化 | active |
| unresolved | 任一行动失败 | open |
| contradicted | 关联 contradiction 机会且无新证据 | active |
| new_question | 关联 contradiction 且有新证据 | active + 派生跟进问题 |

### 硬性架构约束（任何改动不得违反）

- **受控节点**：`EXECUTABLE_NODE_IDS` frozenset — LLM 不能发明节点。
- **确定性行动映射**：`OPPORTUNITY_TO_ACTIONS` dict — 固定 action 类型，
  无未受控 LLM 行动。
- **事件驱动状态**：一切变更经 `state.apply(StateEvent)`；事件类型见
  `memory/state_updater.py` 的 `StateEventType`（含 RESEARCH_QUESTION_CREATED、
  QUESTION_STATUS_CHANGED、QUESTION_EVALUATED，payload 格式见各 `_on_*` handler）。
- **稳定 ID**：`make_question_id()` = SHA-1(normalized question)[:12]。
- **P8 fallback**：无活跃问题时，所有高置信机会可行动（保持 P6 行为）。
- **防递归**：Budget(3/round) + Depth(1) + TTL(72h) +
  Approval(LOW=auto / MEDIUM=opt-in / HIGH=mandatory)。

## 4. 测试与评测状态

- P8 目标测试：`tests/test_memory/test_p8_loop.py` 10/10 通过
- P7+P8 合跑：13/13 通过
- 全量回归：291/291 通过（05b0a6b，`--ignore=tests/test_integration`）
- 全量评测（LangSplat + glm-5.3-flash，不含行动）：已通过，
  research loop 端到端验证（1 问题 → 2 行动 → partially_resolved）
- **尚未做**：开启行动的 P8 全量评测（见 §5 待办 1）

## 5. 待办事项（新窗口按序执行）

1. **P8 全量评测（含行动）**：
   `P8_ACTIONS=1 python workspace/langsplat/eval_langsplat.py --actions`
   （模型 glm-5.3-flash，走 .env 配置）。确认 research loop 在真实论文上
   完整跑通 question → action → outcome → 跟进问题。
2. **前端人工复测**（用户上轮报告的 bug 验证）：
   - 现象：上传 PDF 解析成功，点"生成解读报告"报
     `Unexpected token 'I', "Internal S..." is not valid JSON`。
   - 根因已修复并推送（05b0a6b）：llm_client 流式路径曾把
     `reasoning_content` 误发为 `text_delta` 混入正文；现改为独立
     `reasoning_delta` 事件，前端只进思考面板。
   - 复测步骤：杀掉旧服务进程 → 重启 → 上传 PDF → 生成报告，
     确认无 JSON 错误、推理内容只出现在思考面板。
3. **P9 规划**（下一大版本）：Cross-Paper Research + Research Graph
   Intelligence — 从单篇分析升级为研究领域分析（跨论文证据、方法关系、
   矛盾与研究空白）。按用户流程：先写 spec 规划，再实现。
4. 若本轮有代码改动：先跑定向测试，**全部改完后只跑一次**全量回归
   （用户明确要求尽量少跑全量）。

## 6. 用户偏好与协作约定

- 中文交流；代码与文档中英文皆可。
- 尽量少跑全量回归：完整改完（含 P 级演进全部就位）后再统一跑一次。
- 每个大版本流程：**先规划（spec）→ 实现 → 测试 → 提交 → 推送**。
- 全量评测用 glm-5.3-flash（.env 已配好，无需改配置）。
- 无未受控 LLM 行动；固定 action 类型；事件驱动 state。
- 提交信息风格参照 `git log`：`P8: ...` / `fix: ...` 简洁英文前缀。

## 7. 环境注意事项（踩过的坑）

- **沙箱内 pytest 写文件可能 Errno 13** → 用 `require_escalated` 跑测试。
- **git commit/push 可能因 `.git` DENY ACE 失败** → 用 `require_escalated`。
- **服务器端口占用 10048** → 重启前先杀旧 python 进程。
- **`workspace/` 被 gitignore** → eval 脚本改动仅本地，换机器需重做。
- **API 密钥只在本地 `.env`** → 永远不要写进文档或提交。

## 8. 关键文件索引

| 文件 | 职责 |
|---|---|
| `src/paperwise/orchestration/orchestrator.py` | 主编排 + P8 loop（~684 行起 new_question 派生与 research_loop 状态写入） |
| `src/paperwise/memory/outcome_evaluator.py` | 5 种 outcome 确定性评估 |
| `src/paperwise/memory/question_prioritizer.py` | 问题优先级打分（确定性） |
| `src/paperwise/memory/research_question.py` | ResearchQuestion 模型 + 稳定 ID |
| `src/paperwise/memory/research_state.py` / `state_updater.py` | 事件驱动状态与 StateEventType |
| `src/paperwise/opportunity/` | 机会检测 + `OPPORTUNITY_TO_ACTIONS` 映射 |
| `src/paperwise/generators/narrative.py` | 叙事摘要（含 question outcome） |
| `src/paperwise/core/llm_client.py` | LLM 客户端（reasoning_delta 事件） |
| `docs/P8-RESEARCH-LOOP-SPEC.md` | P8 规范（含验收标准） |
| `tests/test_memory/test_p8_loop.py` | P8 测试（10 个） |
| `tests/test_memory/test_p7_decision.py` | P7 测试（3 个） |
| `workspace/langsplat/eval_langsplat.py` | LangSplat 全量评测脚本（gitignored，支持 `--actions`） |

## 9. 后续路线图（用户已确认的方向）

```text
P8  Research Loop            ✅ 已完成
P9  Cross-Paper Research     ← 下一站：跨论文证据、方法关系、矛盾与空白
P10 Self-Improving Agent     StrategyLibrary 用长期 benchmark 证明经验价值
P11 Research-native Agent    提出假设、设计验证方案、组织实验
```

原则：P0–P8 架构已闭环，**不再横向堆模块**；后续以"研究智能闭环验证"为主线。
