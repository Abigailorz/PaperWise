# P8 — Research Loop: Question-Driven Behavior

P7 made research state durable. P8 makes research state drive behavior.
No new modules, no new agents: the existing ResearchQuestion, Opportunity,
ResearchAction, and Dynamic DAG are composed into a repeating loop.

```text
Research Graph
    -> Research Questions (prioritized)
    -> Action Planning (from question's source opportunities)
    -> Dynamic DAG execution
    -> Evidence / Findings
    -> Outcome Evaluation
    -> Question status update
    -> (next round)
```

## Three core capabilities

### 1. Question Prioritization

When multiple ResearchQuestions exist, the system must decide which to work on
now. A deterministic scorer ranks questions:

```
score = importance
      x (1 + avg(source opportunity confidence))
      x recency_decay(hours since created)
```

Rules:

- Purely deterministic; same input -> same ranking. No LLM involvement.
- Top `max_questions` (default 2) are marked `active`; the rest stay `open`.
- Active questions drive this round's action planning.

### 2. Question-driven Action Planning

Actions are planned from the source opportunities of prioritized questions,
not from all pending opportunities. This makes the loop question-centric:

```text
ResearchQuestion (active)
    -> source_opportunities
    -> existing OPPORTUNITY_TO_ACTIONS mapping
    -> bounded ResearchActions (max 3/round, LOW-risk only auto-execute)
```

When a question's actions start, the question transitions `open -> active`.

### 3. Outcome Evaluation

After actions complete, each targeted question is evaluated. Five outcomes:

| Outcome | Condition |
|---------|-----------|
| `resolved` | All actions succeeded AND new evidence refs were added |
| `partially_resolved` | Actions succeeded but evidence set unchanged |
| `unresolved` | Any action failed |
| `contradicted` | A CONTRADICTION opportunity is linked to this question |
| `new_question` | Evaluation spawned a follow-up question |

Evaluation is recorded via a typed `QUESTION_EVALUATED` event that updates the
question's `status`, `outcome`, and `evaluation_count`. Questions can be
re-evaluated across rounds; `evaluation_count` tracks this.

## Safety constraints (unchanged from P6/P7)

- Max 3 actions per round.
- Only LOW-risk actions auto-execute; MEDIUM/HIGH wait for approval.
- Action DAG depth=1; no cascading opportunity detection.
- All state mutations via typed StateEvents.

## Non-goals

- No cross-paper retrieval (P9).
- No strategy benchmarking (P10).
- No hypothesis-driven experiment execution (P11).
- No new agent roles or memory types.

## Acceptance criteria

1. `QuestionPrioritizer.prioritize()` is deterministic and bounded.
2. Active questions drive action planning; non-prioritized questions stay open.
3. Question transitions to `active` when its actions start.
4. `OutcomeEvaluator` returns the correct outcome for each of the 5 conditions.
5. `QUESTION_EVALUATED` event updates status, outcome, and evaluation_count.
6. Orchestrator runs the full loop when `enable_opportunity_actions=True`.
7. Full loop is visible in `orchestration_status.json` as `research_loop`.
8. Existing controlled tests pass.
