from typing import List, Optional, Literal, Dict, Any, TypedDict
from pydantic import BaseModel, Field


class BibliographyEntry(BaseModel):
    index_num: Optional[int] = Field(default=None, description="Número de orden si usa formato IEEE [N]")
    raw_text: str = Field(description="Texto completo de la referencia en la bibliografía")
    first_author: str = Field(default="", description="Primer autor o institución detectada")
    year: Optional[int] = Field(default=None, description="Año de publicación extraído")
    title: str = Field(default="", description="Título inferido de la obra o artículo")
    doi: Optional[str] = Field(default=None, description="DOI detectado si existe")
    url: Optional[str] = Field(default=None, description="URL detectada si existe")
    source_category: Literal[
        "primaria_paper",
        "primaria_norma_dataset",
        "secundaria_libro_manual",
        "web_blog_wiki",
        "otra",
    ] = Field(default="otra", description="Jerarquía de la fuente")
    formal_issues: List[str] = Field(default_factory=list, description="Datos formales faltantes o inconsistencias")
    cited_in_text: bool = Field(default=False, description="Si figura citada en el cuerpo de la tesis")
    citation_count_in_text: int = Field(default=0, description="Cantidad de veces citada en el cuerpo")
    cited_sections: List[str] = Field(default_factory=list, description="Secciones donde se la cita")
    has_secondary_citation: bool = Field(default=False, description="Si aparece como cita de cita (citado por/apud)")
    context_snippets: List[str] = Field(default_factory=list, description="Fragmentos del texto donde se la cita")


class CitationCrossCheck(BaseModel):
    detected_style: Literal["APA", "IEEE", "Mixta", "Desconocida"] = "Desconocida"
    total_bibliography: int = 0
    total_in_text_citations: int = 0
    cited_but_not_listed: List[str] = Field(default_factory=list)
    listed_but_not_cited: List[str] = Field(default_factory=list)
    coherence_score: float = Field(default=0.0, description="Porcentaje de coherencia 0-100")


class VerifiedReference(BaseModel):
    reference_text: str
    first_author: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    status: Literal["VERIFICADA", "PARCIAL", "NO_ENCONTRADA", "SOSPECHOSA"] = "NO_ENCONTRADA"
    matched_title: str = ""
    matched_authors: str = ""
    matched_year: Optional[int] = None
    matched_url: str = ""
    verification_source: str = ""
    venue: str = ""
    source_type: str = ""
    cited_by_count: Optional[int] = None
    is_retracted: bool = False
    abstract_or_excerpt: str = ""
    thesis_claim_context: str = ""
    alignment_notes: str = ""


class FieldLiteratureItem(BaseModel):
    title: str
    authors: str = ""
    year: Optional[int] = None
    source: str = ""
    url: str = ""
    snippet: str = ""
    category: Literal["fundacional", "debate_actual"] = "debate_actual"
    already_in_thesis: bool = False


class TutorPillarScore(BaseModel):
    score: float = Field(ge=0.0, le=10.0, description="Puntaje de 0 a 10")
    verdict: Literal["Sólido", "Aceptable", "Requiere mejoras", "Crítico"] = "Aceptable"
    summary: str = Field(description="Síntesis evaluativa del criterio")
    strengths: List[str] = Field(default_factory=list, description="Aspectos positivos detectados")
    findings: List[str] = Field(default_factory=list, description="Debilidades, riesgos o hallazgos concretos")
    concrete_recommendations: List[str] = Field(default_factory=list, description="Acciones concretas de mejora")


class ReferenceAlignmentReview(BaseModel):
    reference_short: str = Field(description="Autor/Año o título breve de la referencia muestreada")
    existence_verdict: Literal["Confirmada", "Dudosa / Posible alucinación", "No verificable"] = "Confirmada"
    claim_alignment: str = Field(description="Si el contenido real respalda lo que el tesista afirma")


class ThesisEvaluation(BaseModel):
    overall_score: float = Field(ge=0.0, le=10.0)
    overall_verdict: Literal["Aprobada / Sólida", "Aprobada con observaciones", "Requiere revisión profunda", "Riesgo de integridad / Rechazo"]
    executive_summary: str
    dominio_del_campo: TutorPillarScore
    calidad_y_jerarquia: TutorPillarScore
    actualidad_y_pertinencia: TutorPillarScore
    uso_real_de_fuentes: TutorPillarScore
    integridad_academica: TutorPillarScore
    aspectos_formales: TutorPillarScore
    sampled_reference_reviews: List[ReferenceAlignmentReview] = Field(default_factory=list)
    missing_literature_suggestions: List[str] = Field(
        default_factory=list,
        description="Vacíos concretos detectados (ej: 'Falta incorporar trabajos sobre X')"
    )
    tutoring_questions: List[str] = Field(
        default_factory=list,
        description="Preguntas concretas para la reunión de tutoría"
    )
    actionable_feedback_for_student: List[str] = Field(
        default_factory=list,
        description="Devolución estructurada para el tesista"
    )


class GraphState(TypedDict):
    pdf_path: str
    filename: str
    thesis_topic: str
    sample_size: int
    parsed_thesis: Dict[str, Any]
    verification_results: Dict[str, Any]
    evaluation: Dict[str, Any]
    summary_report: str

