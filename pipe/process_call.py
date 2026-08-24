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

from . import categorize, kontakte, protokoll, store, transcribe


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
    if t.text.strip():
        auswertung, meta = categorize.kategorisiere(t.text)
    else:
        # Kein Text erkannt: Anrufer hat nach dem Signalton nicht gesprochen
        # oder sofort aufgelegt (oder eine bekannte Whisper-Halluzination
        # wurde rausgefiltert, siehe transcribe._ist_bekannte_halluzination).
        # Das ist ein normaler, haeufiger Fall - kein Kategorisierungs-Fehler,
        # der die Aufnahme nach telefon/fehler/ verschwinden lassen sollte.
        print("  Kein Text erkannt - kein Anliegen hinterlassen.")
        auswertung = {
            "kategorie": "sonstiges", "dringlichkeit": "niedrig",
            "anrufer_name": None, "rueckrufnummer": None,
            "rueckruf_gewuenscht": False, "sprache": t.sprache or "de",
            "anliegen_kurz": "Kein Anliegen hinterlassen - Anrufer hat nach "
                             "dem Signalton nicht gesprochen oder sofort "
                             "aufgelegt.",
            "stichworte": [],
        }
        meta = {"backend": "-", "modell": "-", "dauer_s": 0.0, "tokens": None, "kosten_usd": None}
    print(f"  Kategorie: {auswertung['kategorie']} · Dringlichkeit: {auswertung['dringlichkeit']}"
          f" ({meta['modell']}, {meta['dauer_s']}s)")

    # Dauer/Tokens/Kosten mit ins Live-Log (Leitstand-Verlauf), nicht nur in
    # den JSON-Feldern versteckt - fuer den Modell-Vergleich (Dropdown oben
    # rechts) muss man das sofort sehen, ohne meta.json aufzuklappen.
    zusatz = f"{meta['dauer_s']}s"
    if meta.get("tokens"):
        zusatz += f" · {meta['tokens']} Tok"
    if meta.get("kosten_usd"):
        zusatz += f" · ${meta['kosten_usd']:.4f}"
    protokoll.schreibe(
        "notfall" if auswertung["dringlichkeit"] == "notfall" else "anruf",
        f"{auswertung['kategorie']} · {auswertung['dringlichkeit']}"
        + (f" · {auswertung['anrufer_name']}" if auswertung.get("anrufer_name") else "")
        + f" ({meta['modell']}, {zusatz})",
        nummer=anrufer_nummer, kategorie=auswertung["kategorie"],
        dringlichkeit=auswertung["dringlichkeit"], modell=meta["modell"],
        dauer_s=meta["dauer_s"], tokens=meta.get("tokens"), kosten_usd=meta.get("kosten_usd"))

    bekannter_kontakt = kontakte.nachschlagen(anrufer_nummer)
    if bekannter_kontakt:
        print(f"  Kontakt: bekannt als »{bekannter_kontakt['name']}« "
              f"({bekannter_kontakt['adressbuch']})")
        protokoll.schreibe("kontakt", f"bekannter Anrufer: {bekannter_kontakt['name']}",
                           nummer=anrufer_nummer)

    datensatz = {
        "empfangen": empfangen.isoformat(timespec="seconds"),
        "anrufer_nummer": anrufer_nummer,
        "transkript": t.text,
        "erkannte_sprache": t.sprache,
        "audio_dauer_s": t.dauer,
        "auswertung": auswertung,
        "bekannter_kontakt": bekannter_kontakt,
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
