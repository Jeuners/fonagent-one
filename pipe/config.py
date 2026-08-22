"""Zentrale Konfiguration der Anruf-Pipeline.

Alle Werte kommen aus Umgebungsvariablen (optional aus einer .env-Datei im
Projektwurzelverzeichnis). Keine Geheimnisse im Code — die Nextcloud-Zugangs-
daten werden erst gesetzt, wenn sie vorliegen; bis dahin bleibt die Ablage rein
lokal.
"""
from __future__ import annotations

import os
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent


def _lade_dotenv(pfad: Path) -> None:
    """Minimaler .env-Loader ohne externe Abhängigkeit."""
    if not pfad.is_file():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        schluessel = schluessel.strip()
        wert = wert.strip().strip('"').strip("'")
        os.environ.setdefault(schluessel, wert)


_lade_dotenv(WURZEL / ".env")


def _bool(name: str, standard: bool) -> bool:
    wert = os.environ.get(name)
    if wert is None:
        return standard
    return wert.strip().lower() in {"1", "true", "yes", "ja", "on"}


# --- Spracherkennung (lokal) -------------------------------------------------
# Backend: "whispercpp" (Apple-GPU, nutzt vorhandenes ggml-Modell, kein Download)
#          "faster"     (faster-whisper / CTranslate2, lädt Modell bei Bedarf)
STT_BACKEND = os.environ.get("STT_BACKEND", "whispercpp").strip().lower()

# Feste Sprache erzwingen (z. B. "de") oder "auto" für Auto-Erkennung.
WHISPER_SPRACHE = os.environ.get("WHISPER_SPRACHE", "de").strip() or "auto"

# whisper.cpp
WHISPERCPP_BIN = os.environ.get("WHISPERCPP_BIN", "whisper-cli")
WHISPERCPP_MODELL = os.environ.get(
    "WHISPERCPP_MODELL", str(Path.home() / "whisper-models" / "ggml-large-v3-turbo.bin")
)
WHISPER_THREADS = os.environ.get("WHISPER_THREADS", "8")

# Kontext für die Spracherkennung. Whisper erkennt Namen, Rufnummern und
# Praxis-Vokabular deutlich zuverlässiger, wenn es weiß, worum es geht - bei
# der schlechten Tonqualität einer Telefonleitung macht das den Unterschied.
WHISPER_PROMPT = os.environ.get(
    "WHISPER_PROMPT",
    "Anruf auf dem Anrufbeantworter einer Hausarztpraxis. Der Anrufer nennt "
    "seinen Namen, sein Anliegen und eine Rückrufnummer. Häufig geht es um "
    "Termin, Rezept, Überweisung, Befund, Krankschreibung oder Schmerzen.",
).strip()

# faster-whisper (nur bei STT_BACKEND=faster)
WHISPER_MODELL = os.environ.get("WHISPER_MODELL", "medium")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

# --- Kategorisierung (Ollama, lokal) -----------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODELL = os.environ.get("OLLAMA_MODELL", "qwen3.5:latest")
PROMPT_DATEI = WURZEL / "prompts" / os.environ.get(
    "PROMPT_DATEI", "categorize_de.txt"
)

# --- Ablage ------------------------------------------------------------------
ABLAGE_LOKAL = Path(os.environ.get("ABLAGE_LOKAL", str(WURZEL / "ablage")))

# --- Leitstand (Weboberfläche) ----------------------------------------------
LEITSTAND_HOST = os.environ.get("LEITSTAND_HOST", "127.0.0.1")
LEITSTAND_PORT = int(os.environ.get("LEITSTAND_PORT", "8088"))
# Zugangsschutz. Pflicht, sobald der Leitstand nicht nur lokal erreichbar ist -
# auf den Seiten stehen Transkripte und Aufnahmen von Patienten.
LEITSTAND_USER = os.environ.get("LEITSTAND_USER", "praxis")
LEITSTAND_PASS = os.environ.get("LEITSTAND_PASS", "")


def leitstand_oeffentlich() -> bool:
    return LEITSTAND_HOST not in {"127.0.0.1", "localhost", "::1"}


# --- Telefonie ---------------------------------------------------------------
# Die Anlage legt Aufnahmen im Eingang ab; der Watcher (pipe.watch) räumt sie
# nach der Verarbeitung weg.
TELEFON = Path(os.environ.get("TELEFON_ORDNER", str(WURZEL / "telefon")))
TELEFON_EINGANG = TELEFON / "eingang"
TELEFON_VERARBEITET = TELEFON / "verarbeitet"
TELEFON_FEHLER = TELEFON / "fehler"

# SIP-Zugang der Telefonanlage (Werte in .env, nie im Code).
SIP_USER = os.environ.get("SIP_USER", "")
SIP_DOMAIN = os.environ.get("SIP_DOMAIN", "")
SIP_PASS = os.environ.get("SIP_PASS", "")

# Nextcloud (steckbar): nur aktiv, wenn URL + Nutzer + Passwort gesetzt sind.
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "").rstrip("/")
NEXTCLOUD_USER = os.environ.get("NEXTCLOUD_USER", "")  # Login (kann E-Mail sein)
NEXTCLOUD_PASS = os.environ.get("NEXTCLOUD_PASS", "")  # App-Passwort
# WebDAV-Pfad braucht die interne User-ID (weicht bei E-Mail-Login ab).
NEXTCLOUD_USERID = os.environ.get("NEXTCLOUD_USERID", "") or NEXTCLOUD_USER
NEXTCLOUD_ORDNER = os.environ.get("NEXTCLOUD_ORDNER", "Anrufe").strip("/")

# Deck-Triage-Board (optional): pro Anruf eine Karte, Stapel = Kategorie.
NEXTCLOUD_DECK = os.environ.get("NEXTCLOUD_DECK", "").strip().lower() in {
    "1", "true", "yes", "ja", "on"
}
DECK_BOARD = os.environ.get("DECK_BOARD", "Anrufe")
# Stapel des Boards = Arbeitsablauf, nicht Kategorie. Neue Anrufe landen immer
# im ersten Stapel; die Kategorie steht auf der Karte.
DECK_STAPEL = [s.strip() for s in os.environ.get(
    "DECK_STAPEL", "Eingang,In Bearbeitung,Rückfragen,Erledigt").split(",") if s.strip()]
# Nutzer, die das Board sehen sollen (kommagetrennt). Ohne Freigabe sieht nur
# das Konto der Pipe die Karten - die Praxis schaut in ein leeres Deck.
DECK_TEILEN = [n.strip() for n in os.environ.get("DECK_TEILEN", "").split(",") if n.strip()]


def nextcloud_aktiv() -> bool:
    return bool(NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS)
