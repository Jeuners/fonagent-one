"""Kategorien-Schema des aktiven Branchen-Profils (profile/<PROFIL>/kategorien.json).

Gebündelt an einer Stelle, damit categorize.py (JSON-Schema, Eskalationsziel
des Sicherheitsnetzes) und die Anzeige (dashboard.py, deck.py: Kategorie-
Label) dieselbe Quelle nutzen - ein neues Profil ändert nur die JSON-Datei,
kein Python-Code.

Dringlichkeitsstufen sind bewusst NICHT Teil des Profils: die vierstufige
Eskalationsskala (niedrig/normal/hoch/notfall) ist einsatzzweck-unabhängig.
Farbe und Sortierrang je Stufe stehen deshalb ebenfalls hier - eine Quelle
für dashboard.py, deck.py und den Leitstand (server.py), statt an drei
Stellen von Hand synchron zu halten.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config

DRINGLICHKEITEN = ["niedrig", "normal", "hoch", "notfall"]
# Farben (6-stelliges Hex ohne "#" - so will es Nextcloud Deck; dashboard.py
# und server.py stellen sich das "#" selbst voran).
DRINGLICHKEIT_FARBE = {
    "niedrig": "8A93A3",  # grau - unwichtig
    "normal": "31CC7C",   # grün
    "hoch": "E0A339",     # orange
    "notfall": "E9322D",  # rot
}
# Sortierrang (0 = dringlichst zuerst) - aus DRINGLICHKEITEN abgeleitet statt
# separat gepflegt.
DRINGLICHKEIT_RANG = {d: i for i, d in enumerate(reversed(DRINGLICHKEITEN))}


@lru_cache(maxsize=1)
def _daten() -> dict:
    return json.loads(config.KATEGORIEN_DATEI.read_text(encoding="utf-8"))


KATEGORIEN = _daten()["kategorien"]
KATEGORIE_LABEL = _daten().get("kategorie_labels", {})
# Anzeigename des aktiven Profils (Leitstand-Header, Dashboard) - Fallback auf
# den rohen Ordnernamen, falls ein Profil das Feld nicht setzt.
PROFIL_NAME = _daten().get("profil_name", config.PROFIL)
# Kategorie, auf die das deterministische Sicherheitsnetz eskaliert (siehe
# categorize._notfall_sicherheitsnetz) - bei "praxis" ist das "notfall", bei
# anderen Profilen ggf. ein anderer Name (z. B. "eingeschlossen").
SICHERHEITSNETZ_KATEGORIE = _daten().get("sicherheitsnetz_kategorie", "notfall")
