# 能力基线结果

本目录保存 PaperWise 的能力基线结果，用于回归检测。

## 文件命名

- `agent_baseline.json`：由 `python tests/run_agent_tests.py --k 1 --paper simple` 生成
- `rag_baseline.json`：由 `python tests/run_rag_benchmark.py` 生成

## 更新基线

```bash
python tests/run_agent_tests.py --k 1 --paper simple
cp workspace/benchmarks/latest_agent.json tests/baselines/agent_baseline.json

python tests/run_rag_benchmark.py
cp workspace/benchmarks/latest_rag.json tests/baselines/rag_baseline.json
```

## 回归检测

```bash
python tests/compare_baseline.py --current workspace/benchmarks/latest_agent.json --baseline tests/baselines/agent_baseline.json
python tests/compare_baseline.py --current workspace/benchmarks/latest_rag.json --baseline tests/baselines/rag_baseline.json
```
