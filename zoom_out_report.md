# Stage 5: Initial Zoom Out Review Report

**Target:** `/run/media/dracero/DiscoMecanico1/AIProjects/tesis_analisys/README.md` (`full file`)  
**Preset:** Feature spec only (`FeatureRange: 1..End`, `DetailedRange: N/A`)  
**Reviewers:** `egm-zoom-out`, `egm-inventive-catalyst`

---

## Part 1: Zoom Out Reviewer (`egm-zoom-out`)

### 1. [Major] Documented LangGraph Architecture vs. Bypassed Imperative Orchestration in `/api/analyze/stream`
- **Affected Scope**: `README.md` L35–43, L50–51 (`src/graph.py` L12–27, `server.py` L80–185).
- **Discrepancy Description**:
  - `README.md` presents LangGraph (`src/graph.py`) as the core multi-agent orchestrator and states that `POST /api/analyze/stream` is *"Igual que `/api/analyze`, pero transmite el progreso paso a paso en tiempo real mediante Server-Sent Events (SSE)"*.
  - In `server.py` (L115–185), `/api/analyze/stream`—the **only** analysis endpoint consumed by `frontend/src/pages/index.astro` (L386)—does **not** invoke `build_graph()` at all. It manually re-implements the 4-step pipeline imperatively and strips `body_excerpt_*` from `parsed_thesis` (L171), whereas `/api/analyze` (L80–112) runs `graph.invoke(...)` and returns the unstripped `parsed_thesis`.
- **Simplicity / Architectural Rationale**:
  - `src/graph.py` (L12–27) is a purely linear 4-node chain (`START -> pdf_parser -> verifier -> evaluator -> reporter -> END`) with no conditional branching, cycles, or checkpointing. Using LangGraph for a linear sequence—and then duplicating the orchestration imperatively in `server.py` to get streaming progress—creates two divergent execution paths for the same pipeline.

### 2. [Major] Undocumented Heuristic Fallback Mode in `evaluator.py`
- **Affected Scope**: `README.md` L42, L49–51, L66–71.
- **Discrepancy Description**:
  - `README.md` documents Gemini with `GeminiKeyRotator` as required for evaluating the 6 tutor pillars, but omits that `src/agents/evaluator.py` (L139–228, `_heuristic_fallback_evaluation`) automatically falls back to a deterministic rule-based evaluation when no Gemini API keys are configured or all keys fail.
  - `frontend/src/pages/index.astro` (L325–326) explicitly surfaces this state (`"Modo Heurístico (Sin API Key)"`).
- **Simplicity / Architectural Rationale**:
  - A reader/operator reading `README.md` assumes the system fails without `GEMINI_API_KEY`, when in fact it degrades silently to deterministic scoring.

### 3. [Major] Unused REST Endpoint (`POST /api/analyze`) & Unmentioned Production Serving (`GET /`)
- **Affected Scope**: `README.md` L47–52, L73–79.
- **Discrepancy Description**:
  - `POST /api/analyze` is documented alongside `/api/analyze/stream`, yet `frontend/src/pages/index.astro` never calls `/api/analyze`.
  - Conversely, `server.py` (L208–219) mounts `frontend/dist/_astro` and serves `frontend/dist/index.html` at `GET /` for production (`npm run build` / `npm start` in `package.json`), which `README.md` omits (it only documents `npm run dev` on `:4321` and `:8000`).
- **Simplicity / Architectural Rationale**:
  - Maintaining `POST /api/analyze` alongside `/api/analyze/stream` should either be clarified as external REST vs. Web UI SSE, or consolidated; production serving via `npm run build` / `npm start` should be documented.

### 4. [Major] Overstated "Anti-404/Login/CAPTCHA" Validation & Context-vs-Section Redundancy
- **Affected Scope**: `README.md` L22, L26, L40.
- **Discrepancy Description**:
  - **L26 & L40**: Claims Crawl4AI/Playwright performs *"validación anti-404/login/CAPTCHA"*. In `src/agents/verifier.py` (L326–343, `_is_valid_crawled_content`), this is a 12-string blocklist (`_BAD_PAGE_PATTERNS`) plus an 8-token overlap check, executed only on up to 8 URLs lacking an abstract (L348).
  - **L22**: Claims `cited_sections` and tagged context snippets are mapped to evaluate all references' usage. In `src/agents/evaluator.py` (L246–251), `full_bib_compact` only includes `cited_sections` and `citation_count_in_text` (no `context_snippets`), while `context_snippets` are only passed for the 5–10 sampled references (`verified_refs_json`). Additionally, `evaluator.py` (L264–267) sends both `sections_summary` (up to 14k chars) and overlapping `intro_excerpt` / `mid_excerpt` / `end_excerpt` (up to 16.5k chars) to Gemini.
