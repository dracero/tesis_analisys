"""Agente Extractor de PDF: Parsea la tesis, separa cuerpo y bibliografía, detecta citas y calcula métricas formales."""

import re
import logging
from collections import Counter
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

import fitz  # PyMuPDF

from src.state import BibliographyEntry, CitationCrossCheck, GraphState

logger = logging.getLogger(__name__)

_BIB_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(bibliograf[ií]a|referencias(?:\s+bibliogr[aá]ficas)?|fuentes\s+bibliogr[aá]ficas|references|works\s+cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_APPENDIX_HEADING_RE = re.compile(
    r"^\s*(?:anexo|ap[eé]ndice|appendix)\b",
    re.IGNORECASE | re.MULTILINE,
)

_YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
_DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s,;\]\)]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s,;\)\]>]+", re.IGNORECASE)


def extract_pdf_pages(pdf_path: str) -> Tuple[List[str], Dict[str, Any]]:
    """Extrae el texto página por página en orden de lectura y metadatos básicos del PDF."""
    doc = fitz.open(pdf_path)
    pages_text: List[str] = []
    for page in doc:
        pages_text.append(page.get_text("text", sort=True))
    meta = dict(doc.metadata or {})
    page_count = len(doc)
    doc.close()
    total_chars = sum(len(p.strip()) for p in pages_text)
    low_text_warning = page_count > 1 and (total_chars / max(1, page_count)) < 120
    return pages_text, {
        "metadata": meta,
        "page_count": page_count,
        "low_text_warning": low_text_warning,
    }


def _split_body_and_bibliography(pages_text: List[str]) -> Tuple[str, str]:
    """Localiza el inicio real de la bibliografía (en la segunda mitad del documento) y separa el cuerpo."""
    total_pages = len(pages_text)
    full_text = "\n\n".join(pages_text)

    # Buscar encabezados de bibliografía después del 45% del texto para evitar el índice (TOC)
    search_start = int(len(full_text) * 0.45) if total_pages > 4 else 0
    matches = list(_BIB_HEADING_RE.finditer(full_text, pos=search_start))

    if not matches:
        # Reintentar en todo el texto (última ocurrencia)
        matches = list(_BIB_HEADING_RE.finditer(full_text))

    if not matches:
        return full_text, ""

    bib_match = matches[-1]
    body_text = full_text[: bib_match.start()].strip()
    bib_raw = full_text[bib_match.end() :].strip()

    # Cortar si luego aparece un Anexo/Apéndice grande
    app_match = _APPENDIX_HEADING_RE.search(bib_raw)
    if app_match and app_match.start() > 200:
        bib_raw = bib_raw[: app_match.start()].strip()

    return body_text, bib_raw


def _split_bibliography_entries(bib_text: str) -> List[str]:
    """Segmenta la sección de bibliografía en entradas individuales (IEEE o APA)."""
    if not bib_text.strip():
        return []

    lines = [ln.rstrip() for ln in bib_text.splitlines()]
    # Filtrar números de página sueltos
    cleaned_lines = [ln for ln in lines if not re.fullmatch(r"\s*\d{1,4}\s*", ln)]

    # Detectar si predomina formato numerado IEEE: [1] ... o 1. ...
    ieee_starts = sum(1 for ln in cleaned_lines if re.match(r"^\s*\[\d+\]\s+", ln))
    num_dot_starts = sum(1 for ln in cleaned_lines if re.match(r"^\s*\d{1,3}\.\s+[A-ZÁÉÍÓÚÑ]", ln))

    entries: List[str] = []
    current: List[str] = []

    if ieee_starts >= 3:
        for ln in cleaned_lines:
            if re.match(r"^\s*\[\d+\]\s+", ln):
                if current:
                    entries.append(" ".join(current).strip())
                current = [ln.strip()]
            elif ln.strip():
                current.append(ln.strip())
        if current:
            entries.append(" ".join(current).strip())
        return entries

    if num_dot_starts >= 3:
        for ln in cleaned_lines:
            if re.match(r"^\s*\d{1,3}\.\s+[A-ZÁÉÍÓÚÑ]", ln):
                if current:
                    entries.append(" ".join(current).strip())
                current = [ln.strip()]
            elif ln.strip():
                current.append(ln.strip())
        if current:
            entries.append(" ".join(current).strip())
        return entries

    # Formato APA / Autor-Año: nueva entrada suele empezar con "Apellido, ..." o línea tras línea en blanco
    apa_start_re = re.compile(
        r"^\s*(?:[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\-']{1,25},\s+[A-ZÁÉÍÓÚÑ]|[A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,})*\s*\(\d{4}\))"
    )
    for ln in cleaned_lines:
        s = ln.strip()
        if not s:
            if current:
                entries.append(" ".join(current).strip())
                current = []
            continue
        if current and apa_start_re.match(s) and _YEAR_RE.search(" ".join(current)):
            entries.append(" ".join(current).strip())
            current = [s]
        else:
            current.append(s)

    if current:
        entries.append(" ".join(current).strip())

    # Filtrar entradas demasiado cortas que sean ruido
    return [e for e in entries if len(e) >= 20]


