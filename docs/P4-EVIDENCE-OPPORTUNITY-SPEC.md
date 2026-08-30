# P4 — Evidence-driven Research Opportunity

## Goal

Move opportunity detection from rule-only reviewer findings to
`Evidence -> Reasoning -> Opportunity`. Evidence Packs created in P4.5 now feed
opportunity detection directly.

## Design

`EvidenceOpportunityBridge` has two responsibilities:

1. **Derive candidates**
   - low-recall Evidence Pack -> `MissingEvidence`
   - snippets from two papers with shared topic tokens ->
     `MethodComplementarity`
2. **Ground existing candidates**
   - Match opportunity entities to snippets.
   - Attach up to three `EvidenceRef` objects with snippet citations.

`OpportunityDetector.detect(..., evidence_packs=[...])` remains bounded by the
P4 safety constraints: depth limit, run budget, confidence threshold, dedup,
and pending-only proactive behavior.

## Acceptance

- Evidence-backed missing-evidence opportunities are generated only when the
  pack is marked low-recall.
- Cross-paper evidence can produce method-complementarity opportunities with
  citations from both papers.
- Existing opportunities receive additional grounded evidence.
- All existing P4 detector tests remain green.
