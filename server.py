"""Servidor FastAPI Full-Stack para el Analista de Tesis en PDF."""

import asyncio
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from harness.scripts.verify_single_ref import verify_reference
from scripts.harness2_offline import (
    CANDIDATES_DIR,
    EDITABLE_FILES,
    FIXTURES_DIR,
    LEDGER_PATH,
    RUNS_DIR,
    cmd_optimize,
)
from src.agents.evaluator import evaluate_thesis
from src.agents.pdf_parser import parse_thesis_pdf
from src.agents.reporter import generate_thesis_markdown_report
from src.agents.verifier import verify_references_and_field_async
from src.graph import build_graph
from src.harness_loader import get_harness_metadata, resolve_harness_dir, save_harness_fixture
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
        "harness": get_harness_metadata(),
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
        save_harness_fixture(
            filename=file.filename or "tesis.pdf",
            thesis_topic=thesis_topic,
            parsed_thesis=final_state.get("parsed_thesis", {}),
            verification_results=final_state.get("verification_results", {}),
        )
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
        save_harness_fixture(
            filename=filename,
            thesis_topic=thesis_topic,
            parsed_thesis=parsed,
            verification_results=verification,
        )
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


# ==============================================================================
# ENDPOINTS ONLINE DE HARNESS² (Optimización, Promoción con Backup, Rollback y Verify-Ref)
# ==============================================================================
HARNESS_BACKUPS_DIR = RUNS_DIR / "backups"
_harness_lock = asyncio.Lock()
_harness_job: Dict[str, Any] = {
    "state": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "rounds_total": 0,
    "rounds_completed": 0,
    "last_results": [],
    "error": None,
}


class HarnessOptimizeRequest(BaseModel):
    focus: str = Field(default="")
    max_fixtures: int = Field(default=3, ge=1, le=10)
    rounds: int = Field(default=1, ge=1, le=3)
    auto_promote: bool = Field(default=False)


class HarnessPromoteRequest(BaseModel):
    run_id: str = Field(..., min_length=4, max_length=64)


class VerifyRefRequest(BaseModel):
    query: str = Field(default="")
    doi: str = Field(default="")


def _promote_candidate_dir(candidate_dir: Path) -> Path:
    prod_harness = resolve_harness_dir()
    HARNESS_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = HARNESS_BACKUPS_DIR / ts
    shutil.copytree(prod_harness, backup_dir)
    for rel in EDITABLE_FILES:
        src = candidate_dir / rel
        if src.is_file():
            dst = prod_harness / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return backup_dir


def _read_recent_ledger(limit: int = 10) -> list[dict[str, Any]]:
    if not LEDGER_PATH.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(rows[-limit:]))


@app.get("/api/harness/status")
async def get_harness_status():
    fixtures = sorted(p.name for p in FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.is_dir() else []
    backups = sorted(p.name for p in HARNESS_BACKUPS_DIR.iterdir() if p.is_dir()) if HARNESS_BACKUPS_DIR.is_dir() else []
    return {
        "harness": get_harness_metadata(),
        "fixtures_count": len(fixtures),
        "fixtures": fixtures,
        "backups_count": len(backups),
        "latest_backup": backups[-1] if backups else None,
        "job": _harness_job,
        "recent_runs": _read_recent_ledger(10),
    }


async def _run_harness_optimize_job(req: HarnessOptimizeRequest) -> None:
    async with _harness_lock:
        _harness_job.update(
            {
                "state": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "rounds_total": req.rounds,
                "rounds_completed": 0,
                "last_results": [],
                "error": None,
            }
        )
        try:
            results = []
            for _ in range(req.rounds):
                entry = await asyncio.to_thread(cmd_optimize, req.max_fixtures, req.focus)
                promoted = False
                if req.auto_promote and entry.get("winner") == "candidate" and entry.get("edited_files"):
                    _promote_candidate_dir(Path(entry["candidate_dir"]))
                    promoted = True
                results.append({**entry, "promoted": promoted})
                _harness_job["rounds_completed"] += 1
                _harness_job["last_results"] = results
            _harness_job["state"] = "done"
            _harness_job["finished_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as err:
            _harness_job["state"] = "error"
            _harness_job["error"] = str(err)
            _harness_job["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/api/harness/optimize")
async def start_harness_optimize(req: HarnessOptimizeRequest):
    fixtures = list(FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.is_dir() else []
    if not fixtures:
        raise HTTPException(
            status_code=400,
            detail="No hay fixtures capturados todavía. Analizá al menos una tesis en PDF primero para generar su fixture automáticamente.",
        )
    if _harness_job["state"] == "running":
        raise HTTPException(status_code=409, detail="Ya hay una optimización de Harness² en ejecución.")

    asyncio.create_task(_run_harness_optimize_job(req))
    return {"started": True, "fixtures_available": len(fixtures), "rounds": req.rounds}


@app.post("/api/harness/promote")
async def promote_harness_run(req: HarnessPromoteRequest):
    if not re.fullmatch(r"[0-9_]+", req.run_id):
        raise HTTPException(status_code=400, detail="run_id inválido.")
    cand_dir = (CANDIDATES_DIR / req.run_id).resolve()
    if not cand_dir.is_dir() or CANDIDATES_DIR.resolve() not in cand_dir.parents:
        raise HTTPException(status_code=404, detail=f"Candidato no encontrado: {req.run_id}")

    backup_dir = _promote_candidate_dir(cand_dir)
    return {
        "promoted": True,
        "run_id": req.run_id,
        "backup": backup_dir.name,
        "harness": get_harness_metadata(),
    }


@app.post("/api/harness/rollback")
async def rollback_harness():
    if not HARNESS_BACKUPS_DIR.is_dir():
        raise HTTPException(status_code=404, detail="No hay backups de harness disponibles.")
    backups = sorted(p for p in HARNESS_BACKUPS_DIR.iterdir() if p.is_dir())
    if not backups:
        raise HTTPException(status_code=404, detail="No hay backups de harness disponibles.")

    latest = backups[-1]
    prod_harness = resolve_harness_dir()
    for rel in EDITABLE_FILES:
        src = latest / rel
        if src.is_file():
            dst = prod_harness / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    return {
        "rolled_back": True,
        "restored_from": latest.name,
        "harness": get_harness_metadata(),
    }


@app.post("/api/harness/verify-ref")
async def verify_single_ref_online(req: VerifyRefRequest):
    if not req.query.strip() and not req.doi.strip():
        raise HTTPException(status_code=400, detail="Indicá query o doi para verificar.")
    return await asyncio.to_thread(verify_reference, req.query.strip(), req.doi.strip())


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

