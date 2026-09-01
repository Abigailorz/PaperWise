# P9 — Cross-Paper Research Intelligence

> Version: 1.0 · Phase: P9 (final feature phase before project freeze)
> Prerequisite: P8 Research Loop complete (`v0.5.0-p8-research-loop`)
> Guiding principle: **No new agent categories, no new memory layers, no DAG rewrite.**
> Compose existing modules into cross-paper capability.

---

## 1. Problem Statement

P0–P8 built a complete single-paper analysis pipeline:

```text
PDF → Parse → Dynamic DAG → Evidence → Review
     → Opportunity → Research Question → Action → Research Loop
     → Research Graph → Narrative → Report / PPT
```

But the system currently answers questions about **one paper at a time**.
Real research intelligence requires answering questions about **a collection
of papers around a research topic**:

- How does Method A in Paper A compare to Method B in Paper B?
- Where do papers contradict each other?
- Where are complementary strengths that suggest a combination direction?
- What is still unresolved across the entire literature set?

P9 upgrades the system from **single-paper analyzer** to
**cross-paper research assistant**, then freezes the architecture.

---

## 2. Architecture Overview

```text
                    PaperWise
                        │
                SmartOrchestrator
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
        Dynamic DAG          Research State
              │                   │
              ▼                   ▼
          Agent/Tools       Research Graph
              │                   │
              ▼                   ▼
        Evidence/RAG       Research Question
              │                   │
              └─────────┬─────────┘
                        ▼
                  Research Loop          ← P8 (done)
                        │
                        ▼
              Cross-Paper Intelligence   ← P9 (this spec)
                        │
                        ▼
                Narrative / Report / PPT
```

P9 does **not** add a parallel architecture. It extends three existing layers:

| Layer | Module | Extension |
|-------|--------|-----------|
| Evidence | `evidence/` | Add cross-paper scope to `EvidenceRetriever` |
| Reasoning | `opportunity/rules.py` | Add cross-paper comparison/contradiction/complementarity rules |
| Graph | `research_graph/` | Add cross-paper edges + reverse-driven query |

---

## 3. P9.1 — Cross-Paper Evidence

### 3.1 Problem

Current `EvidenceRetriever` retrieves from `scope="current_paper"` only.
Cross-paper questions need evidence from **multiple papers simultaneously**.

### 3.2 Design

Extend `EvidenceRetriever` with a new scope:

```python
class EvidenceScope(str, Enum):
    CURRENT_PAPER = "current_paper"
    CROSS_PAPER = "cross_paper"      # NEW in P9
```

When `scope=CROSS_PAPER`, the retriever:

1. Discovers all parsed papers in the workspace (`workspace/*/text.md`).
2. Expands the query via the existing `KnowledgeBase.search()` pipeline
   (HyDE + RRF + Rerank already implemented in P0–P7).
3. Tags each evidence snippet with its source paper ID and title.
4. Produces a single `EvidencePack` containing snippets from multiple papers.

### 3.3 Data Model

Extend `EvidenceSnippet` with:

```python
@dataclass
class EvidenceSnippet:
    # ... existing fields ...
    paper_id: str = ""        # NEW: which paper this snippet comes from
    paper_title: str = ""     # NEW: display title
```

Extend `EvidencePack` with:

```python
@dataclass
class EvidencePack:
    # ... existing fields ...
    scope: EvidenceScope = EvidenceScope.CURRENT_PAPER  # NEW
    papers_covered: list[str] = field(default_factory=list)  # NEW
```

### 3.4 Query Expansion

Cross-paper retrieval uses a two-stage query:

```text
ResearchQuestion (from Research Graph gap)
    → Stage 1: Entity extraction (methods, metrics, datasets mentioned in the question)
    → Stage 2: For each paper in library, search with expanded query
    → Merge: deduplicate + tag source + rank by RRF score
```

No LLM call is needed for query expansion — use existing entity extraction
from the Research Graph (`ResearchGraphBuilder.extract_entities()`).

### 3.5 Acceptance Criteria

1. `EvidenceRetriever.retrieve(scope="cross_paper")` returns snippets from ≥ 2 papers.
2. Each snippet has `paper_id` and `paper_title`.
3. `EvidencePack.papers_covered` lists all contributing papers.
4. Works when workspace contains 2–10 parsed papers.
5. Gracefully degrades to single-paper behavior when only 1 paper exists.

---

## 4. P9.2 — Cross-Paper Reasoning

Three deterministic reasoning capabilities, all built on the existing
`opportunity/rules.py` pattern (no LLM, no new agent):

### 4.1 Method Comparison

**Input**: Evidence snippets from ≥ 2 papers that mention comparable methods.

**Output**: `CrossPaperComparison` dataclass:

