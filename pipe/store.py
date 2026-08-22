"""Ablage eines verarbeiteten Anrufs.

Immer lokal (Ordner pro Anruf mit meta.json, zusammenfassung.md, Audiokopie).
Zusätzlich Nextcloud per WebDAV, sobald Zugangsdaten in der Konfiguration
stehen — sonst wird der Nextcloud-Schritt übersprungen (steckbar).
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import config


@lru_cache(maxsize=1)
def _ssl_kontext() -> ssl.SSLContext:
    """SSL-Kontext mit CA-Bundle (macOS-Python bringt keinen System-Store mit)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

DRINGLICHKEIT_SYMBOL = {"niedrig": "▁", "normal": "▂", "hoch": "▆", "notfall": "█"}


def _ordnername(zeit: datetime, nummer: str | None) -> str:
    """Ordnername aus Uhrzeit und Rufnummer, dateisystemsicher.

    Die alte Fassung schnitt auf die letzten 12 Zeichen zu und zerstörte damit
    internationale Nummern: aus +49000000000 wurde 915223062462. Vorwahlen
    werden daher in 00-Schreibweise gebracht und die Nummer bleibt vollstaendig.
    """
    roh = (nummer or "").strip()
    if roh.startswith("+"):
        roh = "00" + roh[1:]
    sauber = re.sub(r"[^0-9A-Za-z]", "", roh)
    return f"{zeit:%H-%M-%S}_{sauber[:24] or 'unbekannt'}"


def _markdown(datensatz: dict) -> str:
    e = datensatz["auswertung"]
    symbol = DRINGLICHKEIT_SYMBOL.get(e["dringlichkeit"], "")
    zeilen = [
        f"# Anruf {datensatz['empfangen']} — {e['kategorie']}",
        "",
        f"- **Dringlichkeit:** {symbol} {e['dringlichkeit']}",
        f"- **Anrufer:** {e.get('anrufer_name') or '—'}",
        f"- **Rufnummer (CLIP):** {datensatz.get('anrufer_nummer') or '—'}",
        f"- **Rückrufnummer (genannt):** {e.get('rueckrufnummer') or '—'}",
        f"- **Rückruf gewünscht:** {'ja' if e.get('rueckruf_gewuenscht') else 'nein'}",
        f"- **Sprache:** {e.get('sprache')}",
        f"- **Stichworte:** {', '.join(e.get('stichworte') or []) or '—'}",
        "",
        "## Anliegen",
        e.get("anliegen_kurz") or "—",
        "",
        "## Transkript",
        datensatz.get("transkript") or "—",
        "",
    ]
    return "\n".join(zeilen)


