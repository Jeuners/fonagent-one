"""Unveraenderliches Ereignisprotokoll - Nachweis fuer den Umgang mit
Anrufdaten (u.a. Loeschungen nach Ablauf der Aufbewahrungsfrist).

Anders als protokoll.log (das fuers Leitstand-UI auf die letzten 2000 Zeilen
gekuerzt wird) wird diese Datei nie gekuerzt oder ueberschrieben - nur
angehaengt. Zusaetzlich technisch gegen nachtraegliches Aendern/Loeschen
abgesichert: macOS' uappnd-Flag (append-only) laesst nur noch Anhaengen zu,
selbst der eigene Prozess kann die Datei danach nicht mehr kuerzen oder
umschreiben, ohne das Flag vorher explizit aufzuheben (`chflags nouappnd`).
Auf anderen Betriebssystemen (kein chflags) ist das Log weiterhin
append-only per Konvention, nur ohne das zusaetzliche OS-Siegel.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

from . import config

DATEI = config.TELEFON / "audit.log"
_gesichert = False


def _absichern() -> None:
    """Setzt das append-only-Flag einmal pro Prozesslaufzeit. Wirft nie."""
    global _gesichert
    if _gesichert:
        return
    _gesichert = True
    try:
        subprocess.run(["chflags", "uappnd", str(DATEI)],
                        check=False, capture_output=True)
    except Exception:
        pass


def schreibe(art: str, text: str, **felder) -> None:
    """Haengt ein Ereignis unveraenderlich an. Wirft nie."""
    try:
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        if not DATEI.exists():
            DATEI.touch()
        eintrag = {"zeit": datetime.now().isoformat(timespec="seconds"),
                   "art": art, "text": text, **felder}
        with DATEI.open("a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        _absichern()
    except Exception:
        pass
