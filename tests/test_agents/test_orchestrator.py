"""编排器测试 — 审核 findings 解析 + 修订 Agent 规格"""

from paperwise.agents.orchestrator import (
    parse_findings,
    PaperAnalysisPipeline,
)


def _write_findings(path, verdict: str, flagged_body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""## Review Summary
- Total claims checked: 12
- Verified: 10
- Flagged: 2
- Hallucinations: 1

## Flagged Claims
{flagged_body}

## Missing Aspects
- Some experiments not discussed

## Verdict
- {verdict}: ...
""", encoding="utf-8")


def test_parse_findings_pass(tmp_path):
    findings = tmp_path / "review" / "findings.md"
    _write_findings(findings, "PASS", "- Claim A verified (minor)\n")
    result = parse_findings(findings)
    assert result["verdict"] == "PASS"
    assert result["critical"] == 0
    assert result["summary"].get("Total claims checked") == 12


def test_parse_findings_reject_with_severity_counts(tmp_path):
    findings = tmp_path / "review" / "findings.md"
    _write_findings(
        findings, "REJECT",
        "- Claim 1: fabricated number, severity: critical\n"
        "- Claim 2: wrong method name, severity: major\n"
        "- Claim 3: missing citation, severity: minor\n",
    )
    result = parse_findings(findings)
    assert result["verdict"] == "REJECT"
    assert result["critical"] >= 1
    assert result["major"] >= 1
    assert result["minor"] >= 1


def test_revision_spec_allows_edit_tools(tmp_path):
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    findings = paper_dir / "review" / "findings.md"
    _write_findings(findings, "REVISE", "- Claim: wrong number, severity: major\n")

    spec = PaperAnalysisPipeline.get_revision_spec(paper_dir, findings)

    assert spec.name == "revision_writer"
    assert "write_file" in spec.allowed_tools
    assert "edit_file" in spec.allowed_tools
    assert "findings.md" in spec.task_template
