"""Zustand der beteiligten Dienste (Telefon, Verarbeitung, Spracherkennung,
Nextcloud) — geteilt zwischen Leitstand (pipe.server) und Störungswache
(pipe.monitor), damit beide dieselbe Sicht auf "läuft/steht" haben.
"""
from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import config, store

# Statusabfragen sind teuer (Netz, Unterprozesse) - kurz zwischenspeichern,
# damit haeufiges Nachfragen den Rechner nicht belastet.
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_S = 4.0


def _laeuft(muster: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-f", muster], capture_output=True).returncode == 0
    except Exception:
        return False


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    """Erreichbarkeitstest. Mit eigenem User-Agent - ein WAF vor Nextcloud
    blockt den Standardnamen von urllib und meldete fälschlich 'nicht
    erreichbar'."""
    req = urllib.request.Request(url, headers={"User-Agent": "praxis-telefon-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=store._ssl_kontext()):
            return True
    except urllib.error.HTTPError:
        return True          # antwortet - reicht als Lebenszeichen
    except Exception:
        return False


def _trunk() -> tuple[str, str]:
    """(zustand, text) des SIP-Trunks."""
    if not _laeuft("[f]reeswitch"):
        return "aus", "FreeSWITCH läuft nicht"
    try:
        roh = subprocess.run(
            ["fs_cli", "-P", config.FS_PORT, "-x", "sofia status gateway plusnet"],
            capture_output=True, text=True, timeout=8).stdout
        for zeile in roh.splitlines():
            if zeile.startswith("Status"):
                wert = zeile.split()[-1]
                return ("gut", "registriert") if wert == "UP" else ("schlecht", f"Trunk {wert}")
        return "unklar", "Gateway unbekannt"
    except Exception:
        return "unklar", "fs_cli nicht erreichbar"


def status() -> dict:
    jetzt = time.time()
    gepuffert = _CACHE.get("status")
    if gepuffert and jetzt - gepuffert[0] < CACHE_S:
        return gepuffert[1]

    trunk_zustand, trunk_text = _trunk()
    watcher = _laeuft("[p]ipe.watch")
    ollama = _http_ok(f"{config.OLLAMA_URL}/api/version")
    nextcloud = (_http_ok(f"{config.NEXTCLOUD_URL}/status.php")
                 if config.nextcloud_aktiv() else None)

    offen = 0
    if config.TELEFON_EINGANG.is_dir():
        offen = sum(1 for d in config.TELEFON_EINGANG.iterdir()
                    if d.is_file() and not d.name.startswith("."))
    fehler = 0
    if config.TELEFON_FEHLER.is_dir():
        fehler = sum(1 for d in config.TELEFON_FEHLER.iterdir() if d.is_file())

    daten = {
        "dienste": [
            {"name": "Telefon", "zustand": trunk_zustand, "text": trunk_text},
            {"name": "Verarbeitung", "zustand": "gut" if watcher else "aus",
             "text": "beobachtet Eingang" if watcher else "Watcher läuft nicht"},
            {"name": "Spracherkennung", "zustand": "gut" if ollama else "schlecht",
             "text": "Ollama bereit" if ollama else "Ollama nicht erreichbar"},
            {"name": "Nextcloud", "zustand": "unklar" if nextcloud is None
             else ("gut" if nextcloud else "schlecht"),
             "text": "nicht konfiguriert" if nextcloud is None
             else ("erreichbar" if nextcloud else "nicht erreichbar")},
        ],
        "eingang_offen": offen,
        "fehler": fehler,
        "stand": datetime.now().strftime("%H:%M:%S"),
    }
    _CACHE["status"] = (jetzt, daten)
    return daten
