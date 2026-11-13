"""Agente Reportero: Genera el informe Markdown completo de tutoría y métricas resumen."""

import logging
from typing import Dict, Any, List

from src.state import GraphState

logger = logging.getLogger(__name__)


def generate_thesis_markdown_report(
    filename: str,
    parsed: Dict[str, Any],
    verification: Dict[str, Any],
    evaluation: Dict[str, Any],
) -> str:
    """Genera un informe detallado en Markdown para la devolución al tesista."""
    cross = parsed.get("cross_check", {})
    analytics = parsed.get("analytics", {})
    temporal = analytics.get("temporal", {})
    hierarchy = analytics.get("hierarchy", {})
    bulk = verification.get("bulk_check_summary", {})

    lines: List[str] = [
        f"# 🎓 Informe de Tutoría Académica: {parsed.get('title_guess', filename)}",
        f"**Archivo:** `{filename}` | **Páginas:** {parsed.get('page_count', 0)} | **Norma detectada:** {cross.get('detected_style', 'Desconocida')}",
        f"**Calificación Global:** `{evaluation.get('overall_score', 0)}/10` — **{evaluation.get('overall_verdict', '')}**\n",
        "## Resumen Ejecutivo",
        evaluation.get("executive_summary", ""),
        "",
        "---",
        "## 📊 Métricas Rápidas de la Bibliografía",
        f"- **Total de referencias en lista final:** {cross.get('total_bibliography', 0)}",
        f"- **Citas detectadas en el texto:** {cross.get('total_in_text_citations', 0)}",
        f"- **Coherencia citas ↔ lista:** {cross.get('coherence_score', 0)}%",
        f"- **Actualidad (últimos 5 años):** {temporal.get('last_5_years', 0)} referencias ({temporal.get('pct_last_5_years', 0)}%)",
        f"- **Jerarquía:** Papers/Artículos ({hierarchy.get('primaria_paper', 0)}) | Normas/Datasets ({hierarchy.get('primaria_norma_dataset', 0)}) | Libros/Manuales ({hierarchy.get('secundaria_libro_manual', 0)}) | Web/Blogs/Wiki ({hierarchy.get('web_blog_wiki', 0)})",
    ]
    if parsed.get("low_text_warning"):
        lines.append("- ⚠️ **Advertencia:** El PDF tiene muy poco texto extraíble por página (posible documento escaneado sin OCR).")
    if analytics.get("duplicate_entries"):
        lines.append(f"- ⚠️ **Posibles duplicados en bibliografía:** {len(analytics['duplicate_entries'])}")
    if bulk.get("checked_entries"):
        lines.append(
            f"- **Chequeo en lote ({bulk['checked_entries']} refs con DOI/URL):** "
            f"DOIs inválidos: {len(bulk.get('invalid_dois', []))} | "
            f"URLs rotas: {len(bulk.get('broken_urls', []))} | "
            f"Retractados: {len(bulk.get('retracted_papers', []))}"
        )
    lines.extend([
        "",
        "---",
        "## 📋 Evaluación por los 6 Criterios del Tutor",
    ])

    pillars = [
        ("1. Dominio del Campo", evaluation.get("dominio_del_campo", {})),
        ("2. Calidad y Jerarquía de las Fuentes", evaluation.get("calidad_y_jerarquia", {})),
        ("3. Actualidad y Pertinencia", evaluation.get("actualidad_y_pertinencia", {})),
        ("4. Uso Real de las Fuentes", evaluation.get("uso_real_de_fuentes", {})),
        ("5. Integridad Académica", evaluation.get("integridad_academica", {})),
        ("6. Aspectos Formales", evaluation.get("aspectos_formales", {})),
    ]

    for title, p in pillars:
        lines.append(f"### {title} — `{p.get('score', 0)}/10` ({p.get('verdict', '')})")
        lines.append(p.get("summary", ""))
        if p.get("strengths"):
            lines.append("- **Fortalezas:**")
            for s in p["strengths"]:
                lines.append(f"  - {s}")
        if p.get("findings"):
            lines.append("- **Hallazgos / Alertas:**")
            for f in p["findings"]:
                lines.append(f"  - {f}")
        if p.get("concrete_recommendations"):
            lines.append("- **Recomendaciones:**")
            for r in p["concrete_recommendations"]:
                lines.append(f"  - {r}")
        lines.append("")

    lines.append("---")
    lines.append(f"## 🔎 Muestreo de Referencias Verificadas ({verification.get('sampled_count', 0)} analizadas)")
    for ref in verification.get("verified_references", []):
        lines.append(f"- **[{ref.get('status')}]** {ref.get('first_author')} ({ref.get('year') or 's/f'}) — *{ref.get('matched_title') or ref.get('reference_text', '')[:90]}*")
        lines.append(f"  - Fuente de verificación: {ref.get('verification_source')} | {ref.get('alignment_notes')}")
        if ref.get("matched_url"):
            lines.append(f"  - Enlace: {ref.get('matched_url')}")

    if evaluation.get("missing_literature_suggestions"):
        lines.append("\n---\n## 📚 Vacíos Detectados y Bibliografía Sugerida")
        for m in evaluation["missing_literature_suggestions"]:
            lines.append(f"- {m}")

    if evaluation.get("tutoring_questions"):
        lines.append("\n---\n## 💬 Preguntas para la Reunión de Tutoría")
        for q in evaluation["tutoring_questions"]:
            lines.append(f"- {q}")

    if evaluation.get("actionable_feedback_for_student"):
        lines.append("\n---\n## ✍️ Devolución Concreta para el Tesista")
        for fb in evaluation["actionable_feedback_for_student"]:
            lines.append(f"- {fb}")

    return "\n".join(lines)


def reporter_node(state: GraphState) -> Dict[str, Any]:
    """Nodo de LangGraph para consolidar el informe final."""
    filename = state.get("filename", "tesis.pdf")
    parsed = state.get("parsed_thesis", {})
    verification = state.get("verification_results", {})
    evaluation = state.get("evaluation", {})
    report = generate_thesis_markdown_report(filename, parsed, verification, evaluation)
    return {"summary_report": report}

