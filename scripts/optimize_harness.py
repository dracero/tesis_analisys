"""Script unificado de optimización del Harness (captura automática + Propose/Probe/Compose + promoción con backup y hot-reload).

Ejemplos de uso:
  # 1. Optimizar pasando un PDF o una carpeta de PDFs (captura solo los nuevos y ejecuta optimización):
  uv run python scripts/optimize_harness.py --pdf tesis_ejemplo.pdf --topic "IA en educación"

  # 2. Optimizar usando los fixtures ya capturados (2 rondas, foco específico):
  uv run python scripts/optimize_harness.py --rounds 2 --focus "evitar falsos positivos en ausencias bibliográficas"

  # 3. Promover automáticamente si gana el candidato (con backup previo y hot-reload en server.py):
  uv run python scripts/optimize_harness.py --auto-promote

  # 4. Revertir al último backup de ./harness/:
  uv run python scripts/optimize_harness.py --rollback
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.harness2_offline import (
    EDITABLE_FILES,
    FIXTURES_DIR,
    RUNS_DIR,
    _slugify,
    cmd_capture,
    cmd_optimize,
)
from src.harness_loader import get_harness_metadata, resolve_harness_dir

BACKUPS_DIR = RUNS_DIR / "backups"
console = Console()


def _collect_pdfs(pdf_input: str) -> List[Path]:
    p = Path(pdf_input).resolve()
    if p.is_file() and p.suffix.lower() == ".pdf":
        return [p]
    if p.is_dir():
        return sorted(p.glob("*.pdf"))
    raise FileNotFoundError(f"No se encontró PDF o directorio válido en: {p}")


def _promote_candidate(candidate_dir: Path) -> Path:
    prod_harness = resolve_harness_dir()
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS_DIR / ts
    shutil.copytree(prod_harness, backup_dir)

    for rel in EDITABLE_FILES:
        src = candidate_dir / rel
        if src.is_file():
            dst = prod_harness / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    return backup_dir


def _rollback_latest() -> None:
    if not BACKUPS_DIR.is_dir():
        console.print("[yellow]No hay backups disponibles en harness_runs/backups/.[/yellow]")
        sys.exit(1)

    backups = sorted(p for p in BACKUPS_DIR.iterdir() if p.is_dir())
    if not backups:
        console.print("[yellow]No hay backups disponibles en harness_runs/backups/.[/yellow]")
        sys.exit(1)

    latest = backups[-1]
    prod_harness = resolve_harness_dir()
    for rel in EDITABLE_FILES:
        src = latest / rel
        if src.is_file():
            dst = prod_harness / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    meta = get_harness_metadata()
    console.print(
        Panel(
            f"Restaurado desde: [bold]{latest.name}[/bold]\n"
            f"Nuevo hash activo (hot-reload inmediato en server.py): [cyan]{meta['harness_hash']}[/cyan]",
            title="Rollback de Harness completado",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimizador unificado de Harness² para tesis_analisys")
    parser.add_argument("--pdf", type=str, default="", help="Ruta a un PDF o carpeta de PDFs para asegurar fixtures")
    parser.add_argument("--topic", type=str, default="", help="Tema opcional al capturar PDFs nuevos")
    parser.add_argument("--sample-size", type=int, default=8, help="Referencias muestreadas al capturar (5-10)")
    parser.add_argument("--refresh", action="store_true", help="Re-capturar fixtures aunque ya existan")
    parser.add_argument("--max-fixtures", type=int, default=3, help="Máximo de fixtures por ronda")
    parser.add_argument("--rounds", type=int, default=1, help="Cantidad de rondas Propose->Probe->Compose")
    parser.add_argument("--focus", nargs="*", default=[], help="Objetivo de mejora (ej: reducir falsos positivos)")
    parser.add_argument("--auto-promote", action="store_true", help="Promover sin confirmación si gana el candidato")
    parser.add_argument("--no-prompt", action="store_true", help="No preguntar por promoción al finalizar")
    parser.add_argument("--rollback", action="store_true", help="Restaurar el último backup de ./harness/")
    args = parser.parse_args()

    if args.rollback:
        _rollback_latest()
        return

    console.rule("[bold cyan]Harness² — Optimización Unificada de tesis_analisys[/bold cyan]")

    # 1. Captura automática solo de PDFs faltantes (o si se pasó --refresh)
    if args.pdf:
        pdfs = _collect_pdfs(args.pdf)
        if not pdfs:
            console.print(f"[red]No se hallaron archivos .pdf en {args.pdf}[/red]")
            sys.exit(1)
        for pdf_file in pdfs:
            expected_fixture = FIXTURES_DIR / f"{_slugify(pdf_file.stem)}.json"
            if expected_fixture.is_file() and not args.refresh:
                console.print(f"[dim]• Fixture ya existente (reutilizando): {expected_fixture.name}[/dim]")
                continue
            cmd_capture(str(pdf_file), args.topic, args.sample_size)

    # 2. Rondas de optimización
    focus_text = " ".join(args.focus).strip() if isinstance(args.focus, list) else str(args.focus or "").strip()
    rounds = max(1, min(5, args.rounds))
    for round_idx in range(1, rounds + 1):
        console.rule(f"[bold]Ronda {round_idx}/{rounds}[/bold]")
        entry = cmd_optimize(max_fixtures=args.max_fixtures, focus=focus_text)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Campo")
        table.add_column("Resultado")
        table.add_row("Run ID", str(entry["run_id"]))
        table.add_row("Ganador", f"[bold green]{entry['winner']}[/bold green]" if entry["winner"] == "candidate" else str(entry["winner"]))
        table.add_row("Hipótesis", str(entry["hypothesis"]))
        table.add_row("Archivos editados", ", ".join(entry["edited_files"]) or "(ninguno)")
        table.add_row("Reporte + Diff", str(entry["report_path"]))
        console.print(table)

        if entry["winner"] != "candidate" or not entry["edited_files"]:
            console.print("[yellow]El candidato no superó al baseline; ./harness/ permanece sin cambios.[/yellow]")
            continue

        should_promote = args.auto_promote
        if not should_promote and not args.no_prompt:
            answer = console.input(
                "\n[bold]¿Promover este candidato a ./harness/ con backup automático (hot-reload inmediato)? [s/N]: [/bold]"
            ).strip().lower()
            should_promote = answer in ("s", "si", "sí", "y", "yes")

        if should_promote:
            backup_dir = _promote_candidate(Path(entry["candidate_dir"]))
            meta = get_harness_metadata()
            console.print(
                Panel(
                    f"Backup guardado en: [dim]{backup_dir}[/dim]\n"
                    f"Hash activo en ./harness/: [bold green]{meta['harness_hash']}[/bold green]\n"
                    "Si server.py está corriendo, el nuevo harness se aplica automáticamente por hot-reload.",
                    title="✓ Candidato promovido",
                )
            )
        else:
            console.print(f"[dim]Candidato conservado en {entry['candidate_dir']} sin modificar ./harness/.[/dim]")


if __name__ == "__main__":
    main()

