"""Nachsync: lädt lokal abgelegte Anrufe nach Nextcloud, die noch nicht oben sind.

Nützlich nach einem Nextcloud-Ausfall (z. B. Wartungsmodus): die Pipe speichert
in dem Fall nur lokal; dieser Befehl holt das Hochladen nach.

    python3 -m pipe.resync

Erkennt bereits hochgeladene Ordner an der Marker-Datei ``.nextcloud_ok``.
"""
from __future__ import annotations

import sys

from . import config, store


def _anruf_ordner():
    """Alle Anruf-Ordner (JJJJ-MM-TT/HH-MM-SS_*) unter der lokalen Ablage."""
    if not config.ABLAGE_LOKAL.is_dir():
        return
    for tag in sorted(config.ABLAGE_LOKAL.iterdir()):
        if tag.is_dir():
            for ordner in sorted(tag.iterdir()):
                if ordner.is_dir() and (ordner / "meta.json").is_file():
                    yield ordner


def nachsync() -> tuple[int, int, int]:
    """Gibt (hochgeladen, bereits_oben, fehlgeschlagen) zurück."""
    hoch = schon = fehler = 0
    for ordner in _anruf_ordner():
        if (ordner / store.MARKER).exists():
            schon += 1
            continue
        if store.hochladen(ordner):
            print(f"  ✔ {ordner.parent.name}/{ordner.name}")
            hoch += 1
        else:
            fehler += 1
    return hoch, schon, fehler


def main() -> int:
    if not config.nextcloud_aktiv():
        print("Nextcloud ist nicht konfiguriert — nichts zu tun.", file=sys.stderr)
        return 1
    hoch, schon, fehler = nachsync()
    print(f"\nNachsync fertig: {hoch} hochgeladen, {schon} bereits oben, {fehler} fehlgeschlagen.")
    return 0 if fehler == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
