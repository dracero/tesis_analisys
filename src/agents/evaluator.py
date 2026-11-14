"""Agente Evaluador (Tutor Académico): Evalúa los 6 pilares de la tesis usando Gemini 3.6 con rotación de API keys."""

import json
import logging
import os
import threading
from typing import Dict, Any, Tuple, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from src.key_manager import get_key_rotator, mask_key, redact_keys
from src.state import (
    GraphState,
    ThesisEvaluation,
    TutorPillarScore,
    ReferenceAlignmentReview,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"

SYSTEM_PROMPT = """Eres un tutor académico senior y jurado de tesis universitarias (grado y posgrado).
Debes auditar una tesis en PDF aplicando los 6 criterios exactos de evaluación de un tutor exigente:

1. **Dominio del campo (`dominio_del_campo`)**:
   - ¿Aparecen los autores y trabajos fundacionales del tema?
   - ¿Hay referencias a los debates actuales o el tesista se quedó en lo básico?
   - ¿Hay ausencias llamativas? Compara la bibliografía de la tesis con la literatura fundacional y reciente recuperada de OpenAlex/Web y señala vacíos concretos ("Falta incorporar trabajos sobre X...").

2. **Calidad y jerarquía de las fuentes (`calidad_y_jerarquia`)**:
   - Proporción de fuentes primarias (papers originales, normas, datasets) frente a secundarias (libros/manuales/divulgación) y fuentes débiles (blogs, Wikipedia, sitios sin autoría).
   - Peso de revistas indexadas y con arbitraje.
   - Alerta si existe dependencia excesiva de una sola fuente o un solo autor.

3. **Actualidad y pertinencia (`actualidad_y_pertinencia`)**:
   - Distribución temporal: si el campo cambia rápido (IA, software, electrónica, etc.), evalúa el % de referencias de los últimos 5 años combinado con clásicos justificados.
   - Relación directa de cada fuente con los objetivos o detección de bibliografía de relleno.

4. **Uso real de las fuentes (`uso_real_de_fuentes`)**:
   - ¿Se discuten y contrastan o solo se citan al pasar?
   - ¿Sustentan el marco teórico, la metodología y la discusión de resultados?
   - ¿Hay lectura crítica (comparar posturas, señalar limitaciones) o solo resumen descriptivo?

5. **Integridad académica (`integridad_academica`)**:
   - Coherencia entre citas en el texto y lista final (citas sin listar / listadas sin citar).
   - Verificación del muestreo de 5 a 10 referencias: comprobar si existen realmente y si su abstract/contenido real respalda lo que el tesista afirma en el contexto citado (detectar posibles alucinaciones de IA generativa o malas atribuciones).
   - Señales de paráfrasis honestas vs. posible copia encubierta.

6. **Aspectos formales (`aspectos_formales`)**:
   - Norma consistente (APA, IEEE, etc.) aplicada de forma uniforme.
   - Datos completos: autores, año, título, fuente, DOI o URL con fecha de acceso cuando corresponda.

Además, genera:
- `sampled_reference_reviews`: evaluación breve de cada referencia muestreada (existencia + si respalda el texto).
- `missing_literature_suggestions`: vacíos concretos ("Falta incorporar trabajos sobre...").
- `tutoring_questions`: incluye las 4 preguntas clave de tutoría adaptadas a esta tesis ("¿Por qué elegiste estos autores y no otros?", "¿Con qué autor discrepás y por qué?", "¿Qué trabajo de la lista es el más cercano a tu propuesta y en qué se diferencia la tuya?", "¿Qué quedó afuera y por qué?") más 2 preguntas específicas sobre hallazgos concretos del documento.
- `actionable_feedback_for_student`: devolución clara y accionable para el tesista.

Responde estrictamente en el formato estructurado solicitado y en español."""

EVALUATOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", """Analiza la siguiente tesis y su evidencia verificada:

=== METADATOS DE LA TESIS ===
Archivo: {filename}
Título / Tema detectado: {title_guess} (Tema indicado: {topic_hint})
Páginas: {page_count}

=== MÉTRICAS CUANTITATIVAS DE BIBLIOGRAFÍA ===
{analytics_json}

=== CRUCE CITAS EN TEXTO VS. LISTA FINAL ===
{cross_check_json}

=== VERIFICACIÓN EN LOTE DE TODA LA BIBLIOGRAFÍA (DOIs, URLs rotas, Retracciones, Venues) ===
{bulk_check_json}

=== LISTA COMPLETA DE BIBLIOGRAFÍA (Resumen compacto con categoría y secciones donde se cita) ===
{full_bib_compact}

=== MUESTREO DE REFERENCIAS VERIFICADAS (Crossref / OpenAlex / Semantic Scholar / Web / Crawl4AI) ===
{verified_refs_json}

=== LITERATURA DE REFERENCIA EN EL CAMPO (Fundacionales y Últimos 5 Años; already_in_thesis indica si ya aparece) ===
{field_lit_json}

=== SECCIONES DETECTADAS EN LA TESIS ===
{sections_json}

=== EXTRACTOS DEL CUERPO DE LA TESIS ===
[Inicio / Resumen / Objetivos]:
{intro_excerpt}

[Desarrollo / Marco Teórico / Metodología]:
{mid_excerpt}

[Resultados / Discusión / Conclusiones]:
{end_excerpt}
""")
])

_client_lock = threading.Lock()
_chain_cache: Dict[Tuple[str, str], Any] = {}


def _is_quota_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(tok in lowered for tok in ("429", "resource_exhausted", "quota", "rate limit", "too many requests"))


def _is_auth_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(tok in lowered for tok in (
        "401", "403", "api_key_invalid", "api key not valid",
        "invalid api key", "permission_denied", "expired", "unauthenticated"
    ))


def _get_or_create_chain(model_name: str, api_key: str) -> Any:
    cache_key = (model_name, api_key)
    with _client_lock:
        chain = _chain_cache.get(cache_key)
        if chain is None:
            if len(_chain_cache) >= 16:
                _chain_cache.pop(next(iter(_chain_cache)))
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=0.15,
                max_retries=0,
                timeout=90,
            )
            chain = EVALUATOR_PROMPT | llm.with_structured_output(ThesisEvaluation)
            _chain_cache[cache_key] = chain
        return chain


def _serialize_list_bounded(items: List[Any], max_chars: int) -> str:
    kept: List[Any] = []
    for item in items:
        candidate = json.dumps(kept + [item], ensure_ascii=False, indent=2)
        if len(candidate) > max_chars and kept:
            break
        kept.append(item)
    return json.dumps(kept, ensure_ascii=False, indent=2)


def _serialize_dict_bounded(data: Dict[str, str], max_chars: int) -> str:
    kept: Dict[str, str] = {}
    for k, v in data.items():
        candidate = json.dumps({**kept, k: v}, ensure_ascii=False, indent=2)
        if len(candidate) > max_chars:
            remaining = max_chars - len(json.dumps(kept, ensure_ascii=False, indent=2)) - len(k) - 20
            if remaining > 200:
                kept[k] = v[:remaining] + "..."
            break
        kept[k] = v
    return json.dumps(kept, ensure_ascii=False, indent=2)


def _heuristic_fallback_evaluation(parsed: Dict[str, Any], verification: Dict[str, Any]) -> ThesisEvaluation:
    """Evaluación determinística de respaldo cuando no hay API keys disponibles o fallan todas."""
    analytics = parsed.get("analytics", {})
    temporal = analytics.get("temporal", {})
    hierarchy = analytics.get("hierarchy", {})
    cross = parsed.get("cross_check", {})
    entries = parsed.get("bibliography_entries", [])
    total = len(entries)
    pct_5y = temporal.get("pct_last_5_years", 0.0)
    coherence = cross.get("coherence_score", 0.0)
    suspect = verification.get("suspect_or_missing_count", 0)

    def _verdict(s: float) -> str:
        if s >= 8.0:
            return "Sólido"
        if s >= 6.0:
            return "Aceptable"
        if s >= 4.0:
            return "Requiere mejoras"
        return "Crítico"

    s_dom = 6.5 if total >= 15 else 4.5
    s_cal = max(2.0, min(9.0, 7.0 - hierarchy.get("web_blog_wiki", 0) * 0.6))
    s_act = min(9.5, max(3.0, pct_5y / 10.0))
    s_uso = 6.0 if cross.get("total_in_text_citations", 0) >= 10 else 4.0
    s_int = max(2.0, min(9.5, (coherence / 10.0) - suspect * 0.8))
    s_for = max(3.0, 8.0 - analytics.get("formal_issues_count", 0) * 0.25)
    overall = round((s_dom + s_cal + s_act + s_uso + s_int + s_for) / 6.0, 1)

    reviews = [
        ReferenceAlignmentReview(
            reference_short=f"{r.get('first_author', 'Autor')} ({r.get('year') or 's/f'})",
            existence_verdict="Confirmada" if r.get("status") == "VERIFICADA" else (
                "Dudosa / Posible alucinación" if r.get("status") == "SOSPECHOSA" else "No verificable"
            ),
            claim_alignment=r.get("alignment_notes", "Verificación automática sin revisión semántica LLM."),
        )
        for r in verification.get("verified_references", [])
    ]

    return ThesisEvaluation(
        overall_score=overall,
        overall_verdict="Aprobada con observaciones" if overall >= 6.0 else "Requiere revisión profunda",
        executive_summary="Evaluación generada por el motor heurístico de respaldo (sin respuesta activa de Gemini).",
        dominio_del_campo=TutorPillarScore(
            score=round(s_dom, 1), verdict=_verdict(s_dom),  # type: ignore
            summary=f"Se detectaron {total} referencias bibliográficas.",
            findings=["Revisar manualmente la inclusión de autores fundacionales y debates recientes."],
        ),
        calidad_y_jerarquia=TutorPillarScore(
            score=round(s_cal, 1), verdict=_verdict(s_cal),  # type: ignore
            summary=f"Papers: {hierarchy.get('primaria_paper', 0)}, Libros/Manuales: {hierarchy.get('secundaria_libro_manual', 0)}, Web/Blogs/Wiki: {hierarchy.get('web_blog_wiki', 0)}.",
            findings=["Reducir fuentes de divulgación o blogs si superan el 15% de la bibliografía."],
        ),
        actualidad_y_pertinencia=TutorPillarScore(
            score=round(s_act, 1), verdict=_verdict(s_act),  # type: ignore
            summary=f"El {pct_5y}% de las referencias corresponden a los últimos 5 años.",
        ),
        uso_real_de_fuentes=TutorPillarScore(
            score=round(s_uso, 1), verdict=_verdict(s_uso),  # type: ignore
            summary=f"Se detectaron {cross.get('total_in_text_citations', 0)} citas en el cuerpo del texto.",
        ),
        integridad_academica=TutorPillarScore(
            score=round(s_int, 1), verdict=_verdict(s_int),  # type: ignore
            summary=f"Coherencia citas-lista: {coherence}%. Referencias sospechosas o no halladas: {suspect}.",
            findings=[
                f"Citas sin listar: {len(cross.get('cited_but_not_listed', []))}",
                f"Listadas sin citar: {len(cross.get('listed_but_not_cited', []))}",
            ],
        ),
        aspectos_formales=TutorPillarScore(
            score=round(s_for, 1), verdict=_verdict(s_for),  # type: ignore
            summary=f"Estilo detectado: {cross.get('detected_style', 'Desconocida')}. Entradas con faltantes formales: {analytics.get('formal_issues_count', 0)}.",
        ),
        sampled_reference_reviews=reviews,
        missing_literature_suggestions=[
            f"Considerar: {f.get('title')} ({f.get('year') or 's/f'}) - {f.get('authors')}"
            for f in verification.get("field_literature", [])[:4]
        ],
        tutoring_questions=[
            "¿Por qué elegiste estos autores y no otros referentes del área?",
            "¿Con qué autor de tu bibliografía discrepás y por qué?",
            "¿Qué trabajo de la lista es el más cercano a tu propuesta y en qué se diferencia la tuya?",
            "¿Qué literatura o enfoque quedó afuera de esta revisión y por qué?",
        ],
        actionable_feedback_for_student=[
            "Corregir las referencias listadas que no fueron citadas y las citas sin correspondencia en la bibliografía.",
            "Revisar manualmente las referencias marcadas como sospechosas o no encontradas.",
        ],
    )


def evaluate_thesis(
    filename: str,
    parsed: Dict[str, Any],
    verification: Dict[str, Any],
) -> ThesisEvaluation:
    """Ejecuta la evaluación completa con Gemini 3.6 rotando API keys."""
    rotator = get_key_rotator()
    ordered_keys = rotator.next_ordered_keys()
    if not ordered_keys:
        logger.warning("Sin API keys activas para Gemini; usando evaluación heurística de respaldo.")
        return _heuristic_fallback_evaluation(parsed, verification)

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    entries = parsed.get("bibliography_entries", [])
    compact_lines = [
        f"- [{e.get('index_num') or (i + 1)}] {e.get('first_author', '?')} ({e.get('year') or 's/f'}) | "
        f"{e.get('source_category', 'otra')} | cit:{e.get('citation_count_in_text', 0)} "
        f"sec:{','.join(e.get('cited_sections') or []) or 'ninguna'} | {(e.get('title') or '')[:80]}"
        for i, e in enumerate(entries)
    ]

    sections = parsed.get("sections_summary", {})
    use_raw_excerpts = len(sections) < 2

    payload = {
        "filename": filename,
        "title_guess": parsed.get("title_guess", "Tesis"),
        "topic_hint": parsed.get("topic_hint", ""),
        "page_count": parsed.get("page_count", 0),
        "analytics_json": json.dumps(parsed.get("analytics", {}), ensure_ascii=False, indent=2),
        "cross_check_json": json.dumps(parsed.get("cross_check", {}), ensure_ascii=False, indent=2),
        "bulk_check_json": json.dumps(verification.get("bulk_check_summary", {}), ensure_ascii=False, indent=2),
        "full_bib_compact": "\n".join(compact_lines)[:12000] or "(Sin referencias)",
        "verified_refs_json": _serialize_list_bounded(verification.get("verified_references", []), 20000),
        "field_lit_json": _serialize_list_bounded(verification.get("field_literature", []), 6000),
        "sections_json": _serialize_dict_bounded(sections, 16000),
        "intro_excerpt": parsed.get("body_excerpt_intro", "")[:6500] if use_raw_excerpts else "(Omitido: cubierto por secciones detectadas)",
        "mid_excerpt": parsed.get("body_excerpt_mid", "")[:4500] if use_raw_excerpts else "(Omitido: cubierto por secciones detectadas)",
        "end_excerpt": parsed.get("body_excerpt_end", "")[:5500] if use_raw_excerpts else "(Omitido: cubierto por secciones detectadas)",
    }

    for key_idx, api_key in ordered_keys:
        try:
            chain = _get_or_create_chain(model_name, api_key)
            result: ThesisEvaluation = chain.invoke(payload)
            rotator.report_success(api_key)
            return result
        except Exception as e:
            err_msg = str(e)
            if _is_auth_error(err_msg):
                rotator.report_invalid(api_key)
                continue
            if _is_quota_error(err_msg):
                rotator.report_rate_limit(api_key)
                continue
            lowered = err_msg.lower()
            if any(tok in lowered for tok in ("400", "invalid_argument", "validationerror", "context_length")):
                logger.warning(f"Error determinístico de payload/esquema con Gemini ({redact_keys(err_msg)}); usando respaldo heurístico sin agotar claves.")
                break
            logger.warning(f"Error con {model_name} y key #{key_idx} ({mask_key(api_key)}): {redact_keys(err_msg)}. Rotando clave...")
            continue

    logger.warning("Todas las API keys fallaron durante la evaluación; usando respaldo heurístico.")
    return _heuristic_fallback_evaluation(parsed, verification)


def tutor_evaluator_node(state: GraphState) -> Dict[str, Any]:
    """Nodo de LangGraph para el Agente Evaluador (Tutor Académico)."""
    filename = state.get("filename", "tesis.pdf")
    parsed = state.get("parsed_thesis", {})
    verification = state.get("verification_results", {})

    logger.info("Agente Evaluador (Tutor) auditando la tesis con Gemini 3.6 y rotación de claves...")
    evaluation = evaluate_thesis(filename=filename, parsed=parsed, verification=verification)
    return {"evaluation": evaluation.model_dump()}

