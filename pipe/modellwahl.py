"""Aktives Kategorisierungs-Modell - umschaltbar aus dem Leitstand (Dropdown
oben rechts), ohne .env-Aenderung oder Neustart des Watchers.

Der Watcher (pipe.watch, Langzeit-Prozess) und der Leitstand (pipe.server,
eigener Prozess) laufen getrennt - die Auswahl wird deshalb in einer kleinen
JSON-Datei geteilt, die categorize.py bei jedem Anruf frisch liest (kein
Caching), damit eine Umschaltung sofort beim naechsten Anruf greift.
"""
from __future__ import annotations

import json

from . import config

DATEI = config.TELEFON / "modell.json"

# Feste, kuratierte Auswahl statt freier Eingabe - ein Tippfehler in einer
# Modell-ID im Leitstand darf nicht den naechsten Anruf scheitern lassen.
# "openrouter"-Eintraege sind zum Testen gedacht (Transkript verlaesst dabei
# den Rechner) - nicht fuer den Praxisbetrieb.
AUSWAHL = [
    {"id": "ollama", "label": f"{config.OLLAMA_MODELL} (lokal)",
     "backend": "ollama", "modell": config.OLLAMA_MODELL},
    {"id": "or-ultra", "label": "Nemotron Ultra 550B (OpenRouter, Test)",
     "backend": "openrouter", "modell": "nvidia/nemotron-3-ultra-550b-a55b:free"},
    {"id": "or-lightning", "label": "Nemotron 3.5 Lightning (OpenRouter, Test)",
     "backend": "openrouter", "modell": "nvidia/nemotron-3.5-lightning:free"},
]
# Optionaler vierter Eintrag zum schnellen Ausprobieren eines beliebigen
# OpenRouter-Modells, ohne Code zu aendern - einfach OPENROUTER_MODELL in
# .env setzen.
if config.OPENROUTER_MODELL:
    AUSWAHL.append({"id": "or-custom", "label": f"{config.OPENROUTER_MODELL} (OpenRouter, Test)",
                     "backend": "openrouter", "modell": config.OPENROUTER_MODELL})
_STANDARD = AUSWAHL[0]["id"]
_NACH_ID = {e["id"]: e for e in AUSWAHL}


def aktuelle_id() -> str:
    """Liest die aktive Auswahl. Unbekanntes/Fehlendes gilt als Standard."""
    try:
        wert = json.loads(DATEI.read_text(encoding="utf-8")).get("id")
    except Exception:
        return _STANDARD
    return wert if wert in _NACH_ID else _STANDARD


def setze(kennung: str) -> bool:
    """Schreibt die Auswahl. False, wenn die ID nicht vorgesehen ist."""
    if kennung not in _NACH_ID:
        return False
    try:
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        DATEI.write_text(json.dumps({"id": kennung}), encoding="utf-8")
        return True
    except Exception:
        return False


def backend_und_modell() -> tuple[str, str]:
    """Backend + Modell-ID der aktiven Auswahl, fuer categorize.py."""
    eintrag = _NACH_ID[aktuelle_id()]
    return eintrag["backend"], eintrag["modell"]
