"""Nextcloud-Deck-Anbindung: pro Anruf eine Triage-Karte.

Aufbau:
  - Ein Board (Standard: "Anrufe").
  - Ein Stapel je Kategorie (termin, rezept, …) — wird bei Bedarf angelegt.
  - Ein Label je Dringlichkeit (farbig) — wird bei Bedarf angelegt und der Karte
    zugewiesen.
  - Karte je Anruf: Titel kompakt, Beschreibung = die Zusammenfassung (Markdown).

Nur aktiv, wenn NEXTCLOUD_DECK=true. Alles über die Deck-REST-API (v1.1).
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from functools import lru_cache

from . import config, kategorien

_API = "/index.php/apps/deck/api/v1.1"

# Farben (6-stelliges Hex ohne #) je Dringlichkeit.
DRINGLICHKEIT_FARBE = {
    "niedrig": "8A93A3",  # grau
    "normal": "31CC7C",   # grün
    "hoch": "E0A339",     # orange
    "notfall": "E9322D",  # rot
}
# Lesbare Namen der Kategorien für den Kartentitel - der Stapel sagt jetzt,
# wie weit die Bearbeitung ist, nicht mehr worum es geht.
KATEGORIE_LABEL = kategorien.KATEGORIE_LABEL


@lru_cache(maxsize=1)
def _ssl_kontext() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _api(method: str, pfad: str, koerper: dict | None = None):
    roh = f"{config.NEXTCLOUD_USER}:{config.NEXTCLOUD_PASS}".encode("utf-8")
    headers = {
        "Authorization": "Basic " + base64.b64encode(roh).decode("ascii"),
        "OCS-APIRequest": "true",
        "Content-Type": "application/json",
        "User-Agent": "praxis-telefon-agent/1.0",
    }
    daten = json.dumps(koerper).encode("utf-8") if koerper is not None else None
    req = urllib.request.Request(
        f"{config.NEXTCLOUD_URL}{_API}{pfad}", data=daten, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=30, context=_ssl_kontext()) as antwort:
        text = antwort.read().decode("utf-8")
    return json.loads(text) if text.strip() else None


# --- Board / Stapel / Labels sicherstellen -----------------------------------

def _freigabe(board_id: int, acl: list | None) -> None:
    """Teilt das Board mit den Nutzern aus DECK_TEILEN, falls noch nicht geschehen.

    Das Board gehört dem Konto, mit dem die Pipe arbeitet. Ohne Freigabe legt
    sie zwar Karten an, die Praxis sieht aber ein leeres Deck.
    """
    if not config.DECK_TEILEN:
        return
    schon = {(a.get("participant") or {}).get("uid") for a in (acl or [])}
    for nutzer in config.DECK_TEILEN:
        if nutzer in schon:
            continue
        try:
            _api("POST", f"/boards/{board_id}/acl",
                 {"type": 0, "participant": nutzer, "permissionEdit": True,
                  "permissionShare": False, "permissionManage": False})
            print(f"  → Deck: Board mit '{nutzer}' geteilt")
        except Exception as fehler:
            print(f"  → Deck: Freigabe für '{nutzer}' fehlgeschlagen ({fehler})")


def _board_id() -> int:
    for board in _api("GET", "/boards") or []:
        if board.get("title") == config.DECK_BOARD and not board.get("archived"):
            _freigabe(board["id"], board.get("acl"))
            return board["id"]
    neu = _api("POST", "/boards", {"title": config.DECK_BOARD, "color": "0082C9"})
    _freigabe(neu["id"], [])
    return neu["id"]


def _stapel(board_id: int) -> dict[str, int]:
    """Stellt die Stapel des Arbeitsablaufs sicher und gibt sie zurück."""
    vorhanden = {s["title"]: s["id"] for s in (_api("GET", f"/boards/{board_id}/stacks") or [])}
    for i, name in enumerate(config.DECK_STAPEL):
        if name not in vorhanden:
            neu = _api("POST", f"/boards/{board_id}/stacks", {"title": name, "order": i})
            vorhanden[name] = neu["id"]
    return vorhanden


def _labels(board_id: int) -> dict[str, int]:
    board = _api("GET", f"/boards/{board_id}")
    vorhanden = {l["title"]: l["id"] for l in (board.get("labels") or [])}
    for name, farbe in DRINGLICHKEIT_FARBE.items():
        if name not in vorhanden:
            neu = _api("POST", f"/boards/{board_id}/labels",
                       {"title": name, "color": farbe})
            vorhanden[name] = neu["id"]
    return vorhanden


# --- Karte anlegen ------------------------------------------------------------

def _kartentitel(datensatz: dict) -> str:
    """Zeit, Kategorie, Anrufer - in dieser Reihenfolge lesbar auf der Karte."""
    e = datensatz["auswertung"]
    wer = e.get("anrufer_name") or datensatz.get("anrufer_nummer") or "unbekannt"
    zeit = datensatz["empfangen"][11:16]
    kategorie = KATEGORIE_LABEL.get(e["kategorie"], e["kategorie"])
    return f"{zeit} · {kategorie} · {wer}"


def karte_anlegen(datensatz: dict, beschreibung: str) -> int | None:
    """Legt eine Deck-Karte für den Anruf an. Gibt die Karten-ID zurück.

    Wirft nie — Deck ist ein Zusatz, kein Muss.
    """
    if not (config.nextcloud_aktiv() and config.NEXTCLOUD_DECK):
        return None
    try:
        board_id = _board_id()
        stapel = _stapel(board_id)
        labels = _labels(board_id)

        e = datensatz["auswertung"]
        eingang = config.DECK_STAPEL[0]
        stack_id = stapel[eingang]
        karte = _api(
            "POST", f"/boards/{board_id}/stacks/{stack_id}/cards",
            {"title": _kartentitel(datensatz), "type": "plain", "order": 0,
             "description": beschreibung},
        )
        karten_id = karte["id"]

        label_id = labels.get(e.get("dringlichkeit"))
        if label_id is not None:
            _api("PUT",
                 f"/boards/{board_id}/stacks/{stack_id}/cards/{karten_id}/assignLabel",
                 {"labelId": label_id})
        return karten_id
    except Exception as fehler:
        print(f"  → Deck: FEHLGESCHLAGEN ({fehler})")
        return None