- **Simplicity / Architectural Rationale**:
  - Calling a substring blocklist "validación anti-404/login/CAPTCHA" overstates the capability. Sending both `sections_summary` and raw `intro/mid/end_excerpt` duplicates prompt context.

### 5. [Nit] Altitude Drops (Internal Implementation Weeds in High-Level Overview)
- **Affected Scope**: `README.md` L12, L15, L22, L37, L40.
- **Discrepancy Description**:
  - High-level feature and architecture sections leak internal Pydantic field names and library arguments: `already_in_thesis` (L12), `venue` and `cited_by_count` (L15), `cited_sections` (L22), PyMuPDF `sort=True` (L37), and `status_code` (L40).

### 6. [Nit] Minor Discrepancy in Field Literature `already_in_thesis` for Web Results
- **Affected Scope**: `README.md` L12.
- **Discrepancy Description**:
  - In `src/agents/verifier.py` (L462–477), DuckDuckGo results in `_search_field_literature` hardcode `already_in_thesis=False` (L476) without running `_is_in_thesis_bibliography`, and hardcode `year=current_year` (L471).

---

## Part 2: Inventive Catalyst Reviewer (`egm-inventive-catalyst`)

### 1. [Major] False Compromise: Dual Orchestration Between LangGraph (`src/graph.py`) and SSE Streaming (`server.py`)
- **Affected Scope**: `README.md` L35–43, L50–51 (`src/graph.py` L12–27, `server.py` L80–205).
- **Rationale (TRIZ Contradiction)**: Using LangGraph's synchronous `invoke` sacrifices real-time SSE progress; achieving SSE progress led to duplicating orchestration outside LangGraph, creating drift risk (e.g., `server.py:171` strips `body_excerpt_*` in `/api/analyze/stream`, whereas `/api/analyze` does not).
- **Proposed Resolution**: Convert `src/graph.py` nodes to async-compatible and have `/api/analyze/stream` consume `graph.astream(..., stream_mode="updates")` as the single source of truth.

### 2. [Major] Hidden Bottleneck: Sequential Execution Inside `verifier` Despite Parallelism Claims
- **Affected Scope**: `README.md` L25–26, L38–41 (`src/agents/verifier.py` L139–323, L346–388, L573–601).
- **Rationale (TRIZ Contradiction)**: `_run_batch()` runs sampled refs, then sequentially runs `_search_field_literature()`, then sequentially runs `_verify_all_bibliography_light()`. `_crawl_urls_for_evidence()` then crawls up to 8 URLs sequentially with an 18s timeout each (worst-case ~144s), and sampled DOIs are queried twice.
- **Proposed Resolution**: Run sampled verification, bulk check, and field search concurrently; bound Playwright concurrency (`asyncio.gather` + semaphore); reuse DOI lookup results between bulk check and sampled verification.

### 3. [Major] Token-Budget Waste & Signal Loss Between `pdf_parser` and `evaluator`
- **Affected Scope**: `README.md` L15, L22, L42 (`src/agents/pdf_parser.py` L255–261, L498–502; `src/agents/evaluator.py` L253–268; `src/agents/verifier.py` L539).
- **Rationale (TRIZ Contradiction)**: Truncates `sections_summary` and `full_bib_compact` while spending ~16,500 chars on overlapping `intro/mid/end_excerpt`. Also collapses bulk-checked `venues_detected` into a flat 15-item list without per-entry attribution.
- **Proposed Resolution**: Send raw `intro/mid/end_excerpt` only as fallback when `sections_summary` is sparse (<2 sections), reallocating budget to `sections_summary` and `full_bib_compact` enriched with per-entry `venue`.

### 4. [Major] Key-Rotation & Fallback Fragility (`GeminiKeyRotator` vs. `evaluator`)
- **Affected Scope**: `README.md` L42 (`src/agents/evaluator.py` L139–228, L238–288; `src/key_manager.py` L104–140).
- **Rationale (TRIZ Contradiction)**: Any non-auth/non-quota exception (e.g., 400 Bad Request or Pydantic validation error) burns every key in `ordered_keys` on the exact same failing payload; and strict cooldown filtering silently falls back to `_heuristic_fallback_evaluation()` without a structured `evaluation_mode` flag.
- **Proposed Resolution**: Fail fast on deterministic 400/validation errors; add `evaluation_mode: Literal["llm", "heuristic_fallback"]` to `ThesisEvaluation`.

### 5. [Nit] Minor Scope Caps Not Reflected in `README.md`
- **Affected Scope**: `README.md` L25–26 (`src/agents/verifier.py` L348, L523).
- **Discrepancy**: `verifier.py:348` caps Playwright targets at `[:8]` even when `sample_size` is 9–10, and `verifier.py:523` skips HTTP URL checks when a DOI already resolved (worth documenting as an intentional optimization).

