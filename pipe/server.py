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

from . import bewertung, config, dashboard, export, modellwahl, protokoll, stapel, store

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
            "rueckrufnummer": e.get("rueckrufnummer"),
            "email": e.get("email"),
            "email_korrigiert": bool(e.get("email_vermutete_korrektur")),
            "rueckruf": bool(e.get("rueckruf_gewuenscht")),
            "anliegen": e.get("anliegen_kurz") or "",
            "stichworte": e.get("stichworte") or [],
            "transkript": d.get("transkript") or "",
            "audio": audio,
            "kontakt": (d.get("bekannter_kontakt") or {}).get("name"),
            "bewertung": (bewertung.lies(config.ABLAGE_LOKAL / ordner) or {}).get("bewertung") if ordner else None,
        })
    return liste


def archiviere_tag() -> int:
    """Markiert alle 'Erledigt'-Anrufe als archiviert (simuliert - es wird
    nichts geloescht, nur vom Board geraeumt). Bewertete Anrufe werden dabei
    zusaetzlich als Trainingsdaten exportiert, siehe pipe.export. Gibt die
    Anzahl archivierter Anrufe zurueck."""
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
            _, export_anzahl = export.exportiere()
            if export_anzahl:
                protokoll.schreibe("export", f"{export_anzahl} bewertete Anrufe exportiert")
            # Verlauf-Log sichern + leeren, fuer eine frische Ansicht ab jetzt.
            # audit.log (dauerhaft, unveraenderlich) ist davon nicht betroffen.
            log_archiv = protokoll.archiviere_und_leere()
            if log_archiv:
                protokoll.schreibe("system", f"Verlauf archiviert nach {log_archiv.name} und geleert")
            self._json({"ok": True, "anzahl": anzahl, "simuliert": True, "export_anzahl": export_anzahl,
                        "log_archiviert": log_archiv.name if log_archiv else None})
            return
        if pfad == "/api/export":
            ziel, anzahl = export.exportiere()
            protokoll.schreibe("export", f"{anzahl} bewertete Anrufe exportiert nach {ziel.name}")
            self._json({"ok": True, "anzahl": anzahl, "datei": ziel.name})
            return
        if pfad == "/api/bewertung":
            try:
                laenge = int(self.headers.get("Content-Length") or 0)
                wunsch = json.loads(self.rfile.read(min(laenge, 10_000)).decode("utf-8"))
                kennung, wert = wunsch["id"], wunsch["bewertung"]
                korrektur = wunsch.get("korrektur")
            except Exception:
                self._json({"ok": False, "fehler": "ungültige Anfrage"})
                return
            ordner = stapel.ordner_zu(kennung)
            if ordner is None:
                self._json({"ok": False, "fehler": "Anruf nicht gefunden"})
                return
            if not bewertung.setze(ordner, wert, korrektur):
                self._json({"ok": False, "fehler": f"ungültige Bewertung: {wert}"})
                return
            protokoll.schreibe("bewertung", f"{ordner.name} → {wert}"
                               + (f" (Korrektur: {korrektur})" if korrektur else ""))
            self._json({"ok": True})
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
<title>Logpy:AgentOne — Anruf-Leitstand</title>
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
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.titelblock{display:flex;flex-direction:column;gap:1px}
h1{font-size:19px;margin:0;line-height:1.25}
.untertitel{font-size:12px;color:var(--muted);font-weight:400}
.logo{width:34px;height:34px;border-radius:50%;flex:none;box-shadow:0 1px 3px rgba(0,0,0,.15)}
.stand{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums;margin-left:auto}
.modell-select{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 10px;font-size:13px;font-family:inherit;cursor:pointer;max-width:280px}
.verbindung-banner{display:none;align-items:center;gap:9px;background:var(--unklar);color:#1a1500;
font-weight:700;font-size:14px;padding:9px 14px;border-radius:9px;margin:9px 0}
.verbindung-banner.zeigen{display:flex}
.fuss{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:26px;padding-top:14px;
border-top:1px solid var(--rand);color:var(--muted);font-size:12.5px}
.fuss a{display:inline-flex;align-items:center;gap:5px;color:var(--muted);text-decoration:none}
.fuss a:hover{color:var(--fg);text-decoration:underline}
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
.export-btn{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit;margin-left:8px}
.export-btn:hover{border-color:var(--muted)}
.bewertung{display:flex;align-items:center;gap:6px;margin-top:8px}
.bewertung button{font-size:15px;background:var(--bg);border:1px solid var(--rand);border-radius:7px;
padding:3px 8px;cursor:pointer;line-height:1;opacity:.55}
.bewertung button:hover{opacity:1;border-color:var(--muted)}
.bw-gut.aktiv{opacity:1;background:color-mix(in srgb,var(--gut) 18%,var(--bg));border-color:var(--gut)}
.bw-schlecht.aktiv{opacity:1;background:color-mix(in srgb,var(--schlecht) 18%,var(--bg));border-color:var(--schlecht)}
.bw-korrektur{display:none;align-items:center;gap:5px}
.bw-korrektur.zeigen{display:flex}
.bw-korrektur select{font:inherit;font-size:12px;padding:2px 4px;border-radius:6px;
border:1px solid var(--rand);background:var(--bg);color:var(--fg)}
.bw-korrektur button{font-size:12px;padding:3px 8px;opacity:1;background:var(--gut);
color:#fff;border-color:var(--gut)}
.sicher-btn{background:var(--karte);border:1px solid var(--rand);color:var(--fg);border-radius:8px;
padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit;margin-right:8px}
.sicher-btn:hover{border-color:var(--muted)}
.sicher-btn.aktiv{background:var(--gut);border-color:var(--gut);color:#fff}
/* Sicher-Modus: Patientendaten unscharf, fuer Bildschirmaufnahmen/Screenshots.
   Struktur (Kategorie, Dringlichkeit, Zeit, Stapel) bleibt lesbar - nur was
   auf eine Person zurueckfuehrt wird geblurrt. */
body.sicher a.waehlen{filter:blur(6px);user-select:none}
body.sicher .tel-blur{filter:blur(6px);user-select:none}
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
a.mail{color:inherit;text-decoration:underline;text-decoration-style:dotted;
text-underline-offset:2px;cursor:pointer;word-break:break-all}
a.mail:hover{color:var(--gut);text-decoration-style:solid}
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
height:180px;overflow-y:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
scrollbar-width:thin;scrollbar-color:var(--muted) transparent}
#verlauf::-webkit-scrollbar{width:8px}
#verlauf::-webkit-scrollbar-track{background:transparent}
#verlauf::-webkit-scrollbar-thumb{background:var(--muted);border-radius:99px;border:2px solid var(--log)}
#verlauf::-webkit-scrollbar-thumb:hover{background:var(--fg)}
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
<header><img class="logo" src="data:image/png;base64,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" alt=""><div class="titelblock"><h1>Logpy:AgentOne</h1><span class="untertitel">Anruf-Leitstand</span></div>
<button class="sicher-btn" id="sicherBtn" title="Namen, Nummern, Anliegen und Transkripte unscharf - fuer Bildschirmaufnahmen/Screenshots">🙈 Sicher-Modus</button>
<button class="archiv-btn" id="archivBtn" title="Erledigte Anrufe vom Board raeumen (simuliert, nichts wird geloescht) - bewertete Anrufe werden als Trainingsdaten exportiert, das Verlauf-Log wird gesichert und geleert">🗄️ Tag archivieren</button>
<button class="export-btn" id="exportBtn" title="Bewertete Anrufe (👍/👎) als JSONL-Trainingsdaten exportieren, unabhaengig vom Archivieren">📤 Bewertungen exportieren</button>
<span class="stand" id="stand">…</span>
<select class="modell-select" id="modellSelect" title="Kategorisierungs-Modell fuer neue Anrufe - Aenderung greift sofort, kein Neustart noetig"></select>
</header>
<div class="dienste-kopf">Dienste</div>
<div class="dienste" id="dienste"></div>
<div id="verlauf"></div>
<div class="verbindung-banner" id="verbindungBanner">📡 <span id="verbindungText"></span></div>
<div id="hinweise"></div>
<div class="notfall-banner" id="notfallBanner">🚨 <span id="notfallText"></span></div>
<div class="board" id="board"></div>
<footer class="fuss">
  <span>Logpy:AgentOne von Dilles</span>
  <a href="https://github.com/Jeuners/fonagent-one" target="_blank" rel="noopener">
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
    0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58
    1.32.82.77 1.3 2.01.93 2.5.71.08-.55.3-.93.55-1.14-1.9-.22-3.9-.95-3.9-4.22 0-.93.33-1.7.87-2.29-.09-.22-.38-1.09.08-2.27
    0 0 .71-.23 2.34.87.68-.19 1.4-.29 2.12-.29.72 0 1.44.1 2.12.29 1.63-1.1 2.34-.87 2.34-.87.46 1.18.17 2.05.08 2.27.54.59.87
    1.36.87 2.29 0 3.28-2 4-3.9 4.22.31.27.58.79.58 1.6 0 1.15-.01 2.08-.01 2.36 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
    Projekt auf GitHub
  </a>
  <a href="https://paypal.me/jeuner" target="_blank" rel="noopener">☕ Unterstützen</a>
</footer>
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

// Bewertung dient gleichzeitig als Trainingsdaten (siehe pipe.export) - 👍/👎
// pro Anruf, bei 👎 optional die richtige Kategorie/Dringlichkeit dazu.
async function bewerte(id,wert,korrektur){
  const a=ANRUFE.find(x=>x.id===id); if(a)a.bewertung=wert;
  const r=await fetch('/api/bewertung',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,bewertung:wert,korrektur:korrektur||null})});
  const d=await r.json().catch(()=>null);
  toast(d&&d.ok?`Bewertung gespeichert (${wert==='gut'?'👍':'👎'})`:'Bewertung fehlgeschlagen');
  if(a)zeichneBoard();
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
  // Genannte Rueckrufnummer nur zeigen, wenn sie sich von der CLIP-Nummer
  // unterscheidet - sonst reine Wiederholung. Genau der Fall (Anruf von
  // fremdem Anschluss, Rueckruf auf eigener Nummer gewuenscht) ging bisher
  // im UI unter, obwohl er extrahiert wird (auswertung.rueckrufnummer).
  const abweichend=d.rueckrufnummer&&waehlbar(d.rueckrufnummer)!==waehlbar(d.nummer);
  const rueckrufZeile=abweichend?`<div class="meta">☎️ Rückruf an: ${nummerLink({...d,nummer:d.rueckrufnummer})}
    <span class="tag" title="Nummer aus dem Sprach-Transkript erkannt (Ollama) - nicht verifiziert">📝 Text</span></div>`:'';
  // email_korrigiert: Whisper transkribiert gesprochenes "at" beim Diktieren
  // manchmal als Punkt statt "@" (z.B. "gdg.dillenberg.net" statt
  // "gdg@dillenberg.net") - categorize.py repariert das als Vorschlag, aber
  // mehrdeutig (koennte auch eine genannte Subdomain sein), deshalb hier
  // sichtbar als Vermutung markiert statt stillschweigend uebernommen.
  const emailZeile=d.email?`<div class="meta">📧 <a class="mail" href="mailto:${esc(d.email)}">${esc(d.email)}</a>
    ${d.email_korrigiert?`<span class="tag" title="Whisper hat das @ vermutlich als Punkt transkribiert - automatisch repariert, bitte pruefen">⚠️ vermutete Korrektur</span>`
      :`<span class="tag" title="E-Mail aus dem Sprach-Transkript erkannt (Ollama) - nicht verifiziert">📝 Text</span>`}</div>`:'';
  return `<article class="karte${notfallOffen?' notfall-offen':''}" draggable="true" data-id="${esc(d.id)}" style="--akzent:${f}">
    <div class="kopf"><span class="titel">${esc(KAT[d.kategorie]||d.kategorie)} · ${esc(d.name||'unbekannt')}</span> ${quelleTag}
    <span class="dring" style="background:${f}">${esc(d.kategorie==='notfall'?'notfall':d.dringlichkeit)}</span></div>
    <div class="meta">${esc(d.empfangen.replace('T',' '))} · ${nummerLink(d)}${d.rueckruf?' · 📞 Rückruf':''}${d.kontakt?`<span class="kontakt-badge" title="Name via Rufnummer-Abgleich gegen das Adressbuch bestaetigt">✓ ${esc(d.kontakt)}</span>`:''}</div>
    ${rueckrufZeile}
    ${emailZeile}
    <p class="anliegen">${esc(d.anliegen)}</p>
    <div>${(d.stichworte||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join('')}</div>
    ${d.audio?`<audio controls preload="none" src="${d.audio}"></audio>`:''}
    <details data-id="${esc(d.id)}"${offen}><summary>Transkript</summary><p class="tr">${esc(d.transkript)}</p></details>
    <div class="bewertung" data-id="${esc(d.id)}">
      <button class="bw-gut${d.bewertung==='gut'?' aktiv':''}" data-wert="gut" title="Kategorisierung war korrekt - dient als Trainingsdaten">👍</button>
      <button class="bw-schlecht${d.bewertung==='schlecht'?' aktiv':''}" data-wert="schlecht" title="Kategorisierung war falsch - optional korrigieren">👎</button>
      <span class="bw-korrektur">
        <select class="bw-kat"></select>
        <select class="bw-dring"></select>
        <button class="bw-speichern">Speichern</button>
      </span>
    </div>
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
  el.querySelectorAll('.bewertung').forEach(bw=>{
    const id=bw.dataset.id, korrektur=bw.querySelector('.bw-korrektur');
    const selKat=bw.querySelector('.bw-kat'), selDring=bw.querySelector('.bw-dring');
    selKat.innerHTML='<option value="">Kategorie unverändert</option>'
      +Object.keys(KAT).map(k=>`<option value="${k}">${esc(KAT[k])}</option>`).join('');
    selDring.innerHTML='<option value="">Dringlichkeit unverändert</option>'
      +['niedrig','normal','hoch','notfall'].map(x=>`<option value="${x}">${x}</option>`).join('');
    bw.querySelector('.bw-gut').onclick=e=>{e.stopPropagation();bewerte(id,'gut')};
    bw.querySelector('.bw-schlecht').onclick=e=>{e.stopPropagation();korrektur.classList.toggle('zeigen')};
    bw.querySelector('.bw-speichern').onclick=e=>{
      e.stopPropagation();
      const k={};
      if(selKat.value)k.kategorie=selKat.value;
      if(selDring.value)k.dringlichkeit=selDring.value;
      bewerte(id,'schlecht',Object.keys(k).length?k:null);
    };
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

// Log-Zeilen enthalten oft die Anruf-ID (z.B. "23-44-01_004915223062462" -
// Zeitstempel + Rufnummer zusammen, aus ordner.name). Nur die Ziffernfolge
// ab 9 Stellen blurren (Rufnummern), nicht Datum/Uhrzeit-Anteile (<=8 Ziffern
// in diesem Format) und nicht den restlichen Log-Text.
const telBlur=s=>s.replace(/\d{9,}/g,m=>`<span class="tel-blur">${m}</span>`);

function zeichneVerlauf(v){
  if(!v)return;
  const el=document.getElementById('verlauf');
  const unten=el.scrollHeight-el.scrollTop-el.clientHeight<40;
  el.innerHTML=v.map(e=>`<div class="z"><time>${esc((e.zeit||'').slice(11,19))}</time>
    <span class="a a-${esc(e.art)}">${esc(e.art)}</span><span>${telBlur(esc(e.text))}</span></div>`).join('')
    ||'<p class="leer">Noch nichts passiert.</p>';
  if(unten)el.scrollTop=el.scrollHeight;
}

// Verbindungsverlust sichtbar machen statt still zu verharren: hole()
// schluckt Netzwerkfehler und gibt nur null zurueck - ohne dieses Tracking
// sah man dem Board nicht an, dass es seit X Minuten nicht mehr aktualisiert
// wurde. Erst ab 2 aufeinanderfolgenden Fehlversuchen zeigen (~6s), damit ein
// einzelner kurzer Netz-Hakler nicht sofort Alarm ausloest.
let verbindungsFehler=0, letzteAktualisierung=null;
function zeichneVerbindung(){
  const banner=document.getElementById('verbindungBanner');
  banner.classList.toggle('zeigen',verbindungsFehler>=2);
  if(verbindungsFehler>=2){
    const seit=letzteAktualisierung?letzteAktualisierung.toLocaleTimeString('de-DE'):'nie';
    document.getElementById('verbindungText').textContent=
      `Keine Verbindung zum Server — zuletzt aktualisiert: ${seit}`;
  }
}

async function takt(){
  if(pausiert)return;            // nicht neu zeichnen, waehrend jemand zieht
  // Auch nicht neu zeichnen, waehrend eine Aufnahme laeuft - zeichneBoard()
  // ersetzt die Karten per innerHTML und reisst damit jedes spielende
  // <audio>-Element mitten in der Wiedergabe weg (Abbruch nach <3s).
  // BUGFIX: [paused] ist ein HTML-Attribut, das <audio> nie gesetzt hat - der
  // Abspielzustand ist eine reine JS/DOM-Eigenschaft (.paused). Der Selektor
  // "audio:not([paused])" traf deshalb auf JEDES Audio-Element zu, egal ob
  // etwas lief - das Board hat sich praktisch nie mehr neu gezeichnet, sobald
  // irgendeine Karte eine Aufnahme hatte (also fast immer).
  if([...document.querySelectorAll('#board audio')].some(a=>!a.paused))return;
  // Gleiches Problem wie beim Audio-Player: zeichneBoard() baut die Karten
  // per innerHTML komplett neu, das reisst eine offene 👎-Korrektur-Auswahl
  // wieder weg, bevor man sie ausfuellen kann ("erscheint, verschwindet
  // wieder"). Nicht neu zeichnen, solange eine offen ist.
  if(document.querySelector('.bw-korrektur.zeigen'))return;
  const [s,a,v]=await Promise.all([hole('/api/status'),hole('/api/anrufe'),hole('/api/verlauf')]);
  if(s===null&&a===null&&v===null){verbindungsFehler++}
  else{verbindungsFehler=0;letzteAktualisierung=new Date()}
  zeichneVerbindung();
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
  if(d&&d.ok){
    let text=`✓ ${d.anzahl} Anruf(e) archiviert (simuliert)`;
    if(d.export_anzahl)text+=` · ${d.export_anzahl} bewertete Anrufe exportiert`;
    if(d.log_archiviert)text+=' · Verlauf gesichert & geleert';
    toast(text);takt();
  } else{toast('Archivieren fehlgeschlagen.')}
};

document.getElementById('exportBtn').onclick=async()=>{
  const bewertete=ANRUFE.filter(a=>a.bewertung).length;
  if(!confirm(`${bewertete} bewertete Anrufe auf dem aktuellen Board gefunden. `
    +'Export ist für externe Auswertung gedacht (z.B. Modell-Verbesserung) — '
    +'Transkripte und Namen der bewerteten Anrufe verlassen damit den Rechner. Fortfahren?'))return;
  const r=await fetch('/api/export',{method:'POST'});
  const d=await r.json().catch(()=>null);
  toast(d&&d.ok?`✓ ${d.anzahl} Anruf(e) exportiert → training/${d.datei}`:'Export fehlgeschlagen');
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
