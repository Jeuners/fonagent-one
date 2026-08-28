"""Kategorien-Schema des aktiven Branchen-Profils (profile/<PROFIL>/kategorien.json).

Gebündelt an einer Stelle, damit categorize.py (JSON-Schema, Eskalationsziel
des Sicherheitsnetzes) und die Anzeige (dashboard.py, deck.py: Kategorie-
Label) dieselbe Quelle nutzen - ein neues Profil ändert nur die JSON-Datei,
kein Python-Code.

Dringlichkeitsstufen sind bewusst NICHT Teil des Profils: die vierstufige
Eskalationsskala (niedrig/normal/hoch/notfall) ist einsatzzweck-unabhängig
und steckt fest in Farben/Reihenfolge in dashboard.py, deck.py und der
Leitstand-Oberfläche - nur die Kategorien selbst unterscheiden sich fachlich.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config

DRINGLICHKEITEN = ["niedrig", "normal", "hoch", "notfall"]


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
