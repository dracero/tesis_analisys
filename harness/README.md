# Harness Modular de `tesis_analisys` (Compatible con `google-research/harness2` Offline)

Este directorio externaliza el comportamiento del agente evaluador ([evaluator.py](../src/agents/evaluator.py)) en archivos editables versionados en Git, siguiendo la estructura de componentes de **Harness²** sin introducir servidores MCP innecesarios:

- `system_prompt.md`: instrucciones base del tutor académico.
- `guardrails.md`: reglas estrictas anti-falsos positivos y anclaje en evidencia.
- `tool_guidance.md`: criterios para interpretar Crossref, OpenAlex, Semantic Scholar, DuckDuckGo y Crawl4AI.
- `memory.md`: lecciones promovidas tras corridas offline.
- `skills/*.md`: rúbricas modulares (6 pilares, normas APA/IEEE, integridad y contraste de abstracts).
- `scripts/verify_single_ref.py`: utilidad CLI directa (sin MCP) para verificar una referencia puntual.

## Flujo Simplificado en Un Solo Comando (con Hot-Reload y Backup)

Podés ejecutar todo el ciclo (captura automática de PDFs nuevos + `Propose → Probe → Compose` + promoción opcional con backup) con un solo script:

```bash
# Optimizar pasando un PDF o una carpeta de PDFs (pregunta antes de promover a ./harness/)
npm run harness:optimize -- --pdf ruta/a/tesis.pdf --topic "Tema de la tesis"

# Optimizar 2 rondas sobre fixtures existentes y promover si gana el candidato
npm run harness:optimize -- --rounds 2 --focus "reducir falsos positivos" --auto-promote

# Revertir al último backup en 1 comando si algo empeoró
npm run harness:rollback
```

Como `src/harness_loader.py` recarga por `mtime`, al promover un candidato el servidor `server.py` (si está corriendo) adopta el nuevo `harness_hash` en caliente sin reiniciar.

## Flujo Manual Paso a Paso (`harness2_offline.py`)

El servidor de producción (`server.py`) **nunca** modifica este directorio en vivo. Las mejoras se iteran offline sobre fixtures congelados:

```bash
# 1. Capturar un fixture congelado (ejecuta pdf_parser + verifier una sola vez)
uv run python scripts/harness2_offline.py capture ruta/a/tesis.pdf --topic "Tema de la tesis"

# 2. Correr un ciclo Propose -> Probe -> Compose aislado en ./harness_runs/
uv run python scripts/harness2_offline.py optimize --max-fixtures 3 --focus "reducir falsos positivos en ausencias bibliográficas"
```

Los resultados quedan en `harness_runs/reports/<run_id>.md` con el `diff` propuesto y el registro en `harness_runs/improvement_ledger.jsonl`. Si el contraste resulta favorable, el humano promueve los cambios seleccionados a `./harness/` y los versiona con Git.

