# P9 — Cross-Paper Research Intelligence Status

> Updated: 2026-09-02  
> Branch: `main`  
> Baseline: `7880644 docs: P9 cross-paper research spec + unified version control policy + tag v0.5.0-p8`  
> Target tag: `v0.6.0-p9-research-native`

## Current Situation

P9 is now functionally implemented across the five core layers named in the
spec: cross-paper evidence, deterministic cross-paper reasoning, graph-driven
cross-paper discovery, narrative projection, and state-driven action planning.
The implementation follows the agreed P9 constraint of extending the existing
architecture rather than adding a new agent category, memory layer, or DAG.

The full regression suite currently passes:

```text
308 passed
```

This includes the 17 new P9 tests and the 16 existing P7/P8 tests.  The
previous failure in the legacy cross-paper/library retrieval path was fixed by
keeping `"library"` as a compatibility alias for `EvidenceScope.CROSS_PAPER`.

## Implemented Changes

### P9.1 — Cross-Paper Evidence

- Added `EvidenceScope` with `CURRENT_PAPER` and `CROSS_PAPER`.
- Added `paper_title` to `EvidenceSnippet`.
- Added `papers_covered` to `EvidencePack`.
- Added cross-paper retrieval to `EvidenceRetriever.retrieve(scope="cross_paper")`.
- Kept `scope="library"` as a backward-compatible alias for the existing API.
- Refactored chunk ingestion into `_ingest_chunk()` to avoid duplicated logic.

### P9.2 — Deterministic Cross-Paper Rules

Added three deterministic rules to `opportunity/rules.py`:

1. `CrossPaperMethodComparisonRule`
2. `CrossPaperContradictionRule`
3. `CrossPaperComplementarityRule`

All three are registered in `DEFAULT_RULES`, do not call an LLM, and emit
existing opportunity types:

- Method comparison → `KnowledgeGap`
- Contradiction → `Contradiction`
- Complementarity → `MethodComplementarity`

### P9.3 — Research Graph Reverse-Driven Queries

Added `GraphNodePair` and three typed graph queries to
`ResearchGraphQuery`:

- `find_cross_paper_relationships()`
- `find_contradiction_hubs()`
- `find_complementarity_pairs()`

`SmartOrchestrator._run_complex()` now feeds these graph results back into
research opportunities, so the graph participates in next-round planning.

### P9.4 — Narrative Projection

Added `CrossPaperNarrativeSection` and `ResearchNarrative.cross_paper_sections`.
The narrative now:

- derives sections from cross-paper opportunities,
- serializes and restores cross-paper sections,
- emits a `Cross-Paper Analysis` block in `to_prompt_context()`.

### P9.5 — Research Loop Benchmark Coverage

Added `tests/test_memory/test_p9_cross_paper.py` with 17 tests covering:

- multi-paper retrieval and single-paper graceful degradation,
- serialization of paper scope and titles,
- the three deterministic rules,
- graph cross-paper relationships, contradiction hubs, and complementary pairs,
- narrative generation and prompt context,
- state-driven action changes,
- evidence citation precision.

## Remaining Work Before Freeze

1. **Report and PPT integration**  
   `ResearchNarrative` now contains cross-paper sections, but
   `ReportGenerator` and `SlideContentBuilder` do not yet inject those sections
   into their final report and slide prompts/templates.  This is the main
   missing P9.4 surface.

2. **P9 evaluation script**  
   The planned `workspace/langsplat/eval_p9_cross_paper.py` still needs to run
   the four benchmark categories and persist structured results under
   `workspace/test_runs/`.

3. **Real-paper validation**  
   Run the cross-paper path against a 2–3 paper real library and record:
   - method comparison count,
   - contradiction count or explicit no-contradiction result,
   - complementarity count or explicit no-complementarity result,
   - citation precision/recall.

4. **Quality tuning**  
   The current deterministic rules intentionally favor precision, but their
   keyword heuristics can be brittle for domain-specific terminology.  After
   real-paper validation, tune thresholds and marker vocabulary only from
   observed false positives/negatives.

5. **Architecture and API consistency**  
   Before tagging, normalize remaining naming around `library` and
   `cross_paper`, ensure the public API prefers `cross_paper`, and add explicit
   documentation that `library` remains a compatibility alias.

6. **Final tag and freeze**  
   After report/PPT wiring, benchmark execution, and real-paper validation,
   create the final tag:

   ```text
   v0.6.0-p9-research-native
   ```

   After that tag, the project enters maintenance mode.  Future work should be
   documented as paper/demo/release work rather than new P-phases.

## Version Control Policy

- One active branch: `main`.
- P-phase work uses atomic commits with `P9:` prefixes.
- Documentation-only changes use `docs:`.
- A phase tag is created only after all acceptance criteria pass.
- Old specs are preserved; the active spec remains
  `docs/P9-CROSS-PAPER-SPEC.md`.
- P10/P11 remain cancelled; self-improvement and automated experimentation are
  recorded as future work rather than implementation scope.
