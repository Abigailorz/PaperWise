# P6 — Research Loop Foundation (Phase A)

Based on PaperWise Agent Evolution Technical Specification v0.6, combined with
P0-P5 existing implementations. Focus: Phase A — Opportunity to Action to DAG
to State closed loop.

## Goal

Evolve PaperWise from "State-aware Paper Analysis Agent" to
"Evidence-driven Research Agent". Core loop:

```text
Research State
    -> Dynamic DAG
    -> Evidence Retrieval
    -> Analysis / Verification
    -> Findings
    -> Opportunity Detection
    -> Action Planner
    -> Dynamic DAG (next round)
    -> (repeat)
```

Current gap: Opportunity can be discovered, scored, and persisted, but cannot
yet become a stable source of next-round research actions.

## Status Quo Gap

| Component | Current | Missing |
|-----------|---------|---------|
| ActionPlanner | build_action_plan() maps suggested_actions to nodes | No formal ResearchAction; no risk levels |
| ResearchState | opportunities / findings / gaps | No pending_actions / completed_actions / hypotheses; no event-driven updates |
| State updates | Agents modify state directly | No unified StateUpdater; cannot audit who changed what |
| Graph Query | Graph has Store but no query layer | Planner cannot discover gaps via structured queries |
| Action safety | enable_opportunity_actions=False by default | No TTL / staleness / user approval |

## Phase A Scope

### 1. ResearchAction domain object

File: src/paperwise/opportunity/action.py

8 fixed action types (LLM cannot create new types):

| Action | Risk | Triggered by |
|--------|------|-------------|
| retrieve_evidence | LOW | MissingEvidence, KnowledgeGap |
| verify_claim | LOW | Contradiction, MissingEvidence |
| compare_methods | LOW | MethodComplementarity |
| analyze_gap | LOW | KnowledgeGap |
| search_related_work | LOW | KnowledgeGap |
| generate_hypothesis | MEDIUM | MethodComplementarity |
| design_experiment | MEDIUM | Hypothesis (Phase E) |
| ask_user | HIGH | Unresolvable Contradiction |

Each action carries:

```python
ResearchAction:
    action_id: str           # act_{uuid8}
    opportunity_id: str      # source opportunity
    action_type: ActionType  # controlled enum
    objective: str           # LLM-parameterized specific goal
    required_capabilities: list[str]
    input_refs: list[str]    # evidence / finding / paper refs
    expected_outputs: list[str]
    priority: float          # 0.0-1.0
    confidence: float        # inherited from opportunity
    risk_level: ActionRisk   # LOW / MEDIUM / HIGH
    status: ActionStatus     # pending -> approved -> running -> completed/failed
    requires_user_approval: bool
    created_at: str
```

### 2. Action Planner upgrade

File: src/paperwise/opportunity/action_planner.py

New interface:

```python
plan_actions(
    opportunities: list[ResearchOpportunity],
    research_state: ResearchState,
    max_actions: int = 3,     # Action Budget: max 1-3 per round
) -> list[ResearchAction]
```

Deterministic mapping (not LLM-decided):

```text
KnowledgeGap          -> retrieve_evidence + analyze_gap
MissingEvidence       -> retrieve_evidence + verify_claim
Contradiction         -> retrieve_evidence + verify_claim
MethodComplementarity -> search_related_work + compare_methods
```

LLM only parameterizes: query terms / scope / priority / analysis question.
LLM never decides: dangerous operations / node creation / constraint bypass.

actions_to_dag(actions) converts action list to controlled Plan,
reusing to_executable_plan() whitelist. Action DAG runs at depth=1
and does not cascade-opportunity-detect.

### 3. StateUpdater: event-driven state machine

File: src/paperwise/memory/state_updater.py

Agents must not modify ResearchState directly. Unified via:

```python
state.apply(event)
```

8 event types:

| Event | Effect |
|-------|--------|
| EvidenceFound | Add finding, increase state confidence |
| ClaimVerified | Update finding confidence up |
| ClaimRejected | Mark finding rejected, may create gap |
| GapDetected | Add KnowledgeGap |
| OpportunityCreated | Add ResearchOpportunity |
| ActionStarted | Update opportunity status -> acting |
| ActionCompleted | Update opportunity status -> acted |
| HypothesisCreated | Add hypothesis |

### 4. ResearchState extensions

New fields:

```python
hypotheses: list[Hypothesis]
pending_actions: list[ResearchAction]
completed_actions: list[ResearchAction]
```

New methods:

```python
def apply(self, event: StateEvent) -> None
def expire_stale_opportunities(self, ttl_hours: float = 72) -> list[ResearchOpportunity]
def get_pending_actions(self) -> list[ResearchAction]
```

### 5. Graph Query Layer (Phase B prep)

File: src/paperwise/research_graph/query.py

```python
class ResearchGraphQuery:
    find_related_papers(question)
    find_supporting_evidence(claim)
    find_contradictions(claim)
    find_method_complements(method)
    find_open_opportunities(project)
    find_unverified_claims(project)
    find_research_gaps(project)
```

Planner must not operate Graph Store directly. Use Query layer.

### 6. Safety constraints

| Constraint | Rule |
|-----------|------|
| Action Budget | Max 3 per round |
| Depth | Action execution depth=1; no cascading opportunity detection |
| TTL | Opportunity pending > 72h -> expired |
| Approval | LOW=auto / MEDIUM=configurable / HIGH=mandatory user confirmation |
| Controlled nodes | Action -> executable node mapping via to_executable_plan() whitelist |

## Phase A does NOT do

- No design_experiment / ask_user execution handlers (Phase E/F)
- No Context Retrieval Policy (Phase B+)
- No unified Report/PPT Output Layer (Phase D)
- No changes to existing P4/P4.5/P5 verified behavior

## Acceptance criteria

1. ResearchAction can be created, serialized, deserialized
2. plan_actions() is deterministic: same input -> same output
3. Action Budget works: opportunities beyond max_actions are truncated
4. StateUpdater.apply() correctly mutates ResearchState for each event type
5. ResearchState.expire_stale_opportunities() marks expired by TTL
6. ResearchGraphQuery returns correct nodes for each query method
7. Full regression passes (existing 233+ tests not broken)
8. Action DAG passes to_executable_plan() whitelist filtering
