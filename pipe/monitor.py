"""Störungswache: prüft die Dienste-Ampel (pipe.dienste) periodisch und
alarmiert lokal (macOS-Benachrichtigung + Ton), wenn ein Dienst ausfällt -
auch wenn gerade niemand auf den Leitstand schaut.

    python3 -m pipe.monitor [--einmal]

Läuft als eigener Prozess (siehe telefon/starten.sh), unabhängig von
pipe.watch und pipe.server - fällt einer der beiden aus, kann die Wache das
trotzdem noch melden. Alarmiert nur bei echten Störungen ("schlecht"/"aus"),
nicht bei "unklar" (z. B. Nextcloud nicht konfiguriert - das ist gewollt,
kein Fehler).
"""
from __future__ import annotations

import argparse
import time

from . import alarm, config, dienste

GESTOERT = {"schlecht", "aus"}


def pruefe(letzte_alarme: dict[str, float]) -> None:
    """Ein Durchlauf: Ampel abfragen, bei Bedarf alarmieren.

    `letzte_alarme` hält je Dienstname den Zeitpunkt (time.monotonic) des
    letzten Alarms - fehlt ein Name, gilt der Dienst aktuell als nicht
    gestört. Der Aufrufer reicht dasselbe dict über alle Durchläufe weiter.
    """
    jetzt = time.monotonic()
    for dienst in dienste.status()["dienste"]:
        name, zustand, text = dienst["name"], dienst["zustand"], dienst["text"]
        letzter_alarm = letzte_alarme.get(name)
        if zustand in GESTOERT:
            if letzter_alarm is None:
                alarm.sende(f"⚠ {name} gestört", text)
                letzte_alarme[name] = jetzt
            elif jetzt - letzter_alarm >= config.MONITOR_WIEDERHOLUNG_MIN * 60:
                alarm.sende(f"⚠ {name} weiterhin gestört", text)
                letzte_alarme[name] = jetzt
        elif zustand == "gut" and letzter_alarm is not None:
            alarm.sende(f"✓ {name} wieder ok", text)
            del letzte_alarme[name]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Dienste-Ampel beobachten und bei Störung lokal alarmieren.")
    p.add_argument("--einmal", action="store_true",
                    help="nur einmal prüfen statt dauerhaft zu beobachten")
    args = p.parse_args(argv)

    letzte_alarme: dict[str, float] = {}
    if args.einmal:
        pruefe(letzte_alarme)
        return 0

    print(f"Störungswache aktiv - Takt {config.MONITOR_TAKT_S}s, "
          f"Erinnerung alle {config.MONITOR_WIEDERHOLUNG_MIN} min - Abbruch mit Strg-C")
    try:
        while True:
            pruefe(letzte_alarme)
            time.sleep(config.MONITOR_TAKT_S)
    except KeyboardInterrupt:
        print("\nBeobachtung beendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
