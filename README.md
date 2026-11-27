# 🎓 Analista de Tesis Full-Stack (Multi-Agente + Verificación Académica + Harness²)

Sistema Full-Stack diseñado para **tutores y jurados académicos**: permite subir una tesis en formato **PDF** desde la web o la terminal y auditar su bibliografía, integridad académica y uso real de las fuentes mediante [LangGraph](https://www.langchain.com/langgraph), [PyMuPDF](https://pymupdf.readthedocs.io/), [Crossref](https://www.crossref.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), [ddgs (DuckDuckGo Search)](https://pypi.org/project/ddgs/), [Crawl4AI](https://crawl4ai.com/) + [Playwright](https://playwright.dev/), **Google Gemini** (con rotación automática de API keys), un **Harness² modular evolutivo** y [Astro.js](https://astro.build/) v5.

---

## 🔍 Qué Evalúa el Sistema (Los 6 Criterios del Tutor)

### 1. Dominio del campo
- **Literatura fundacional y debates actuales:** Limpia el tema de la tesis quitando *stopwords* en español/inglés y consulta **OpenAlex** para recuperar los 5 trabajos históricos más citados (`fundacional`) y los 5 más citados de los últimos 5 años (`debate_actual`), complementados con 3 resultados web en español vía **DuckDuckGo**.
- **Cruce contra toda la bibliografía:** Compara por similitud de título y apellido+año cada trabajo recuperado contra **todas** las entradas bibliográficas de la tesis (marcando cuáles ya figuran y cuáles están ausentes).
- **Tema de búsqueda:** Se toma del campo opcional `thesis_topic` (`--topic` en CLI); si se omite, se infiere automáticamente desde los metadatos del PDF o las primeras líneas de la portada.

### 2. Calidad y jerarquía de las fuentes
- **Clasificación en 5 niveles:** Agrupa cada entrada bibliográfica en:
  - `primaria_paper` (artículos con DOI, revistas, conferencias, IEEE/ACM/Springer/Elsevier, arXiv/bioRxiv/medRxiv, SciELO, Redalyc, Dialnet, PubMed, repositorios `.edu`, tesis).
  - `primaria_norma_dataset` (normas ISO/IEEE/RFC, leyes/decretos, organismos `.gov`/WHO/IETF/W3C, datasets en Kaggle/HuggingFace/GitHub).
  - `secundaria_libro_manual` (editoriales, libros, manuales, capítulos con ISBN).
  - `web_blog_wiki` (Wikipedia, blogs, Medium, YouTube, sitios sin filiación académica).
  - `otra` (entradas sin pistas clasificatorias).
- **Métricas de calidad:** Extrae revista o conferencia (`venue`), tipo de documento y conteo de citas desde Crossref, OpenAlex y Semantic Scholar.
- **Alertas de concentración y citas indirectas:** Calcula los 6 autores más frecuentes (`top_authors`) para detectar dependencia excesiva de una sola fuente y detecta citas de segunda mano (*citado por*, *citado en*, *cited by*, *apud*).

### 3. Actualidad y pertinencia
- **Distribución temporal:** Clasifica la bibliografía en `<= 5 años` (incluyendo porcentaje sobre el total), `6–10 años`, `> 10 años` y `sin año`.
- **Pertinencia por sección:** Cruza los objetivos declarados en la tesis contra las secciones donde efectivamente se citan las fuentes para detectar referencias sin vínculo con los objetivos.

### 4. Uso real de las fuentes
- **Segmentación por secciones:** Detecta automáticamente encabezados de *Resumen / Abstract*, *Introducción / Objetivos*, *Marco Teórico / Estado del Arte*, *Metodología / Diseño*, *Resultados* y *Discusión y Conclusiones*.
- **Mapeo cita ↔ sección:** Registra en qué secciones aparece citada cada referencia y extrae hasta 3 fragmentos de contexto (`-160/+140` caracteres etiquetados con `[Sección]`) para evaluar si la fuente sustenta el marco teórico, la metodología o la discusión y si existe lectura crítica o mero resumen descriptivo.

### 5. Integridad académica
- **Cruce determinístico citas ↔ lista final:**
  - Soporta citas estilo **IEEE** (`[1]`, `[1, 2]`, `[1-3]`) y **APA** (`(Autor, 2021)`, `Autor (2021)`, citas narrativas con `et al.`/`&`/`y`, y citas agrupadas entre paréntesis como `(García, 2020; López, 2021)`).
  - Calcula `cited_but_not_listed` (citas huérfanas), `listed_but_not_cited` (bibliografía no citada) y `coherence_score` (`0–100%`).
- **Chequeo en lote (hasta 60 referencias con DOI o URL):**
  - Valida concurrentemente DOIs en OpenAlex/Crossref, detecta **artículos retractados** (`is_retracted` / `update-to: retraction`) y comprueba enlaces HTTP vivos (`HEAD` con fallback a `GET` en 403/405/501) para detectar URLs rotas (`404`/timeout). Cuando un DOI resuelve correctamente, omite la petición HTTP redundante.
  - Las referencias sin DOI/URL o por encima del tope de 60 no se verifican en red en esta pasada.
- **Muestreo de 5 a 10 referencias clave:**
  - Prioriza referencias citadas en el texto, con mayor cantidad de citas, con DOI/URL y con faltantes formales.
  - Consulta en cascada **Crossref → OpenAlex → Semantic Scholar → DuckDuckGo**; si Crossref coincide pero no trae abstract, busca el abstract en OpenAlex/Semantic Scholar.
  - Verifica coincidencia de título (`SequenceMatcher`), autor y año (`±1` año); marca como `SOSPECHOSA` si el DOI corresponde a otro título, si difiere el autor o si el artículo está retractado.
  - **Crawling con Crawl4AI + Playwright:** Para hasta 10 URLs que aún no obtuvieron abstract en las APIs anteriores, navega con Chromium headless (con semáforo global de proceso de 2 y concurrencia interna de 4) validando código HTTP `< 400`, bloqueando páginas 404/login/Cloudflare/CAPTCHA y exigiendo coincidencia de términos clave de la referencia antes de aceptar el texto.

### 6. Aspectos formales
- **Consistencia de norma:** Detecta `APA`, `IEEE`, `Mixta` o `Desconocida`.
- **Validación estructural:** Detecta entradas duplicadas, numeración IEEE no secuencial, rupturas de orden alfabético en APA, múltiples obras de mismo autor/año sin desambiguación `a`/`b`, faltantes de año/autor, URLs sin fecha de acceso y papers sin DOI ni URL.
- **Detección de PDFs escaneados:** Emite `low_text_warning` cuando el promedio de texto extraíble es inferior a 120 caracteres por página.

Con Gemini activo, el sistema genera además **vacíos bibliográficos**, **devolución para el tesista** y **preguntas para la reunión de tutoría** adaptadas al documento; en modo heurístico sin Gemini, emite métricas por regla y las 4 preguntas estándar de tutoría.

---

## 🏛️ Arquitectura Multi-Agente

El flujo principal está definido en `src/graph.py` (`START → pdf_parser → verifier → evaluator → reporter → END`):

1. **`pdf_parser` (`src/agents/pdf_parser.py`)**:
   - Extrae texto en orden de lectura con PyMuPDF (`sort=True`), separa cuerpo y bibliografía (evitando índices/TOC y cortando anexos/apéndices), segmenta secciones, parsea entradas y ejecuta el cruce determinístico citas ↔ bibliografía.
2. **`verifier` (`src/agents/verifier.py`)**:
   - Ejecuta en paralelo mediante `asyncio.gather`: el muestreo profundo de 5–10 referencias, el chequeo en lote de hasta 60 DOIs/URLs y la búsqueda de literatura del campo; luego ejecuta Crawl4AI/Playwright sobre las URLs sin abstract.
3. **`evaluator` (`src/agents/evaluator.py`)**:
   - Carga las instrucciones modulares del **Harness²** desde `harness/` mediante `src/harness_loader.py`.
   - Serializa secciones, métricas, cruce, chequeo en lote, lista compacta de toda la bibliografía y referencias verificadas con acotación válida de JSON (sin cortar strings JSON por la mitad), omitiendo recortes crudos de texto cuando hay ≥ 2 secciones detectadas.
   - Invoca **Google Gemini** (`GEMINI_MODEL`, por defecto `gemini-3.6-flash`) con salida estructurada Pydantic (`ThesisEvaluation`) y **`GeminiKeyRotator`** (`src/key_manager.py`: *round-robin*, *cooldown* de 60s ante HTTP 429, exclusión ante 401/403, corte rápido ante errores determinísticos 400/validación y sanitización de claves en logs).
   - **Modo heurístico de respaldo:** Si no hay claves activas o todas fallan, degrada a `_heuristic_fallback_evaluation`, calculando puntajes determinísticos por regla a partir de las métricas cuantitativas y emitiendo las 4 preguntas estándar (sin análisis semántico cualitativo).
4. **`reporter` (`src/agents/reporter.py`)**:
   - Compila el informe completo en Markdown exportable (`generate_thesis_markdown_report`), incluyendo advertencias de PDF escaneado y el detalle de duplicados, artículos retractados, DOIs inválidos y URLs rotas.
   - El payload JSON completo para el dashboard web es ensamblado por `server.py`.

---

## 🧪 Sistema Harness² Modular y Evolución (`harness/` y `harness_runs/`)

El comportamiento del agente evaluador está externalizado en archivos Markdown editables dentro de `harness/`, compatibles con evolución offline estilo `google-research/harness2`:

- `harness/system_prompt.md`: Rol e instrucciones base del tutor académico.
- `harness/guardrails.md`: Reglas anti-falsos positivos y anclaje en evidencia verificada.
- `harness/tool_guidance.md`: Criterios para interpretar Crossref, OpenAlex, Semantic Scholar, DuckDuckGo y Crawl4AI.
- `harness/memory.md`: Lecciones promovidas tras ciclos de mejora.
- `harness/skills/*.md`: Rúbricas modulares (`rubrica_6_pilares.md`, `normas_apa_ieee.md`, `integridad_y_alucinaciones.md`).
- `harness/scripts/verify_single_ref.py`: Script CLI autónomo (sin MCP) para verificar una referencia puntual por `--doi` (Crossref) o `--query` (OpenAlex).

### Captura automática, evolución y rollback
- **Hot-reload:** `src/harness_loader.py` cachea por `mtime` y calcula un `harness_hash` SHA-256 corto. Al promover cambios a `harness/`, `server.py` adopta la nueva versión en caliente sin reiniciar.
- **Fixtures congelados (`harness_runs/fixtures/`):** Cada tesis analizada por `/api/analyze` o `/api/analyze/stream` guarda automáticamente su snapshot (`parsed_thesis` + `verification_results`) mediante `save_harness_fixture()` (desactivable con `HARNESS_AUTO_CAPTURE=false`) para iterar offline sin repetir scraping ni descargas PDF.
- **Ciclo `Propose → Probe → Compose` (`scripts/harness2_offline.py` y `scripts/optimize_harness.py`):**
  1. **Propose:** Genera una hipótesis y ediciones sobre hasta 2 archivos de `harness/` en un directorio candidato aislado (`harness_runs/candidates/<run_id>/`).
  2. **Probe:** Evalúa los fixtures congelados con el `baseline` actual y con el `candidate`, comparándolos con un juez estructurado (`ProbeComparison`).
  3. **Compose / Promote:** Guarda el reporte comparativo con `diff` en `harness_runs/reports/<run_id>.md` y registra la corrida en `harness_runs/improvement_ledger.jsonl`. Si se promueve, crea antes un backup completo en `harness_runs/backups/<timestamp>/`, permitiendo revertir en 1 paso (`--rollback`).

---

## 🖥️ Interfaz Web (`frontend/src/pages/index.astro`)

Construida en **Astro.js v5** + **Tailwind CSS**:
- **Carga y seguimiento en vivo:** Subida de PDF (≤ 50 MB), tema opcional y selector de muestreo (5–10 referencias) con barra de progreso en tiempo real vía **SSE** y badge de estado de claves Gemini / modo heurístico.
- **Dashboard de resultados:** Calificación global, métricas rápidas (total de referencias, citas, coherencia %, % últimos 5 años, jerarquía), tarjetas con puntaje/veredicto/fortalezas/hallazgos/recomendaciones para los 6 pilares, listado de referencias muestreadas verificadas (con enlaces sanitizados `http://`/`https://`), preguntas de tutoría, devolución para el tesista y descarga en `.md` o `.json`.
- **Panel Harness² Online:** Permite inspeccionar el `harness_hash` activo y fixtures disponibles, lanzar rondas de optimización en segundo plano, promover candidatos o ejecutar rollback, revisar el historial del ledger y verificar referencias puntuales desde la UI.

---

## 🌐 Endpoints del Backend ([FastAPI](https://fastapi.tiangolo.com/) en `server.py`)

### Auditoría de Tesis y Frontend
- **`GET /`**: Sirve la compilación estática de producción del frontend (`frontend/dist`) cuando existe.
- **`GET /api/config`**: Devuelve el modelo configurado (`GEMINI_MODEL`), estado del pool de claves (`GeminiKeyRotator`) y metadatos del harness activo.
- **`POST /api/analyze`**: Endpoint REST síncrono (ejecuta `build_graph()`). Recibe `file` (PDF ≤ 50 MB leído en *streaming* por bloques de 64 KB), `thesis_topic` (opcional) y `sample_size` (5–10, por defecto `8`); retorna JSON con `filename`, `parsed_thesis`, `verification_results`, `evaluation`, `summary_report` y `key_status`.
- **`POST /api/analyze/stream`**: Endpoint SSE consumido por la UI web; transmite eventos `start`, `pdf_parser`, `pdf_parser_done`, `verifier`, `verifier_done`, `evaluator`, `evaluator_done`, `reporter` y `completed` / `error`. En `completed` omite `body_excerpt_*` para aligerar la respuesta al navegador.

### Harness² Online
- **`GET /api/harness/status`**: Estado del harness (`harness_hash`, archivos cargados), lista de fixtures, backups disponibles, estado del job en curso y últimas 10 corridas del ledger.
- **`POST /api/harness/optimize`**: Inicia en segundo plano un trabajo de optimización (`{ "focus": "...", "rounds": 1, "max_fixtures": 3, "auto_promote": false }`).
- **`POST /api/harness/promote`**: Promueve un candidato (`{ "run_id": "..." }`) a `./harness/` realizando backup previo.
- **`POST /api/harness/rollback`**: Restaura `./harness/` desde el último backup en `harness_runs/backups/`.
- **`POST /api/harness/verify-ref`**: Verifica una referencia puntual (`{ "query": "...", "doi": "..." }`) contra Crossref/OpenAlex.

---

## 📁 Estructura del Repositorio

```text
tesis_analisys/
├── server.py                      # Backend FastAPI (SSE, REST, Harness Online y estáticos)
├── main.py                        # CLI para auditoría directa desde terminal
├── pyproject.toml                 # Dependencias Python (>= 3.12) y lockfile uv.lock
├── package.json                   # Scripts dev/build/start y comandos npm run harness:*
├── src/
│   ├── graph.py                   # Grafo secuencial LangGraph (4 nodos)
│   ├── state.py                   # Esquemas Pydantic y GraphState
│   ├── key_manager.py             # Rotador de API keys Gemini con cooldown y redacción
│   ├── harness_loader.py          # Cargador modular del harness (caché mtime + auto-capture)
│   └── agents/
│       ├── pdf_parser.py          # PyMuPDF, secciones, citas IEEE/APA, métricas y duplicados
│       ├── verifier.py            # Crossref, OpenAlex, Semantic Scholar, DDG y Crawl4AI
│       ├── evaluator.py           # Evaluación Gemini estructurada + respaldo heurístico
│       └── reporter.py            # Generación de informe Markdown
├── harness/                       # Prompts, guardrails, skills, memory y verify_single_ref.py
├── scripts/
│   ├── harness2_offline.py        # Corredor offline Propose -> Probe -> Compose
│   └── optimize_harness.py        # Orquestador unificado con captura, promoción y rollback
└── frontend/                      # Aplicación Astro.js v5 + Tailwind CSS
```

---

## ⚙️ Instalación, Configuración y Comandos

### Prerrequisitos

- **Python `>= 3.12`** y **[`uv`](https://docs.astral.sh/uv/)**
- **Node.js y `npm`** (para el frontend Astro y `concurrently` en la raíz)

### 1. Instalar dependencias y navegador para Crawl4AI

```bash
uv sync
uv run playwright install chromium
npm install
npm --prefix frontend install
```

### 2. Configurar variables de entorno

Copia `.env.example` a `.env`. Todas las variables son opcionales (sin claves de Gemini se activa el modo heurístico):

```bash
cp .env.example .env
# GEMINI_API_KEYS=clave1,clave2,...   # Pool en rotación (también acepta GEMINI_API_KEY, GOOGLE_API_KEY o GEMINI_API_KEY_1..N)
# GEMINI_MODEL=gemini-3.6-flash       # Modelo Gemini a utilizar
# CORS_ORIGINS=                       # Orígenes adicionales separados por coma
# HARNESS_DIR=./harness               # Directorio del harness modular activo
# HARNESS_AUTO_CAPTURE=true           # Guarda fixtures automáticamente en harness_runs/fixtures/
```

### 3. Ejecutar en desarrollo y producción

```bash
# Desarrollo: Backend FastAPI (:8000) + Frontend Astro (:4321) en paralelo
npm run dev

# Producción: compilar frontend/dist y servir todo desde FastAPI (:8000)
npm run build
npm start
```

### 4. Usar modo CLI (opcional)

```bash
uv run python main.py ruta/a/tesis.pdf --topic "Tema de la tesis" --sample-size 8 --output-md informe.md --output-json resultado.json
```

- `ruta/a/tesis.pdf`: Ruta al archivo PDF (obligatorio).
- `--topic`: Tema opcional para orientar la búsqueda bibliográfica (por defecto `""`, se infiere del PDF).
- `--sample-size`: Cantidad de referencias clave a verificar (`5`–`10`, por defecto `8`).
- `--output-md` / `--output-json`: Rutas opcionales para guardar el informe Markdown o el JSON completo. Usar `--help` para ver la ayuda en terminal.

### 5. Comandos de optimización Harness²

```bash
# Capturar PDF(s) nuevos + optimizar + preguntar antes de promover
npm run harness:optimize -- --pdf ruta/a/tesis.pdf --topic "Tema de la tesis"

# Optimizar 2 rondas sobre fixtures existentes y promover si gana el candidato
npm run harness:optimize -- --rounds 2 --focus "reducir falsos positivos" --auto-promote

# Capturar fixture congelado manualmente
npm run harness:capture -- ruta/a/tesis.pdf --topic "Tema de la tesis" --sample-size 8

# Correr ciclo offline aislado en harness_runs/
npm run harness:offline -- --max-fixtures 3 --focus "reducir falsos positivos en ausencias bibliográficas"

# Revertir ./harness/ al último backup
npm run harness:rollback

# Verificar una referencia puntual por CLI
uv run python harness/scripts/verify_single_ref.py --query "Attention Is All You Need Vaswani 2017"
uv run python harness/scripts/verify_single_ref.py --doi "10.1038/nature14539"
```
