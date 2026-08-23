"""Kontakt-Abgleich: bekannte Anrufer per Nextcloud-Adressbuch (CardDAV).

Steckbar wie die Nextcloud-Ablage in store.py - ohne Zugangsdaten passiert
nichts, ein Anruf bleibt dann einfach ohne Kontakt-Treffer. Das Adressbuch wird
komplett geladen und kurz zwischengespeichert (siehe CACHE_S); für eine
Arztpraxis mit ein paar hundert Kontakten ist das schneller und robuster als
eine serverseitige Suche pro Anruf.
"""
from __future__ import annotations

import base64
import re
import ssl
import time
import urllib.error
import urllib.request
from functools import lru_cache

from . import config

CACHE_S = 300   # Adressbuch-Cache - neue Kontakte brauchen bis zu 5 Min.
TIMEOUT = 15


@lru_cache(maxsize=1)
def _ssl_kontext() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _basis() -> str:
    return (f"{config.NEXTCLOUD_URL}/remote.php/dav/addressbooks/users/"
            f"{config.NEXTCLOUD_USERID}/{config.NEXTCLOUD_ADRESSBUCH}/")


def _auth_header() -> dict:
    roh = f"{config.NEXTCLOUD_USER}:{config.NEXTCLOUD_PASS}".encode("utf-8")
    return {
        "Authorization": "Basic " + base64.b64encode(roh).decode("ascii"),
        "User-Agent": "praxis-telefon-agent/1.0",
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1",
    }


def _normalisiere(nummer: str | None) -> str:
    """Rein numerische Kennung fuer den Vergleich - Schreibweise darf abweichen.

    +49 152 230-62462, 0049152230622462, 0152 23062462 sollen alle auf dieselbe
    Kennung fuehren. Deutschland-Annahme (0 -> 49), wie der Rest des Projekts.
    """
    z = re.sub(r"[^0-9+]", "", (nummer or ""))
    if z.startswith("00"):
        z = z[2:]
    elif z.startswith("+"):
        z = z[1:]
    elif z.startswith("0"):
        z = "49" + z[1:]
    return z


_HREF = re.compile(r"<[a-z]+:href>([^<]+\.vcf)</[a-z]+:href>", re.I)


def _vcf_hrefs() -> list[str]:
    """Listet alle .vcf-Pfade im Adressbuch (PROPFIND, Depth 1)."""
    req = urllib.request.Request(
        _basis(), method="PROPFIND", headers=_auth_header(),
        data=b'<?xml version="1.0"?><a:propfind xmlns:a="DAV:">'
             b'<a:prop><a:getetag/></a:prop></a:propfind>',
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_kontext()) as r:
        rumpf = r.read().decode("utf-8", errors="replace")
    return [h for h in _HREF.findall(rumpf) if not h.rstrip("/").endswith(config.NEXTCLOUD_ADRESSBUCH)]


_FN = re.compile(r"^FN:(.*)$", re.M)
_TEL = re.compile(r"^TEL[^:]*:(.*)$", re.M)


def _vcards_laden() -> list[tuple[str, list[str]]]:
    """[(Name, [normalisierte Rufnummern]), ...] fuer jede Karte im Adressbuch."""
    ergebnis = []
    for href in _vcf_hrefs():
        url = href if href.startswith("http") else f"{config.NEXTCLOUD_URL}{href}"
        req = urllib.request.Request(url, headers=_auth_header())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_kontext()) as r:
                karte = r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError):
            continue
        namen = _FN.findall(karte)
        if not namen:
            continue
        nummern = [_normalisiere(n) for n in _TEL.findall(karte)]
        ergebnis.append((namen[0].strip(), [n for n in nummern if n]))
    return ergebnis


_cache: list[tuple[str, list[str]]] | None = None
_cache_zeit = 0.0


def _adressbuch() -> list[tuple[str, list[str]]]:
    global _cache, _cache_zeit
    if _cache is None or time.time() - _cache_zeit > CACHE_S:
        try:
            _cache = _vcards_laden()
        except Exception:
            return _cache or []   # letzten guten Stand behalten statt leer
        _cache_zeit = time.time()
    return _cache


def nachschlagen(nummer: str | None) -> dict | None:
    """Bekannter Kontakt zu einer Rufnummer, oder None (auch bei fehlender
    Konfiguration, unbekannter Nummer oder nicht erreichbarem Nextcloud)."""
    if not config.nextcloud_aktiv() or not nummer:
        return None
    ziel = _normalisiere(nummer)
    if not ziel:
        return None
    for name, nummern in _adressbuch():
        if ziel in nummern:
            return {"name": name, "quelle": "nextcloud", "adressbuch": config.NEXTCLOUD_ADRESSBUCH}
    return None
