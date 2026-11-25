# 🎓 Analista de Tesis Full-Stack (Multi-Agente + Verificación Web/Académica + Rotación Gemini)

Sistema Full-Stack diseñado para **tutores y jurados académicos**: permite subir una tesis en formato **PDF** desde la web o la terminal y auditar su bibliografía, integridad académica y uso de las fuentes mediante [LangGraph](https://www.langchain.com/langgraph), [Crossref](https://www.crossref.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), [ddgs (DuckDuckGo Search)](https://pypi.org/project/ddgs/), [Crawl4AI](https://crawl4ai.com/) + [Playwright](https://playwright.dev/), Google Gemini (con rotación de API keys) y [Astro.js](https://astro.build/) v5.

---

## 🔍 Qué Evalúa el Sistema (Los 6 Criterios del Tutor)

1. **Dominio del campo**:
   - Presencia de autores y trabajos fundacionales del tema.
   - Cobertura de debates actuales frente a bibliografía introductoria o básica.
   - Detección de **ausencias bibliográficas** comparando la **bibliografía completa** de la tesis contra literatura fundacional y de los últimos 5 años recuperada en OpenAlex (marcando cuáles ya figuran en la tesis) y búsqueda web complementaria.
   - El tema de búsqueda se toma del campo opcional `thesis_topic` (`--topic` en CLI); si se omite, el sistema lo infiere desde los metadatos o las primeras líneas del PDF.
2. **Calidad y jerarquía de las fuentes**:
   - Proporción entre fuentes primarias (papers arbitrados, repositorios académicos/preprints, normas, datasets, organismos oficiales), secundarias (libros/manuales) y fuentes débiles (blogs, Wikipedia, sitios sin autoría).
   - Extracción de revista o conferencia de publicación, tipo de documento y conteo de citas desde Crossref, OpenAlex y Semantic Scholar.
   - Detección de dependencia excesiva de una sola fuente o autor repetido y alertas por citas de segunda mano (*citado por / apud*).
3. **Actualidad y pertinencia**:
   - Distribución temporal (últimos 5 años, 6–10 años, >10 años, sin año).
   - Relación directa con los objetivos y secciones de la tesis para detectar referencias sin vínculo con los objetivos.
4. **Uso real de las fuentes**:
   - Segmentación automática por secciones (*Resumen/Abstract*, *Introducción/Objetivos*, *Marco Teórico/Estado del Arte*, *Metodología/Diseño*, *Resultados*, *Discusión y Conclusiones*).
   - Mapeo de en qué secciones se cita cada referencia (y extracción de fragmentos de contexto en las referencias muestreadas) para evaluar si sustentan el marco teórico, la metodología y la discusión, y si hay lectura crítica o resumen descriptivo.
5. **Integridad académica**:
   - **Cruce determinístico citas ↔ lista final**: citas en el cuerpo que no figuran en la bibliografía y referencias listadas que no se citan en el texto (incluyendo citas agrupadas entre paréntesis).
   - **Chequeo en lote (hasta 60 referencias con DOI o URL)**: valida DOIs inválidos, artículos retractados (Crossref/OpenAlex) y enlaces HTTP rotos (omitiendo la petición HTTP cuando el DOI ya resolvió correctamente). Las referencias sin DOI/URL o por encima del tope de 60 no se verifican en red en esta pasada.
   - **Muestreo de 5 a 10 referencias clave (con recuperación de abstract)**: selecciona las referencias priorizando las más citadas en el cuerpo, las que tienen DOI/URL y las que presentan faltantes formales. Las verifica en **Crossref**, **OpenAlex**, **Semantic Scholar** y **DuckDuckGo**, y usa **Crawl4AI/Playwright** sobre hasta 10 URLs que aún no obtuvieron abstract en esas APIs (validando código HTTP, patrones de error 404/login/CAPTCHA y coincidencia de términos).
6. **Aspectos formales**:
   - Consistencia de norma (APA / IEEE / Mixta), detección de entradas duplicadas, verificación de orden secuencial (IEEE) u orden alfabético y desambiguación `a`/`b` (APA), y completitud de datos (autores, año, título, DOI/URL y fecha de acceso).
   - Advertencia automática si el PDF es escaneado o tiene poco texto extraíble sin OCR.

Con Gemini activo, el sistema genera **vacíos bibliográficos**, **devolución para el tesista** y **preguntas para la reunión de tutoría** adaptadas al documento; en modo heurístico sin Gemini, emite métricas por regla y las 4 preguntas estándar de tutoría.

---

## 🏛️ Arquitectura Multi-Agente

1. **`pdf_parser` (`src/agents/pdf_parser.py`)**: Extrae texto en orden de lectura con [PyMuPDF](https://pymupdf.readthedocs.io/), detecta PDFs con bajo texto, segmenta las secciones principales de la tesis, separa cuerpo y bibliografía, detecta estilo (APA/IEEE), cruza citas en texto vs. lista final por sección, detecta duplicados/citas secundarias y calcula métricas temporales/jerárquicas.
2. **`verifier` (`src/agents/verifier.py`)**:
   - Ejecuta en paralelo el **chequeo en lote** de DOIs, retracciones y enlaces HTTP (hasta 60 entradas con DOI/URL), la **búsqueda de literatura del campo** y la **verificación detallada** de 5–10 referencias clave.
   - Usa **Crawl4AI** (Chromium headless con límite de concurrencia global y por solicitud) sobre hasta 10 URLs sin abstract previo proveniente de Crossref/OpenAlex/Semantic Scholar.
3. **`evaluator` (`src/agents/evaluator.py`)**: Audita los 6 pilares con **Google Gemini** (configurable vía `GEMINI_MODEL`, por defecto `gemini-3.6-flash`) y **`GeminiKeyRotator`** (`src/key_manager.py`: *round-robin*, *cooldown* ante 429, exclusión ante 401/403 y sanitización de claves en logs).
   - **Modo heurístico de respaldo**: Si no hay claves de Gemini configuradas o todas fallan, degrada a un motor determinístico por reglas que calcula puntajes desde las métricas cuantitativas (coherencia, actualidad, jerarquía, errores formales y referencias sospechosas), emite una revisión básica de existencia y devuelve las 4 preguntas estándar de tutoría, pero **sin análisis semántico cualitativo ni contraste entre abstract y texto citado**.
4. **`reporter` (`src/agents/reporter.py`)**: Compila el informe completo en Markdown exportable (incluyendo advertencias de OCR y el detalle de duplicados, DOIs inválidos, URLs rotas y retracciones). El payload JSON estructurado para el dashboard web se ensambla en `server.py`.

---

## 🌐 Endpoints del Backend

El servidor [FastAPI](https://fastapi.tiangolo.com/) en `server.py` expone los siguientes endpoints y sirve la interfaz web compilada de `frontend/`:

- **`GET /`**: Sirve la compilación estática de producción del frontend (`frontend/dist`) cuando existe.
- **`GET /api/config`**: Devuelve el modelo configurado (`GEMINI_MODEL`) y el estado del pool de claves en rotación (`GeminiKeyRotator`).
- **`POST /api/analyze`**: Endpoint REST síncrono (ejecuta el grafo de LangGraph en `src/graph.py`) para integraciones externas. Recibe `file` (PDF ≤ 50 MB), `thesis_topic` (opcional) y `sample_size` (5–10, por defecto `8`), y retorna JSON con `filename`, `parsed_thesis`, `verification_results`, `evaluation`, `summary_report` y `key_status`.
- **`POST /api/analyze/stream`**: Endpoint consumido por el dashboard web en Astro.js (`frontend/src/pages/index.astro`); ejecuta los 4 pasos transmitiendo eventos **Server-Sent Events (SSE)** (`start`, `pdf_parser`, `pdf_parser_done`, `verifier`, `verifier_done`, `evaluator`, `evaluator_done`, `reporter`, `completed` / `error`). En el evento `completed` devuelve el mismo esquema JSON que `/api/analyze`, omitiendo los extractos largos del cuerpo para aligerar la respuesta al navegador.

---

## ⚙️ Instalación y Ejecución

### Prerrequisitos

- **Python `>= 3.12`** y **[`uv`](https://docs.astral.sh/uv/)**
- **Node.js y `npm`** (para el frontend Astro y el script `concurrently` de la raíz)

### 1. Instalar dependencias y navegador para Crawl4AI

```bash
uv sync
uv run playwright install chromium
npm install
npm --prefix frontend install
```

*(El `npm install` en la raíz instala `concurrently` definido en `package.json` para iniciar backend y frontend con un solo comando).*

### 2. Configurar variables de entorno

Copia `.env.example` a `.env`. Todas las variables son opcionales (sin claves de Gemini se activa el modo heurístico):

```bash
cp .env.example .env
# GEMINI_API_KEYS=clave1,clave2,...   # Pool de claves en rotación (o GEMINI_API_KEY)
# GEMINI_MODEL=gemini-3.6-flash       # Modelo de Gemini a utilizar
# CORS_ORIGINS=                       # Orígenes adicionales separados por coma
```

### 3. Ejecutar en desarrollo

Inicia en paralelo el Backend FastAPI (`:8000`) y el Frontend Astro (`:4321`):

```bash
npm run dev
```

### 4. Compilar y ejecutar en producción

Genera `frontend/dist` y sirve tanto la API como el frontend estático desde FastAPI en el puerto `8000`:

```bash
npm run build
npm start
```

### 5. Usar modo CLI (opcional)

```bash
uv run python main.py ruta/a/tesis.pdf --topic "Tema de la tesis" --sample-size 8 --output-md informe.md --output-json resultado.json
```

- `ruta/a/tesis.pdf`: Ruta al archivo PDF (obligatorio).
- `--topic`: Tema opcional para orientar la búsqueda bibliográfica (por defecto `""`, se infiere del PDF).
- `--sample-size`: Cantidad de referencias clave a verificar (`5`–`10`, por defecto `8`).
- `--output-md` / `--output-json`: Rutas opcionales para guardar el informe Markdown o el JSON completo. Usar `--help` para ver la ayuda en terminal.


---

## 🧪 Optimización del Evaluador con Harness² (`harness/`)

El evaluador carga sus instrucciones, guardrails y rúbricas de forma modular desde `harness/` (`system_prompt.md`, `guardrails.md`, `tool_guidance.md`, `memory.md` y `skills/*.md`) con recarga en caliente por `mtime` y trazabilidad en `GET /api/config`.

Cada PDF procesado por la web (`/api/analyze/stream`) o la API (`/api/analyze`) guarda automáticamente su fixture congelado en `harness_runs/fixtures/` (desactivable con `HARNESS_AUTO_CAPTURE=false`).

### 1. Optimización en un solo comando (recomendado)

```bash
# Capturar un PDF (o una carpeta de PDFs) si aún no tiene fixture + optimizar + preguntar antes de promover
npm run harness:optimize -- --pdf ruta/a/tesis.pdf --topic "Tema de la tesis"

# Optimizar sobre los fixtures ya guardados (2 rondas) y promover automáticamente si gana el candidato
npm run harness:optimize -- --rounds 2 --focus "reducir falsos positivos" --auto-promote

# Forzar re-captura de fixtures existentes y limitar a 5 fixtures por ronda sin preguntar al final
npm run harness:optimize -- --pdf carpeta_tesis/ --refresh --max-fixtures 5 --no-prompt

# Revertir ./harness/ al último backup guardado (hot-reload inmediato en server.py)
npm run harness:rollback
```

### 2. Flujo offline paso a paso (`harness2_offline.py`)

```bash
# Paso 1: Capturar fixture congelado (ejecuta pdf_parser + verifier una sola vez)
npm run harness:capture -- ruta/a/tesis.pdf --topic "Tema de la tesis" --sample-size 8

# Paso 2: Ejecutar un ciclo Propose -> Probe -> Compose aislado en harness_runs/ (sin modificar ./harness/)
npm run harness:offline -- --max-fixtures 3 --focus "reducir falsos positivos en ausencias bibliográficas"
```

Los reportes comparativos con `diff` se guardan en `harness_runs/reports/<run_id>.md`, los backups en `harness_runs/backups/` y el historial en `harness_runs/improvement_ledger.jsonl`.

### 3. Verificación puntual de una referencia (CLI sin MCP)

```bash
# Por título / cita textual (consulta OpenAlex)
uv run python harness/scripts/verify_single_ref.py --query "Attention Is All You Need Vaswani 2017"

# Por DOI exacto (consulta Crossref)
uv run python harness/scripts/verify_single_ref.py --doi "10.1038/nature14539"
```

### Variables de entorno del Harness (opcionales)

```bash
HARNESS_DIR=./harness          # Directorio del harness modular activo
HARNESS_AUTO_CAPTURE=true      # Guarda fixtures automáticamente al analizar PDFs en server.py
```

### 4. Endpoints Online del Harness (`server.py` + Panel Web)

También podés operar todo el ciclo desde el panel **Harness² Online** en la interfaz web o directamente vía API REST mientras `server.py` está activo:

- `GET /api/harness/status`: estado del harness activo (`harness_hash`), fixtures disponibles, backups, estado del job y últimas corridas.
- `POST /api/harness/optimize`: lanza `Propose → Probe → Compose` en segundo plano (`{ "focus": "...", "rounds": 1, "max_fixtures": 3, "auto_promote": false }`).
- `POST /api/harness/promote`: promueve un candidato ganador (`{ "run_id": "..." }`) a `./harness/` con backup previo y recarga en caliente.
- `POST /api/harness/rollback`: restaura `./harness/` desde el último backup en `harness_runs/backups/`.
- `POST /api/harness/verify-ref`: verifica una referencia puntual (`{ "query": "...", "doi": "..." }`) contra Crossref/OpenAlex.
