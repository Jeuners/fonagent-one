"""Bearbeitungsstand eines Anrufs — wo er auf dem Board liegt.

Der Stand gehört nicht in ``meta.json``: dort steht, was der Anruf war, und das
ändert sich nie. Wo er in der Bearbeitung steht, ändert sich dauernd. Deshalb
eine eigene kleine Datei im Anrufordner — so bleibt beides unabhängig, und ein
verlorener Stand kostet nie die Anrufdaten.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import config

DATEI = "stapel.json"


def erlaubt() -> list[str]:
    return config.DECK_STAPEL


def lies(ordner) -> str:
    """Stapel eines Anrufs. Unbekanntes oder Fehlendes gilt als Eingang."""
    standard = erlaubt()[0]
    pfad = ordner / DATEI
    if not pfad.is_file():
        return standard
    try:
        wert = json.loads(pfad.read_text(encoding="utf-8")).get("stapel")
    except Exception:
        return standard
    return wert if wert in erlaubt() else standard


def setze(ordner, stapel: str) -> bool:
    """Schreibt den Stapel. False, wenn der Name nicht vorgesehen ist."""
    if stapel not in erlaubt():
        return False
    try:
        (ordner / DATEI).write_text(json.dumps(
            {"stapel": stapel, "geaendert": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


ARCHIV_DATEI = "archiviert.json"


def ist_archiviert(ordner) -> bool:
    return (ordner / ARCHIV_DATEI).is_file()


def archiviere(ordner) -> bool:
    """Markiert einen Anruf als archiviert - simuliert, es wird nichts verschoben
    oder geloescht. Macht ihn nur vom Board verschwinden (siehe server.anrufe())."""
    try:
        (ordner / ARCHIV_DATEI).write_text(json.dumps(
            {"archiviert": datetime.now().isoformat(timespec="seconds"), "simuliert": True},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def ordner_zu(kennung: str):
    """Wandelt eine Anruf-Kennung (JJJJ-MM-TT/HH-MM-SS_nummer) in einen Pfad.

    Prüft, dass der Pfad wirklich in der Ablage liegt — die Kennung kommt aus
    dem Browser und ist damit nichts, worauf man sich verlässt.
    """
    ziel = (config.ABLAGE_LOKAL / kennung).resolve()
    wurzel = config.ABLAGE_LOKAL.resolve()
    if not str(ziel).startswith(str(wurzel) + "/") or not ziel.is_dir():
        return None
    return ziel
