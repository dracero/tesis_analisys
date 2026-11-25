"""Cargador modular del Harness (compatible con evolución offline estilo google-research/harness2).

Compone dinámicamente:
  - system_prompt.md
  - guardrails.md
  - tool_guidance.md
  - memory.md
  - skills/*.md

Con caché por mtime y hash de contenido para trazabilidad y recarga segura sin tocar código Python.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HARNESS_DIR = PROJECT_ROOT / "harness"


@dataclass(frozen=True)
class LoadedHarness:
    harness_dir: Path
    content_hash: str
    files_loaded: Tuple[str, ...]
    composed_system_prompt: str


_CACHE: Dict[str, Tuple[Tuple[Tuple[str, float], ...], LoadedHarness]] = {}


def resolve_harness_dir(harness_dir: Optional[str | Path] = None) -> Path:
    if harness_dir is not None:
        return Path(harness_dir).resolve()
    env_dir = os.getenv("HARNESS_DIR", "").strip()
    if env_dir:
        p = Path(env_dir)
        return p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return DEFAULT_HARNESS_DIR.resolve()


def _collect_harness_files(root: Path) -> List[Tuple[str, Path]]:
    ordered: List[Tuple[str, Path]] = []
    for rel in ("system_prompt.md", "guardrails.md", "tool_guidance.md", "memory.md"):
        candidate = root / rel
        if candidate.is_file():
            ordered.append((rel, candidate))

    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for skill_file in sorted(skills_dir.glob("*.md")):
            ordered.append((f"skills/{skill_file.name}", skill_file))

    return ordered


def load_harness(harness_dir: Optional[str | Path] = None) -> LoadedHarness:
    root = resolve_harness_dir(harness_dir)
    files = _collect_harness_files(root)
    sig = tuple((rel, path.stat().st_mtime) for rel, path in files)
    cache_key = str(root)

    cached = _CACHE.get(cache_key)
    if cached and cached[0] == sig:
        return cached[1]

    sections: List[str] = []
    hasher = hashlib.sha256()
    loaded_names: List[str] = []

    for rel, path in files:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        loaded_names.append(rel)
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(text.encode("utf-8"))
        sections.append(text)

    composed = "\n\n---\n\n".join(sections)
    loaded = LoadedHarness(
        harness_dir=root,
        content_hash=hasher.hexdigest()[:12] if loaded_names else "empty",
        files_loaded=tuple(loaded_names),
        composed_system_prompt=composed,
    )
    _CACHE[cache_key] = (sig, loaded)
    return loaded


def get_harness_metadata(harness_dir: Optional[str | Path] = None) -> Dict[str, object]:
    loaded = load_harness(harness_dir)
    return {
        "harness_dir": str(loaded.harness_dir),
        "harness_hash": loaded.content_hash,
        "files_loaded": list(loaded.files_loaded),
    }


def save_harness_fixture(
    filename: str,
    thesis_topic: str,
    parsed_thesis: Dict[str, Any],
    verification_results: Dict[str, Any],
) -> Optional[Path]:
    """Guarda un fixture congelado en harness_runs/fixtures/ para alimentar optimize_harness.py."""
    if os.getenv("HARNESS_AUTO_CAPTURE", "true").strip().lower() == "false":
        return None
    try:
        fixtures_dir = PROJECT_ROOT / "harness_runs" / "fixtures"
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(filename or "tesis").stem
        slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "tesis"
        fixture_path = fixtures_dir / f"{slug}.json"
        payload = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
            "thesis_topic": thesis_topic,
            "parsed_thesis": parsed_thesis,
            "verification_results": verification_results,
        }
        fixture_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return fixture_path
    except Exception:
        return None

