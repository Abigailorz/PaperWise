# P5 — Research State / Research Graph

## Goal

Upgrade PaperWise from task-local analysis memory to a durable,
evidence-linked research state. The graph records what is known, where it came
from, what remains unresolved, and what should be tried next.

## Entities

`User`, `Project`, `ResearchQuestion`, `Paper`, `Method`, `Claim`,
`Evidence`, `Dataset`, `Experiment`, `Finding`, `Opportunity`, and
`Hypothesis`.

## Core relations

```text
User -owns-> Project
Project -studies-> ResearchQuestion
ResearchQuestion -related_to-> Paper
ResearchQuestion -uses-> Method
Paper -proposes-> Method
Paper/Claim/Finding -supported_by-> Evidence
Paper -evaluates-> Experiment
ResearchQuestion -has_gap|contradicts|complements-> Opportunity
ResearchQuestion -suggests_hypothesis-> Hypothesis
```

## Runtime behavior

After the complex DAG completes, `ResearchGraphBuilder` combines:

- `ResearchState.findings`, `next_steps`, and opportunities;
- `evidence/evidence_pack.json`;
- `facts.json` claims, method, datasets, and experiments.

The run graph is written to `workspace/{paper}/research_graph.json` and merged
into the persistent user graph under `workspace/.research_graph/`. The status
file records graph statistics.

## API

`GET /api/research-graph?paper_dir=...` returns a paper graph; omitting
`paper_dir` returns the user-level graph.

## Acceptance

- Stable hashed IDs allow merge without duplicate concepts.
- Evidence nodes carry line/figure/table citations.
- Opportunity edges preserve evidence IDs and typed relation semantics.
- Repeated runs merge into one graph rather than overwrite it.
