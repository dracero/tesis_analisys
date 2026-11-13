# 🎓 Analista de Tesis Full-Stack (Multi-Agente + Verificación Web/Académica + Rotación Gemini)

Sistema Full-Stack diseñado para **tutores y jurados académicos**: permite subir una tesis en formato **PDF** desde la web y auditar automáticamente la bibliografía, la integridad académica y el uso real de las fuentes mediante **LangGraph**, **Crossref**, **OpenAlex**, **Semantic Scholar**, **DuckDuckGo (`ddgs`)**, **Crawl4AI + Playwright**, **Google Gemini con rotación de API keys** y **Astro.js v5**.

---

## 🔍 Qué Evalúa el Sistema (Los 6 Criterios del Tutor)

1. **Dominio del campo**:
   - Presencia de autores y trabajos fundacionales del tema.
   - Cobertura de debates actuales vs. bibliografía meramente básica.
   - Detección de **ausencias llamativas** comparando la **bibliografía completa** de la tesis contra literatura fundacional y de los últimos 5 años recuperada en OpenAlex (con extracción de palabras clave y marcado `already_in_thesis`) y búsqueda web.
2. **Calidad y jerarquía de las fuentes**:
   - Proporción entre fuentes primarias (papers arbitrados, repositorios académicos/preprints, normas, datasets, organismos oficiales), secundarias (libros/manuales) y fuentes débiles (blogs, Wikipedia, sitios sin autoría).
   - Extracción de `venue` (revista/conferencia), tipo de publicación y conteo de citas (`cited_by_count`) desde Crossref, OpenAlex y Semantic Scholar.
   - Detección de dependencia excesiva de una sola fuente o autor repetido y alertas por citas de segunda mano (*citado por / apud*).
3. **Actualidad y pertinencia**:
   - Distribución temporal (últimos 5 años, 6–10 años, >10 años, sin año).
   - Relación directa con los objetivos y secciones de la tesis para detectar bibliografía de relleno.
4. **Uso real de las fuentes**:
   - Segmentación automática por secciones (*Resumen/Abstract*, *Introducción/Objetivos*, *Marco Teórico/Estado del Arte*, *Metodología/Diseño*, *Resultados*, *Discusión y Conclusiones*).
   - Mapeo de en qué secciones se cita cada referencia (`cited_sections` y fragmentos de contexto etiquetados) para evaluar si sustentan el marco teórico, la metodología y la discusión, y si hay lectura crítica o mero resumen descriptivo.
5. **Integridad académica**:
   - **Cruce determinístico citas ↔ lista final**: citas en el cuerpo que no figuran en la bibliografía y referencias listadas que no se citan en el texto.
   - **Chequeo rápido en lote de toda la bibliografía**: validación de hasta 60 referencias con DOI/URL para detectar **DOIs inválidos**, **artículos retractados** (Crossref/OpenAlex) y **enlaces HTTP rotos (404/timeout)**.
   - **Muestreo profundo de 5 a 10 referencias clave**: verificación en **Crossref**, **OpenAlex**, **Semantic Scholar**, **DuckDuckGo** y **Crawl4AI/Playwright** (con validación anti-404/login/CAPTCHA y coincidencia de contenido) para comprobar que existen realmente y respaldan lo que afirma el tesista (detección de posibles referencias inventadas por IA o mal atribuidas).
6. **Aspectos formales**:
   - Consistencia de norma (APA / IEEE / Mixta), detección de entradas duplicadas, verificación de orden secuencial (IEEE) u orden alfabético y desambiguación `a`/`b` (APA), y completitud de datos (autores, año, título, DOI/URL y fecha de acceso).
   - Advertencia automática si el PDF es escaneado o tiene poco texto extraíble sin OCR.

Además genera automáticamente **vacíos concretos**, **devolución accionable para el tesista** y las **preguntas clave para la reunión de tutoría**.

---

## 🏛️ Arquitectura Multi-Agente (LangGraph)

1. **`pdf_parser` (`src/agents/pdf_parser.py`)**: Extrae texto ordenado con PyMuPDF (`sort=True`), detecta PDFs con bajo texto, segmenta las secciones principales de la tesis, separa cuerpo y bibliografía, detecta estilo (APA/IEEE), cruza citas en texto vs. lista final por sección, detecta duplicados/citas secundarias y calcula métricas temporales/jerárquicas.
2. **`verifier` (`src/agents/verifier.py`)**:
   - Ejecuta un **chequeo en lote** de DOIs, retracciones y enlaces HTTP sobre toda la bibliografía.
   - Verifica en profundidad 5–10 referencias clave en **Crossref**, **OpenAlex**, **Semantic Scholar**, **DuckDuckGo** y **Crawl4AI** (Chromium headless con validación de `status_code` y contenido).
   - Busca literatura fundacional y de los últimos 5 años del área y la cruza contra las referencias de la tesis.
3. **`evaluator` (`src/agents/evaluator.py`)**: Audita los 6 pilares con **Google Gemini** (configurable vía `GEMINI_MODEL`, por defecto `gemini-3.6-flash`) y **`GeminiKeyRotator`** (`src/key_manager.py`: *round-robin*, *cooldown* ante 429, exclusión ante 401/403 y sanitización de claves en logs), recibiendo las secciones detectadas, la bibliografía completa compacta, el chequeo en lote y las referencias verificadas.
4. **`reporter` (`src/agents/reporter.py`)**: Compila el informe completo en Markdown exportable (incluyendo advertencias de OCR, duplicados, DOIs inválidos, URLs rotas y retracciones) y el JSON estructurado para el dashboard web.

---

## ⚙️ Instalación y Ejecución

Las dependencias ya están sincronizadas con `uv` y `npm`. Para iniciar el servidor Full-Stack (Backend FastAPI `:8000` + Frontend Astro `:4321`):

```bash
npm run dev
```

- **Frontend Web**: `http://localhost:4321`
- **Backend API**: `http://localhost:8000`

### Modo CLI (Opcional)
```bash
uv run python main.py ruta/a/tesis.pdf --topic "Tema de la tesis" --sample-size 8 --output-md informe.md
```

