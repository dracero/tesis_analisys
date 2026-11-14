"""Agente Verificador de Referencias y Literatura: Usa Crossref, OpenAlex, DuckDuckGo (ddgs) y Crawl4AI/Playwright."""

import asyncio
import difflib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from ddgs import DDGS

from src.state import VerifiedReference, FieldLiteratureItem, GraphState

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "TesisAnalisys/1.0 (mailto:tutor@fi.uba.ar)"}
_CRAWLER_GLOBAL_SEM: Optional[asyncio.Semaphore] = None


def _get_crawler_global_semaphore() -> asyncio.Semaphore:
    global _CRAWLER_GLOBAL_SEM
    if _CRAWLER_GLOBAL_SEM is None:
        _CRAWLER_GLOBAL_SEM = asyncio.Semaphore(2)
    return _CRAWLER_GLOBAL_SEM


def _http_get_json(url: str, timeout: int = 8) -> Optional[Dict[str, Any]]:
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt == 0:
                time.sleep(1.0)
                continue
            return None
        except Exception:
            if attempt == 0:
                time.sleep(0.4)
                continue
            return None
    return None


def _reconstruct_openalex_abstract(inv_index: Optional[Dict[str, List[int]]]) -> str:
    if not inv_index:
        return ""
    words: Dict[int, str] = {}
    for word, positions in inv_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words.keys()))[:800]


def _query_crossref(doi: Optional[str], title: str, first_author: str) -> Optional[Dict[str, Any]]:
    if doi:
        clean_doi = urllib.parse.quote(doi.strip(), safe="")
        data = _http_get_json(f"https://api.crossref.org/works/{clean_doi}")
        if data and data.get("message"):
            return data["message"]

    q_parts = [p for p in (title[:120], first_author) if p and p != "Desconocido"]
    if not q_parts:
        return None
    q = urllib.parse.quote_plus(" ".join(q_parts))
    data = _http_get_json(f"https://api.crossref.org/works?query.bibliographic={q}&rows=2")
    items = (data or {}).get("message", {}).get("items", [])
    return items[0] if items else None


def _query_openalex(doi: Optional[str], title: str, first_author: str) -> Optional[Dict[str, Any]]:
    if doi:
        clean_doi = urllib.parse.quote(f"https://doi.org/{doi.strip()}", safe="")
        data = _http_get_json(f"https://api.openalex.org/works/{clean_doi}")
        if data and data.get("id"):
            return data

    q_parts = [p for p in (title[:110], first_author) if p and p != "Desconocido"]
    if not q_parts:
        return None
    q = urllib.parse.quote_plus(" ".join(q_parts))
    data = _http_get_json(f"https://api.openalex.org/works?search={q}&per-page=2")
    results = (data or {}).get("results", [])
    return results[0] if results else None