def speichere(datensatz: dict, audio: str | Path | None) -> Path:
    """Legt den Anruf lokal ab (und in Nextcloud, falls konfiguriert).

    Gibt den lokalen Ordnerpfad zurück.
    """
    zeit = datetime.fromisoformat(datensatz["empfangen"])
    tages_ordner = config.ABLAGE_LOKAL / f"{zeit:%Y-%m-%d}"
    # Die Rufnummer der Anlage (CLIP) ist verlässlich, die aus dem Transkript
    # gelesene nur geraten - daher hat CLIP Vorrang im Ordnernamen.
    nummer = (datensatz.get("anrufer_nummer")
              or datensatz["auswertung"].get("rueckrufnummer"))
    ziel = tages_ordner / _ordnername(zeit, nummer)
    ziel.mkdir(parents=True, exist_ok=True)

    markdown = _markdown(datensatz)
    (ziel / "meta.json").write_text(
        json.dumps(datensatz, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ziel / "zusammenfassung.md").write_text(markdown, encoding="utf-8")

    audio_ziel = None
    if audio and Path(audio).is_file():
        audio_ziel = ziel / f"aufnahme{Path(audio).suffix or '.wav'}"
        shutil.copy2(audio, audio_ziel)

    if config.nextcloud_aktiv():
        if hochladen(ziel):
            print(f"  → Nextcloud: hochgeladen ({config.NEXTCLOUD_ORDNER}/{zeit:%Y-%m-%d}/{ziel.name})")
        else:
            print("  → Nextcloud: nicht erreichbar, nur lokal gespeichert (Nachsync via pipe.resync)")

    if config.NEXTCLOUD_DECK:
        from . import deck  # späte Einbindung: Deck ist optional
        if deck.karte_anlegen(datensatz, markdown):
            print(f"  → Deck: Karte in '{config.DECK_STAPEL[0]}' angelegt")

    return ziel


MARKER = ".nextcloud_ok"


def hochladen(ordner: Path) -> bool:
    """Lädt einen Anruf-Ordner nach Nextcloud. True bei Erfolg.

    Legt bei Erfolg eine Marker-Datei an, damit der Nachsync weiß, was schon
    oben ist. Wirft nie — die lokale Ablage darf nie an Nextcloud scheitern.
    """
    if not config.nextcloud_aktiv():
        return False
    try:
        zeit = datetime.strptime(ordner.parent.name, "%Y-%m-%d")
        _nach_nextcloud(ordner, zeit)
        (ordner / MARKER).write_text("", encoding="utf-8")
        return True
    except Exception as fehler:
        print(f"    Upload {ordner.name}: {fehler}")
        return False


# --- Nextcloud WebDAV (steckbar, stdlib) -------------------------------------

def _webdav_basis() -> str:
    return f"{config.NEXTCLOUD_URL}/remote.php/dav/files/{config.NEXTCLOUD_USERID}"


def _auth_header() -> dict:
    roh = f"{config.NEXTCLOUD_USER}:{config.NEXTCLOUD_PASS}".encode("utf-8")
    return {
        "Authorization": "Basic " + base64.b64encode(roh).decode("ascii"),
        # Ein WAF/Proxy vor Nextcloud blockt den Standard-User-Agent von urllib.
        "User-Agent": "praxis-telefon-agent/1.0",
    }


# Nextcloud antwortet gelegentlich träge (PHP-Kaltstart, Hintergrund-Jobs). Ein
# einzelner Aussetzer darf einen Anruf nicht in den manuellen Nachsync schicken.
WEBDAV_TIMEOUT = 60
WEBDAV_VERSUCHE = 3


def _sende(req: urllib.request.Request, timeout: int = WEBDAV_TIMEOUT):
    """Führt eine WebDAV-Anfrage aus, mit Wiederholung bei Aussetzern.

    Wiederholt nur, was sich durch Wiederholen lösen lässt: Zeitüberschreitung,
    Verbindungsabbruch, Serverfehler (5xx). MKCOL und PUT sind idempotent, ein
    zweiter Versuch kann also nichts doppelt anlegen.
    """
    for versuch in range(1, WEBDAV_VERSUCHE + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_ssl_kontext())
        except urllib.error.HTTPError as fehler:
            if fehler.code < 500 or versuch == WEBDAV_VERSUCHE:
                raise
            grund = f"HTTP {fehler.code}"
        except OSError as fehler:  # umfasst URLError und Zeitüberschreitung
            if versuch == WEBDAV_VERSUCHE:
                raise
            grund = str(fehler) or type(fehler).__name__
        wartezeit = 2 ** versuch
        print(f"    {req.get_method()} {req.selector.rsplit('/', 1)[-1]}: {grund}"
              f" - neuer Versuch in {wartezeit}s")
        time.sleep(wartezeit)


def _mkcol(pfad: str) -> None:
    req = urllib.request.Request(
        f"{_webdav_basis()}/{pfad}", method="MKCOL", headers=_auth_header()
    )
    try:
        _sende(req)
    except urllib.error.HTTPError as e:
        if e.code != 405:  # 405 = Ordner existiert bereits
            raise


def _put(lokal: Path, pfad: str) -> None:
    req = urllib.request.Request(
        f"{_webdav_basis()}/{pfad}",
        data=lokal.read_bytes(),
        method="PUT",
        headers=_auth_header(),
    )
    _sende(req, timeout=180)


def _nach_nextcloud(ordner: Path, zeit: datetime) -> None:
    basis = config.NEXTCLOUD_ORDNER
    tag = f"{zeit:%Y-%m-%d}"
    _mkcol(basis)
    _mkcol(f"{basis}/{tag}")
    _mkcol(f"{basis}/{tag}/{ordner.name}")
    for datei in sorted(ordner.iterdir()):
        if datei.is_file() and datei.name != MARKER:
            _put(datei, f"{basis}/{tag}/{ordner.name}/{datei.name}")
