"""Bewertung eines Anrufs durch das Praxispersonal (👍/👎 im Leitstand).

Dient gleichzeitig als Trainingsdaten fuer die Modellverbesserung - siehe
pipe.export fuer den Export als JSONL. Gleiches Muster wie stapel.py: eine
kleine JSON-Datei pro Anruf-Ordner, unabhaengig von meta.json (das aendert
sich nach der Ablage nie mehr, die Bewertung schon).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATEI = "bewertung.json"


def lies(ordner: Path) -> dict | None:
    """Bewertung eines Anrufs, oder None wenn (noch) unbewertet."""
    pfad = ordner / DATEI
    if not pfad.is_file():
        return None
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return None


def setze(ordner: Path, bewertung: str, korrektur: dict | None = None) -> bool:
    """Speichert eine Bewertung. False bei ungueltigem Wert."""
    if bewertung not in {"gut", "schlecht"}:
        return False
    try:
        (ordner / DATEI).write_text(json.dumps({
            "bewertung": bewertung,
            "korrektur": korrektur,
            "zeitpunkt": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False
