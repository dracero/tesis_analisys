# Stage 6: Comprehension Test Report

**Target:** `/run/media/dracero/DiscoMecanico1/AIProjects/tesis_analisys/README.md` (`FeatureRange: 1..End`, `DetailedRange: N/A`)  
**Reviewer:** `egm-comprehension`

---

**What this proposes**
This document describes a full-stack, multi-agent academic auditing application ("Analista de Tesis Full-Stack") intended for thesis tutors and evaluation committees. It allows users to upload a thesis in PDF format via a web dashboard or CLI and automatically evaluate its bibliography, academic integrity, and source usage against six tutor criteria. The system combines deterministic PDF parsing, external academic APIs and web scraping, and Google Gemini (with API key rotation and a deterministic fallback) to generate an actionable Markdown report and structured JSON output.

**How the existing system works, per this document**
The core pipeline is implemented as a LangGraph workflow (`src/graph.py`) composed of four sequential agents: `pdf_parser` (extracts text via PyMuPDF, segments sections, detects citation styles, and cross-references in-text citations with the bibliography), `verifier` (runs parallel batch checks on up to 60 DOIs/URLs, searches field literature, and deeply verifies 5–10 key references using Crossref, OpenAlex, Semantic Scholar, DuckDuckGo, and Crawl4AI/Playwright), `evaluator` (audits the six criteria using Google Gemini via `GeminiKeyRotator` or falls back to a deterministic heuristic mode), and `reporter` (compiles the Markdown report and web dashboard JSON). A FastAPI backend (`server.py`) exposes synchronous (`POST /api/analyze`) and streaming SSE (`POST /api/analyze/stream`) endpoints, a config endpoint (`GET /api/config`), and serves the built Astro.js v5 frontend (`frontend/dist`) at `GET /`. Locally, `npm run dev` starts both the FastAPI backend (`:8000`) and Astro frontend (`:4321`), while `main.py` provides an optional CLI interface.

---

### Findings

1. **Severity**: Major
2. **Affected Scope**: `⚙️ Instalación y Ejecución` (Lines 55–76)
3. **Discrepancy Description & Rationale**: Unstated system prerequisites and build workflow. The installation commands invoke `uv` and `npm` without stating that `uv`, Python (and which version), or Node.js/npm (and which version) must be pre-installed. Additionally, `GET /` (Line 48) states it serves `frontend/dist` "cuando está disponible", but the document never explains how to build `frontend/dist` for production or how to run the backend in production mode (only `npm run dev` is documented).

1. **Severity**: Major
2. **Affected Scope**: `🔍 Qué Evalúa el Sistema` / `🏛️ Arquitectura Multi-Agente` — Sampling and limits (Lines 12, 25–26, 39–40, 50)
3. **Discrepancy Description & Rationale**: Vague explanations of selection criteria and truncation behavior. The document states that batch checking validates "hasta 60 referencias con DOI/URL", deep sampling checks "5 a 10 referencias clave", and Crawl4AI runs on "hasta 10 URLs sin abstract previo", but never explains how references are prioritized or selected when a thesis exceeds those limits, nor what happens to the unchecked references (e.g., whether they are flagged as unverified or silently skipped). Similarly, "sin abstract previo" is ambiguous without stating where a prior abstract would have come from (e.g., from Crossref/OpenAlex/Semantic Scholar).

1. **Severity**: Major
2. **Affected Scope**: `🏛️ Arquitectura Multi-Agente` — `evaluator` fallback mode (Line 41)
3. **Discrepancy Description & Rationale**: Vague explanation of fallback behavior. The document states that if no Gemini keys are configured or all fail, `evaluator` "degrada automáticamente a un modo heurístico determinístico de respaldo", without explaining what this heuristic mode actually evaluates or which outputs (such as qualitative critique, "vacíos concretos", or "preguntas clave para la reunión de tutoría") are degraded or omitted when Gemini is unavailable.

1. **Severity**: Major
2. **Affected Scope**: `🔍 Qué Evalúa el Sistema` & `🌐 Endpoints del Backend` — `thesis_topic` and input/output contracts (Lines 12, 39, 50–51, 83)
3. **Discrepancy Description & Rationale**: Unclear role and requirement of `thesis_topic`. Sections 1 and 2 describe comparing the bibliography against field literature without mentioning how the system knows the thesis topic; `thesis_topic` (`--topic`) only appears later in the API and CLI sections without specifying whether it is required, optional, or inferred from the PDF if omitted. Likewise, `POST /api/analyze/stream` mentions omitting "extractos largos del cuerpo" in the final SSE event, but neither the SSE event schema nor the JSON response structure is defined or linked.

1. **Severity**: Nit
2. **Affected Scope**: `⚙️ Instalación y Ejecución` — Environment configuration & dual package managers (Lines 58–71)
3. **Discrepancy Description & Rationale**: Minor clarity gaps in setup instructions. First, `npm install` at the root alongside `npm --prefix frontend install` and `uv sync` is not explained (a fresh reader may not realize the root `package.json` orchestrates `npm run dev` across both servers). Second, `GEMINI_API_KEYS` and `GEMINI_MODEL` are shown as commented lines in the `.env` snippet without clarifying which variables are required vs. optional (given the heuristic fallback) or what other variables `.env.example` contains.

1. **Severity**: Nit
2. **Affected Scope**: `Modo CLI (Opcional)` (Lines 81–84)
3. **Discrepancy Description & Rationale**: Incomplete CLI usage documentation. Only a single example invocation is provided, leaving unclear which flags (`--topic`, `--sample-size`, `--output-md`) are optional, what their defaults are, and whether `--help` or JSON output flags are supported.

1. **Severity**: Nit
2. **Affected Scope**: Project overview & architecture — External tools and libraries (Lines 3, 25–26, 37–41)
3. **Discrepancy Description & Rationale**: External tools, APIs, and libraries (`LangGraph`, `Crossref`, `OpenAlex`, `Semantic Scholar`, `ddgs`, `Crawl4AI`, `Playwright`, `PyMuPDF`, `Astro.js v5`, `FastAPI`) are referenced by name without links to their documentation. While most are well-known ecosystem tools, `ddgs` and `Crawl4AI` in particular would benefit from brief inline links or descriptions for a newcomer.

