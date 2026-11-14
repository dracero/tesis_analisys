# Stage 7: Critic Review Report

**Target:** `/run/media/dracero/DiscoMecanico1/AIProjects/tesis_analisys/README.md` (`FeatureRange: 1..End`)  
**Reviewers:** `egm-design`, `egm-structural`, `egm-style`

---

## 🔴 Blocker Findings

1. **`[egm-design]` Unbounded Concurrent Chromium Instances in `_crawl_urls_for_evidence`**
   - **Scope:** `src/agents/verifier.py` (`_crawl_urls_for_evidence`, L346–393) & `server.py`
   - **Issue:** Every request instantiates a fresh `AsyncWebCrawler` (headless Chromium) without a global process-wide semaphore or bounded pool across simultaneous API requests, risking OOM kills under concurrent uploads.

---

## 🟡 Major Findings

2. **`[egm-design]` JSON Dashboard Payload Attribution (`README.md` L44 vs. `reporter.py` & `server.py`)**
   - `reporter.py` only compiles the Markdown report (`str`); the JSON payload for the web dashboard is assembled in `server.py`.

3. **`[egm-design]` Duplicate Orchestration Paths (`server.py` `/api/analyze` vs. `/api/analyze/stream`)**
   - `/api/analyze` invokes `build_graph()` (`src/graph.py`), while `/api/analyze/stream` bypasses LangGraph and manually invokes the 4 agents sequentially.

4. **`[egm-design]` Unbounded Upload Buffering in `_save_temp_pdf` (`server.py` L49–64)**
   - `await file.read()` buffers the entire file into RAM before checking `MAX_PDF_BYTES` (50 MB). Stream chunks to disk with a running byte counter instead.

5. **`[egm-design]` Process-Local Key Rotator & Unbounded `_chain_cache` (`evaluator.py`, `key_manager.py`)**
   - `_chain_cache` has no size bound/eviction, and multi-worker deployments duplicate key-rotator state per worker.

6. **`[egm-design]` Verifier Correctness & Rate-Limit Conflation (`verifier.py`)**
   - `_http_get_json` returns `None` on HTTP 429/timeouts without retry/backoff, conflating rate limits with `NO_ENCONTRADA` / `invalid_dois`.
   - Crossref DOI match marks `VERIFICADA` even when `sim` is low (if a valid DOI belongs to a different paper by the same author).
   - Crossref early-return skips OpenAlex/Semantic Scholar when Crossref has no abstract, forcing unnecessary Playwright crawls.

7. **`[egm-design]` Mid-JSON Truncation & Aggressive Timeout (`evaluator.py` L132, L261–271)**
   - Slicing serialized JSON strings (`[:20000]`, `[:16000]`, `[:6000]`) produces malformed JSON in the prompt. `timeout=45` is tight for large prompts with structured output.

8. **`[egm-design]` Grouped APA Citations & Silent Truncation (`pdf_parser.py`)**
   - `apa_paren_re` misses grouped multi-citations like `(García, 2020; López, 2021)`, inflating `listed_but_not_cited`.

9. **`[egm-design]` [Security] Unvalidated `matched_url` URI Scheme (`frontend/src/pages/index.astro` L642)**
   - `escapeHtml` does not block `javascript:` or `data:` URIs in `<a href="...">`. Validate `http://` or `https://` before rendering links.

10. **`[egm-style]` Self-Contradictory Batch Scope & Uncalibrated Fallback Claim (`README.md` L26, L32)**
    - "toda la bibliografía" contradicts the 60-entry/DOI-URL cap; Line 32 presents qualitative outputs unconditionally without noting heuristic fallback limitations.

---

## 🟢 Nit Findings

11. **`[egm-design]`** `reporter.py` prints only counts for invalid DOIs/broken URLs/retractions/duplicates rather than listing the offending entries; `server.py`/`evaluator.py`/`index.astro` hardcode `"Gemini 3.6"` in status text; `verifier.py` L322 omits Semantic Scholar in fallback notes; `_is_valid_crawled_content` can match generic stopwords; `index.astro` doesn't call `URL.revokeObjectURL`.
12. **`[egm-structural]`** Minor Markdown spacing around `###` headings and fenced `bash` blocks, H2 heading symmetry (`Endpoints`), step-heading grammatical parallelism, and `ruta/a/tesis.pdf` vs `` `pdf` `` alignment in `README.md`.
13. **`[egm-style]`** Replace subjective/colloquial qualifiers (`rápido`, `llamativas`, `profundo/a`, `meramente`, `de relleno`, `levanta todo`, `para pruebas`) with objective technical phrasing in `README.md`.

