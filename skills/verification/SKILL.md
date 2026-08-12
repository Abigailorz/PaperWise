---
name: verification
description: >
  Use when verifying the accuracy and quality of generated reports.
  Covers fact-checking against source paper, hallucination detection,
  and rubric-based quality scoring.
  Don't use for: initial report generation, casual proofreading.
---

# Report Verification Workflow

## Fact-Checking Process
1. Extract ALL factual claims from the report
2. For each claim, search the paper text for evidence
3. Flag claims that cannot be verified
4. Categorize: NUMERICAL / METHODOLOGICAL / FINDING

## Hallucination Types
- NUMERICAL: Made-up numbers, percentages, metrics
- METHODOLOGICAL: Fabricated methods, architectures
- FINDING: Fabricated conclusions, contributions

## Rubric Dimensions
1. Accuracy: Faithfulness to original paper
2. Completeness: Coverage of key aspects
3. Insight Depth: Beyond surface summary
4. Evidence Quality: Proper citations
5. Readability: Clarity and organization

## Severity Levels
- critical: Core claim fabricated → VETO
- major: Significant error → Requires revision
- minor: Small inaccuracy → Note for improvement
