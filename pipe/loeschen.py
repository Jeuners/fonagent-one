"""Loescht Anrufe nach Ablauf der Aufbewahrungsfrist (LOESCHFRIST_TAGE).

Nur Anrufe im letzten Stapel (per Default "Erledigt") werden angefasst -
unbearbeitete Anrufe bleiben unangetastet, egal wie alt: der Eingang ist
tabu. Loescht lokal UND (falls hochgeladen) die Nextcloud-Kopie - sonst
bringt lokales Loeschen fuer die Datenminimierung nichts. Jede Loeschung
landet unveraenderlich in audit.log (ueber protokoll.schreibe).

    python3 -m pipe.loeschen              # loescht faellige Anrufe
    python3 -m pipe.loeschen --pruefen    # zeigt nur, was faellig waere

Laeuft automatisch alle 24h aus pipe.watch heraus (kein Cron/launchd noetig,
solange der Watcher laeuft - siehe telefon/starten.sh). Manueller Aufruf hier
ist nur fuer Tests/Ad-hoc-Kontrolle gedacht.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta

from . import config, protokoll, stapel, store


def _faellig(zeit: datetime) -> bool:
    if not config.LOESCHFRIST_TAGE:
        return False
    return datetime.now() - zeit > timedelta(days=config.LOESCHFRIST_TAGE)


def laeuft(pruefen: bool = False) -> int:
    """Loescht (oder zeigt bei pruefen=True nur) faellige Anrufe. Gibt die Anzahl zurück."""
    if not config.LOESCHFRIST_TAGE:
        print("LOESCHFRIST_TAGE ist 0 - Loeschung ist aus, nichts zu tun.")
        return 0
    if not config.ABLAGE_LOKAL.is_dir():
        return 0

    letzter_stapel = stapel.erlaubt()[-1]
    anzahl = 0

    for meta_pfad in sorted(config.ABLAGE_LOKAL.glob("*/*/meta.json")):
        ordner = meta_pfad.parent
        try:
            daten = json.loads(meta_pfad.read_text(encoding="utf-8"))
            zeit = datetime.fromisoformat(daten["empfangen"])
        except Exception:
            continue

        if stapel.lies(ordner) != letzter_stapel:
            continue   # unbearbeitet - Eingang ist tabu, egal wie alt
        if not _faellig(zeit):
            continue

        hatte_cloud_kopie = (ordner / store.MARKER).is_file()
        wartet_noch_auf_upload = (
            config.nextcloud_aktiv() and config.NEXTCLOUD_UPLOAD and not hatte_cloud_kopie
        )
        if wartet_noch_auf_upload:
            # Noch nicht gesichert - nicht loeschen, sonst waere die lokale
            # Kopie die einzige und faellt komplett weg. Naechster Sync-Lauf
            # (pipe.resync) holt das nach, dann greift die Loeschung.
            continue

        alter_tage = (datetime.now() - zeit).days
        kennung = str(ordner.relative_to(config.ABLAGE_LOKAL))

        if pruefen:
            print(f"wuerde loeschen: {kennung} ({alter_tage} Tage alt)")
            anzahl += 1
            continue

        if hatte_cloud_kopie:
            store.loeschen_bei_nextcloud(ordner)
        shutil.rmtree(ordner)
        protokoll.schreibe(
            "loeschung",
            f"{ordner.name} geloescht (Frist {config.LOESCHFRIST_TAGE}d abgelaufen, "
            f"war {alter_tage} Tage alt)",
            id=kennung, alter_tage=alter_tage, hatte_nextcloud_kopie=hatte_cloud_kopie,
        )
        anzahl += 1

    return anzahl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pruefen", action="store_true",
                     help="nur anzeigen, was faellig waere - nichts loeschen")
    args = ap.parse_args()

    anzahl = laeuft(pruefen=args.pruefen)
    if config.LOESCHFRIST_TAGE:
        verb = "waeren faellig" if args.pruefen else "geloescht"
        print(f"{anzahl} Anruf(e) {verb} (Frist: {config.LOESCHFRIST_TAGE} Tage, "
              f"nur Stapel '{stapel.erlaubt()[-1]}').")


if __name__ == "__main__":
    main()
