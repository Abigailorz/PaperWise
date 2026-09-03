# P9 — Cross-Paper Research Intelligence Status

> Updated: 2026-09-03  
> Branch: `main`  
> Baseline: `7880644 docs: P9 cross-paper research spec + unified version control policy + tag v0.5.0-p8`  
> Target tag: `v0.6.0-p9-research-native`

## Current Situation

P9 is now functionally implemented across the five core layers named in the
spec: cross-paper evidence, deterministic cross-paper reasoning, graph-driven
cross-paper discovery, narrative projection, and state-driven action planning.
The implementation follows the agreed P9 constraint of extending the existing
architecture rather than adding a new agent category, memory layer, or DAG.

The regression suite currently passes:

```text
311 passed (280 offline/integration + 31 evaluation)
```

The previous failure in the legacy cross-paper/library
retrieval path was fixed by normalizing `"library"` to `cross_paper` inside
the retriever (documented as a compatibility alias).

Of the 4 v0.4.1-era e2e mock tests, 2 pass and 2 hang in the current
sandboxed environment: the hang occurs inside pytest's tempfile-backed stdout
capture while the DAG executor emits progress (pre-existing, unrelated to P9
code paths).  The LLM provider for this environment is the OpenCode Go
gateway (`glm-5.3-flash` main model, `kimi-k3` judge) configured in the
gitignored `.env`.

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

### P9.4 — Narrative Projection (completed)

Added `CrossPaperNarrativeSection` and `ResearchNarrative.cross_paper_sections`.
The narrative now:

- derives sections from cross-paper opportunities,
- serializes and restores cross-paper sections,
- emits a `Cross-Paper Analysis` block in `to_prompt_context()`.

The final P9.4 surface is now wired end to end:

- `ReportGenerator` gained a `cross_paper_analysis` section (ordered between
  Related Work and Conclusion), with matching task-prompt instructions and
  deterministic `assemble()` ordering by `REPORT_SECTIONS`.
- `GeneratePPTXTool._collect_narrative()` packs `cross_paper_sections` into
  the slide-builder context.
- The API `generate_pptx` endpoint injects `research_narrative.json`
  cross-paper sections as the `cross_paper` report section.
- `build_fallback_slides()` emits a "跨论文分析" content slide when the
  `cross_paper` section exists; `SlideContentBuilder` adds a matching
  requirement to the LLM prompt.

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

2. **P9 evaluation script** — DONE  
   `测评/scripts/eval_p9_cross_paper.py` runs the four benchmark categories
   (retrieval coverage, method comparison, contradiction, complementarity)
   plus citation precision and persists JSON results under
   `workspace/test_runs/p9_cross_paper_eval.json`.  The script itself is
   version-controlled; `workspace/` remains runtime data.

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

5. **Architecture and API consistency** — DONE  
   The retriever normalizes the `library` alias to `cross_paper`, so the
   public API always reports `cross_paper`.  Docstrings document the alias;
   internal callers and tests use `cross_paper` directly.

6. **Version unification** — DONE  
   `pyproject.toml`, `paperwise/__init__.py`, `api/server.py`,
   `mcp/server.py`, and the README title are unified at `0.6.0` in
   preparation for the final tag.

7. **Final tag and freeze**  
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
