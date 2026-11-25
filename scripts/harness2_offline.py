"""Corredor Offline estilo google-research/harness2 (Propose -> Probe -> Compose) para tesis_analisys.

Regla de seguridad y reproducibilidad:
  - NUNCA modifica el directorio productivo ./harness/.
  - Trabaja sobre fixtures congelados (parsed_thesis + verification_results) para evitar repetir scraping.
  - Genera candidatos aislados en ./harness_runs/candidates/<run_id>/, reportes comparativos + diff en
    ./harness_runs/reports/<run_id>.md y registra evidencia en ./harness_runs/improvement_ledger.jsonl.
  - La promoción a ./harness/ es siempre una decisión humana versionada en Git.

Uso:
  1) Capturar fixture congelado desde un PDF (corre pdf_parser + verifier una sola vez):
     uv run python scripts/harness2_offline.py capture ruta/a/tesis.pdf --topic "IA en educación"

  2) Ejecutar ciclo Propose -> Probe -> Compose sobre los fixtures capturados:
     uv run python scripts/harness2_offline.py optimize --max-fixtures 3
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.agents.evaluator import DEFAULT_MODEL, evaluate_thesis
from src.agents.pdf_parser import pdf_parser_node
from src.agents.verifier import reference_verifier_node
from src.harness_loader import resolve_harness_dir
from src.key_manager import get_key_rotator

RUNS_DIR = PROJECT_ROOT / "harness_runs"
FIXTURES_DIR = RUNS_DIR / "fixtures"
CANDIDATES_DIR = RUNS_DIR / "candidates"
REPORTS_DIR = RUNS_DIR / "reports"
LEDGER_PATH = RUNS_DIR / "improvement_ledger.jsonl"

EDITABLE_FILES = (
    "guardrails.md",
    "tool_guidance.md",
    "memory.md",
    "skills/rubrica_6_pilares.md",
    "skills/normas_apa_ieee.md",
    "skills/integridad_y_alucinaciones.md",
)


class HarnessFileEdit(BaseModel):
    relative_path: str = Field(description="Ruta relativa editable dentro del harness (ej: guardrails.md)")
    rationale: str = Field(description="Debilidad observada en baseline que justifica el cambio")
    new_content: str = Field(description="Contenido completo propuesto para el archivo")


class HarnessProposal(BaseModel):
    hypothesis: str = Field(description="Hipótesis concreta de mejora del evaluador")
    edits: List[HarnessFileEdit] = Field(default_factory=list, max_length=2)


class ProbeComparison(BaseModel):
    winner: str = Field(description="'candidate', 'baseline' o 'tie'")
    evidence_grounded_delta: str = Field(description="Diferencias concretas respaldadas por el fixture")
    regressions_detected: List[str] = Field(default_factory=list)
    recommendation: str = Field(description="Si conviene promover el diff a ./harness/ o descartarlo")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tesis"


def _invoke_structured(schema_cls: type[BaseModel], prompt: str) -> Any:
    rotator = get_key_rotator()
    ordered = rotator.next_ordered_keys()
    if not ordered:
        raise RuntimeError("No hay GEMINI_API_KEYS configuradas para ejecutar Propose/Compose offline.")

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    last_err = None
    for _, api_key in ordered:
        try:
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=0.2,
                max_retries=0,
                timeout=90,
            )
            res = llm.with_structured_output(schema_cls).invoke(prompt)
            rotator.report_success(api_key)
            return res
        except Exception as err:
            last_err = err
            continue
    raise RuntimeError(f"Fallaron todas las claves Gemini en harness2_offline: {last_err}")


def cmd_capture(pdf_path: str, topic: str, sample_size: int) -> None:
    src_pdf = Path(pdf_path).resolve()
    if not src_pdf.is_file():
        raise FileNotFoundError(f"No existe el PDF: {src_pdf}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    state: Dict[str, Any] = {
        "pdf_path": str(src_pdf),
        "filename": src_pdf.name,
        "thesis_topic": topic,
        "sample_size": max(5, min(10, sample_size)),
        "parsed_thesis": {},
        "verification_results": {},
        "evaluation": {},
        "summary_report": "",
    }

    print(f"[capture] 1/2 Ejecutando pdf_parser sobre {src_pdf.name}...")
    state.update(pdf_parser_node(state))  # type: ignore[arg-type]

    print(f"[capture] 2/2 Ejecutando verifier (Crossref/OpenAlex/S2/DDG/Crawl4AI)...")
    state.update(reference_verifier_node(state))  # type: ignore[arg-type]

    fixture_path = FIXTURES_DIR / f"{_slugify(src_pdf.stem)}.json"
    fixture_payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "filename": src_pdf.name,
        "thesis_topic": topic,
        "parsed_thesis": state["parsed_thesis"],
        "verification_results": state["verification_results"],
    }
    fixture_path.write_text(json.dumps(fixture_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[capture] ✓ Fixture congelado guardado en {fixture_path}")


def cmd_optimize(max_fixtures: int, focus: str) -> Dict[str, Any]:
    fixture_files = sorted(FIXTURES_DIR.glob("*.json"))[:max_fixtures]
    if not fixture_files:
        print(f"No hay fixtures en {FIXTURES_DIR}. Ejecuta primero: python scripts/harness2_offline.py capture <tesis.pdf>")
        sys.exit(1)

    base_harness = resolve_harness_dir()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cand_dir = CANDIDATES_DIR / run_id
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. BASELINE
    baseline_outputs: List[Dict[str, Any]] = []
    for fpath in fixture_files:
        fx = json.loads(fpath.read_text(encoding="utf-8"))
        print(f"[baseline] Evaluando fixture {fpath.name} con harness actual...")
        ev = evaluate_thesis(
            filename=fx["filename"],
            parsed=fx["parsed_thesis"],
            verification=fx["verification_results"],
            harness_dir=base_harness,
        )
        baseline_outputs.append({"fixture": fpath.name, "evaluation": ev.model_dump()})

    # 2. PROPOSE
    current_files_dump = {
        rel: (base_harness / rel).read_text(encoding="utf-8")
        for rel in EDITABLE_FILES
        if (base_harness / rel).is_file()
    }
    propose_prompt = (
        "Eres el editor de harness de Harness² (offline). Analiza los resultados baseline del evaluador de tesis "
        "y propón como máximo 2 ediciones puntuales y justificadas sobre los archivos editables del harness "
        f"(permitidos: {', '.join(EDITABLE_FILES)}) para reducir falsos positivos, mejorar anclaje en evidencia "
        "y hacer más accionables las observaciones.\n"
        f"Foco opcional del operador: {focus or 'General (rigor y cero falsos positivos)'}\n\n"
        f"=== ARCHIVOS ACTUALES DEL HARNESS ===\n{json.dumps(current_files_dump, ensure_ascii=False, indent=2)[:18000]}\n\n"
        f"=== EVALUACIONES BASELINE ===\n{json.dumps(baseline_outputs, ensure_ascii=False, indent=2)[:18000]}"
    )
    print("[propose] Generando candidato de harness...")
    proposal: HarnessProposal = _invoke_structured(HarnessProposal, propose_prompt)

    shutil.copytree(base_harness, cand_dir)
    applied_diffs: List[str] = []
    for edit in proposal.edits:
        if edit.relative_path not in EDITABLE_FILES:
            continue
        target = cand_dir / edit.relative_path
        old_text = target.read_text(encoding="utf-8") if target.is_file() else ""
        target.write_text(edit.new_content.strip() + "\n", encoding="utf-8")
        diff_lines = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            (edit.new_content.strip() + "\n").splitlines(keepends=True),
            fromfile=f"baseline/{edit.relative_path}",
            tofile=f"candidate/{edit.relative_path}",
        )
        applied_diffs.append("".join(diff_lines))

    # 3. PROBE
    candidate_outputs: List[Dict[str, Any]] = []
    for fpath in fixture_files:
        fx = json.loads(fpath.read_text(encoding="utf-8"))
        print(f"[probe] Evaluando fixture {fpath.name} con harness candidato {run_id}...")
        ev = evaluate_thesis(
            filename=fx["filename"],
            parsed=fx["parsed_thesis"],
            verification=fx["verification_results"],
            harness_dir=cand_dir,
        )
        candidate_outputs.append({"fixture": fpath.name, "evaluation": ev.model_dump()})

    # 4. COMPOSE (comparación sin ground truth fijo)
    compose_prompt = (
        "Compara las evaluaciones BASELINE vs CANDIDATE producidas sobre los mismos fixtures de tesis. "
        "Evalúa cuál está mejor anclada en evidencia, tiene menos falsos positivos y ofrece retroalimentación más útil.\n\n"
        f"Hipótesis del candidato: {proposal.hypothesis}\n\n"
        f"=== BASELINE ===\n{json.dumps(baseline_outputs, ensure_ascii=False, indent=2)[:16000]}\n\n"
        f"=== CANDIDATE ===\n{json.dumps(candidate_outputs, ensure_ascii=False, indent=2)[:16000]}"
    )
    print("[compose] Contrastando baseline vs candidato...")
    comparison: ProbeComparison = _invoke_structured(ProbeComparison, compose_prompt)

    report_path = REPORTS_DIR / f"{run_id}.md"
    report_md = "\n".join(
        [
            f"# Harness² Offline Run `{run_id}`",
            "",
            f"- **Fixtures evaluados**: {', '.join(f.name for f in fixture_files)}",
            f"- **Ganador**: `{comparison.winner}`",
            f"- **Hipótesis**: {proposal.hypothesis}",
            f"- **Directorio candidato aislado**: `{cand_dir}`",
            "",
            "## Contraste de Evidencia",
            comparison.evidence_grounded_delta,
            "",
            "## Regresiones Detectadas",
            *(f"- {r}" for r in (comparison.regressions_detected or ["Ninguna detectada."])),
            "",
            "## Recomendación",
            comparison.recommendation,
            "",
            "## Diff Propuesto (NO aplicado automáticamente a `./harness/`)",
            "```diff",
            *applied_diffs,
            "```",
        ]
    )
    report_path.write_text(report_md, encoding="utf-8")

    ledger_entry = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixtures": [f.name for f in fixture_files],
        "hypothesis": proposal.hypothesis,
        "edited_files": [e.relative_path for e in proposal.edits if e.relative_path in EDITABLE_FILES],
        "winner": comparison.winner,
        "report_path": str(report_path),
        "candidate_dir": str(cand_dir),
    }
    with LEDGER_PATH.open("a", encoding="utf-8") as lf:
        lf.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

    print(f"[compose] ✓ Reporte guardado en {report_path}")
    print(f"[compose] ✓ Ledger actualizado en {LEDGER_PATH}")
    print(f"[compose] Ganador: {comparison.winner}. Revisá el diff antes de promover a ./harness/.")
    return ledger_entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimizador offline estilo google-research/harness2")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capture", help="Captura fixture congelado (pdf_parser + verifier) desde un PDF")
    p_cap.add_argument("pdf", help="Ruta al PDF de tesis")
    p_cap.add_argument("--topic", default="", help="Tema opcional de la tesis")
    p_cap.add_argument("--sample-size", type=int, default=8, help="Cantidad de referencias muestreadas (5-10)")

    p_opt = sub.add_parser("optimize", help="Ejecuta Propose -> Probe -> Compose offline sobre fixtures")
    p_opt.add_argument("--max-fixtures", type=int, default=3, help="Cantidad máxima de fixtures a evaluar")
    p_opt.add_argument("--focus", default="", help="Objetivo opcional de optimización")

    args = parser.parse_args()
    if args.cmd == "capture":
        cmd_capture(args.pdf, args.topic, args.sample_size)
    elif args.cmd == "optimize":
        cmd_optimize(args.max_fixtures, args.focus)


if __name__ == "__main__":
    main()

