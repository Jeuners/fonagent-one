"""Beobachtet den Eingangsordner und schiebt neue Aufnahmen durch die Pipe.

    python3 -m pipe.watch [--einmal]

Die Telefonanlage legt Aufnahmen in ``telefon/eingang`` ab. Der Dateiname trägt
die Rufnummer des Anrufers (CLIP), damit sie nicht verloren geht:

    JJJJMMTT-HHMMSS_<nummer>.wav

Verarbeitete Aufnahmen wandern nach ``telefon/verarbeitet``, gescheiterte nach
``telefon/fehler`` — dort bleiben sie liegen, statt still verloren zu gehen.

Bewusst mit Polling statt Dateisystem-Ereignissen: eine Praxis bekommt Anrufe im
Minutenabstand, nicht im Millisekundenabstand, und Polling übersteht Neustarts
und Netzlaufwerke ohne Sonderfälle.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, loeschen, process_call, protokoll

TAKT_S = 3          # Wartezeit zwischen zwei Durchläufen
RUHE_S = 2          # So lange muss die Dateigröße stabil sein (Aufnahme fertig)
ENDUNGEN = {".wav", ".mp3", ".opus", ".ogg", ".m4a", ".aiff"}

# Aufbewahrungsfrist pruefen (pipe.loeschen) - kein Cron noetig, der Watcher
# laeuft ohnehin dauerhaft. time.monotonic() statt datetime.now(): unempfindlich
# gegen Systemzeit-Spruenge (Zeitzone/NTP), zaehlt einfach vergangene Sekunden.
LOESCH_TAKT_S = 24 * 60 * 60

# JJJJMMTT-HHMMSS_<nummer>.<ext> - beides optional, damit auch handverlesene
# Dateien verarbeitet werden.
_NAME = re.compile(r"^(?:(\d{8})-(\d{6}))?_?(.*)$")


def _nummer_und_zeit(datei: Path) -> tuple[str | None, datetime | None]:
    """Liest Rufnummer und Aufnahmezeit aus dem Dateinamen."""
    treffer = _NAME.match(datei.stem)
    if not treffer:
        return None, None
    tag, uhrzeit, rest = treffer.groups()
    zeit = None
    if tag and uhrzeit:
        try:
            zeit = datetime.strptime(f"{tag}{uhrzeit}", "%Y%m%d%H%M%S")
        except ValueError:
            zeit = None
    nummer = rest.strip() or None
    if nummer in {"unbekannt", "anonymous", "restricted", "0"}:
        nummer = None
    return nummer, zeit


def _noch_offen(datei: Path) -> bool:
    """Wahr, wenn ein Prozess (FreeSWITCH) die Datei noch zum Schreiben offen
    haelt. Reine Groessenstabilitaet reicht nicht: eine Sprechpause laesst die
    Datei fuer RUHE_S Sekunden still stehen, obwohl der Anruf noch laeuft - der
    Watcher hat dann eine Aufnahme mitten im Schreiben gegriffen, ffmpeg/
    whisper-cli lasen einen unfertigen RIFF-Header und scheiterten wortlos
    (kein Absturz, nur keine Ausgabedatei - sah wie ein Transkriptions-Bug
    aus, war aber ein Race gegen die laufende Aufnahme)."""
    try:
        r = subprocess.run(["lsof", "-t", str(datei)],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False   # lsof fehlt/schlaegt fehl: nicht blockieren


def _fertig(datei: Path) -> bool:
    """Wahr, wenn die Datei nicht mehr waechst UND kein Prozess sie noch offen
    haelt - die Aufnahme also wirklich abgeschlossen ist."""
    try:
        groesse = datei.stat().st_size
    except FileNotFoundError:
        return False
    if groesse == 0:
        return False
    time.sleep(RUHE_S)
    try:
        if datei.stat().st_size != groesse:
            return False
    except FileNotFoundError:
        return False
    return not _noch_offen(datei)


def _neue_dateien(eingang: Path):
    for datei in sorted(eingang.iterdir()):
        if datei.is_file() and datei.suffix.lower() in ENDUNGEN and not datei.name.startswith("."):
            yield datei


def verarbeite_datei(datei: Path) -> bool:
    """Schiebt eine Aufnahme durch die Pipe und räumt sie weg. True bei Erfolg."""
    nummer, zeit = _nummer_und_zeit(datei)
    print(f"\n▸ Neue Aufnahme: {datei.name}"
          f"{f' (Rufnummer {nummer})' if nummer else ' (Rufnummer unbekannt)'}")
    protokoll.schreibe("eingang", f"Neue Aufnahme {datei.name}", nummer=nummer)
    try:
        process_call.verarbeite(str(datei), anrufer_nummer=nummer, empfangen=zeit)
    except Exception as fehler:
        print(f"✖ Verarbeitung fehlgeschlagen: {fehler}", file=sys.stderr)
        protokoll.schreibe("fehler", f"{datei.name}: {fehler}", nummer=nummer)
        config.TELEFON_FEHLER.mkdir(parents=True, exist_ok=True)
        shutil.move(str(datei), config.TELEFON_FEHLER / datei.name)
        print(f"  Aufnahme liegt in {config.TELEFON_FEHLER}/{datei.name} und geht nicht verloren.")
        return False
    config.TELEFON_VERARBEITET.mkdir(parents=True, exist_ok=True)
    shutil.move(str(datei), config.TELEFON_VERARBEITET / datei.name)
    return True


def durchlauf(eingang: Path) -> int:
    """Verarbeitet alles, was gerade fertig im Eingang liegt."""
    zahl = 0
    for datei in _neue_dateien(eingang):
        if _fertig(datei):
            verarbeite_datei(datei)
            zahl += 1
    return zahl


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Eingangsordner beobachten und Anrufe verarbeiten.")
    p.add_argument("--einmal", action="store_true",
                   help="nur einmal durchlaufen statt dauerhaft beobachten")
    args = p.parse_args(argv)

    eingang = config.TELEFON_EINGANG
    eingang.mkdir(parents=True, exist_ok=True)

    if args.einmal:
        zahl = durchlauf(eingang)
        print(f"{zahl} Aufnahme(n) verarbeitet.")
        return 0

    print(f"Beobachte {eingang} — Abbruch mit Strg-C")
    if config.LOESCHFRIST_TAGE:
        print(f"Aufbewahrungsfrist: {config.LOESCHFRIST_TAGE} Tage "
              f"(Pruefung alle {LOESCH_TAKT_S // 3600}h, kein Cron noetig)")
    protokoll.schreibe("system", "Watcher gestartet")
    letzter_loeschlauf = 0.0   # 0 => gleich beim ersten Durchlauf pruefen
    try:
        while True:
            durchlauf(eingang)
            if config.LOESCHFRIST_TAGE and time.monotonic() - letzter_loeschlauf >= LOESCH_TAKT_S:
                anzahl = loeschen.laeuft()
                if anzahl:
                    print(f"🗑  {anzahl} Anruf(e) nach Aufbewahrungsfrist geloescht.")
                letzter_loeschlauf = time.monotonic()
            time.sleep(TAKT_S)
    except KeyboardInterrupt:
        print("\nBeobachtung beendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
