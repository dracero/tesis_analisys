"""Servidor FastAPI Full-Stack para el Analista de Tesis en PDF."""

import asyncio
import json
import os
import tempfile
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from src.agents.evaluator import evaluate_thesis
from src.agents.pdf_parser import parse_thesis_pdf
from src.agents.reporter import generate_thesis_markdown_report
from src.agents.verifier import verify_references_and_field_async
from src.graph import build_graph
from src.key_manager import get_key_rotator

app = FastAPI(
    title="Analista de Tesis Full-Stack",
    description="Auditoría académica de tesis en PDF con verificación web/Crossref/OpenAlex/Crawl4AI y Gemini 3.6 con rotación de claves",
    version="1.0.0",
)

_default_origins = [
    "http://localhost:4321",
    "http://127.0.0.1:4321",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


async def _save_temp_pdf(file: UploadFile) -> str:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe tener extensión .pdf")

    fd, tmp_path = tempfile.mkstemp(prefix="tesis_", suffix=".pdf")
    total_bytes = 0
    first_chunk = True
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await file.read(65536):
                if first_chunk:
                    first_chunk = False
                    if not chunk.startswith(b"%PDF"):
                        raise HTTPException(status_code=400, detail="El archivo subido no es un PDF válido")
                total_bytes += len(chunk)
                if total_bytes > MAX_PDF_BYTES:
                    raise HTTPException(status_code=413, detail="El archivo supera el límite máximo de 50 MB")
                f.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="El archivo PDF está vacío")
        return tmp_path
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


@app.get("/api/config")
async def get_config():
    """Devuelve el estado de la configuración de Gemini y su pool de claves en rotación."""
    key_status = get_key_rotator().get_status()
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    return {
        "gemini_configured": key_status["total_keys"] > 0,
        "gemini_keys_count": key_status["total_keys"],
        "gemini_key_status": key_status,
        "model": model,
    }


@app.post("/api/analyze")
async def analyze_thesis_rest(
    file: UploadFile = File(...),
    thesis_topic: str = Form(default=""),
    sample_size: int = Form(default=8),
):
    """Ejecuta el grafo multi-agente completo sobre el PDF subido y retorna JSON."""
    tmp_path = await _save_temp_pdf(file)
    safe_sample = max(5, min(10, sample_size))
    try:
        graph = build_graph()
        initial_state = {
            "pdf_path": tmp_path,
            "filename": file.filename or "tesis.pdf",
            "thesis_topic": thesis_topic,
            "sample_size": safe_sample,
            "parsed_thesis": {},
            "verification_results": {},
            "evaluation": {},
            "summary_report": "",
        }
        final_state = await asyncio.to_thread(graph.invoke, initial_state)
        return {
            "filename": file.filename,
            "parsed_thesis": final_state.get("parsed_thesis", {}),
            "verification_results": final_state.get("verification_results", {}),
            "evaluation": final_state.get("evaluation", {}),
            "summary_report": final_state.get("summary_report", ""),
            "key_status": get_key_rotator().get_status(),
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _thesis_stream_generator(
    tmp_path: str,
    filename: str,
    thesis_topic: str,
    sample_size: int,
) -> AsyncGenerator[str, None]:
    def send_event(step: str, message: str, payload: dict = None) -> str:
        data = {"step": step, "message": message, "payload": payload or {}}
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield send_event("start", f"Iniciando auditoría de '{filename}'...")
        await asyncio.sleep(0.1)

        # Paso 1: Extracción PDF y análisis cuantitativo + cruce citas-lista
        yield send_event("pdf_parser", "Extrayendo texto del PDF, bibliografía y cruzando citas en el texto...")
        parsed = await asyncio.to_thread(parse_thesis_pdf, tmp_path, thesis_topic)
        bib_count = len(parsed.get("bibliography_entries", []))
        cross = parsed.get("cross_check", {})
        yield send_event(
            "pdf_parser_done",
            f"PDF procesado: {parsed.get('page_count', 0)} págs · {bib_count} referencias · Norma {cross.get('detected_style', 'Desconocida')}.",
            {"pages": parsed.get("page_count", 0), "bibliography_count": bib_count, "style": cross.get("detected_style")},
        )

        # Paso 2: Verificación web/académica de muestreo (5-10 refs) + literatura del campo
        yield send_event("verifier", f"Verificando {min(sample_size, bib_count)} referencias clave en Crossref, OpenAlex, DuckDuckGo y Crawl4AI...")
        verification = await verify_references_and_field_async(parsed, sample_size=sample_size)
        yield send_event(
            "verifier_done",
            f"Muestreo verificado: {verification.get('verified_count', 0)} confirmadas, {verification.get('partial_count', 0)} parciales, {verification.get('suspect_or_missing_count', 0)} sospechosas/no halladas.",
            {
                "sampled": verification.get("sampled_count", 0),
                "verified": verification.get("verified_count", 0),
                "suspect": verification.get("suspect_or_missing_count", 0),
            },
        )

        # Paso 3: Evaluación de Tutor con Gemini + rotación de API keys
        key_status = get_key_rotator().get_status()
        active_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
        yield send_event(
            "evaluator",
            f"Evaluando los 6 criterios de tutoría con {active_model} ({key_status.get('active_keys', 0)} keys activas en rotación)...",
        )
        evaluation_model = await asyncio.to_thread(evaluate_thesis, filename, parsed, verification)
        evaluation = evaluation_model.model_dump()
        yield send_event(
            "evaluator_done",
            f"Evaluación completada: {evaluation.get('overall_score', 0)}/10 ({evaluation.get('overall_verdict', '')}).",
        )

        # Paso 4: Generación de reporte Markdown
        yield send_event("reporter", "Generando informe final y preguntas de tutoría...")
        report_md = generate_thesis_markdown_report(filename, parsed, verification, evaluation)

        # Aligerar extractos largos del cuerpo en el payload final enviado al navegador
        parsed_light = {k: v for k, v in parsed.items() if not k.startswith("body_excerpt_")}

        yield send_event("completed", "¡Auditoría de tesis completada!", {
            "filename": filename,
            "parsed_thesis": parsed_light,
            "verification_results": verification,
            "evaluation": evaluation,
            "summary_report": report_md,
            "key_status": get_key_rotator().get_status(),
        })
    except Exception as e:
        yield send_event("error", f"Error durante el análisis: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/analyze/stream")
async def analyze_thesis_stream(
    file: UploadFile = File(...),
    thesis_topic: str = Form(default=""),
    sample_size: int = Form(default=8),
):
    """Recibe la tesis en PDF y transmite el avance en tiempo real mediante SSE."""
    tmp_path = await _save_temp_pdf(file)
    safe_sample = max(5, min(10, sample_size))
    return StreamingResponse(
        _thesis_stream_generator(
            tmp_path=tmp_path,
            filename=file.filename or "tesis.pdf",
            thesis_topic=thesis_topic,
            sample_size=safe_sample,
        ),
        media_type="text/event-stream",
    )


astro_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.exists(os.path.join(astro_dist, "_astro")):
    app.mount("/_astro", StaticFiles(directory=os.path.join(astro_dist, "_astro")), name="astro_assets")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(astro_dist, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Ejecuta 'npm run dev' para el modo desarrollo o 'npm run build' para producción.</h1>"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