```python
@dataclass
class CrossPaperComparison:
    comparison_id: str
    papers: list[str]                    # paper IDs
    methods: list[str]                   # method names extracted from snippets
    similarity_notes: list[str]          # shared techniques
    difference_notes: list[str]          # distinct techniques
    strengths: dict[str, list[str]]      # paper_id → strengths
    weaknesses: dict[str, list[str]]     # paper_id → weaknesses
    shared_assumptions: list[str]
    confidence: float                    # evidence-backed confidence
```

**Detection rule** (deterministic, added to `opportunity/rules.py`):

```text
MethodComparisonRule:
  1. Extract method entities from Research Graph nodes (type=METHOD).
  2. For each pair of papers sharing ≥ 1 common evaluation metric
     (type=METRIC in graph), create a comparison candidate.
  3. Score confidence = min(1.0, shared_metrics_count / 3.0).
  4. Filter: confidence ≥ 0.5 AND papers ≥ 2.
```

### 4.2 Contradiction Discovery

**Input**: Evidence snippets from ≥ 2 papers with conflicting claims
about the same entity (method, metric, or concept).

**Output**: `CrossPaperContradiction` dataclass:

```python
@dataclass
class CrossPaperContradiction:
    contradiction_id: str
    entity: str                          # the disputed method/metric/concept
    claim_a: str                         # what Paper A says
    claim_b: str                         # what Paper B says
    paper_a: str
    paper_b: str
    evidence_a: str                      # snippet ID or quote
    evidence_b: str
    confidence: float
```

**Detection rule** (added to `opportunity/rules.py`):

```text
ContradictionRule:
  1. Group evidence snippets by shared entity name.
  2. For each pair of snippets from different papers mentioning the same entity:
     a. Check for opposing sentiment keywords:
        ("improves", "outperforms", "significant") vs
        ("no improvement", "does not outperform", "no significant", "worse")
     b. Check for opposing numerical direction (if both contain numbers
        for the same metric: A says X increases, B says X decreases or stays flat).
  3. If opposing direction detected → create contradiction candidate.
  4. Score confidence = keyword_match_score × evidence_overlap_inverse.
  5. Filter: confidence ≥ 0.6.
```

### 4.3 Method Complementarity

**Input**: Evidence snippets from ≥ 2 papers addressing **different problem
dimensions** with compatible methods.

**Output**: `CrossPaperComplementarity` dataclass:

```python
@dataclass
class CrossPaperComplementarity:
    complementarity_id: str
    paper_a: str
    paper_b: str
    method_a: str
    method_b: str
    dimension_a: str        # e.g., "geometric accuracy"
    dimension_b: str        # e.g., "semantic consistency"
    combination_hypothesis: str
    confidence: float
```

**Detection rule** (added to `opportunity/rules.py`):

```text
ComplementarityRule:
  1. For each pair of papers, extract their primary method entities.
  2. Check dimension labels from Research Graph (type=DIMENSION).
  3. If paper A addresses dimension D1 and paper B addresses dimension D2
     where D1 ≠ D2 AND their methods share ≥ 1 common base technique:
     → create complementarity candidate.
  4. Score confidence = dimension_disjointness × shared_technique_ratio.
  5. Filter: confidence ≥ 0.5.
```

### 4.4 Integration with Existing Opportunity System

All three cross-paper rule outputs map to existing opportunity types:

| Cross-paper output | Maps to existing OpportunityType |
|--------------------|----------------------------------|
| MethodComparison | `KnowledgeGap` (new subtype: cross_paper_comparison) |
| ContradictionDiscovery | `Contradiction` (existing type, now cross-paper source) |
| MethodComplementarity | `MethodComplementarity` (existing type, now cross-paper source) |

This means P9 does NOT create new opportunity types. It extends the
**source scope** of existing types from single-paper to cross-paper.

### 4.5 Acceptance Criteria

1. `MethodComparisonRule` fires when 2+ papers share evaluation metrics.
2. `ContradictionRule` fires when 2+ papers make opposing claims about the same entity.
3. `ComplementarityRule` fires when 2+ papers address different dimensions with shared techniques.
4. All three rules are deterministic (same input → same output, no LLM).
5. All three rules produce opportunities that flow into the existing P8 Research Loop.

---

## 5. P9.3 — Research Graph Reverse-Driven

### 5.1 Problem

Currently the Research Graph is a **record system**: the DAG writes to it,
but it does not drive the next round's decisions.

P8 already introduced partial graph-driven planning (`ResearchGraphQuery.find_research_gaps()`
feeds into `ResearchState.opportunities`). P9 makes this the **primary driver**.

### 5.2 Design

Extend `ResearchGraphQuery` with three cross-paper queries:

