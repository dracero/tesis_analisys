# Guía de Interpretación de Herramientas Externas (Crossref, OpenAlex, Semantic Scholar, DuckDuckGo, Crawl4AI)

- **Crossref / OpenAlex / Semantic Scholar**: Son fuentes primarias para papers arbitrados y preprints con DOI. Que un libro impreso, tesis de grado local, ley o informe técnico no aparezca en estas APIs es normal y no implica inexistencia.
- **Chequeo en lote (`bulk_check_json`)**: Cubre hasta 60 entradas con DOI/URL. Un artículo marcado como `retracted: true` es un hallazgo crítico de integridad académica que debe señalarse explícitamente.
- **Crawl4AI + DuckDuckGo**: Se ejecutan solo cuando las APIs académicas no devolvieron abstract. Si Crawl4AI reporta paywall/login/CAPTCHA, considéralo evidencia incompleta, no evidencia negativa.
- **Literatura del campo (`field_lit_json`)**: Úsala para sugerir vacíos bibliográficos concretos únicamente cuando `already_in_thesis` es `false` y el trabajo sea pertinente al `topic_hint` o `title_guess`.