def _classify_source_hierarchy(raw_text: str, doi: Optional[str], url: Optional[str]) -> str:
    low = raw_text.lower()
    if any(w in low for w in ("wikipedia", "blogspot", "wordpress", "medium.com", "rincondelvago", "monografias.com", "youtube.com", "youtu.be", "tiktok.com", "blog")):
        return "web_blog_wiki"
    if any(w in low for w in ("iso ", "iso.org", "ieee std", "ietf.org", "w3.org", "ley n", "ley ", "decreto", "resolución", "resolucion", "rfc ", "dataset", "kaggle", "huggingface", "github.com", "norma ", "who.int", "gob.", ".gov")):
        return "primaria_norma_dataset"
    if doi or any(w in low for w in (
        "journal", "revista", "proceedings", "conference", "symposium", "ieee ", "ieeexplore",
        "acm ", "dl.acm.org", "springer", "elsevier", "sciencedirect", "nature", "science",
        "arxiv", "biorxiv", "medrxiv", "openreview", "scielo", "redalyc", "dialnet",
        "pubmed", "ncbi.nlm.nih.gov", "vol.", "pp.", "issn", "tesis", "thesis", "repositorio", ".edu"
    )):
        return "primaria_paper"
    if any(w in low for w in ("editorial", "press", "edition", "edición", "edicion", "handbook", "manual", "isbn", "mcgraw", "pearson", "wiley", "oreilly", "o'reilly", "capítulo", "capitulo")):
        return "secundaria_libro_manual"
    if url:
        return "web_blog_wiki"
    return "otra"


def _extract_first_author(raw: str) -> str:
    cleaned = re.sub(r"^\s*(?:\[\d+\]|\d{1,3}\.)\s*", "", raw).strip()
    # APA: "Apellido, I."
    m = re.match(r"^([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\-'\s]{1,30}),", cleaned)
    if m:
        return m.group(1).strip()
    # IEEE: "I. Apellido," o "Nombre Apellido,"
    m2 = re.match(r"^([A-ZÁÉÍÓÚÑ][^,(]{2,35}?)(?:,|\band\b|\by\b|\(\d{4}\))", cleaned)
    if m2:
        tokens = m2.group(1).strip().split()
        return tokens[-1] if tokens else m2.group(1).strip()
    return cleaned.split()[0][:25] if cleaned.split() else "Desconocido"


def _extract_title_guess(raw: str) -> str:
    # Entre comillas (típico IEEE)
    m_q = re.search(r"[\"“«]([^\"”»]{10,200})[\"”»]", raw)
    if m_q:
        return m_q.group(1).strip()
    # Después de (YYYY). (típico APA)
    m_apa = re.search(r"\(\d{4}[a-z]?\)\.?\s+([^\.]{10,200})\.", raw)
    if m_apa:
        return m_apa.group(1).strip()
    cleaned = re.sub(r"^\s*(?:\[\d+\]|\d{1,3}\.)\s*", "", raw).strip()
    return cleaned[:140]


