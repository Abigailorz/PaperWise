# P7 — Research Decision Layer

P6 completed the first controlled research loop: Opportunity -> Action -> DAG ->
State -> Graph -> Narrative. P7 does not expand agent count or introduce a new
memory type. It closes the remaining decision-layer gaps on top of P6.

## Goal

Make PaperWise maintain durable research decisions instead of only recording a
single run:

```text
Opportunity -> ResearchQuestion -> ResearchAction
    -> Evidence -> Narrative -> Graph -> next planning round
```

## Scope

### 1. ResearchQuestion as the durable decision object

An Opportunity is a discovered signal. A ResearchQuestion is the stable
research decision that survives across runs. P7 adds a controlled object with:

```python
ResearchQuestion:
    question_id      # stable hash of the normalized question
    question
    status           # open | active | answered | parked
    importance
    source_opportunities
    evidence_refs
    related_hypotheses
    created_at / updated_at
```

Rules:

- One normalized question is represented by one stable ID.
- The same opportunity signal must not create duplicate questions.
- Question creation and lifecycle changes enter `ResearchState` only through
  typed `StateEvent`s.
- Questions are persisted with `ResearchState` and merged into `ResearchGraph`.

### 2. Action execution becomes fully event-auditable

P6 already maps opportunities to bounded `ResearchAction`s. P7 makes the
orchestrator use the formal action planner instead of legacy suggested-action
strings.

New state events:

| Event | Effect |
|-------|--------|
| `ACTION_PLANNED` | Append bounded actions to `pending_actions` |
| `ACTION_STARTED` | Mark action running and opportunity acting |
| `ACTION_COMPLETED` | Move action to completed/failed and update opportunity |

Execution constraints remain unchanged:

- Max three planned actions per round.
- Only LOW-risk actions auto-execute.
- MEDIUM/HIGH risk actions wait for explicit user approval.
- Action DAG depth remains one; no cascading opportunity detection.

### 3. Narrative becomes the output contract

Report already consumes the narrative. PPT must do the same. The PPT writer and
PPT generation tool read `research_narrative.json` when present and use it as
the primary source of findings, hypotheses, opportunities, and evidence.

`ResearchNarrative` is extended with:

```python
questions_summary  # durable research questions and status
actions_summary    # bounded actions and lifecycle status
```

PPT must not infer opportunities or mutate research state.

### 4. Context retrieval remains bounded

Context assembly gets an explicit policy with node-aware top-k and a hard prompt
budget. It may never default to injecting the full `text.md`.

Defaults:

| Node family | Strategy |
|-------------|----------|
| Evidence / analysis | Current paper snippets, procedures, no preference episodes |
| Report / PPT | Narrative plus verified facts; no raw paper dump |
| Planning | Open questions, gaps, bounded procedures |

## Non-goals

- No new agent role.
- No external literature crawler.
- No automatic experiment execution.
- No policy optimizer training loop.
- No replacement of P0-P6 verified behavior.

## Acceptance criteria

1. Equivalent opportunities converge to one stable `ResearchQuestion`.
2. Question lifecycle is event-driven and serializable.
3. Enabled opportunity actions are planned from `ResearchAction`s, persisted in
   `pending_actions`, and audited into `completed_actions` after execution.
4. Pending actions are reused on the next run instead of being silently lost.
5. Research graph contains stable question nodes linked to opportunities.
6. Narrative includes questions and actions; PPT prompts consume narrative.
7. Context policy is explicit, bounded, and node-aware.
8. Existing unit tests and the controlled integration suite pass.
