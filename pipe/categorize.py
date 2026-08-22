"""Kategorisierung: Transkript -> strukturierte Felder (Ollama/Qwen, lokal).

Nutzt den Chat-Endpunkt von Ollama mit erzwungenem JSON-Schema. Denk-Tokens des
Modells landen separat im "thinking"-Feld und werden ignoriert; zurück kommt
reines JSON.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from functools import lru_cache

from . import config

KATEGORIEN = [
    "termin", "rezept", "ueberweisung", "befund",
    "verwaltung", "beschwerden", "notfall", "rueckruf", "sonstiges",
]
DRINGLICHKEITEN = ["niedrig", "normal", "hoch", "notfall"]

# JSON-Schema, das Ollama dem Modell als Ausgabeformat aufzwingt.
_SCHEMA = {
    "type": "object",
    "properties": {
        "kategorie": {"type": "string", "enum": KATEGORIEN},
        "dringlichkeit": {"type": "string", "enum": DRINGLICHKEITEN},
        "anrufer_name": {"type": ["string", "null"]},
        "rueckrufnummer": {"type": ["string", "null"]},
        "rueckruf_gewuenscht": {"type": "boolean"},
        "sprache": {"type": "string"},
        "anliegen_kurz": {"type": "string"},
        "stichworte": {"type": "array", "items": {"type": "string"}},
    },
    # Alle Felder erzwingen: was hier fehlt, lässt das Modell einfach weg statt
    # null zu liefern - Name und Rückrufnummer gingen so still verloren, obwohl
    # sie im Transkript standen. Der Typ erlaubt weiterhin null, wenn nichts da ist.
    "required": [
        "kategorie", "dringlichkeit", "anrufer_name", "rueckrufnummer",
        "rueckruf_gewuenscht", "sprache", "anliegen_kurz", "stichworte",
    ],
}


@lru_cache(maxsize=1)
def _prompt() -> str:
    return config.PROMPT_DATEI.read_text(encoding="utf-8")


class KategorisierungsFehler(RuntimeError):
    pass


def kategorisiere(transkript: str) -> dict:
    """Ordnet ein Transkript ein. Wirft KategorisierungsFehler bei Problemen."""
    if not transkript.strip():
        raise KategorisierungsFehler("Leeres Transkript kann nicht kategorisiert werden.")

    anfrage = {
        "model": config.OLLAMA_MODELL,
        "messages": [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": f"Transkript der Sprachnachricht:\n\n{transkript}"},
        ],
        "stream": False,
        "think": False,  # Klassifikation braucht kein Reasoning -> deutlich schneller
        "format": _SCHEMA,
        "options": {"temperature": 0},
    }
    daten = json.dumps(anfrage).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/chat",
        data=daten,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as antwort:
            roh = json.loads(antwort.read().decode("utf-8"))
    except urllib.error.URLError as fehler:
        raise KategorisierungsFehler(
            f"Ollama nicht erreichbar unter {config.OLLAMA_URL}: {fehler}"
        ) from fehler

    inhalt = roh.get("message", {}).get("content", "").strip()
    if not inhalt:
        raise KategorisierungsFehler("Ollama lieferte keine Antwort.")
    try:
        ergebnis = json.loads(inhalt)
    except json.JSONDecodeError as fehler:
        raise KategorisierungsFehler(f"Antwort war kein gültiges JSON: {inhalt[:200]}") from fehler

    return _bereinige(ergebnis)


def _bereinige(d: dict) -> dict:
    """Absichern gegen Ausreißer trotz Schema (defensiv)."""
    if d.get("kategorie") not in KATEGORIEN:
        d["kategorie"] = "sonstiges"
    if d.get("dringlichkeit") not in DRINGLICHKEITEN:
        d["dringlichkeit"] = "normal"
    # Das Modell vergibt gelegentlich die Kategorie "notfall" und stuft die
    # Dringlichkeit trotzdem nur auf "hoch" ein. Das ist widersprüchlich und
    # führt zu einer harmloseren Farbe auf dem Board, als der Anruf verdient.
    # In der Praxis eskaliert man im Zweifel, statt abzuschwächen.
    if d["kategorie"] == "notfall":
        d["dringlichkeit"] = "notfall"
    d.setdefault("anrufer_name", None)
    d.setdefault("rueckrufnummer", None)
    d.setdefault("rueckruf_gewuenscht", False)
    d.setdefault("sprache", "de")
    d.setdefault("anliegen_kurz", "")
    d.setdefault("stichworte", [])
    if not isinstance(d["stichworte"], list):
        d["stichworte"] = []
    return d
