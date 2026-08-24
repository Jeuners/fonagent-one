"""Gemeinsames Ereignisprotokoll der Pipe.

Jede Zeile ist ein JSON-Objekt (JSONL) — maschinenlesbar für das Dashboard und
trotzdem mit ``tail -f`` lesbar. Bewusst schlank: anhängen, nie sperren, nie
scheitern. Ein kaputtes Protokoll darf keinen Anruf kosten.

Diese Datei ist reine UI-Historie (siehe MAX_ZEILEN/kuerze) - fuer einen
dauerhaften Nachweis (Compliance, u.a. Loeschungen) schreibt schreibe()
jedes Ereignis zusaetzlich unveraenderlich nach audit.log, siehe pipe.audit.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import audit, config

DATEI = config.TELEFON / "verlauf.log"
MAX_ZEILEN = 2000          # darüber wird beim Schreiben gekürzt


def schreibe(art: str, text: str, **felder) -> None:
    """Hängt ein Ereignis an (UI-Verlauf + dauerhaftes Audit-Log). Wirft nie."""
    try:
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        eintrag = {"zeit": datetime.now().isoformat(timespec="seconds"),
                   "art": art, "text": text, **felder}
        with DATEI.open("a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except Exception:
        pass
    audit.schreibe(art, text, **felder)


def lies(anzahl: int = 200) -> list[dict]:
    """Liest die letzten Ereignisse. Defekte Zeilen werden übersprungen."""
    if not DATEI.is_file():
        return []
    try:
        zeilen = DATEI.read_text(encoding="utf-8").splitlines()[-anzahl:]
    except Exception:
        return []
    eintraege = []
    for z in zeilen:
        try:
            eintraege.append(json.loads(z))
        except json.JSONDecodeError:
            continue
    return eintraege


def kuerze() -> None:
    """Hält das Protokoll auf MAX_ZEILEN. Wirft nie."""
    try:
        if not DATEI.is_file():
            return
        zeilen = DATEI.read_text(encoding="utf-8").splitlines()
        if len(zeilen) > MAX_ZEILEN:
            DATEI.write_text("\n".join(zeilen[-MAX_ZEILEN:]) + "\n", encoding="utf-8")
    except Exception:
        pass
