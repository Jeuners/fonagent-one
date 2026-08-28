"""Aktives Kategorisierungs-Modell - umschaltbar aus dem Leitstand (Dropdown
oben rechts), ohne .env-Aenderung oder Neustart des Watchers.

Der Watcher (pipe.watch, Langzeit-Prozess) und der Leitstand (pipe.server,
eigener Prozess) laufen getrennt - die Auswahl wird deshalb in einer kleinen
JSON-Datei geteilt, die categorize.py bei jedem Anruf frisch liest (kein
Caching), damit eine Umschaltung sofort beim naechsten Anruf greift.

Die lokalen Ollama-Modelle werden live von Ollama abgefragt (GET /api/tags)
statt fest im Code zu stehen - jedes per "ollama pull" installierte Modell
taucht automatisch im Dropdown auf, ohne Code-Aenderung. Eine feste
OpenRouter-Testauswahl bleibt daneben bestehen: die Modell-IDs dort lassen
sich nicht lokal verifizieren, ein Tippfehler wuerde sonst erst beim naechsten
Anruf sichtbar - fuer freie Eingabe siehe OPENROUTER_MODELL in der .env.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config

DATEI = config.TELEFON / "modell.json"

# Ollama-Abfrage ist ein Netzwerkaufruf - kurz zwischenspeichern, damit ein
# offener Leitstand (Polling alle paar Sekunden) Ollama nicht dauerbelastet.
_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_S = 30.0


def _ollama_modelle() -> list[str]:
    """Namen der lokal installierten, textfähigen Ollama-Modelle (leer bei Fehler).

    Reine Embedding-Modelle (z. B. nomic-embed-text) werden ausgefiltert - sie
    können nicht kategorisieren, ein Aufruf würde nur mit einem Fehler enden.
    Ältere Ollama-Versionen liefern kein "capabilities"-Feld - dann bleibt das
    Modell sicherheitshalber drin, statt es zu Unrecht zu verstecken.
    """
    req = urllib.request.Request(f"{config.OLLAMA_URL}/api/tags",
                                  headers={"User-Agent": "praxis-telefon-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as antwort:
            daten = json.loads(antwort.read().decode("utf-8"))
    except Exception:
        return []
    namen = []
    for m in daten.get("models", []):
        name = m.get("name")
        faehigkeiten = m.get("capabilities")
        if name and (faehigkeiten is None or "completion" in faehigkeiten):
            namen.append(name)
    return sorted(namen)


def auswahl() -> list[dict]:
    """Aktuelle Dropdown-Optionen: lokale Ollama-Modelle live + feste OpenRouter-Testeintraege."""
    jetzt = time.time()
    gepuffert = _CACHE.get("auswahl")
    if gepuffert and jetzt - gepuffert[0] < CACHE_S:
        return gepuffert[1]

    # Ist Ollama gerade nicht erreichbar, bleibt wenigstens die konfigurierte
    # Standardauswahl im Dropdown - ein leeres Dropdown waere schlechter.
    lokale = _ollama_modelle() or [config.OLLAMA_MODELL]
    liste = [{"id": f"ollama:{name}", "label": f"{name} (lokal)",
              "backend": "ollama", "modell": name} for name in lokale]
    liste.append({"id": "or-ultra", "label": "Nemotron Ultra 550B (OpenRouter, Test)",
                  "backend": "openrouter", "modell": "nvidia/nemotron-3-ultra-550b-a55b:free"})
    liste.append({"id": "or-lightning", "label": "Nemotron 3.5 Lightning (OpenRouter, Test)",
                  "backend": "openrouter", "modell": "nvidia/nemotron-3.5-lightning:free"})
    # Optionaler Eintrag zum schnellen Ausprobieren eines beliebigen
    # OpenRouter-Modells, ohne Code zu aendern - einfach OPENROUTER_MODELL in
    # .env setzen.
    if config.OPENROUTER_MODELL:
        liste.append({"id": "or-custom", "label": f"{config.OPENROUTER_MODELL} (OpenRouter, Test)",
                      "backend": "openrouter", "modell": config.OPENROUTER_MODELL})

    _CACHE["auswahl"] = (jetzt, liste)
    return liste


def _standard_id(optionen: list[dict]) -> str:
    """Bevorzugt das in der .env konfigurierte Modell, sonst die erste Option."""
    for eintrag in optionen:
        if eintrag["backend"] == "ollama" and eintrag["modell"] == config.OLLAMA_MODELL:
            return eintrag["id"]
    return optionen[0]["id"]


def aktuelle_id() -> str:
    """Liest die aktive Auswahl. Unbekanntes/Fehlendes gilt als Standard."""
    optionen = auswahl()
    standard = _standard_id(optionen)
    try:
        wert = json.loads(DATEI.read_text(encoding="utf-8")).get("id")
    except Exception:
        return standard
    return wert if wert in {e["id"] for e in optionen} else standard


def setze(kennung: str) -> bool:
    """Schreibt die Auswahl. False, wenn die ID nicht (mehr) vorgesehen ist."""
    if kennung not in {e["id"] for e in auswahl()}:
        return False
    try:
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        DATEI.write_text(json.dumps({"id": kennung}), encoding="utf-8")
        return True
    except Exception:
        return False


def backend_und_modell() -> tuple[str, str]:
    """Backend + Modell-ID der aktiven Auswahl, fuer categorize.py."""
    kennung = aktuelle_id()
    eintrag = next(e for e in auswahl() if e["id"] == kennung)
    return eintrag["backend"], eintrag["modell"]
