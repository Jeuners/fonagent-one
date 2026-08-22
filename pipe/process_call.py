"""Orchestrator der Anruf-Pipeline: Audio -> Transkript -> Kategorie -> Ablage.

Aufruf:
    python3 -m pipe.process_call <audiodatei> [--nummer 021031234567]

Gibt am Ende den Ablageort aus. Vollständig lokal, sofern keine Nextcloud-
Zugangsdaten konfiguriert sind.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import categorize, protokoll, store, transcribe


def verarbeite(audio: str, anrufer_nummer: str | None = None,
               empfangen: datetime | None = None) -> dict:
    empfangen = empfangen or datetime.now()

    print(f"▸ Transkribiere {audio} …")
    protokoll.schreibe("anruf", "Transkribiere …", nummer=anrufer_nummer)
    t = transcribe.transkribiere(audio)
    protokoll.schreibe("anruf", (t.text[:200] or "(kein Text erkannt)"),
                       nummer=anrufer_nummer, dauer=t.dauer)
    print(f"  Sprache: {t.sprache} ({t.sprache_wahrscheinlichkeit}), Dauer: {t.dauer}s")
    print(f"  Text: {t.text[:160]}{'…' if len(t.text) > 160 else ''}")

    print("▸ Kategorisiere …")
    auswertung = categorize.kategorisiere(t.text)
    print(f"  Kategorie: {auswertung['kategorie']} · Dringlichkeit: {auswertung['dringlichkeit']}")
    protokoll.schreibe(
        "notfall" if auswertung["dringlichkeit"] == "notfall" else "anruf",
        f"{auswertung['kategorie']} · {auswertung['dringlichkeit']}"
        + (f" · {auswertung['anrufer_name']}" if auswertung.get("anrufer_name") else ""),
        nummer=anrufer_nummer, kategorie=auswertung["kategorie"],
        dringlichkeit=auswertung["dringlichkeit"])

    datensatz = {
        "empfangen": empfangen.isoformat(timespec="seconds"),
        "anrufer_nummer": anrufer_nummer,
        "transkript": t.text,
        "erkannte_sprache": t.sprache,
        "audio_dauer_s": t.dauer,
        "auswertung": auswertung,
    }

    print("▸ Lege ab …")
    ziel = store.speichere(datensatz, audio)
    print(f"✔ Fertig: {ziel}")
    protokoll.schreibe("fertig", f"abgelegt: {ziel.parent.name}/{ziel.name}",
                       nummer=anrufer_nummer)
    protokoll.kuerze()
    return datensatz


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Anruf verarbeiten und ablegen.")
    p.add_argument("audio", help="Pfad zur Audiodatei (WAV/OPUS/…)")
    p.add_argument("--nummer", default=None, help="Rufnummer des Anrufers (CLIP), falls bekannt")
    args = p.parse_args(argv)
    try:
        verarbeite(args.audio, anrufer_nummer=args.nummer)
    except Exception as fehler:
        print(f"✖ Fehler: {fehler}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
