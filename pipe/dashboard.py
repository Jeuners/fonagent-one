"""Erzeugt aus der lokalen Ablage eine HTML-Triage-Übersicht.

    python3 -m pipe.dashboard [ausgabe.html]

Rein lokal, liest nur die meta.json-Dateien. Notfälle stehen oben.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from . import config, kategorien

RANG = {"notfall": 0, "hoch": 1, "normal": 2, "niedrig": 3}
FARBE = {"notfall": "#E9322D", "hoch": "#E0A339", "normal": "#31CC7C", "niedrig": "#8A93A3"}
KAT_LABEL = kategorien.KATEGORIE_LABEL


def _anrufe() -> list[dict]:
    daten = []
    if not config.ABLAGE_LOKAL.is_dir():
        return daten
    for meta in config.ABLAGE_LOKAL.glob("*/*/meta.json"):
        try:
            daten.append(json.loads(meta.read_text(encoding="utf-8")))
        except Exception:
            continue
    daten.sort(key=lambda d: (RANG.get(d["auswertung"]["dringlichkeit"], 9), d["empfangen"]))
    return daten


def _karte(d: dict) -> str:
    e = d["auswertung"]
    farbe = FARBE.get(e["dringlichkeit"], "#888")
    wer = html.escape(e.get("anrufer_name") or "—")
    nummer = html.escape(d.get("anrufer_nummer") or e.get("rueckrufnummer") or "—")
    stich = " ".join(f"<span class='tag'>{html.escape(s)}</span>" for s in (e.get("stichworte") or []))
    return f"""
    <article class="karte" style="--akzent:{farbe}">
      <div class="kopf">
        <span class="kat">{html.escape(KAT_LABEL.get(e['kategorie'], e['kategorie']))}</span>
        <span class="dring" style="background:{farbe}">{html.escape(e['dringlichkeit'])}</span>
        <span class="zeit">{html.escape(d['empfangen'].replace('T', ' '))}</span>
      </div>
      <div class="wer"><strong>{wer}</strong> · <span class="nr">{nummer}</span>
        {"· 📞 Rückruf" if e.get('rueckruf_gewuenscht') else ""}</div>
      <p class="anliegen">{html.escape(e.get('anliegen_kurz') or '')}</p>
      <div class="tags">{stich}</div>
      <details><summary>Transkript</summary><p class="tr">{html.escape(d.get('transkript') or '')}</p></details>
    </article>"""


def baue(ausgabe: Path) -> Path:
    anrufe = _anrufe()
    zahl = {k: sum(1 for d in anrufe if d["auswertung"]["dringlichkeit"] == k) for k in RANG}
    zusammenfassung = " · ".join(
        f"<b style='color:{FARBE[k]}'>{zahl[k]}</b> {k}" for k in RANG if zahl[k]
    )
    karten = "\n".join(_karte(d) for d in anrufe)
    doc = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anruf-Übersicht</title>
<style>
:root{{--bg:#f6f7f9;--fg:#1a2230;--karte:#fff;--rand:#e4e8ee;--muted:#6b7688}}
@media(prefers-color-scheme:dark){{:root{{--bg:#12151b;--fg:#e7ecf3;--karte:#1b2029;--rand:#2a313d;--muted:#93a0b4}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:840px;margin:0 auto;padding:28px 18px}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:var(--muted);margin:0 0 20px;font-size:14px}}
.karte{{background:var(--karte);border:1px solid var(--rand);border-left:5px solid var(--akzent);
border-radius:12px;padding:14px 16px;margin:12px 0}}
.kopf{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}}
.kat{{font-weight:700}}.dring{{color:#fff;font-size:11px;font-weight:700;text-transform:uppercase;
padding:2px 8px;border-radius:999px;letter-spacing:.03em}}
.zeit{{margin-left:auto;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}}
.wer{{margin:2px 0 6px}}.nr{{color:var(--muted)}}
.anliegen{{margin:6px 0}}.tags{{margin:6px 0}}
.tag{{display:inline-block;background:var(--bg);border:1px solid var(--rand);border-radius:6px;
padding:1px 7px;font-size:12px;color:var(--muted);margin:2px 4px 2px 0}}
details{{margin-top:6px}}summary{{cursor:pointer;color:var(--muted);font-size:13px}}
.tr{{color:var(--muted);font-size:13px;margin:6px 0 0}}
</style></head><body><div class="wrap">
<h1>Anruf-Übersicht</h1>
<p class="sub">{len(anrufe)} Anrufe · {zusammenfassung or '—'} · lokal erzeugt aus der Ablage</p>
{karten}
</div></body></html>"""
    ausgabe.write_text(doc, encoding="utf-8")
    return ausgabe


def main() -> int:
    ziel = Path(sys.argv[1]) if len(sys.argv) > 1 else config.WURZEL / "dashboard.html"
    baue(ziel)
    print(f"Dashboard: {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