```python
class ResearchGraphQuery:
    # ... existing methods ...

    def find_cross_paper_relationships(self) -> list[GraphNodePair]:
        """Find METHOD↔METHOD edges spanning different papers."""

    def find_contradiction_hubs(self) -> list[GraphNode]:
        """Find entities with opposing CLAIM edges from different papers."""

    def find_complementarity_pairs(self) -> list[GraphNodePair]:
        """Find METHOD pairs addressing different DIMENSION nodes."""
```

### 5.3 Integration with Research Loop

P8's `SmartOrchestrator._run_complex()` already queries the graph for gaps.
P9 extends this to also query for cross-paper relationships:

```python
# In _run_complex(), after loading persisted_graph:
cross_paper_opps = []
query = ResearchGraphQuery(persisted_graph)
for pair in query.find_cross_paper_relationships():
    opps.extend(MethodComparisonRule.detect(pair))
for hub in query.find_contradiction_hubs():
    opps.extend(ContradictionRule.detect(hub))
for pair in query.find_complementarity_pairs():
    opps.extend(ComplementarityRule.detect(pair))
# These merge with single-paper opportunities into research_state.opportunities
```

### 5.4 Acceptance Criteria

1. `ResearchGraphQuery.find_cross_paper_relationships()` returns METHOD↔METHOD pairs from different papers.
2. `find_contradiction_hubs()` returns entities with opposing claims.
3. `find_complementarity_pairs()` returns complementary method pairs.
4. Cross-paper opportunities appear in `research_state.opportunities` alongside single-paper ones.
5. The Research Loop (P8) processes cross-paper opportunities identically to single-paper ones.

---

## 6. P9.4 — Final Research Report / PPT

### 6.1 Problem

Current output answers "what does this paper say?" P9 output must answer:

```text
研究问题是什么？
已有工作解决了什么？
不同论文之间有什么关系？
存在什么矛盾？
证据缺口在哪里？
有哪些研究机会？
哪些问题已经解决？
哪些仍未解决？
```

### 6.2 Design

Extend `ResearchNarrative` (`generators/narrative.py`) with cross-paper sections:

```python
@dataclass
class CrossPaperNarrativeSection:
    section_type: str  # "method_comparison" | "contradictions" | "complementarity" | "research_gaps"
    title: str
    content: str       # generated from CrossPaperComparison/Contradiction/Complementarity data
    source_papers: list[str]
    evidence_refs: list[str]
```

Add to `ResearchNarrative`:

```python
@dataclass
class ResearchNarrative:
    # ... existing fields ...
    cross_paper_sections: list[CrossPaperNarrativeSection] = field(default_factory=list)
```

### 6.3 Report Generation

Extend `PaperAnalysisPipeline.get_report_writer_spec()` with a conditional
cross-paper instruction block:

```text
IF workspace contains ≥ 2 parsed papers AND research_state has cross-paper opportunities:
  Add section instructions:
  1. "Method Comparison" — compare methods from different papers
  2. "Contradictions" — list conflicting claims with evidence
  3. "Complementarity" — suggest combination directions
  4. "Research Gaps" — what remains unresolved across papers
```

### 6.4 PPT Generation

Extend `SlideContentBuilder` with cross-paper slide templates:

| Slide | Content Source |
|-------|---------------|
| Research Questions | `research_state.questions` (existing) |
| Method Landscape | Cross-paper comparison data |
| Contradictions | Contradiction discovery data |
| Complementary Directions | Complementarity data |
| Evidence Gaps | Research Graph gaps (existing) |
| Research Opportunities | `research_state.opportunities` (existing) |
| Resolved vs Unresolved | Question outcome summary (P8) |

### 6.5 Acceptance Criteria

1. `ResearchNarrative` includes `cross_paper_sections` when 2+ papers analyzed.
2. Report contains all 4 cross-paper sections.
3. PPT contains at least "Contradictions" and "Research Gaps" slides.
4. Every claim in cross-paper sections cites evidence from specific papers.
5. Report/PPT answer all 8 questions listed in §6.1.

---

## 7. P9.5 — Final Benchmark

### 7.1 Problem

Current evaluation only tests single-paper report quality (Pass@k / Pass^k).
P9 must prove the system works at the research intelligence level.

### 7.2 Four Benchmark Categories

| Benchmark | Tests | Key Metric |
|-----------|-------|-----------|
| **Single-Paper** | Report quality, completeness, insight | Pass@k (existing) |
| **Evidence** | Citation accuracy, evidence grounding | Evidence Precision/Recall |
| **Cross-Paper** | Multi-paper comparison, contradiction, complementarity | Cross-paper F1 |
| **Research Loop** | Question → Action → Outcome state transition | Loop Convergence Rate |

### 7.3 Research Loop Benchmark (most important)

This benchmark proves:

> **Agent's second-round decisions are influenced by first-round results.**

```text
Test Protocol:
  Round 1: Seed question → Action → Outcome (baseline state S1)
  Round 2: Same question, state S1 → Action' (must differ from Round 1 action)
  Check: Action' ≠ Action (state drove different decision)
  Check: Outcome evaluation reflects updated evidence
```