def _parse_single_entry(raw: str) -> BibliographyEntry:
    idx_match = re.match(r"^\s*(?:\[(\d+)\]|(\d{1,3})\.)\s+", raw)
    index_num = int(idx_match.group(1) or idx_match.group(2)) if idx_match else None

    doi_match = _DOI_RE.search(raw)
    doi = doi_match.group(1).rstrip(".") if doi_match else None

    url_match = _URL_RE.search(raw)
    url = url_match.group(0).rstrip(".,;)") if url_match else None

    years = [int(y) for y in _YEAR_RE.findall(raw)]
    current_year = datetime.now().year
    valid_years = [y for y in years if 1900 <= y <= current_year + 1]
    year = valid_years[0] if valid_years else None

    first_author = _extract_first_author(raw)
    title = _extract_title_guess(raw)
    category = _classify_source_hierarchy(raw, doi, url)

    formal_issues: List[str] = []
    if year is None:
        formal_issues.append("Falta año de publicación")
    if not first_author or first_author == "Desconocido":
        formal_issues.append("Falta autoría clara")
    if url and not any(k in raw.lower() for k in ("accedido", "recuperado", "consultado", "accessed", "retrieved", "fecha de acceso")):
        formal_issues.append("URL sin fecha de acceso/consulta")
    if category == "primaria_paper" and not doi and not url:
        formal_issues.append("Artículo sin DOI ni URL")

    return BibliographyEntry(
        index_num=index_num,
        raw_text=raw,
        first_author=first_author,
        year=year,
        title=title,
        doi=doi,
        url=url,
        source_category=category,  # type: ignore
        formal_issues=formal_issues,
    )


_SECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Resumen / Abstract", re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(resumen|abstract)\s*$", re.IGNORECASE | re.MULTILINE)),
    ("Introducción / Objetivos", re.compile(r"^\s*(?:cap[ií]tulo\s+\d+[:.\s-]*)?(?:\d+(?:\.\d+)*\.?\s+)?(introducci[oó]n|planteamiento\s+del\s+problema|objetivos)\b.*$", re.IGNORECASE | re.MULTILINE)),
    ("Marco Teórico / Estado del Arte", re.compile(r"^\s*(?:cap[ií]tulo\s+\d+[:.\s-]*)?(?:\d+(?:\.\d+)*\.?\s+)?(marco\s+te[oó]rico|estado\s+del\s+arte|revisi[oó]n\s+(?:de\s+la\s+)?literatura|antecedentes|fundamentaci[oó]n\s+te[oó]rica|theoretical\s+framework|literature\s+review|related\s+work)\b.*$", re.IGNORECASE | re.MULTILINE)),
    ("Metodología / Diseño", re.compile(r"^\s*(?:cap[ií]tulo\s+\d+[:.\s-]*)?(?:\d+(?:\.\d+)*\.?\s+)?(metodolog[ií]a|dise[nñ]o\s+metodol[oó]gico|materiales\s+y\s+m[eé]todos|dise[nñ]o\s+experimental|architecture|methodology|methods)\b.*$", re.IGNORECASE | re.MULTILINE)),
    ("Resultados", re.compile(r"^\s*(?:cap[ií]tulo\s+\d+[:.\s-]*)?(?:\d+(?:\.\d+)*\.?\s+)?(resultados(?:\s+y\s+an[aá]lisis)?|an[aá]lisis\s+de\s+resultados|evaluaci[oó]n|results)\b.*$", re.IGNORECASE | re.MULTILINE)),
    ("Discusión y Conclusiones", re.compile(r"^\s*(?:cap[ií]tulo\s+\d+[:.\s-]*)?(?:\d+(?:\.\d+)*\.?\s+)?(discusi[oó]n|conclusiones(?:\s+y\s+trabajos?\s+futuros?)?|discussion|conclusions)\b.*$", re.IGNORECASE | re.MULTILINE)),
]

_SECONDARY_CITE_RE = re.compile(r"\b(citado\s+(?:por|en)|cited\s+(?:by|in)|apud|seg[uú]n\s+[^,]{2,30},\s*citado)\b", re.IGNORECASE)


def _extract_thesis_sections(body_text: str) -> Tuple[List[Tuple[int, str]], Dict[str, str]]:
    """Detecta secciones principales de la tesis y extrae un resumen de cada una."""
    if not body_text.strip():
        return [], {}
    # Ignorar el primer 3% si contiene un índice muy temprano (buscar todas y filtrar TOC si hay duplicados)
    hits: List[Tuple[int, str]] = []
    for label, pat in _SECTION_PATTERNS:
        matches = list(pat.finditer(body_text))
        if matches:
            # Preferir la última ocurrencia si la primera está en los primeros 3500 chars (probable índice)
            chosen = matches[-1] if len(matches) > 1 and matches[0].start() < 3500 else matches[0]
            hits.append((chosen.start(), label))
    hits.sort(key=lambda x: x[0])

    sections_summary: Dict[str, str] = {}
    for idx, (start_pos, label) in enumerate(hits):
        end_pos = hits[idx + 1][0] if idx + 1 < len(hits) else len(body_text)
        chunk = " ".join(body_text[start_pos:end_pos].split())
        if len(chunk) > 80:
            sections_summary[label] = chunk[:3500]
    return hits, sections_summary


