import json
import pathlib
import time

project = pathlib.Path(__file__).resolve().parent
src = project / "workspace" / "test_runs" / "test_results_1786956185.json"
data = json.loads(src.read_text(encoding="utf-8"))

bench = project / "workspace" / "benchmarks"
bench.mkdir(parents=True, exist_ok=True)
bp = bench / f"agent_eval_{int(time.time())}.json"
bp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
(bench / "latest_agent.json").write_text(
    json.dumps({
        "latest": str(bp.relative_to(project)),
        "report": data,
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"copied agent result to {bp}")
