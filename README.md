# Praxis-Telefon-Agent

Nimmt Anrufe (Sprachnachrichten) einer Hausarztpraxis entgegen, **transkribiert**
sie lokal, **kategorisiert** das Anliegen und **legt** das Ergebnis strukturiert
ab (lokal und optional in Nextcloud).

**Vollständig on-prem** — keine Patientendaten verlassen den Rechner.

![Der Leitstand: Board mit Eingang, In Bearbeitung, Rückfragen und Erledigt, darüber der Verlauf, darunter die Dienste-Ampeln](docs/leitstand.png)

> **Hinweis:** Alle Namen, Rufnummern und Anliegen in diesem Repository sind
> erfunden — auch im Screenshot und in der Beispielkonfiguration. „Praxis
> Musterhausen" ist ein Platzhalter. Echte Zugangsdaten und Anrufdaten liegen
> außerhalb des Repositorys (`.env`, `ablage/`, `telefon/`).

## Stufen

1. **Die Pipe** (fertig): Audio → Transkript → Kategorie → Ablage.
2. **Telefonie** (steht): FreeSWITCH nimmt über den Plusnet-Trunk an, spielt die
   Ansage, nimmt auf und legt die Datei im Eingang der Pipe ab.
3. **Feinschliff** (offen): Löschfristen, Notfall-Alarm, Monitoring.

## Bausteine

| Datei | Aufgabe |
|---|---|
| `pipe/transcribe.py` | Spracherkennung (whisper.cpp / faster-whisper), lokal |
| `pipe/categorize.py` | Kategorisierung via Ollama/Qwen, erzwungenes JSON-Schema |
| `pipe/store.py` | Ablage lokal + Nextcloud (WebDAV, steckbar) |
| `pipe/process_call.py` | Orchestrator / CLI |
| `pipe/config.py` | Konfiguration aus `.env` |
| `pipe/watch.py` | Beobachtet den Eingang und schiebt Aufnahmen durch die Pipe |
| `pipe/server.py` | Leitstand: Live-Ansicht mit Status, Anrufen und Verlauf |
| `pipe/protokoll.py` | Ereignisprotokoll (JSONL) für den Leitstand |
| `prompts/categorize_de.txt` | Kategorisierungs-Prompt (deutsch, mehrsprachig-tauglich) |
| `telefon/` | Ansage, FreeSWITCH-Vorlagen, Start- und Einrichtungsskripte |

## Kategorien

`termin · rezept · ueberweisung · befund · verwaltung · beschwerden · notfall ·
rueckruf · sonstiges`
Dringlichkeit: `niedrig · normal · hoch · notfall`

## Voraussetzungen

- **Ollama** läuft (`ollama serve`) mit einem Qwen-Modell (`qwen3.5:latest`).
- **whisper.cpp** (`whisper-cli`) + ggml-Modell, oder `STT_BACKEND=faster` für faster-whisper.
- **ffmpeg** (Audio-Normalisierung auf 16 kHz mono).

## Nutzung

```bash
cp .env.example .env        # bei Bedarf anpassen
python3 -m pipe.process_call test/rezept.wav --nummer 021031234567
```

Ergebnis unter `ablage/JJJJ-MM-TT/HH-MM-SS_<nummer>/`:
- `meta.json` — strukturierte Felder (maschinenlesbar)
- `zusammenfassung.md` — für Menschen
- `aufnahme.<ext>` — Audiokopie

## Telefonie (Stufe 2)

FreeSWITCH registriert sich mit dem SIP-Konto der Praxis beim Provider, nimmt
eingehende Anrufe an, spielt die Ansage und nimmt auf. Die Aufnahme landet unter
`telefon/eingang/JJJJMMTT-HHMMSS_<nummer>.wav`; `pipe.watch` erkennt sie, schiebt
sie durch die Pipe und räumt sie nach `telefon/verarbeitet/` weg.

```bash
./telefon/ansage_bauen.sh              # Ansage aus telefon/ansage.txt erzeugen
./telefon/freeswitch/einrichten.sh     # Trunk + Dialplan in FreeSWITCH eintragen
./telefon/starten.sh                   # startet FreeSWITCH, Verarbeitung, Leitstand
```

Danach läuft der **Leitstand** unter <http://127.0.0.1:8088> — das Arbeitsbrett
der Praxis, unabhängig von Nextcloud:

