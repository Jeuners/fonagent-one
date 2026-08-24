"""Exportiert bewertete Anrufe als JSONL-Trainingsdaten (Transkript + Modell-
Ausgabe + Bewertung + ggf. Korrektur) - fuer externe Auswertung, z.B. um
Kategorisierungs-Fehler systematisch statt zufaellig zu finden.

    python3 -m pipe.export

Nimmt jeden Anruf mit bewertung.json (unabhaengig vom Datum - kein
inkrementeller Export, einfach alle aktuell vorhandenen Bewertungen). Legt
unter training/ ab, das wie ablage/ nicht ins Git gehoert (siehe .gitignore) -
volle Transkripte + Namen sind bewusst drin (Testphase, siehe README-Hinweis
zu erfundenen Daten), fuer den Produktivbetrieb muss das neu entschieden
werden, siehe Warnhinweis beim Export-Knopf im Leitstand.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import bewertung, config

EXPORT_ORDNER = config.WURZEL / "training"


def sammle() -> list[dict]:
    """Alle aktuell bewerteten Anrufe, als flache Trainingsdatensaetze."""
    eintraege = []
    if not config.ABLAGE_LOKAL.is_dir():
        return eintraege
    for meta_pfad in sorted(config.ABLAGE_LOKAL.glob("*/*/meta.json")):
        ordner = meta_pfad.parent
        b = bewertung.lies(ordner)
        if b is None:
            continue
        try:
            daten = json.loads(meta_pfad.read_text(encoding="utf-8"))
        except Exception:
            continue
        eintraege.append({
            "anruf_id": str(ordner.relative_to(config.ABLAGE_LOKAL)),
            "empfangen": daten.get("empfangen"),
            "transkript": daten.get("transkript"),
            "modell_ausgabe": daten.get("auswertung"),
            "kategorisierung": daten.get("kategorisierung"),
            "bewertung": b["bewertung"],
            "korrektur": b.get("korrektur"),
            "bewertet_am": b.get("zeitpunkt"),
        })
    return eintraege


def exportiere() -> tuple[Path, int]:
    """Schreibt eine neue JSONL-Datei. Gibt (Pfad, Anzahl) zurueck."""
    eintraege = sammle()
    EXPORT_ORDNER.mkdir(parents=True, exist_ok=True)
    ziel = EXPORT_ORDNER / f"export_{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with ziel.open("w", encoding="utf-8") as f:
        for e in eintraege:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return ziel, len(eintraege)


def main() -> None:
    ziel, anzahl = exportiere()
    print(f"{anzahl} bewertete Anrufe exportiert nach {ziel}")


if __name__ == "__main__":
    main()