def _section_at_offset(offset: int, section_spans: List[Tuple[int, str]]) -> str:
    current = "Cuerpo"
    for pos, label in section_spans:
        if pos <= offset:
            current = label
        else:
            break
    return current


def _detect_bibliography_consistency(entries: List[BibliographyEntry], style: str) -> Tuple[List[str], List[str]]:
    """Detecta duplicados y problemas formales globales (orden, desambiguación)."""
    duplicates: List[str] = []
    notes: List[str] = []

    seen_keys: Dict[str, str] = {}
    author_year_counts: Counter = Counter()
    for e in entries:
        norm_title = re.sub(r"[^a-z0-9]", "", (e.title or "").lower())[:50]
        if norm_title and len(norm_title) >= 15:
            if norm_title in seen_keys:
                duplicates.append(f"{e.first_author} ({e.year or 's/f'}): '{e.title[:60]}'")
            else:
                seen_keys[norm_title] = e.raw_text
        if e.first_author and e.first_author != "Desconocido" and e.year:
            author_year_counts[(e.first_author.lower(), e.year)] += 1

    if style == "IEEE":
        nums = [e.index_num for e in entries if e.index_num is not None]
        if nums and nums != list(range(1, len(nums) + 1)):
            notes.append("Numeración IEEE no estrictamente secuencial desde [1] (posibles saltos o desorden).")
    elif style == "APA":
        authors = [e.first_author.lower() for e in entries if e.first_author and e.first_author != "Desconocido"]
        if len(authors) >= 5:
            inversions = sum(1 for i in range(1, len(authors)) if authors[i] < authors[i - 1])
            if inversions > max(2, len(authors) // 4):
                notes.append(f"La lista APA presenta {inversions} rupturas de orden alfabético.")
        ambiguous = [f"{a} ({y})" for (a, y), c in author_year_counts.items() if c > 1]
        if ambiguous:
            # Verificar si faltan sufijos a/b en raw_text
            missing_disambig = [
                lbl for lbl in ambiguous[:5]
                if not any(re.search(rf"\({lbl.split('(')[1][:4]}[ab]\)", e.raw_text) for e in entries)
            ]
            if missing_disambig:
                notes.append(f"Múltiples obras del mismo autor/año sin clara desambiguación (a/b): {', '.join(missing_disambig)}.")

    return duplicates[:10], notes


def _cross_check_citations(
    body_text: str,
    entries: List[BibliographyEntry],
    section_spans: Optional[List[Tuple[int, str]]] = None,
) -> CitationCrossCheck:
    """Cruza citas en el cuerpo de la tesis contra la lista final de bibliografía."""
    spans = section_spans or []
    # Detectar citas IEEE [1], [1, 2], [1-3]
    ieee_tokens: List[int] = []
    for m in re.finditer(r"\[(\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*)\]", body_text):
        chunk = m.group(1)
        for part in re.split(r"\s*,\s*", chunk):
            if "-" in part or "–" in part:
                bounds = re.split(r"\s*[-–]\s*", part)
                if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                    a, b = int(bounds[0]), int(bounds[1])
                    if 1 <= a <= b <= 300 and (b - a) <= 25:
                        ieee_tokens.extend(range(a, b + 1))
            elif part.isdigit():
                ieee_tokens.append(int(part))

    # Detectar citas APA: (Autor, 2021), (Autor & Autor, 2021), Autor (2021)
    apa_citations: List[Tuple[str, int, int, int]] = []
    apa_paren_re = re.compile(
        r"\(([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\-']{1,25}(?:\s+(?:et\s+al\.|y|&)\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\-']{1,25})?),?\s*((?:19|20)\d{2}[a-z]?)\)"
    )
    apa_narr_re = re.compile(
        r"\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\-']{1,25})\s*(?:et\s+al\.)?\s*\(((?:19|20)\d{2}[a-z]?)\)"
    )
    for m in apa_paren_re.finditer(body_text):
        yr = int(m.group(2)[:4])
        apa_citations.append((m.group(1).split()[0].rstrip(","), yr, m.start(), m.end()))
    for m in apa_narr_re.finditer(body_text):
        yr = int(m.group(2)[:4])
        apa_citations.append((m.group(1), yr, m.start(), m.end()))

    has_ieee_bib = sum(1 for e in entries if e.index_num is not None) >= max(2, len(entries) // 3)
    if has_ieee_bib and len(ieee_tokens) >= len(apa_citations):
        style = "IEEE" if len(apa_citations) < 3 else "Mixta"
    elif len(apa_citations) > 0:
        style = "APA" if len(ieee_tokens) < 3 else "Mixta"
    else:
        style = "Desconocida"

    def _record_context(entry: BibliographyEntry, start: int, end: int) -> None:
        sec = _section_at_offset(start, spans)
        if sec not in entry.cited_sections:
            entry.cited_sections.append(sec)
        s = max(0, start - 160)
        e_pos = min(len(body_text), end + 140)
        window = " ".join(body_text[s:e_pos].split())
        if _SECONDARY_CITE_RE.search(window):
            entry.has_secondary_citation = True
        if len(entry.context_snippets) < 3:
            entry.context_snippets.append(f"[{sec}] {window}")

    cited_but_not_listed: List[str] = []
    if style in ("IEEE", "Mixta") and has_ieee_bib:
        bib_by_idx = {e.index_num: e for e in entries if e.index_num is not None}
        counts = Counter(ieee_tokens)
        for num, cnt in counts.items():
            if num in bib_by_idx:
                bib_by_idx[num].cited_in_text = True
                bib_by_idx[num].citation_count_in_text += cnt
                for m in re.finditer(rf"\[[^\]]*\b{num}\b[^\]]*\]", body_text):
                    _record_context(bib_by_idx[num], m.start(), m.end())
            else:
                cited_but_not_listed.append(f"[{num}]")
    else:
        # Cruce por Autor + Año (APA / general)
        seen_unmatched = set()
        for author, yr, start, end in apa_citations:
            auth_low = author.lower()
            matched = False
            for entry in entries:
                if entry.year == yr and auth_low in entry.raw_text.lower():
                    entry.cited_in_text = True
                    entry.citation_count_in_text += 1
                    _record_context(entry, start, end)
                    matched = True
            if not matched:
                label = f"{author} ({yr})"
                if label not in seen_unmatched:
                    seen_unmatched.add(label)
                    cited_but_not_listed.append(label)

        # Fallback: si un autor de la bibliografía aparece mencionado con su año en el texto aunque sin paréntesis exacto
        for entry in entries:
            if not entry.cited_in_text and entry.first_author and entry.year:
                auth_low = entry.first_author.lower()
                if len(auth_low) >= 3:
                    m_fb = re.search(rf"\b{re.escape(auth_low)}\b.{{0,40}}\b{entry.year}\b", body_text, re.IGNORECASE)
                    if m_fb:
                        entry.cited_in_text = True
                        entry.citation_count_in_text = 1
                        _record_context(entry, m_fb.start(), m_fb.end())

    listed_but_not_cited = [
        (f"[{e.index_num}] {e.first_author} ({e.year or 's/f'})" if e.index_num else f"{e.first_author} ({e.year or 's/f'}) - {e.title[:55]}")
        for e in entries
        if not e.cited_in_text
    ]

    total_bib = len(entries)
    total_in_text = len(ieee_tokens) if style == "IEEE" else len(apa_citations)
    if total_bib > 0:
        cited_ratio = sum(1 for e in entries if e.cited_in_text) / total_bib
        penalty = min(0.4, len(cited_but_not_listed) * 0.04)
        coherence = round(max(0.0, (cited_ratio - penalty) * 100), 1)
    else:
        coherence = 0.0

    return CitationCrossCheck(
        detected_style=style,  # type: ignore
        total_bibliography=total_bib,
        total_in_text_citations=total_in_text,
        cited_but_not_listed=cited_but_not_listed[:25],
        listed_but_not_cited=listed_but_not_cited[:25],
        coherence_score=coherence,
    )


def _compute_bib_analytics(entries: List[BibliographyEntry]) -> Dict[str, Any]:
    current_year = datetime.now().year
    last_5 = 0
    years_6_10 = 0
    older_10 = 0
    no_year = 0
    year_counts: Counter = Counter()
    hierarchy_counts: Counter = Counter()
    author_counts: Counter = Counter()

    for e in entries:
        hierarchy_counts[e.source_category] += 1
        if e.first_author and e.first_author != "Desconocido":
            author_counts[e.first_author] += 1
        if e.year is None:
            no_year += 1
        else:
            year_counts[str(e.year)] += 1
            age = current_year - e.year
            if age <= 5:
                last_5 += 1
            elif age <= 10:
                years_6_10 += 1
            else:
                older_10 += 1

    total = len(entries) or 1
    return {
        "temporal": {
            "last_5_years": last_5,
            "pct_last_5_years": round((last_5 / total) * 100, 1),
            "years_6_to_10": years_6_10,
            "older_than_10": older_10,
            "no_year": no_year,
            "by_year": dict(sorted(year_counts.items())),
        },
        "hierarchy": {
            "primaria_paper": hierarchy_counts.get("primaria_paper", 0),
            "primaria_norma_dataset": hierarchy_counts.get("primaria_norma_dataset", 0),
            "secundaria_libro_manual": hierarchy_counts.get("secundaria_libro_manual", 0),
            "web_blog_wiki": hierarchy_counts.get("web_blog_wiki", 0),
            "otra": hierarchy_counts.get("otra", 0),
        },
        "top_authors": [{"author": a, "count": c} for a, c in author_counts.most_common(6)],
        "formal_issues_count": sum(1 for e in entries if e.formal_issues),
    }


def parse_thesis_pdf(pdf_path: str, topic_hint: str = "") -> Dict[str, Any]:
    """Extrae y analiza estructuralmente la tesis en PDF."""
    pages_text, pdf_info = extract_pdf_pages(pdf_path)
    body_text, bib_text = _split_body_and_bibliography(pages_text)
    section_spans, sections_summary = _extract_thesis_sections(body_text)
    raw_entries = _split_bibliography_entries(bib_text)
    entries = [_parse_single_entry(raw) for raw in raw_entries]
    cross_check = _cross_check_citations(body_text, entries, section_spans=section_spans)
    duplicates, consistency_notes = _detect_bibliography_consistency(entries, cross_check.detected_style)
    analytics = _compute_bib_analytics(entries)
    analytics["secondary_citations_count"] = sum(1 for e in entries if e.has_secondary_citation)
    analytics["duplicate_entries"] = duplicates
    analytics["formal_consistency_notes"] = consistency_notes

    # Extraer fragmentos representativos del cuerpo para el LLM (portada/resumen, objetivos, metodología, discusión)
    intro_excerpt = body_text[:7000]
    mid_idx = max(0, len(body_text) // 2 - 2500)
    mid_excerpt = body_text[mid_idx : mid_idx + 5000] if len(body_text) > 9000 else ""
    end_excerpt = body_text[-6000:] if len(body_text) > 7000 else ""

    title_guess = (pdf_info.get("metadata", {}).get("title") or "").strip()
    if not title_guess:
        first_lines = [ln.strip() for ln in pages_text[0].splitlines() if len(ln.strip()) > 12] if pages_text else []
        title_guess = " ".join(first_lines[:2])[:180] if first_lines else "Tesis en PDF"

    return {
        "title_guess": title_guess,
        "topic_hint": topic_hint.strip() or title_guess,
        "page_count": pdf_info["page_count"],
        "total_chars": sum(len(p) for p in pages_text),
        "low_text_warning": pdf_info.get("low_text_warning", False),
        "sections_summary": sections_summary,
        "body_excerpt_intro": intro_excerpt,
        "body_excerpt_mid": mid_excerpt,
        "body_excerpt_end": end_excerpt,
        "bibliography_entries": [e.model_dump() for e in entries],
        "cross_check": cross_check.model_dump(),
        "analytics": analytics,
    }


def pdf_parser_node(state: GraphState) -> Dict[str, Any]:
    """Nodo de LangGraph para extraer y analizar la estructura del PDF."""
    pdf_path = state["pdf_path"]
    topic_hint = state.get("thesis_topic", "")
    logger.info(f"Agente Extractor PDF analizando: {pdf_path}")
    parsed = parse_thesis_pdf(pdf_path, topic_hint=topic_hint)
    logger.info(
        f"PDF procesado: {parsed['page_count']} páginas, "
        f"{len(parsed['bibliography_entries'])} referencias detectadas."
    )
    return {"parsed_thesis": parsed}

