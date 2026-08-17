# CLAUDE.md — PaperWise 项目指令文件

## 项目概述
PaperWise 是一个 AI Agent 驱动的学术论文智能解读系统。输入 PDF 论文，自动生成深度解读报告。

## 构建与测试命令
- 安装依赖: `pip install -e .`
- 运行 CLI: `python -m paperwise.cli.app --help`
- 运行测试: `pytest tests/ -v`
- 单文件测试: `pytest tests/test_harness/test_constraints.py -v`
- 覆盖率: `pytest tests/ --cov=paperwise --cov-report=term`
- Agent 能力测试: `python tests/run_agent_tests.py`
- MCP 传输集成脚本: `python tests/test_mcp_transport.py`

## 代码风格
- Python 3.10+, 使用 async/await
- 类型注解使用 typing 模块（非 3.12 新语法）
- Pydantic v2 进行数据验证
- 所有工具实现 BaseTool 抽象类
- Harness 钩子命名: pre_llm / post_llm / pre_tool / post_tool

## 核心架构原则（来自《深入理解 AI Agent》）
- Agent = LLM + 上下文 + 工具
- 保持简单：先直接 API 调用，不过早引入框架
- 设计好 ACI：工具描述写清楚"何时用"和"不要用于"
- KV Cache 友好：静态前缀（system prompt + tools）固定不变
- Agent 状态栏：用代码（非 LLM）统计，放在上下文末尾
- 约束优于信任：高风险操作默认禁止，显式授权

## 不能做的事
- 不要改动 workspace/ 目录的内容（运行时数据）
- 不要提交 .env 文件
- 不要在工具间静默转换参数（参数传递保真性原则）

## 文档一致性
- 修改代码后同步更新 README.md / docs/ARCHITECTURE.md / docs/AUDIT.md
- 版本号统一维护（pyproject.toml / api/server.py / mcp/server.py / README 标题）
- 工具数量、行数等统计以 `src/paperwise/` 实际代码为准，不手工编造


## 路径与平台约定

- 文档和脚本中不得出现用户名、桌面路径或平台特定的绝对路径。
- 使用相对路径（如 `cd PaperWise`）或从 `__file__` / `PAPERWISE_WORKSPACE` 推导。
- CLI 示例假设工作目录就是项目根目录。
