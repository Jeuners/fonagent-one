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
    "Termin, Rezept, Überweisung, Befund, Krankschreibung oder Schmerzen. "
    "Nennt der Anrufer eine E-Mail-Adresse, wird sie mit @-Zeichen "
    "geschrieben, zum Beispiel max.mueller@example.de.",
).strip()

# faster-whisper (nur bei STT_BACKEND=faster)
WHISPER_MODELL = os.environ.get("WHISPER_MODELL", "medium")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

# --- Kategorisierung -----------------------------------------------------
# Welches Modell aktiv ist, waehlt der Leitstand per Dropdown (pipe.modellwahl,
# geteilte Zustandsdatei telefon/modell.json) - kein Neustart noetig. Hier nur
# die Bausteine dafuer: Ollama-Zugang (lokal, Default-Auswahl) und der
# OpenRouter-Zugang fuers Testen staerkerer Cloud-Modelle. Patientendaten
# (Transkript) verlassen bei OpenRouter den Rechner - nur zu Testzwecken,
# nicht im Praxisbetrieb.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODELL = os.environ.get("OLLAMA_MODELL", "qwen3:8b")

OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
# Optional: eigenes Testmodell zusaetzlich zu den zwei fest im Dropdown
# hinterlegten (siehe pipe/modellwahl.py) - leer lassen wenn nicht gebraucht.
OPENROUTER_MODELL = os.environ.get("OPENROUTER_MODELL", "")

# --- Branchen-Profil -----------------------------------------------------
# Buendelt alles, was fachlich vom Einsatzzweck abhaengt (Kategorien, Prompt,
# Notfall-Sicherheitsnetz, Ansage) an einer Stelle - siehe profile/<name>/.
# So bleiben diese vier Teile beim Wechsel des Einsatzzwecks (z.B. Praxis vs.
# 24h-Aufzug-Notdienst) automatisch zueinander konsistent, statt dass man eine
# Datei beim Umstellen vergisst. Neues Profil anlegen: profile/<name>/ Ordner
# mit prompt.txt, kategorien.json, sicherheitsnetz.txt, ansage.txt anlegen
# (profile/praxis/ als Vorlage nehmen) und hier PROFIL=<name> setzen.
PROFIL = os.environ.get("PROFIL", "praxis").strip() or "praxis"
PROFIL_ORDNER = WURZEL / "profile" / PROFIL

PROMPT_DATEI = PROFIL_ORDNER / "prompt.txt"
KATEGORIEN_DATEI = PROFIL_ORDNER / "kategorien.json"
SICHERHEITSNETZ_DATEI = PROFIL_ORDNER / "sicherheitsnetz.txt"
ANSAGE_DATEI = PROFIL_ORDNER / "ansage.txt"

# --- Ablage ------------------------------------------------------------------
ABLAGE_LOKAL = Path(os.environ.get("ABLAGE_LOKAL", str(WURZEL / "ablage")))

# --- Leitstand (Weboberfläche) ----------------------------------------------
# Log der Telefonanlage - der Leitstand liest daraus die Anruf-Ereignisse.
FREESWITCH_LOG = Path(os.environ.get(
    "FREESWITCH_LOG", "/opt/homebrew/var/log/freeswitch/freeswitch.log"))

# Event-Socket-Port - muss mit autoload_configs/event_socket.conf.xml
# uebereinstimmen. Der Homebrew-Default variiert je nach Installation.
FS_PORT = os.environ.get("FS_PORT", "8021")

LEITSTAND_HOST = os.environ.get("LEITSTAND_HOST", "127.0.0.1")
LEITSTAND_PORT = int(os.environ.get("LEITSTAND_PORT", "8088"))
# Zugangsschutz. Pflicht, sobald der Leitstand nicht nur lokal erreichbar ist -
# auf den Seiten stehen Transkripte und Aufnahmen von Patienten.
LEITSTAND_USER = os.environ.get("LEITSTAND_USER", "praxis")
LEITSTAND_PASS = os.environ.get("LEITSTAND_PASS", "")

# Geteiltes Geheimnis mit dilles-agent.php ("agent-one test"-Kommando auf
# dillenberg.net) fuer signierte, 1h gueltige Testzugangslinks - siehe
# pipe.testzugang. Leer lassen deaktiviert das Feature (kein Fallback-Wert,
# ein leeres Secret darf niemals gueltige Signaturen erzeugen).
TESTZUGANG_SECRET = os.environ.get("TESTZUGANG_SECRET", "")


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
# Adressbuch (CardDAV) fuer den Kontakt-Abgleich bei eingehenden Anrufen.
# Nextclouds Standard-Adressbuch heisst "contacts"; eigene Adressbuecher
# tragen ihre URI (z. B. "hgod").
NEXTCLOUD_ADRESSBUCH = os.environ.get("NEXTCLOUD_ADRESSBUCH", "contacts")
# Getrennter Schalter fuer den Datei-Upload: Zugangsdaten allein reichen fuer
# den Kontakt-Abgleich (kontakte.py) - wer nur das will, ohne dass Aufnahmen
# und Transkripte zusaetzlich nach Nextcloud hochgeladen werden, setzt hier 0.
# Default an, damit sich am bisherigen Verhalten (Zugangsdaten -> Upload) fuer
# bestehende Installationen nichts aendert.
NEXTCLOUD_UPLOAD = os.environ.get("NEXTCLOUD_UPLOAD", "1").strip().lower() in {
    "1", "true", "yes", "ja", "on"
}

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


# --- Störungswache (pipe.monitor) --------------------------------------------
# Prueft die Dienste-Ampel (pipe.dienste: Telefon, Verarbeitung, Spracherkennung,
# Nextcloud) periodisch und alarmiert lokal per macOS-Benachrichtigung + Ton,
# wenn niemand auf den Leitstand schaut. Eigener Prozess (siehe
# telefon/starten.sh) - faellt pipe.watch oder pipe.server aus, meldet die
# Wache das trotzdem noch.
MONITOR_TAKT_S = int(os.environ.get("MONITOR_TAKT_S", "60") or "60")
# Erinnerungsabstand, waehrend eine Stoerung anhaelt - sonst nur ein einziger
# Alarm beim Ausfall, der leicht untergeht.
MONITOR_WIEDERHOLUNG_MIN = int(os.environ.get("MONITOR_WIEDERHOLUNG_MIN", "15") or "15")
# macOS-Systemton (siehe /System/Library/Sounds), der bei jedem Alarm abgespielt wird.
MONITOR_TON = os.environ.get("MONITOR_TON", "Sosumi").strip() or "Sosumi"

# --- Aufbewahrungsfrist (pipe.loeschen) --------------------------------------
# Nach so vielen Tagen wird ein Anruf-Ordner (Aufnahme + Transkript + Meta)
# geloescht - lokal und, falls hochgeladen, auch bei Nextcloud. 0 = aus (nichts
# wird automatisch geloescht). Greift nur fuer Anrufe im letzten Stapel
# (DECK_STAPEL[-1], per Default "Erledigt") - unbearbeitete Anrufe bleiben
# unangetastet, egal wie alt.
LOESCHFRIST_TAGE = int(os.environ.get("LOESCHFRIST_TAGE", "0") or "0")