Implementation: extend `tests/test_memory/test_p8_loop.py` with a
`test_state_drives_different_actions()` that:

1. Creates ResearchState with 1 question.
2. Runs action planning → records action set A1.
3. Simulates outcome (marks resolved).
4. Re-runs action planning → records action set A2.
5. Asserts A2 ≠ A1 (different actions because state changed).

### 7.4 Cross-Paper Benchmark

Test with 2–3 real papers (e.g., LangSplat + Feature3DGS + 3DGS):

```text
Input: 3 papers' text.md in workspace
Execute: cross-paper evidence retrieval + contradiction detection
Expected:
  - ≥ 1 method comparison detected
  - ≥ 1 contradiction found (or explicitly "no contradiction found")
  - ≥ 1 complementarity candidate (or explicitly "no complementarity")
```

### 7.5 Evaluation Script

Create `workspace/langsplat/eval_p9_cross_paper.py` (gitignored, local only):

```python
# Runs all 4 benchmark categories in sequence:
# 1. Single-paper baseline (reuse existing eval_langsplat.py)
# 2. Evidence precision/recall on citation checking
# 3. Cross-paper comparison on 3-paper library
# 4. Research loop state-driven action test
```

### 7.6 Acceptance Criteria

1. All 4 benchmark categories produce structured results.
2. Research Loop benchmark proves state-driven decision change.
3. Cross-paper benchmark runs on real papers without LLM hallucination.
4. Results are persisted to `workspace/test_runs/` for dashboard display.

---

## 8. Non-Goals

P9 explicitly does **NOT**:

1. ❌ Add new Agent categories (no new sub-agent types)
2. ❌ Add new Memory layers (no new memory type)
3. ❌ Rewrite the Dynamic DAG (reuse existing DAGExecutor)
4. ❌ Add self-improvement beyond what P8 already does
5. ❌ Add automatic experiment execution
6. ❌ Add hypothesis-driven experiment design
7. ❌ Add real-time paper monitoring

These belong to P10/P11 which are explicitly **cancelled** per project direction.
Self-improvement and automatic experimentation become "Future Work" in the final paper.

---

## 9. Implementation Order

```text
Step 1: P9.1 Cross-Paper Evidence (extend EvidenceRetriever + EvidenceSnippet)
Step 2: P9.2 Cross-Paper Rules (extend opportunity/rules.py with 3 new rules)
Step 3: P9.3 Graph Queries (extend ResearchGraphQuery)
Step 4: Integration (wire into orchestrator._run_complex + research_state)
Step 5: P9.4 Narrative + Report + PPT cross-paper sections
Step 6: P9.5 Benchmark suite + evaluation script
Step 7: Full regression + real-paper validation
Step 8: Final tag v0.6.0-p9-research-native + project freeze
```

Each step builds on the previous. No step requires changes to modules
outside the extension scope defined above.

---

## 10. Version Control Policy

### Tagging Convention

```text
v{major}.{minor}.{patch}-{phase}-{description}

Examples:
  v0.5.0-p8-research-loop    ← P8 complete
  v0.6.0-p9-research-native  ← P9 complete (project freeze)
```

### Branching Policy

- **Single branch**: `main` (no feature branches; P-level work commits directly)
- Each P-phase completes as a series of atomic commits with `P{N}:` prefix
- Tag is created only when the entire P-phase passes all acceptance criteria
- After P9 tag: architecture freeze — only bug fixes and documentation updates

### Commit Convention

```text
P{N}: {action} {description}          ← feature work
fix: {description}                    ← bug fix
docs: {description}                    ← documentation only
test: {description}                    ← test only
```

### Spec Management

- Each P-phase has exactly one spec file: `docs/P{N}-{NAME}-SPEC.md`
- Old specs are kept for historical reference (never deleted)
- The current active spec is referenced in `HANDOFF-P9.md` (the handoff doc for P9)

---

## 11. Final State After P9

```text
                    PaperWise v0.6.0
                        │
                SmartOrchestrator
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
        Dynamic DAG          Research State
              │                   │
              ▼                   ▼
          Agent/Tools       Research Graph
              │                   │
              ▼                   ▼
        Evidence/RAG       Research Question
              │                   │
              └─────────┬─────────┘
                        ▼
                  Research Loop (P8)
                        │
                        ▼
              Cross-Paper Intelligence (P9)
                        │
                        ▼
                Narrative / Report / PPT
```

**Capability level achieved: Research-native Paper Agent**

After P9, the project enters maintenance mode:

```text
Real Benchmark → A/B Evaluation → Cost/Latency Analysis
→ Failure Analysis → Paper/Demo/Release
```

No further P-phases. Self-improvement and automated experimentation
are documented as Future Work.
