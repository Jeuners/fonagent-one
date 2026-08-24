"""Live-Dashboard: zeigt Anrufe, Systemzustand und das laufende Protokoll.

    python3 -m pipe.server [--port 8088]

Rein lokal, stdlib. Liest die Ablage, das Ereignisprotokoll und den Zustand der
beteiligten Dienste. Die Seite fragt alle paar Sekunden nach — kein WebSocket,
kein Framework, nichts, was im Praxisbetrieb kaputtgehen kann.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config, dashboard, modellwahl, protokoll, stapel, store

# Statusabfragen sind teuer (Netz, Unterprozesse) - kurz zwischenspeichern,
# damit haeufiges Nachfragen den Rechner nicht belastet.
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_S = 4.0


def _laeuft(muster: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-f", muster], capture_output=True).returncode == 0
    except Exception:
        return False


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    """Erreichbarkeitstest. Mit eigenem User-Agent - ein WAF vor Nextcloud
    blockt den Standardnamen von urllib und meldete fälschlich 'nicht
    erreichbar'."""
    req = urllib.request.Request(url, headers={"User-Agent": "praxis-telefon-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=store._ssl_kontext()):
            return True
    except urllib.error.HTTPError:
        return True          # antwortet - reicht als Lebenszeichen
    except Exception:
        return False


def _trunk() -> tuple[str, str]:
    """(zustand, text) des SIP-Trunks."""
    if not _laeuft("[f]reeswitch"):
        return "aus", "FreeSWITCH läuft nicht"
    try:
        roh = subprocess.run(
            ["fs_cli", "-P", config.FS_PORT, "-x", "sofia status gateway plusnet"],
            capture_output=True, text=True, timeout=8).stdout
        for zeile in roh.splitlines():
            if zeile.startswith("Status"):
                wert = zeile.split()[-1]
                return ("gut", "registriert") if wert == "UP" else ("schlecht", f"Trunk {wert}")
        return "unklar", "Gateway unbekannt"
    except Exception:
        return "unklar", "fs_cli nicht erreichbar"


def status() -> dict:
    jetzt = time.time()
    gepuffert = _CACHE.get("status")
    if gepuffert and jetzt - gepuffert[0] < CACHE_S:
        return gepuffert[1]

    trunk_zustand, trunk_text = _trunk()
    watcher = _laeuft("[p]ipe.watch")
    ollama = _http_ok(f"{config.OLLAMA_URL}/api/version")
    nextcloud = (_http_ok(f"{config.NEXTCLOUD_URL}/status.php")
                 if config.nextcloud_aktiv() else None)

    offen = 0
    if config.TELEFON_EINGANG.is_dir():
        offen = sum(1 for d in config.TELEFON_EINGANG.iterdir()
                    if d.is_file() and not d.name.startswith("."))
    fehler = 0
    if config.TELEFON_FEHLER.is_dir():
        fehler = sum(1 for d in config.TELEFON_FEHLER.iterdir() if d.is_file())

    daten = {
        "dienste": [
            {"name": "Telefon", "zustand": trunk_zustand, "text": trunk_text},
            {"name": "Verarbeitung", "zustand": "gut" if watcher else "aus",
             "text": "beobachtet Eingang" if watcher else "Watcher läuft nicht"},
            {"name": "Spracherkennung", "zustand": "gut" if ollama else "schlecht",
             "text": "Ollama bereit" if ollama else "Ollama nicht erreichbar"},
            {"name": "Nextcloud", "zustand": "unklar" if nextcloud is None
             else ("gut" if nextcloud else "schlecht"),
             "text": "nicht konfiguriert" if nextcloud is None
             else ("erreichbar" if nextcloud else "nicht erreichbar")},
        ],
        "eingang_offen": offen,
        "fehler": fehler,
        "stand": datetime.now().strftime("%H:%M:%S"),
    }
    _CACHE["status"] = (jetzt, daten)
    return daten


def anrufe() -> list[dict]:
    liste = []
    for d in dashboard._anrufe():
        e = d["auswertung"]
        zeit = d["empfangen"]
        ordner = None
        try:
            z = datetime.fromisoformat(zeit)
            tag = f"{z:%Y-%m-%d}"
            for kandidat in (config.ABLAGE_LOKAL / tag).iterdir():
                if kandidat.is_dir() and kandidat.name.startswith(f"{z:%H-%M-%S}"):
                    ordner = f"{tag}/{kandidat.name}"
                    break
        except Exception:
            pass
        if ordner and stapel.ist_archiviert(config.ABLAGE_LOKAL / ordner):
            continue    # archiviert (simuliert) - verschwindet vom Board
        audio = None
        if ordner:
            for endung in (".wav", ".mp3", ".opus", ".m4a"):
                if (config.ABLAGE_LOKAL / ordner / f"aufnahme{endung}").is_file():
                    audio = f"/audio/{ordner}/aufnahme{endung}"
                    break
        liste.append({
            "id": ordner or "",
            "stapel": stapel.lies(config.ABLAGE_LOKAL / ordner) if ordner else stapel.erlaubt()[0],
            "empfangen": zeit,
            "kategorie": e["kategorie"],
            "dringlichkeit": e["dringlichkeit"],
            "name": e.get("anrufer_name"),
            "nummer": d.get("anrufer_nummer") or e.get("rueckrufnummer"),
            "rueckruf": bool(e.get("rueckruf_gewuenscht")),
            "anliegen": e.get("anliegen_kurz") or "",
            "stichworte": e.get("stichworte") or [],
            "transkript": d.get("transkript") or "",
            "audio": audio,
            "kontakt": (d.get("bekannter_kontakt") or {}).get("name"),
        })
    return liste


def archiviere_tag() -> int:
    """Markiert alle 'Erledigt'-Anrufe als archiviert (simuliert - es wird
    nichts exportiert oder geloescht, nur vom Board geraeumt). Gibt die Anzahl
    zurueck."""
    letzter = stapel.erlaubt()[-1]
    anzahl = 0
    for a in anrufe():
        if a["stapel"] != letzter or not a["id"]:
            continue
        ordner = stapel.ordner_zu(a["id"])
        if ordner and stapel.archiviere(ordner):
            anzahl += 1
    return anzahl


FS_LOG = config.FREESWITCH_LOG
_ZEIT = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

# Was im FreeSWITCH-Log für die Praxis interessant ist - und wie es heißen soll.
_FS_MUSTER = [
    ("New Channel sofia/external/", "anruf", "Anruf von {nummer}"),
    ("has been answered", "anruf", "abgenommen, Ansage läuft"),
    ("Hangup sofia/external/", "anruf", "Anrufer hat aufgelegt"),
    ("Failed Registration", "fehler", "Telefon: Registrierung fehlgeschlagen"),
    ("Ping failed", "fehler", "Telefon: Provider antwortet nicht"),
]


def _telefon_ereignisse(anzahl: int = 60) -> list[dict]:
    """Liest Anruf-Ereignisse aus dem FreeSWITCH-Log.

    So sieht man den Anruf schon beim Klingeln, nicht erst wenn die fertige
    Aufnahme durch die Pipe gelaufen ist.
    """
    if not FS_LOG.is_file():
        return []
    try:
        with FS_LOG.open("rb") as f:                 # nur das Ende lesen
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            zeilen = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []

    ereignisse = []
    for zeile in zeilen[-4000:]:
        for schnipsel, art, vorlage in _FS_MUSTER:
            if schnipsel not in zeile:
                continue
            # Bei Kanal-Ereignissen steht die Channel-UUID vor dem Zeitstempel,
            # die Position ist also nicht verlässlich - daher per Muster suchen.
            treffer = _ZEIT.search(zeile)
            zeit = treffer.group(0) if treffer else ""
            nummer = ""
            if "sofia/external/" in zeile:
                rest = zeile.split("sofia/external/", 1)[1]
                nummer = rest.split("@", 1)[0].strip()
            ereignisse.append({
                "zeit": zeit.replace(" ", "T"),
                "art": art,
                "text": vorlage.format(nummer=nummer or "unbekannt"),
            })
            break
    return ereignisse[-anzahl:]


def verlauf(anzahl: int = 150) -> list[dict]:
    """Pipe-Protokoll und Telefonie-Ereignisse, zeitlich zusammengeführt."""
    alle = protokoll.lies(anzahl) + _telefon_ereignisse()
    alle.sort(key=lambda e: e.get("zeit") or "")
    return alle[-anzahl:]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # kein Zugriffslog auf der Konsole
        pass

    def _angemeldet(self) -> bool:
        """Prüft die Zugangsdaten, sofern eines gesetzt ist.

        Ohne Passwort bleibt der Leitstand offen - das ist nur zulässig, wenn er
        an 127.0.0.1 hängt; darauf besteht main() beim Start.
        """
        if not config.LEITSTAND_PASS:
            return True
        kopf = self.headers.get("Authorization", "")
        if not kopf.startswith("Basic "):
            return False
        try:
            nutzer, _, wort = base64.b64decode(kopf[6:]).decode("utf-8").partition(":")
        except Exception:
            return False
        # compare_digest: Vergleichsdauer verrät nichts über das Passwort.
        return (hmac.compare_digest(nutzer, config.LEITSTAND_USER)
                and hmac.compare_digest(wort, config.LEITSTAND_PASS))

    def _anmeldung_verlangen(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Anruf-Leitstand"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sende(self, inhalt: bytes, typ: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(inhalt)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(inhalt)

    def _json(self, daten) -> None:
        self._sende(json.dumps(daten, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._angemeldet():
            self._anmeldung_verlangen()
            return
        pfad = unquote(urlparse(self.path).path)
        if pfad == "/":
            self._sende(SEITE.encode("utf-8"), "text/html; charset=utf-8")
        elif pfad == "/api/status":
            self._json(status())
        elif pfad == "/api/anrufe":
            self._json({"stapel": stapel.erlaubt(), "anrufe": anrufe()})
        elif pfad == "/api/verlauf":
            self._json(verlauf(150))
        elif pfad == "/api/modell":
            self._json({"aktuell": modellwahl.aktuelle_id(), "optionen": modellwahl.AUSWAHL})
        elif pfad.startswith("/audio/"):
            self._audio(pfad[len("/audio/"):])
        else:
            self._sende(b"nicht gefunden", "text/plain; charset=utf-8", 404)

    def do_POST(self) -> None:
        if not self._angemeldet():
            self._anmeldung_verlangen()
            return
        pfad = unquote(urlparse(self.path).path)
        if pfad == "/api/archivieren":
            anzahl = archiviere_tag()
            protokoll.schreibe("archiv", f"{anzahl} Anruf(e) archiviert (simuliert)", anzahl=anzahl)
            self._json({"ok": True, "anzahl": anzahl, "simuliert": True})
            return
        if pfad == "/api/modell":
            try:
                laenge = int(self.headers.get("Content-Length") or 0)
                kennung = json.loads(self.rfile.read(min(laenge, 10_000)).decode("utf-8"))["id"]
            except Exception:
                self._json({"ok": False, "fehler": "ungültige Anfrage"})
                return
            if not modellwahl.setze(kennung):
                self._json({"ok": False, "fehler": f"unbekanntes Modell: {kennung}"})
                return
            protokoll.schreibe("system", f"Kategorisierungs-Modell → {kennung}")
            self._json({"ok": True})
            return
        if pfad != "/api/stapel":
            self._sende(b"nicht gefunden", "text/plain; charset=utf-8", 404)
            return
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
            if laenge > 10_000:
                raise ValueError("Anfrage zu groß")
            wunsch = json.loads(self.rfile.read(laenge).decode("utf-8"))
            kennung, ziel = wunsch["id"], wunsch["stapel"]
        except Exception:
            self._json({"ok": False, "fehler": "ungültige Anfrage"})
            return

        ordner = stapel.ordner_zu(kennung)
        if ordner is None:
            self._json({"ok": False, "fehler": "Anruf nicht gefunden"})
            return
        if not stapel.setze(ordner, ziel):
            self._json({"ok": False, "fehler": f"unbekannter Stapel: {ziel}"})
            return
        protokoll.schreibe("stapel", f"{ordner.name} → {ziel}")
        self._json({"ok": True})

    def _audio(self, rest: str) -> None:
        """Liefert eine Aufnahme aus der Ablage - nur von dort.

        Muss HTTP-Range unterstuetzen: der <audio>-Player im Leitstand schickt
        beim Laden erst eine kleine Range-Anfrage (z.B. die ersten paar KB),
        um Dauer/Seekbarkeit zu ermitteln. Antwortet der Server darauf mit
        200 + der kompletten Datei statt 206 + dem angefragten Ausschnitt,
        haelt der Browser sich an die urspruenglich angefragte (kleine)
        Groesse und bricht die Wiedergabe nach ein paar Sekunden ab, obwohl
        die Datei komplett uebertragen wurde.
        """
        ziel = (config.ABLAGE_LOKAL / rest).resolve()
        wurzel = config.ABLAGE_LOKAL.resolve()
        if not str(ziel).startswith(str(wurzel) + "/") or not ziel.is_file():
            self._sende(b"nicht gefunden", "text/plain; charset=utf-8", 404)
            return
        typ = {".wav": "audio/wav", ".mp3": "audio/mpeg",
               ".opus": "audio/ogg", ".m4a": "audio/mp4"}.get(ziel.suffix, "application/octet-stream")
        groesse = ziel.stat().st_size
        bereich = self.headers.get("Range")
        if not bereich:
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(groesse))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with ziel.open("rb") as datei:
                self.wfile.write(datei.read())
            return

        treffer = re.match(r"bytes=(\d*)-(\d*)", bereich)
        start = int(treffer.group(1)) if treffer and treffer.group(1) else 0
        ende = int(treffer.group(2)) if treffer and treffer.group(2) else groesse - 1
        ende = min(ende, groesse - 1)
        if not treffer or start > ende or start >= groesse:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{groesse}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        laenge = ende - start + 1
        self.send_response(206)
        self.send_header("Content-Type", typ)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{ende}/{groesse}")
        self.send_header("Content-Length", str(laenge))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with ziel.open("rb") as datei:
            datei.seek(start)
            self.wfile.write(datei.read(laenge))


SEITE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anruf-Leitstand — Praxis Musterhausen</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%8E%EF%B8%8F%3C/text%3E%3C/svg%3E">
<style>
:root{--bg:#f4f6f9;--fg:#18202c;--karte:#fff;--spalte:#eceff4;--rand:#dde3ec;--muted:#68748a;
--gut:#1f9d5c;--schlecht:#d93a34;--unklar:#c8901f;--aus:#8a93a3;--log:#f8fafc}
@media(prefers-color-scheme:dark){:root{--bg:#0f1218;--fg:#e6ecf4;--karte:#1b212b;
--spalte:#151a22;--rand:#28303d;--muted:#8d99ad;--log:#10141b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1600px;margin:0 auto;padding:18px 16px 30px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px}
h1{font-size:19px;margin:0}
.stand{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums;margin-left:auto}
.modell-select{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 10px;font-size:13px;font-family:inherit;cursor:pointer;max-width:280px}
.dienste{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px}
.dienst{background:var(--karte);border:1px solid var(--rand);border-radius:9px;padding:9px 11px;
display:flex;align-items:center;gap:9px}
.punkt{width:9px;height:9px;border-radius:50%;flex:none}
.gut{background:var(--gut)}.schlecht{background:var(--schlecht)}
.unklar{background:var(--unklar)}.aus{background:var(--aus)}
.dienst b{display:block;font-size:13px}.dienst span{color:var(--muted);font-size:12px}
.hinweis{background:#fdf1e7;border:1px solid #f0c9a0;color:#8a4b12;border-radius:8px;
padding:7px 11px;margin:9px 0;font-size:13px}
@media(prefers-color-scheme:dark){.hinweis{background:#2b1e12;border-color:#5a3a18;color:#f0c08a}}
.notfall-banner{display:none;align-items:center;gap:9px;background:var(--schlecht);color:#fff;
font-weight:700;font-size:14px;padding:9px 14px;border-radius:9px;margin:9px 0;
animation:notfallBlinken 1.6s ease-in-out infinite}
.notfall-banner.zeigen{display:flex}
@keyframes notfallBlinken{0%,100%{opacity:1}50%{opacity:.6}}
@keyframes notfallRahmen{0%,100%{box-shadow:0 0 0 0 rgba(233,50,45,.6)}
50%{box-shadow:0 0 0 9px rgba(233,50,45,0)}}
.karte.notfall-offen{animation:notfallRahmen 1.6s ease-in-out infinite}
.archiv-btn{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit}
.archiv-btn:hover{border-color:var(--muted)}
.archiv-btn:disabled{opacity:.5;cursor:default}
.sicher-btn{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit;margin-right:8px}
.sicher-btn:hover{border-color:var(--muted)}
.sicher-btn.aktiv{background:var(--gut);border-color:var(--gut);color:#fff}
/* Sicher-Modus: Patientendaten unscharf, fuer Bildschirmaufnahmen/Screenshots.
   Struktur (Kategorie, Dringlichkeit, Zeit, Stapel) bleibt lesbar - nur was
   auf eine Person zurueckfuehrt wird geblurrt. */
body.sicher a.waehlen{filter:blur(6px);user-select:none}
.toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,12px);background:var(--fg);
color:var(--bg);padding:9px 16px;border-radius:8px;font-size:13px;opacity:0;pointer-events:none;
transition:opacity .2s,transform .2s;z-index:10;box-shadow:0 4px 16px rgba(0,0,0,.2)}
.toast.zeigen{opacity:1;transform:translate(-50%,0)}

.board{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:12px;align-items:start}
@media(max-width:1100px){.board{grid-template-columns:repeat(2,1fr)}}
@media(max-width:660px){.board{grid-template-columns:1fr}}
.spalte{background:var(--spalte);border:1px solid var(--rand);border-radius:12px;padding:10px;
min-height:130px;transition:background .12s,border-color .12s}
.spalte.ueber{border-color:var(--gut);background:color-mix(in srgb,var(--gut) 8%,var(--spalte))}
.spalte h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:0 0 9px;display:flex;gap:7px;align-items:center}
.zahl{background:var(--rand);color:var(--fg);border-radius:999px;padding:0 7px;font-size:11px}
a.waehlen{color:inherit;text-decoration:underline;text-decoration-style:dotted;
text-underline-offset:2px;cursor:pointer}
a.waehlen:hover{color:var(--gut);text-decoration-style:solid}
.karte{background:var(--karte);border:1px solid var(--rand);border-left:4px solid var(--akzent,#888);
border-radius:9px;padding:9px 11px;margin-bottom:9px;cursor:grab}
.karte:active{cursor:grabbing}
.karte.zieht{opacity:.4}
.kopf{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.titel{font-weight:650;font-size:14px}
.dring{color:#fff;font-size:9px;font-weight:700;text-transform:uppercase;padding:2px 6px;
border-radius:999px;letter-spacing:.04em;margin-left:auto}
.meta{color:var(--muted);font-size:12px;margin:3px 0 5px;font-variant-numeric:tabular-nums}
.anliegen{margin:5px 0;font-size:13.5px}
.tag{display:inline-block;background:var(--bg);border:1px solid var(--rand);border-radius:5px;
padding:1px 6px;font-size:11px;color:var(--muted);margin:2px 3px 2px 0}
audio{width:100%;height:32px;margin-top:6px;display:block}
details{margin-top:4px}summary{cursor:pointer;color:var(--muted);font-size:12px}
.tr{color:var(--muted);font-size:12.5px;margin:4px 0 0}
.schieber{display:flex;gap:5px;margin-top:7px;flex-wrap:wrap}
.schieber button{font:inherit;font-size:11px;padding:2px 8px;border-radius:6px;cursor:pointer;
border:1px solid var(--rand);background:var(--bg);color:var(--muted)}
.schieber button:hover{color:var(--fg);border-color:var(--muted)}
.leer{color:var(--muted);font-size:12.5px;padding:5px 2px}

.dienste-kopf{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:22px 0 7px}
#verlauf{background:var(--log);border:1px solid var(--rand);border-radius:11px;padding:10px;
height:180px;overflow-y:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.z{display:flex;gap:9px;padding:1px 0}
.z time{color:var(--muted);flex:none;font-variant-numeric:tabular-nums}
.z .a{flex:none;width:64px;font-weight:600}
.a-anruf{color:#3b86d8}.a-eingang{color:#1f9d5c}.a-fertig{color:#1f9d5c}
.a-fehler{color:var(--schlecht)}.a-notfall{color:var(--schlecht);font-weight:700}
.a-system{color:var(--muted)}.a-stapel{color:#9b6bd8}.a-archiv{color:#9b6bd8}
.a-kontakt{color:var(--gut)}
.kontakt-badge{display:inline-block;background:color-mix(in srgb,var(--gut) 16%,transparent);
color:var(--gut);border-radius:999px;padding:1px 8px;font-size:11px;font-weight:600;margin-left:6px}
</style></head><body><div class="wrap">
<header><h1>Anruf-Leitstand</h1>
<button class="sicher-btn" id="sicherBtn" title="Namen, Nummern, Anliegen und Transkripte unscharf - fuer Bildschirmaufnahmen/Screenshots">🙈 Sicher-Modus</button>
<button class="archiv-btn" id="archivBtn" title="Erledigte Anrufe vom Board raeumen (simuliert - nichts wird geloescht oder exportiert)">🗄️ Tag archivieren</button>
<span class="stand" id="stand">…</span>
<select class="modell-select" id="modellSelect" title="Kategorisierungs-Modell fuer neue Anrufe - Aenderung greift sofort, kein Neustart noetig"></select>
</header>
<div id="verlauf"></div>
<div id="hinweise"></div>
<div class="notfall-banner" id="notfallBanner">🚨 <span id="notfallText"></span></div>
<div class="board" id="board"></div>
<div class="dienste-kopf">Dienste</div>
<div class="dienste" id="dienste"></div>
</div>
<div class="toast" id="toast"></div>
<script>
const FARBE={notfall:'#d93a34',hoch:'#e08a2a',normal:'#c8a227',niedrig:'#1f9d5c'};
const KAT={termin:'Termin',rezept:'Rezept',ueberweisung:'Überweisung',befund:'Befund',
verwaltung:'Verwaltung',beschwerden:'Beschwerden',notfall:'Notfall',rueckruf:'Rückruf',sonstiges:'Sonstiges'};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let STAPEL=[], ANRUFE=[], offeneTranskripte=new Set(), pausiert=false;

async function hole(p){try{const r=await fetch(p);return r.ok?await r.json():null}catch(e){return null}}

async function verschiebe(id,ziel){
  // sofort anzeigen, dann speichern - fuehlt sich unmittelbar an
  const a=ANRUFE.find(x=>x.id===id); if(!a||a.stapel===ziel)return;
  a.stapel=ziel; zeichneBoard();
  const r=await fetch('/api/stapel',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,stapel:ziel})});
  const d=await r.json().catch(()=>null);
  if(!d||!d.ok){takt()}   // hat nicht geklappt: echten Stand neu holen
}

// Nur Ziffern und ein fuehrendes Plus taugen fuer tel:
const waehlbar=n=>String(n||'').replace(/[^\d+]/g,'').replace(/(?!^)\+/g,'');

function nummerLink(d){
  const n=d.nummer, w=waehlbar(n);
  if(!w)return '—';
  return `<a class="waehlen" href="tel:${esc(w)}" data-id="${esc(d.id)}"
           title="Rückruf an ${esc(n)} — verschiebt die Karte in ${esc(STAPEL[1]||'')}">${esc(n)}</a>`;
}

function karte(d){
  // Notfall zaehlt als Notfall, auch wenn aeltere Datensaetze die Dringlichkeit
  // niedriger gesetzt haben.
  const f=(d.kategorie==='notfall')?FARBE.notfall:(FARBE[d.dringlichkeit]||'#888');
  const i=STAPEL.indexOf(d.stapel);
  const knoepfe=STAPEL.map((s,j)=>j===i?'':
    `<button data-id="${esc(d.id)}" data-ziel="${esc(s)}">${j<i?'←':'→'} ${esc(s)}</button>`).join('');
  const offen=offeneTranskripte.has(d.id)?' open':'';
  // Notfall, solange nicht im letzten Stapel (= erledigt) - pulsiert als
  // Blickfang, siehe pruefeNotfall() fuer den zugehoerigen Minuten-Ton.
  const notfallOffen=d.dringlichkeit==='notfall'&&d.stapel!==STAPEL[STAPEL.length-1];
  // Name kommt vom LLM aus dem Transkript (Vermutung, kein Abgleich) - im
  // Unterschied zum kontakt-badge (echter Rufnummer-Abgleich gegen das
  // Adressbuch). Quelle sichtbar machen, damit ein spaeterer Abgleich beider
  // (Text vs. Telefon) auf unterscheidbaren Daten aufsetzen kann.
  const quelleTag=d.name?`<span class="tag" title="Name aus dem Sprach-Transkript erkannt (Ollama) - nicht verifiziert">📝 Text</span>`:'';
  return `<article class="karte${notfallOffen?' notfall-offen':''}" draggable="true" data-id="${esc(d.id)}" style="--akzent:${f}">
    <div class="kopf"><span class="titel">${esc(KAT[d.kategorie]||d.kategorie)} · ${esc(d.name||'unbekannt')}</span> ${quelleTag}
    <span class="dring" style="background:${f}">${esc(d.kategorie==='notfall'?'notfall':d.dringlichkeit)}</span></div>
    <div class="meta">${esc(d.empfangen.replace('T',' '))} · ${nummerLink(d)}${d.rueckruf?' · 📞 Rückruf':''}${d.kontakt?`<span class="kontakt-badge" title="Name via Rufnummer-Abgleich gegen das Adressbuch bestaetigt">✓ ${esc(d.kontakt)}</span>`:''}</div>
    <p class="anliegen">${esc(d.anliegen)}</p>
    <div>${(d.stichworte||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join('')}</div>
    ${d.audio?`<audio controls preload="none" src="${d.audio}"></audio>`:''}
    <details data-id="${esc(d.id)}"${offen}><summary>Transkript</summary><p class="tr">${esc(d.transkript)}</p></details>
    <div class="schieber">${knoepfe}</div>
  </article>`;
}

function zeichneBoard(){
  const el=document.getElementById('board');
  el.innerHTML=STAPEL.map(s=>{
    const drin=ANRUFE.filter(a=>a.stapel===s);
    return `<section class="spalte" data-stapel="${esc(s)}">
      <h2>${esc(s)}<span class="zahl">${drin.length}</span></h2>
      ${drin.map(karte).join('')||'<p class="leer">leer</p>'}
    </section>`}).join('');

  el.querySelectorAll('.schieber button').forEach(b=>
    b.onclick=e=>{e.stopPropagation();verschiebe(b.dataset.id,b.dataset.ziel)});
  el.querySelectorAll('a.waehlen').forEach(a=>
    a.onclick=()=>{                       // der Link waehlt, wir ziehen nur nach
      const d=ANRUFE.find(x=>x.id===a.dataset.id);
      if(d&&STAPEL[1]&&d.stapel===STAPEL[0])verschiebe(d.id,STAPEL[1]);
    });
  el.querySelectorAll('details').forEach(d=>{
    d.ontoggle=()=>{d.open?offeneTranskripte.add(d.dataset.id):offeneTranskripte.delete(d.dataset.id)};
  });
  el.querySelectorAll('.karte').forEach(k=>{
    k.ondragstart=e=>{pausiert=true;k.classList.add('zieht');e.dataTransfer.setData('text/plain',k.dataset.id)};
    k.ondragend=()=>{pausiert=false;k.classList.remove('zieht')};
  });
  el.querySelectorAll('.spalte').forEach(sp=>{
    sp.ondragover=e=>{e.preventDefault();sp.classList.add('ueber')};
    sp.ondragleave=()=>sp.classList.remove('ueber');
    sp.ondrop=e=>{e.preventDefault();sp.classList.remove('ueber');pausiert=false;
      verschiebe(e.dataTransfer.getData('text/plain'),sp.dataset.stapel)};
  });
}

// Notfall-Alarm: solange mindestens ein Notfall offen ist (nicht im letzten
// Stapel), pulsiert die Karte (CSS) und alle 60s ein doppelter Piepton -
// zusaetzlich sofort einmal, wenn ein Notfall neu auftaucht. Browser
// blockieren Audio ohne vorherigen Klick auf die Seite (Autoplay-Policy) -
// deshalb der einmalige 'click'-Listener unten, der den AudioContext oeffnet.
let audioCtx=null, notfallOffenVorher=false;
function audioFreischalten(){
  try{audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)()}catch(e){}
}
document.addEventListener('click',audioFreischalten,{once:true});

function piepton(){
  if(!audioCtx)return;   // noch nicht freigeschaltet - naechster Klick holt es nach
  const einzelton=(zeit,frequenz)=>{
    const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.type='square'; o.frequency.value=frequenz;
    o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.0001,zeit);
    g.gain.exponentialRampToValueAtTime(0.28,zeit+0.02);
    g.gain.exponentialRampToValueAtTime(0.0001,zeit+0.45);
    o.start(zeit); o.stop(zeit+0.46);
  };
  const jetzt=audioCtx.currentTime;
  einzelton(jetzt,880);
  einzelton(jetzt+0.3,1100);
}

function pruefeNotfall(){
  const letzterStapel=STAPEL[STAPEL.length-1];
  const offen=ANRUFE.filter(a=>a.dringlichkeit==='notfall'&&a.stapel!==letzterStapel);
  const banner=document.getElementById('notfallBanner');
  banner.classList.toggle('zeigen',offen.length>0);
  if(offen.length){
    document.getElementById('notfallText').textContent=offen.length===1
      ?'1 offene Notfall-Meldung'
      :`${offen.length} offene Notfall-Meldungen`;
    if(!notfallOffenVorher)piepton();   // sofort beim erstmaligen Auftreten
  }
  notfallOffenVorher=offen.length>0;
}
setInterval(()=>{if(notfallOffenVorher)piepton()},60000);

function zeichneStatus(s){
  if(!s)return;
  document.getElementById('stand').textContent='Stand '+s.stand;
  document.getElementById('dienste').innerHTML=s.dienste.map(d=>
    `<div class="dienst"><span class="punkt ${d.zustand}"></span>
     <span><b>${esc(d.name)}</b><span>${esc(d.text)}</span></span></div>`).join('');
  const h=[];
  // Die Ampeln stehen unten - eine Stoerung muss trotzdem sofort auffallen.
  s.dienste.filter(d=>d.zustand==='schlecht'||d.zustand==='aus')
           .forEach(d=>h.push(`${d.name}: ${d.text}`));
  if(s.eingang_offen>0)h.push(`${s.eingang_offen} Aufnahme(n) warten auf Verarbeitung.`);
  if(s.fehler>0)h.push(`${s.fehler} Aufnahme(n) in telefon/fehler — nicht verarbeitet.`);
  document.getElementById('hinweise').innerHTML=h.map(t=>`<div class="hinweis">${esc(t)}</div>`).join('');
}

function zeichneVerlauf(v){
  if(!v)return;
  const el=document.getElementById('verlauf');
  const unten=el.scrollHeight-el.scrollTop-el.clientHeight<40;
  el.innerHTML=v.map(e=>`<div class="z"><time>${esc((e.zeit||'').slice(11,19))}</time>
    <span class="a a-${esc(e.art)}">${esc(e.art)}</span><span>${esc(e.text)}</span></div>`).join('')
    ||'<p class="leer">Noch nichts passiert.</p>';
  if(unten)el.scrollTop=el.scrollHeight;
}

async function takt(){
  if(pausiert)return;            // nicht neu zeichnen, waehrend jemand zieht
  // Auch nicht neu zeichnen, waehrend eine Aufnahme laeuft - zeichneBoard()
  // ersetzt die Karten per innerHTML und reisst damit jedes spielende
  // <audio>-Element mitten in der Wiedergabe weg (Abbruch nach <3s).
  if(document.querySelector('#board audio:not([paused])'))return;
  const [s,a,v]=await Promise.all([hole('/api/status'),hole('/api/anrufe'),hole('/api/verlauf')]);
  zeichneStatus(s);
  if(a){STAPEL=a.stapel;ANRUFE=a.anrufe;zeichneBoard();pruefeNotfall()}
  zeichneVerlauf(v);
}
let toastZeit=null;
function toast(text){
  const el=document.getElementById('toast');
  el.textContent=text; el.classList.add('zeigen');
  clearTimeout(toastZeit);
  toastZeit=setTimeout(()=>el.classList.remove('zeigen'),3500);
}

// Sicher-Modus merkt sich den Zustand pro Browser (localStorage) - beim
// naechsten Aufruf (z.B. vor einer Bildschirmaufnahme) nicht vergessen.
const sicherBtn=document.getElementById('sicherBtn');
function sicherSetzen(an){
  document.body.classList.toggle('sicher',an);
  sicherBtn.classList.toggle('aktiv',an);
  try{localStorage.setItem('sicherModus',an?'1':'0')}catch(e){}
}
try{sicherSetzen(localStorage.getItem('sicherModus')==='1')}catch(e){}
sicherBtn.onclick=()=>sicherSetzen(!document.body.classList.contains('sicher'));

// Kategorisierungs-Modell (Dropdown oben rechts) - Wechsel greift sofort
// beim naechsten Anruf, kein Neustart des Watchers noetig (siehe
// pipe/modellwahl.py, geteilte Zustandsdatei).
async function modellLaden(){
  const d=await hole('/api/modell'); if(!d)return;
  const sel=document.getElementById('modellSelect');
  sel.innerHTML=d.optionen.map(o=>
    `<option value="${esc(o.id)}"${o.id===d.aktuell?' selected':''}>${esc(o.label)}</option>`).join('');
}
document.getElementById('modellSelect').onchange=async e=>{
  const label=e.target.selectedOptions[0].textContent;
  const r=await fetch('/api/modell',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:e.target.value})});
  const d=await r.json().catch(()=>null);
  toast(d&&d.ok?`Modell gewechselt: ${label}`:'Umschalten fehlgeschlagen');
};
modellLaden();

document.getElementById('archivBtn').onclick=async()=>{
  const btn=document.getElementById('archivBtn');
  const letzter=STAPEL[STAPEL.length-1];
  const anzahl=ANRUFE.filter(a=>a.stapel===letzter).length;
  if(!anzahl){toast(`Nichts zu archivieren — ${letzter||'letzter Stapel'} ist leer.`);return}
  if(!confirm(`${anzahl} Anruf(e) aus "${letzter}" archivieren? (simuliert — nichts wird geloescht)`))return;
  btn.disabled=true;
  const r=await fetch('/api/archivieren',{method:'POST'});
  const d=await r.json().catch(()=>null);
  btn.disabled=false;
  if(d&&d.ok){toast(`✓ ${d.anzahl} Anruf(e) archiviert (simuliert)`);takt()}
  else{toast('Archivieren fehlgeschlagen.')}
};

takt();setInterval(takt,3000);
</script></body></html>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Live-Dashboard der Anruf-Pipe.")
    p.add_argument("--port", type=int, default=config.LEITSTAND_PORT)
    p.add_argument("--host", default=config.LEITSTAND_HOST,
                   help="0.0.0.0 macht den Leitstand im Netz erreichbar (dann ist "
                        "LEITSTAND_PASS Pflicht)")
    args = p.parse_args(argv)

    oeffentlich = args.host not in {"127.0.0.1", "localhost", "::1"}
    if oeffentlich and not config.LEITSTAND_PASS:
        print("Abbruch: Der Leitstand soll über das Netz erreichbar sein, aber es ist\n"
              "kein LEITSTAND_PASS gesetzt. Auf den Seiten stehen Transkripte und\n"
              "Aufnahmen von Patienten — ohne Passwort läge das für jeden im Netz offen.\n"
              "Passwort in die .env eintragen und erneut starten.", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    schutz = "mit Passwort" if config.LEITSTAND_PASS else "ohne Passwort (nur lokal)"
    print(f"Leitstand: http://{args.host}:{args.port}  [{schutz}]  — Abbruch mit Strg-C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLeitstand beendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
