"""Punto de entrada CLI para analizar una tesis en PDF desde la terminal."""

import argparse
import json
import logging
import os

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()

from src.graph import build_graph
from src.key_manager import get_gemini_keys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Analista de Tesis Full-Stack (CLI)")
    parser.add_argument("pdf", help="Ruta al archivo PDF de la tesis")
    parser.add_argument("--topic", type=str, default="", help="Tema opcional de la tesis para orientar la búsqueda")
    parser.add_argument("--sample-size", type=int, default=8, help="Cantidad de referencias clave a verificar (5-10)")
    parser.add_argument("--output-md", type=str, default=None, help="Guardar informe en Markdown")
    parser.add_argument("--output-json", type=str, default=None, help="Guardar resultados completos en JSON")
    args = parser.parse_args()

    console = Console()
    if not os.path.exists(args.pdf):
        console.print(f"[red]Error: no existe el archivo {args.pdf}[/red]")
        return

    keys = get_gemini_keys()
    console.rule("[bold cyan]🎓 Analista de Tesis Académicas[/bold cyan]")
    console.print(f"[bold]Archivo:[/bold] {args.pdf}")
    console.print(f"[bold]Claves Gemini en rotación:[/bold] {len(keys)}")

    graph = build_graph()
    state = {
        "pdf_path": args.pdf,
        "filename": os.path.basename(args.pdf),
        "thesis_topic": args.topic,
        "sample_size": max(5, min(10, args.sample_size)),
        "parsed_thesis": {},
        "verification_results": {},
        "evaluation": {},
        "summary_report": "",
    }

    with console.status("[bold green]Ejecutando auditoría multi-agente...[/bold green]", spinner="dots"):
        final_state = graph.invoke(state)

    ev = final_state.get("evaluation", {})
    console.print(Panel(
        f"Puntaje Global: [bold]{ev.get('overall_score', 0)}/10[/bold] — {ev.get('overall_verdict', '')}\n\n"
        f"{ev.get('executive_summary', '')}",
        title="Resultado de Auditoría",
    ))

    if args.output_md:
        with open(args.output_md, "w", encoding="utf-8") as f:
            f.write(final_state.get("summary_report", ""))
        console.print(f"[green]✓ Reporte Markdown guardado en {args.output_md}[/green]")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(final_state, f, ensure_ascii=False, indent=2)
        console.print(f"[green]✓ JSON guardado en {args.output_json}[/green]")


if __name__ == "__main__":
    main()

