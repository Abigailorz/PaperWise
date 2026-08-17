import json
import pathlib

project = pathlib.Path(__file__).resolve().parent

agent_latest = json.loads((project / "workspace" / "benchmarks" / "latest_agent.json").read_text(encoding="utf-8"))
agent_report = agent_latest["report"]
agent_baseline = {
    "model": agent_report["model"],
    "success_rate": agent_report["overall"]["success_rate"],
    "pass_at_k": agent_report["overall"]["pass_at_k"],
    "avg_score": agent_report["avg_score"],
    "avg_steps": agent_report["avg_steps"],
    "avg_duration": agent_report["avg_duration"],
    "total_errors": agent_report["total_errors"],
}
(project / "tests" / "baselines" / "agent_baseline.json").write_text(
    json.dumps(agent_baseline, ensure_ascii=False, indent=2), encoding="utf-8"
)

rag_latest = json.loads((project / "workspace" / "benchmarks" / "latest_rag.json").read_text(encoding="utf-8"))
rag_report = rag_latest["report"]
rag_baseline = {
    "mode": rag_report["mode"],
    "recall_at_3": rag_report["overall"]["recall_at_3"],
    "precision_at_3": rag_report["overall"]["precision_at_3"],
    "mrr": rag_report["overall"]["mrr"],
}
(project / "tests" / "baselines" / "rag_baseline.json").write_text(
    json.dumps(rag_baseline, ensure_ascii=False, indent=2), encoding="utf-8"
)

print("baselines updated")
print(f"  agent: {agent_baseline}")
print(f"  rag: {rag_baseline}")
