"""Kategorisierung: Transkript -> strukturierte Felder.

Zwei Backends (Auswahl siehe pipe.modellwahl):
  - "ollama"     (Default): lokal, erzwungenes JSON-Schema, Denk-Tokens landen
                 separat im "thinking"-Feld und werden ignoriert.
  - "openrouter": Cloud, zum Testen ob ein staerkeres Modell besser extrahiert.
                 Transkript verlaesst dabei den Rechner - nur zum Testen.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from functools import lru_cache

from . import config, modellwahl

KATEGORIEN = [
    "termin", "rezept", "ueberweisung", "befund",
    "verwaltung", "beschwerden", "notfall", "rueckruf", "sonstiges",
]
DRINGLICHKEITEN = ["niedrig", "normal", "hoch", "notfall"]

# Deterministisches Sicherheitsnetz, unabhaengig vom LLM: dieselben Symptome,
# die der Prompt selbst als Notfall-Beispiele nennt (Atemnot, Brustschmerz,
# Bewusstlosigkeit, starke Blutung). Grund: getestet mit qwen3:8b, 3/3
# identische Laeufe stuften "starke Brustschmerzen ... kriege kaum Luft" nur
# als "hoch" statt "notfall" ein - obwohl das Modell selbst "Brustschmerzen"
# und "Atemnot" als stichworte erkannte. Bei Notfall-Erkennung darf man sich
# nicht allein auf ein LLM verlassen. Eskaliert nur nach oben, nie nach unten
# - ein Fehlalarm kostet einen Blick, ein uebersehener Notfall kann einen
# Patienten kosten (siehe auch die "im Zweifel hoeher"-Regel im Prompt).
_NOTFALL_MUSTER = [re.compile(p, re.IGNORECASE) for p in (
    r"atemnot",
    r"(kriege|bekomme|krieg)\w*\s+(kaum|keine|schwer)\s+luft",
    r"kann\s+(kaum|nicht)\s+(mehr\s+)?atmen",
    r"brust\w*schmerz",
    r"schmerzen?\s+in\s+der\s+brust",
    r"bewusstlos",
    r"ohnm[aä]chtig",
    r"nicht\s+ansprechbar",
    r"blutet\s+(stark|heftig|sehr)",
    r"starke?\s+blutung",
)]


def _notfall_sicherheitsnetz(transkript: str, d: dict) -> dict:
    if d.get("dringlichkeit") != "notfall" and any(m.search(transkript) for m in _NOTFALL_MUSTER):
        d["kategorie"] = "notfall"
        d["dringlichkeit"] = "notfall"
    return d

# JSON-Schema, das Ollama dem Modell als Ausgabeformat aufzwingt.
_SCHEMA = {
    "type": "object",
    "properties": {
        "kategorie": {"type": "string", "enum": KATEGORIEN},
        "dringlichkeit": {"type": "string", "enum": DRINGLICHKEITEN},
        "anrufer_name": {"type": ["string", "null"]},
        "rueckrufnummer": {"type": ["string", "null"]},
        "rueckruf_gewuenscht": {"type": "boolean"},
        "email": {"type": ["string", "null"]},
        "sprache": {"type": "string"},
        "anliegen_kurz": {"type": "string"},
        "stichworte": {"type": "array", "items": {"type": "string"}},
    },
    # Alle Felder erzwingen: was hier fehlt, lässt das Modell einfach weg statt
    # null zu liefern - Name und Rückrufnummer gingen so still verloren, obwohl
    # sie im Transkript standen. Der Typ erlaubt weiterhin null, wenn nichts da ist.
    "required": [
        "kategorie", "dringlichkeit", "anrufer_name", "rueckrufnummer",
        "rueckruf_gewuenscht", "email", "sprache", "anliegen_kurz", "stichworte",
    ],
}

# Deterministische Reparatur, wenn Whisper gesprochenes "at" beim Diktieren
# einer E-Mail-Adresse als Punkt transkribiert statt als "@" (beobachtet:
# "gdg at dillenberg punkt net" -> Transkript "gdg.dillenberg.net", komplett
# ohne @ - kein Kategorisierungs-, sondern ein Spracherkennungs-Problem).
# Nur ein Repair-Vorschlag, kein stiller Ersatz: das erste Segment (ohne
# eigenen Punkt) vor einer domain.tld-Form wird zu einem @ - mehrdeutig
# (koennte auch eine genannte Subdomain sein), deshalb im UI als "vermutete
# Korrektur" markiert statt das Transkript selbst zu veraendern.
_EMAIL_OHNE_AT = re.compile(
    r"^([a-z0-9_-]+)\.([a-z0-9-]+\.[a-z]{2,})$", re.IGNORECASE)


def _repariere_email(wert: str | None) -> tuple[str | None, bool]:
    """Gibt (email, vermutete_korrektur) zurueck."""
    if not wert:
        return wert, False
    wert = wert.strip()
    if "@" in wert:
        return wert, False
    treffer = _EMAIL_OHNE_AT.match(wert)
    if not treffer:
        return wert, False
    return f"{treffer.group(1)}@{treffer.group(2)}", True


@lru_cache(maxsize=1)
def _prompt() -> str:
    return config.PROMPT_DATEI.read_text(encoding="utf-8")


class KategorisierungsFehler(RuntimeError):
    pass


def kategorisiere(transkript: str) -> tuple[dict, dict]:
    """Ordnet ein Transkript ein. Wirft KategorisierungsFehler bei Problemen.

    Gibt (auswertung, meta) zurueck - meta fuers Live-Log im Leitstand:
    backend, modell, dauer_s, tokens, kosten_usd (letzteres nur OpenRouter,
    bei :free-Modellen 0.0).

    Backend + Modell kommen aus pipe.modellwahl (per Leitstand-Dropdown
    umschaltbar, kein Neustart noetig) - Fallback ist config.py, wenn dort
    keine Auswahl getroffen wurde.
    """
    if not transkript.strip():
        raise KategorisierungsFehler("Leeres Transkript kann nicht kategorisiert werden.")

    backend, modell = modellwahl.backend_und_modell()
    start = time.monotonic()
    if backend == "openrouter":
        try:
            ergebnis, meta = _mit_openrouter(transkript, modell)
        except KategorisierungsFehler as fehler:
            # OpenRouter (v.a. :free-Tier) ist nachweislich instabil - 502
            # "temporarily overloaded", Latenz-Ausreisser bis 80s. Ein Anruf
            # darf daran nicht scheitern und nach telefon/fehler verschwinden
            # - Failover aufs lokale Ollama-Modell statt den Anruf zu verlieren.
            print(f"  ⚠ {backend} fehlgeschlagen ({fehler}) - Failover auf {config.OLLAMA_MODELL}")
            ergebnis, meta = _mit_ollama(transkript, config.OLLAMA_MODELL)
            meta["failover_von"] = modell
            backend, modell = "ollama", config.OLLAMA_MODELL
    else:
        ergebnis, meta = _mit_ollama(transkript, modell)
    meta["backend"] = backend
    meta["modell"] = modell
    meta.setdefault("failover_von", None)
    meta["dauer_s"] = round(time.monotonic() - start, 2)
    ergebnis = _notfall_sicherheitsnetz(transkript, _bereinige(ergebnis))
    return ergebnis, meta


def _mit_ollama(transkript: str, modell: str) -> tuple[dict, dict]:
    anfrage = {
        "model": modell,
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
    # eval_count/prompt_eval_count sind Ollamas Token-Zaehler - kein Standard-
    # OpenAI-Feldname, aber inhaltlich dasselbe (Antwort-/Prompt-Tokens).
    tokens = roh.get("prompt_eval_count", 0) + roh.get("eval_count", 0) or None
    try:
        return json.loads(inhalt), {"tokens": tokens, "kosten_usd": None}
    except json.JSONDecodeError as fehler:
        raise KategorisierungsFehler(f"Antwort war kein gültiges JSON: {inhalt[:200]}") from fehler


def _mit_openrouter(transkript: str, modell: str) -> tuple[dict, dict]:
    if not config.OPENROUTER_KEY:
        raise KategorisierungsFehler("OPENROUTER_KEY fehlt in .env.")

    # OpenAI-kompatible Chat-API. json_object statt json_schema, weil nicht
    # jedes ueber OpenRouter geroutete Modell striktes Schema unterstuetzt -
    # der Systemprompt beschreibt die Felder bereits explizit in Prosa.
    # usage.include=true ist eine OpenRouter-Erweiterung: liefert zusaetzlich
    # die tatsaechlichen Kosten (USD) in der Antwort mit, nicht nur Tokens.
    anfrage = {
        "model": modell,
        "messages": [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": f"Transkript der Sprachnachricht:\n\n{transkript}"},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    daten = json.dumps(anfrage).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=daten,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.OPENROUTER_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as antwort:
            roh = json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as fehler:
        detail = fehler.read().decode("utf-8", errors="replace")[:200]
        raise KategorisierungsFehler(f"OpenRouter-Fehler {fehler.code}: {detail}") from fehler
    except urllib.error.URLError as fehler:
        raise KategorisierungsFehler(f"OpenRouter nicht erreichbar: {fehler}") from fehler

    try:
        inhalt = roh["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as fehler:
        raise KategorisierungsFehler(f"Unerwartete OpenRouter-Antwort: {roh}") from fehler
    if not inhalt:
        raise KategorisierungsFehler("OpenRouter lieferte keine Antwort.")
    nutzung = roh.get("usage") or {}
    meta = {"tokens": nutzung.get("total_tokens"), "kosten_usd": nutzung.get("cost")}
    try:
        return json.loads(inhalt), meta
    except json.JSONDecodeError as fehler:
        raise KategorisierungsFehler(f"Antwort war kein gültiges JSON: {inhalt[:200]}") from fehler


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
    d.setdefault("email", None)
    d.setdefault("sprache", "de")
    d.setdefault("anliegen_kurz", "")
    d.setdefault("stichworte", [])
    if not isinstance(d["stichworte"], list):
        d["stichworte"] = []
    d["email"], d["email_vermutete_korrektur"] = _repariere_email(d.get("email"))
    return d
