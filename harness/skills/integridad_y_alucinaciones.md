# Skill: Integridad Académica y Contraste de Abstracts

- Para cada referencia en `verified_refs_json`, contrasta el fragmento donde el tesista la cita (`context_snippets`) contra el `abstract` recuperado de Crossref, OpenAlex, Semantic Scholar o Crawl4AI.
- Clasifica explícitamente si la referencia: (a) respalda directamente la afirmación, (b) tiene relación parcial o imprecisa, (c) contradice o no sustenta lo afirmado, o (d) carece de abstract recuperado para confirmar el contenido.
- Prioriza alertas críticas ante artículos retractados (`retracted: true`) o DOIs que resuelven a un trabajo completamente distinto del citado.