def _query_semantic_scholar(doi: Optional[str], title: str, first_author: str) -> Optional[Dict[str, Any]]:
    fields = "title,authors,year,abstract,citationCount,venue,publicationTypes,externalIds,url"
    if doi:
        clean_doi = urllib.parse.quote(doi.strip(), safe="")
        data = _http_get_json(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{clean_doi}?fields={fields}", timeout=7)
        if data and data.get("title"):
            return data

    q_parts = [p for p in (title[:110], first_author) if p and p != "Desconocido"]
    if not q_parts:
        return None
    q = urllib.parse.quote_plus(" ".join(q_parts))
    data = _http_get_json(f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit=2&fields={fields}", timeout=7)
    items = (data or {}).get("data", [])
    return items[0] if items else None


def _check_url_alive(url: str, timeout: int = 6) -> Tuple[bool, Optional[int]]:
    """Comprueba si una URL responde HTTP < 400 usando HEAD con fallback a GET."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=_HEADERS, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = getattr(resp, "status", 200)
                return (code < 400), code
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 501):
                continue
            return False, e.code
        except Exception:
            if method == "HEAD":
                continue
            return False, None
    return False, None


def _query_ddgs_reference(title: str, first_author: str, year: Optional[int]) -> Optional[Dict[str, str]]:
    parts = [p for p in (f'"{title[:90]}"' if len(title) > 20 else title, first_author if first_author != "Desconocido" else "", str(year) if year else "") if p]
    if not parts:
        return None
    q = " ".join(parts)
    try:
        with DDGS(timeout=7) as ddgs:
            res = list(ddgs.text(q, max_results=3))
            if res:
                return {
                    "title": res[0].get("title", ""),
                    "url": res[0].get("href", ""),
                    "snippet": res[0].get("body", ""),
                }
    except Exception:
        pass
    return None


def _title_similarity(a: str, b: str) -> float:
    na = re.sub(r"[^a-z0-9\s]", " ", (a or "").lower()).split()
    nb = re.sub(r"[^a-z0-9\s]", " ", (b or "").lower()).split()
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, " ".join(na), " ".join(nb)).ratio()


def _verify_single_reference(entry: Dict[str, Any]) -> VerifiedReference:
    raw = entry.get("raw_text", "")
    title = entry.get("title", "")
    author = entry.get("first_author", "")
    year = entry.get("year")
    doi = entry.get("doi")
    url = entry.get("url")
    contexts = entry.get("context_snippets") or []
    claim_ctx = " | ".join(contexts[:2]) if contexts else "(Sin contexto directo extraído en el cuerpo)"

    # 1. Crossref
    cr = _query_crossref(doi, title, author)
    if cr:
        cr_title = (cr.get("title") or [""])[0]
        cr_authors = ", ".join(
            f"{a.get('family', '')}".strip() for a in (cr.get("author") or [])[:4] if a.get("family")
        )
        cr_year = None
        for date_field in ("published-print", "published-online", "issued", "created"):
            parts = (cr.get(date_field) or {}).get("date-parts", [[]])
            if parts and parts[0] and isinstance(parts[0][0], int):
                cr_year = parts[0][0]
                break
        cr_url = cr.get("URL") or (f"https://doi.org/{cr.get('DOI')}" if cr.get("DOI") else "")
        cr_abstract = re.sub(r"<[^>]+>", " ", cr.get("abstract") or "").strip()[:1000]
        cr_venue = ((cr.get("container-title") or [""])[0] or cr.get("publisher") or "").strip()
        cr_type = cr.get("type") or ""
        cr_cites = cr.get("is-referenced-by-count")
        cr_retracted = any(
            (u.get("type") or "").lower() == "retraction"
            for u in (cr.get("update-to") or [])
        )

        sim = _title_similarity(title, cr_title)
        author_match = (not author or author == "Desconocido" or author.lower() in cr_authors.lower())
        year_match = (year is None or cr_year is None or abs(year - cr_year) <= 1)

        if doi or (sim >= 0.55 and author_match):
            doi_title_mismatch = bool(doi and title and len(title) > 15 and sim < 0.35)
            status = "VERIFICADA" if (year_match and not doi_title_mismatch and (sim >= 0.45 or doi)) else "PARCIAL"
            notes = "Coincide en Crossref."
            if cr_retracted:
                status = "SOSPECHOSA"
                notes = "ALERTA: Artículo marcado como RETRACTADO en Crossref."
            elif doi_title_mismatch:
                status = "SOSPECHOSA"
                notes = f"El DOI existe en Crossref pero corresponde a otro título ('{cr_title[:80]}'). Posible error de DOI."
            elif not year_match:
                notes = f"Título/autor encontrados en Crossref, pero el año difiere ({year} vs {cr_year})."
            elif not author_match:
                status = "SOSPECHOSA"
                notes = f"Título similar en Crossref pero autor distinto ({author} vs {cr_authors}). Posible error de atribución."

            if not cr_abstract:
                oa_extra = _query_openalex(doi or cr.get("DOI"), cr_title, author)
                if oa_extra:
                    cr_abstract = _reconstruct_openalex_abstract(oa_extra.get("abstract_inverted_index"))[:1000]
                if not cr_abstract:
                    s2_extra = _query_semantic_scholar(doi or cr.get("DOI"), cr_title, author)
                    if s2_extra:
                        cr_abstract = (s2_extra.get("abstract") or "").strip()[:1000]

            return VerifiedReference(
                reference_text=raw,
                first_author=author,
                year=year,
                doi=doi or cr.get("DOI"),
                url=url,
                status=status,  # type: ignore
                matched_title=cr_title,
                matched_authors=cr_authors,
                matched_year=cr_year,
                matched_url=cr_url,
                verification_source="Crossref",
                venue=cr_venue,
                source_type=cr_type,
                cited_by_count=cr_cites,
                is_retracted=cr_retracted,
                abstract_or_excerpt=cr_abstract,
                thesis_claim_context=claim_ctx,
                alignment_notes=notes,
            )

    # 2. OpenAlex
    oa = _query_openalex(doi, title, author)
    if oa:
        oa_title = oa.get("title") or ""
        oa_year = oa.get("publication_year")
        oa_authors = ", ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (oa.get("authorships") or [])[:4]
        )
        oa_url = oa.get("doi") or oa.get("id") or ""
        oa_abstract = _reconstruct_openalex_abstract(oa.get("abstract_inverted_index"))
        oa_venue = (((oa.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip()
        oa_type = oa.get("type") or ""
        oa_cites = oa.get("cited_by_count")
        oa_retracted = bool(oa.get("is_retracted", False))
        sim = _title_similarity(title, oa_title)
        author_match = (not author or author == "Desconocido" or author.lower() in oa_authors.lower())

        if (doi or sim >= 0.52) and author_match:
            year_match = (year is None or oa_year is None or abs(year - oa_year) <= 1)
            status = "VERIFICADA" if year_match else "PARCIAL"
            notes = "Verificada en OpenAlex." if year_match else f"Año difiere en OpenAlex ({year} vs {oa_year})."
            if oa_retracted:
                status = "SOSPECHOSA"
                notes = "ALERTA: Artículo marcado como RETRACTADO en OpenAlex."
            return VerifiedReference(
                reference_text=raw,
                first_author=author,
                year=year,
                doi=doi,
                url=url,
                status=status,  # type: ignore
                matched_title=oa_title,
                matched_authors=oa_authors,
                matched_year=oa_year,
                matched_url=oa_url,
                verification_source="OpenAlex",
                venue=oa_venue,
                source_type=oa_type,
                cited_by_count=oa_cites,
                is_retracted=oa_retracted,
                abstract_or_excerpt=oa_abstract,
                thesis_claim_context=claim_ctx,
                alignment_notes=notes,
            )

    # 3. Semantic Scholar
    s2 = _query_semantic_scholar(doi, title, author)
    if s2:
        s2_title = s2.get("title") or ""
        s2_year = s2.get("year")
        s2_authors = ", ".join(a.get("name", "") for a in (s2.get("authors") or [])[:4] if a.get("name"))
        s2_url = s2.get("url") or ""
        s2_abstract = (s2.get("abstract") or "").strip()[:1000]
        s2_venue = (s2.get("venue") or "").strip()
        s2_types = ", ".join(s2.get("publicationTypes") or [])
        s2_cites = s2.get("citationCount")
        sim = _title_similarity(title, s2_title)
        author_match = (not author or author == "Desconocido" or author.lower() in s2_authors.lower())
        if (doi or sim >= 0.52) and author_match:
            year_match = (year is None or s2_year is None or abs(year - s2_year) <= 1)
            return VerifiedReference(
                reference_text=raw,
                first_author=author,
                year=year,
                doi=doi or (s2.get("externalIds") or {}).get("DOI"),
                url=url,
                status="VERIFICADA" if year_match else "PARCIAL",
                matched_title=s2_title,
                matched_authors=s2_authors,
                matched_year=s2_year,
                matched_url=s2_url,
                verification_source="Semantic Scholar",
                venue=s2_venue,
                source_type=s2_types,
                cited_by_count=s2_cites,
                abstract_or_excerpt=s2_abstract,
                thesis_claim_context=claim_ctx,
                alignment_notes="Verificada en Semantic Scholar." if year_match else f"Año difiere en Semantic Scholar ({year} vs {s2_year}).",
            )

    # 3. DuckDuckGo Web Search
    ddg_hit = _query_ddgs_reference(title, author, year)
    if ddg_hit:
        sim = _title_similarity(title, ddg_hit["title"])
        author_in_hit = author.lower() in (ddg_hit["title"] + " " + ddg_hit["snippet"]).lower() if author and author != "Desconocido" else True
        if sim >= 0.45 or (author_in_hit and len(title) > 15 and any(w in ddg_hit["title"].lower() for w in title.lower().split()[:3])):
            return VerifiedReference(
                reference_text=raw,
                first_author=author,
                year=year,
                doi=doi,
                url=url,
                status="VERIFICADA" if author_in_hit else "PARCIAL",
                matched_title=ddg_hit["title"],
                matched_authors=author,
                matched_year=year,
                matched_url=ddg_hit["url"],
                verification_source="DuckDuckGo Web",
                abstract_or_excerpt=ddg_hit["snippet"][:600],
                thesis_claim_context=claim_ctx,
                alignment_notes="Localizada en búsqueda web.",
            )

    return VerifiedReference(
        reference_text=raw,
        first_author=author,
        year=year,
        doi=doi,
        url=url,
        status="SOSPECHOSA" if (not url and len(title) > 25) else "NO_ENCONTRADA",
        matched_url=url or "",
        verification_source="Sin coincidencia",
        thesis_claim_context=claim_ctx,
        alignment_notes="No se hallaron coincidencias en Crossref, OpenAlex, Semantic Scholar ni DuckDuckGo. Verificar existencia real.",
    )


_BAD_PAGE_PATTERNS = (
    "404 not found", "page not found", "página no encontrada", "error 404",
    "access denied", "enable javascript", "just a moment...", "cloudflare",
    "captcha", "sign in to continue", "403 forbidden", "502 bad gateway",
)


def _is_valid_crawled_content(clean_md: str, ref: VerifiedReference) -> bool:
    if not clean_md or len(clean_md) < 120:
        return False
    low = clean_md.lower()
    if any(pat in low for pat in _BAD_PAGE_PATTERNS):
        return False
    tokens = [
        w for w in re.findall(r"[a-záéíóúñ]{4,}", (ref.reference_text or "").lower())
        if w not in _STOPWORDS
    ][:8]
    if tokens and not any(t in low for t in tokens):
        return False
    return True


async def _crawl_urls_for_evidence(refs: List[VerifiedReference]) -> None:
    """Usa Crawl4AI + Playwright en paralelo (con límite de concurrencia) para URLs sin extracto."""
    targets = [r for r in refs if (r.url or r.matched_url) and not r.abstract_or_excerpt][:10]
    if not targets:
        return

    browser_config = BrowserConfig(headless=True, verbose=False, browser_type="chromium")
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=15,
        exclude_external_links=True,
        remove_overlay_elements=True,
        delay_before_return_html=1.5,
        page_timeout=18000,
    )

    sem = asyncio.Semaphore(4)

    async def _crawl_one(crawler: AsyncWebCrawler, ref: VerifiedReference) -> None:
        target_url = ref.url or ref.matched_url
        async with sem:
            try:
                res = await crawler.arun(url=target_url, config=run_config)
                status_code = getattr(res, "status_code", None)
                if status_code is not None and status_code >= 400:
                    if ref.status == "NO_ENCONTRADA":
                        ref.alignment_notes = f"URL inaccesible (HTTP {status_code})."
                    return
                if res.success:
                    md = res.markdown.raw_markdown if hasattr(res.markdown, "raw_markdown") else str(res.markdown)
                    clean_md = " ".join(md.split())[:1000]
                    if _is_valid_crawled_content(clean_md, ref):
                        ref.abstract_or_excerpt = clean_md
                        if ref.status == "NO_ENCONTRADA":
                            ref.status = "VERIFICADA"
                            ref.verification_source = "Crawl4AI (URL directa)"
                            ref.alignment_notes = "Enlace accesible y contenido coincidente extraído vía Playwright."
                    elif ref.status == "NO_ENCONTRADA":
                        ref.alignment_notes = "URL respondió pero sin contenido válido o coincidente (posible 404/muro de acceso)."
            except Exception as e:
                logger.debug(f"No se pudo crawlear {target_url}: {e}")

    try:
        async with _get_crawler_global_semaphore():
            async with AsyncWebCrawler(config=browser_config) as crawler:
                await asyncio.gather(*(_crawl_one(crawler, ref) for ref in targets))
    except Exception as e:
        logger.warning(f"Crawl4AI omitido en verificación de URLs: {e}")


_STOPWORDS = {
    "para", "como", "sobre", "desde", "entre", "hacia", "mediante", "estudio", "analisis",
    "análisis", "tesis", "sistema", "aplicacion", "aplicación", "desarrollo", "evaluacion",
    "evaluación", "propuesta", "modelo", "diseño", "implementacion", "implementación",
    "with", "from", "that", "this", "using", "based", "study", "analysis", "system",
}


def _extract_search_keywords(topic: str) -> str:
    words = re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9\-]{3,}", topic)
    filtered = [w for w in words if w.lower() not in _STOPWORDS]
    return " ".join((filtered or words)[:8])[:100]


def _is_in_thesis_bibliography(title: str, authors: str, entries: List[Dict[str, Any]]) -> bool:
    first_surname = (authors.split(",")[0].strip().split()[-1].lower() if authors else "")
    for e in entries:
        e_title = e.get("title") or ""
        if title and e_title and _title_similarity(title, e_title) >= 0.60:
            return True
        if first_surname and len(first_surname) >= 4 and first_surname in (e.get("raw_text") or "").lower():
            if title and any(w in (e.get("raw_text") or "").lower() for w in title.lower().split()[:3] if len(w) >= 4):
                return True
    return False


def _search_field_literature(topic: str, entries: Optional[List[Dict[str, Any]]] = None) -> List[FieldLiteratureItem]:
    """Busca literatura fundacional (más citada) y debates actuales (últimos 5 años) del tema."""
    items: List[FieldLiteratureItem] = []
    clean_topic = _extract_search_keywords(topic)
    if not clean_topic:
        return items

    bib_entries = entries or []
    q = urllib.parse.quote_plus(clean_topic)
    current_year = datetime.now().year

    # 1. Trabajos fundacionales / más citados en OpenAlex
    oa_found = _http_get_json(f"https://api.openalex.org/works?search={q}&sort=cited_by_count:desc&per-page=5")
    for w in (oa_found or {}).get("results", []):
        w_title = w.get("title") or "Sin título"
        authors = ", ".join((a.get("author") or {}).get("display_name", "") for a in (w.get("authorships") or [])[:3])
        items.append(FieldLiteratureItem(
            title=w_title,
            authors=authors,
            year=w.get("publication_year"),
            source=((w.get("primary_location") or {}).get("source") or {}).get("display_name", "OpenAlex"),
            url=w.get("doi") or w.get("id") or "",
            snippet=f"Citado {w.get('cited_by_count', 0)} veces. " + _reconstruct_openalex_abstract(w.get("abstract_inverted_index"))[:240],
            category="fundacional",
            already_in_thesis=_is_in_thesis_bibliography(w_title, authors, bib_entries),
        ))

    # 2. Debates actuales (últimos 5 años) en OpenAlex
    min_year = current_year - 5
    oa_recent = _http_get_json(
        f"https://api.openalex.org/works?search={q}&filter=publication_year:{min_year}-{current_year}&sort=cited_by_count:desc&per-page=5"
    )
    for w in (oa_recent or {}).get("results", []):
        w_title = w.get("title") or "Sin título"
        authors = ", ".join((a.get("author") or {}).get("display_name", "") for a in (w.get("authorships") or [])[:3])
        items.append(FieldLiteratureItem(
            title=w_title,
            authors=authors,
            year=w.get("publication_year"),
            source=((w.get("primary_location") or {}).get("source") or {}).get("display_name", "OpenAlex"),
            url=w.get("doi") or w.get("id") or "",
            snippet=f"Publicado en {w.get('publication_year')}. " + _reconstruct_openalex_abstract(w.get("abstract_inverted_index"))[:240],
            category="debate_actual",
            already_in_thesis=_is_in_thesis_bibliography(w_title, authors, bib_entries),
        ))

    # 3. Complemento DuckDuckGo para detectar guías/debates recientes en español
    try:
        with DDGS(timeout=6) as ddgs:
            ddg_res = list(ddgs.text(f"{clean_topic} estado del arte revisión bibliográfica {current_year - 2}..{current_year}", max_results=3))
            for r in ddg_res:
                if r.get("title") and r.get("href"):
                    items.append(FieldLiteratureItem(
                        title=r["title"],
                        authors="Web / Académico",
                        year=current_year,
                        source="DuckDuckGo",
                        url=r["href"],
                        snippet=(r.get("body") or "")[:240],
                        category="debate_actual",
                        already_in_thesis=False,
                    ))
    except Exception:
        pass

    return items


def _verify_all_bibliography_light(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verificación rápida en lote de DOIs, retracciones y enlaces HTTP de toda la bibliografía."""
    targets = [e for e in entries if e.get("doi") or e.get("url")][:60]
    if not targets:
        return {
            "total_entries": len(entries),
            "checked_entries": 0,
            "dois_checked": 0,
            "invalid_dois": [],
            "retracted_papers": [],
            "urls_checked": 0,
            "broken_urls": [],
            "venues_detected": [],
        }

    def _check_one(e: Dict[str, Any]) -> Dict[str, Any]:
        doi = e.get("doi")
        url = e.get("url")
        label = f"{e.get('first_author', 'Autor')} ({e.get('year') or 's/f'})"
        res: Dict[str, Any] = {"doi_ok": None, "retracted": False, "venue": "", "url_ok": None, "label": label, "doi": doi, "url": url}

        if doi:
            clean_doi = urllib.parse.quote(f"https://doi.org/{doi.strip()}", safe="")
            oa = _http_get_json(f"https://api.openalex.org/works/{clean_doi}?select=id,is_retracted,primary_location", timeout=6)
            if oa and oa.get("id"):
                res["doi_ok"] = True
                res["retracted"] = bool(oa.get("is_retracted", False))
                res["venue"] = (((oa.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip()
            else:
                cr_doi = urllib.parse.quote(doi.strip(), safe="")
                cr = _http_get_json(f"https://api.crossref.org/works/{cr_doi}", timeout=6)
                if cr and cr.get("message"):
                    res["doi_ok"] = True
                    msg = cr["message"]
                    res["venue"] = ((msg.get("container-title") or [""])[0] or "").strip()
                    res["retracted"] = any((u.get("type") or "").lower() == "retraction" for u in (msg.get("update-to") or []))
                else:
                    res["doi_ok"] = False

        if url and not (doi and res["doi_ok"]):
            alive, code = _check_url_alive(url, timeout=6)
            res["url_ok"] = alive
            res["http_code"] = code

        return res

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_check_one, targets))

    invalid_dois = [f"{r['label']} -> DOI:{r['doi']}" for r in results if r["doi_ok"] is False]
    retracted = [f"{r['label']} -> DOI:{r['doi']}" for r in results if r["retracted"]]
    broken_urls = [
        f"{r['label']} -> {r['url']} (HTTP {r.get('http_code') or 'timeout'})"
        for r in results if r["url_ok"] is False
    ]
    venues = list(dict.fromkeys(r["venue"] for r in results if r["venue"]))[:15]

    return {
        "total_entries": len(entries),
        "checked_entries": len(targets),
        "dois_checked": sum(1 for r in results if r["doi_ok"] is not None),
        "invalid_dois": invalid_dois[:15],
        "retracted_papers": retracted[:10],
        "urls_checked": sum(1 for r in results if r["url_ok"] is not None),
        "broken_urls": broken_urls[:15],
        "venues_detected": venues,
    }


def select_sample_references(entries: List[Dict[str, Any]], sample_size: int) -> List[Dict[str, Any]]:
    """Selecciona entre 5 y 10 referencias clave (priorizando citadas en el texto, con DOI/URL y variadas)."""
    if not entries:
        return []
    target_n = max(1, min(len(entries), sample_size))
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for e in entries:
        score = 0
        if e.get("cited_in_text"):
            score += 5
        score += min(3, e.get("citation_count_in_text", 0))
        if e.get("doi") or e.get("url"):
            score += 2
        if e.get("formal_issues"):
            score += 1
        scored.append((score, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:target_n]]


async def verify_references_and_field_async(parsed_thesis: Dict[str, Any], sample_size: int = 8) -> Dict[str, Any]:
    """Ejecuta en paralelo la verificación de 5-10 referencias clave y la búsqueda de literatura del campo."""
    entries = parsed_thesis.get("bibliography_entries", [])
    topic = parsed_thesis.get("topic_hint") or parsed_thesis.get("title_guess") or "Investigación académica"
    sampled = select_sample_references(entries, sample_size=sample_size)

    def _run_sampled():
        with ThreadPoolExecutor(max_workers=4) as ex:
            return list(ex.map(_verify_single_reference, sampled))

    verified_refs, field_lit, bulk_check = await asyncio.gather(
        asyncio.to_thread(_run_sampled),
        asyncio.to_thread(_search_field_literature, topic, entries),
        asyncio.to_thread(_verify_all_bibliography_light, entries),
    )
    await _crawl_urls_for_evidence(verified_refs)

    verified_count = sum(1 for r in verified_refs if r.status == "VERIFICADA")
    partial_count = sum(1 for r in verified_refs if r.status == "PARCIAL")
    suspect_count = sum(1 for r in verified_refs if r.status in ("SOSPECHOSA", "NO_ENCONTRADA"))

    return {
        "sampled_count": len(verified_refs),
        "verified_count": verified_count,
        "partial_count": partial_count,
        "suspect_or_missing_count": suspect_count,
        "verified_references": [r.model_dump() for r in verified_refs],
        "field_literature": [f.model_dump() for f in field_lit],
        "bulk_check_summary": bulk_check,
    }


def reference_verifier_node(state: GraphState) -> Dict[str, Any]:
    """Nodo de LangGraph para verificar referencias muestreadas y buscar literatura del campo."""
    parsed = state.get("parsed_thesis", {})
    sample_size = state.get("sample_size", 8)
    logger.info(f"Agente Verificador comprobando hasta {sample_size} referencias y literatura del campo...")

    try:
        res = asyncio.run(verify_references_and_field_async(parsed, sample_size=sample_size))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(verify_references_and_field_async(parsed, sample_size=sample_size))
        finally:
            loop.close()

    logger.info(
        f"Verificación finalizada: {res['verified_count']} verificadas, "
        f"{res['partial_count']} parciales, {res['suspect_or_missing_count']} sospechosas/no halladas."
    )
    return {"verification_results": res}