- Ampeln für Telefon, Verarbeitung, Spracherkennung und Nextcloud
- die Anrufe als Board mit den Spalten `Eingang · In Bearbeitung · Rückfragen ·
  Erledigt`, verschiebbar per Ziehen oder über die Knöpfe auf der Karte
- je Karte Kategorie, Anrufer, Dringlichkeit als Farbe, Anliegen, Stichworte,
  Abspielknopf für die Aufnahme und das Transkript zum Aufklappen
- die Rufnummer ist anklickbar (`tel:`) und startet den Rückruf in der
  Telefon-App des Rechners; liegt die Karte noch im Eingang, wandert sie dabei
  nach `In Bearbeitung`
- darunter der laufende Verlauf — vom Klingeln über die Erkennung bis zur Ablage

Standardmäßig hört der Leitstand nur auf `127.0.0.1`. Soll er von anderen
Rechnern der Praxis erreichbar sein, `LEITSTAND_HOST=0.0.0.0` setzen — dann ist
`LEITSTAND_PASS` Pflicht, sonst startet er nicht: die Seiten zeigen Transkripte
und Aufnahmen von Patienten, das darf nicht offen im Netz stehen. Der Schutz
gilt auch für die Schnittstellen und die Audiodateien.

Die Seite aktualisiert sich alle drei Sekunden; während man eine Karte zieht,
pausiert das. Der Bearbeitungsstand liegt als `stapel.json` im jeweiligen
Anrufordner, also lokal — die Übertragung nach Nextcloud-Deck kommt später.

Der SIP-Zugang steht in der `.env` (`SIP_USER`, `SIP_DOMAIN`, `SIP_PASS`). Das
Einrichtungsskript trägt das Passwort in die FreeSWITCH-Konfiguration ein und
setzt deren Rechte auf 600 — im Git liegen nur Vorlagen mit Platzhaltern.

Die Ansage nennt den Notruf 112 und weist auf die Aufzeichnung hin, bevor der
Signalton kommt — beides ist bei einer Arztpraxis Pflicht. Text ändern:
`telefon/ansage.txt` bearbeiten, `ansage_bauen.sh` erneut ausführen.

Gesprochen wird lokal, wahlweise mit **Piper** (natürlicher, Modelle unter
`~/piper-voices`) oder dem macOS-`say` als Rückfall. Die Stimme
`de_DE-thorsten_emotional-medium` kennt acht Sprechweisen, wählbar über
`ANSAGE_EMOTION`: 0 amused · 1 angry · 2 disgusted · 3 drunk · 4 neutral ·
5 sleepy · 6 surprised · 7 whisper.

## Nextcloud aktivieren

`NEXTCLOUD_URL`, `NEXTCLOUD_USER`, `NEXTCLOUD_PASS` in `.env` setzen
(App-Passwort, **nicht** das Login-Passwort). Dann lädt jeder Anruf zusätzlich
nach `NEXTCLOUD_ORDNER/JJJJ-MM-TT/…`. Fehlt die Konfiguration, bleibt alles lokal.

## Deck-Board

Jeder Anruf bekommt eine Karte auf dem Board `Anrufe`. Die Stapel bilden den
Arbeitsablauf ab — `Eingang · In Bearbeitung · Rückfragen · Erledigt`, anpassbar
über `DECK_STAPEL`. Neue Anrufe landen immer im ersten Stapel; die Praxis zieht
sie weiter. Kategorie und Anrufer stehen im Kartentitel
(`17:52 · Rezept · Klaus Allofs`), die Dringlichkeit als farbiges Label. Das Board gehört dem Konto, mit dem die
Pipe arbeitet — ohne Freigabe legt sie zwar Karten an, die Praxis sieht aber ein
leeres Deck. Deshalb `DECK_TEILEN` in der `.env` auf die Nextcloud-Nutzer setzen
(kommagetrennt), die es sehen sollen; die Freigabe wird bei jedem Anruf
sichergestellt.

## Datenschutz (DSGVO)

Gesundheitsdaten — Pflichten für den Produktivbetrieb (Stufe 2):
- Aufnahme-Ansage am Gesprächsanfang.
- Notfall-Ansage zuerst (112 / 116117); der Agent gibt **keine** medizinischen Ratschläge.
- Verschlüsselung at rest, Zugriffskontrolle, Löschfristen.
- Verarbeitung lokal (Whisper + Qwen) — kein Cloud-Dienst im Datenpfad.
