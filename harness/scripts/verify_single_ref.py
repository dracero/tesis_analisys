"""Script CLI liviano (sin MCP) para verificar una referencia bibliográfica puntual vía Crossref y OpenAlex."""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def _http_get_json(url: str, timeout: int = 12) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TesisAnalisysHarness/1.0 (mailto:research@local.invalid)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def verify_reference(query: str, doi: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "query": query,
        "doi": doi,
        "found": False,
        "source": None,
        "title": None,
        "year": None,
        "retracted": False,
        "citation_count": None,
        "url": None,
    }

    if doi:
        clean_doi = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        cr = _http_get_json(f"https://api.crossref.org/works/{urllib.parse.quote(clean_doi)}")
        if cr and isinstance(cr.get("message"), dict):
            msg = cr["message"]
            titles = msg.get("title") or []
            issued = (msg.get("issued") or {}).get("date-parts") or [[None]]
            updates = msg.get("update-to") or []
            retracted = any(str(u.get("type", "")).lower() == "retraction" for u in updates)
            result.update(
                {
                    "found": True,
                    "source": "crossref",
                    "title": titles[0] if titles else None,
                    "year": issued[0][0] if issued and issued[0] else None,
                    "retracted": retracted,
                    "citation_count": msg.get("is-referenced-by-count"),
                    "url": msg.get("URL"),
                }
            )
            return result

    if query:
        oa = _http_get_json(
            f"https://api.openalex.org/works?search={urllib.parse.quote(query)}&per-page=1"
        )
        works = (oa or {}).get("results") or []
        if works:
            w = works[0]
            result.update(
                {
                    "found": True,
                    "source": "openalex",
                    "title": w.get("display_name"),
                    "year": w.get("publication_year"),
                    "retracted": bool(w.get("is_retracted")),
                    "citation_count": w.get("cited_by_count"),
                    "url": w.get("doi") or w.get("id"),
                }
            )
            return result

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica una referencia puntual en Crossref/OpenAlex.")
    parser.add_argument("--query", type=str, default="", help="Título o cita textual")
    parser.add_argument("--doi", type=str, default="", help="DOI opcional")
    args = parser.parse_args()
    if not args.query and not args.doi:
        print(json.dumps({"error": "Debe indicar --query o --doi"}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(verify_reference(args.query, args.doi), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

