# Mitgabe für die Einrichtung auf einem zweiten Rechner

Diese Datei ergänzt README (Einrichtung) und HANDOFF (Stand). Sie enthält das,
was auf der anderen Seite nicht im Repository ankommt: die Erfahrungswerte aus
dem ersten Volltest, die Stellen, an denen die Vorlagen unvollständig sind, und
die Reihenfolge, in der man prüft, ob es wirklich läuft.

## 1. Was im Repo *nicht* liegt

| Fehlt | Woher |
|---|---|
| `.env` | aus `.env.example` kopieren, Werte eintragen (Abschnitt 3) |
| Whisper-Modell (~1,6 GB) | `~/whisper-models/ggml-large-v3-turbo.bin`, Download siehe README |
| Ollama-Modell (~6,6 GB) | `ollama pull qwen3.5:latest` |
| Piper-Stimme | `~/piper-voices/…`, nur für die Ansage (Stufe 2) nötig |
| `ablage/`, `telefon/eingang/`, `dashboard.html` | Anruf- und Patientendaten, bleiben absichtlich lokal |
| FreeSWITCH-Konfiguration mit echtem SIP-Passwort | `telefon/freeswitch/einrichten.sh` schreibt sie aus der `.env` |

Alle Namen, Rufnummern und Beispieldaten im Repo sind erfunden. „Praxis
Musterhausen" ist Platzhalter — auch im Screenshot.

## 2. Der kurze Weg: Stufe 1 ohne Telefonie

Telefonie (FreeSWITCH, SIP-Trunk) ist für die Erprobung nicht nötig und macht
die meiste Arbeit. Für einen ersten Durchlauf reicht:

```bash
brew install ffmpeg whisper-cpp
brew install ollama && ollama serve &
ollama pull qwen3.5:latest
mkdir -p ~/whisper-models && cd ~/whisper-models
curl -LO https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
cd ~/praxis-telefon-agent && cp .env.example .env
python3 -m pipe.process_call test/rezept.wav --nummer 021031234567
```

Läuft das durch (~25 s), liegt das Ergebnis unter
`ablage/JJJJ-MM-TT/HH-MM-SS_021031234567/`. Nextcloud, Deck, Leitstand und
Telefonie sind alle abschaltbar und kommen erst danach dran.

## 3. Was die `.env.example` verschweigt

Die Vorlage deckt nicht alle Schalter ab, die `pipe/config.py` liest. Die
Voreinstellungen greifen still — mit diesen Folgen:

| Variable | Voreinstellung | Warum das wichtig ist |
|---|---|---|
| `STT_BACKEND` | `whispercpp` | `WHISPER_MODELL`/`WHISPER_COMPUTE` aus der Vorlage gelten **nur** für `faster`. In der Voreinstellung werden sie ignoriert. |
| `WHISPERCPP_MODELL` | `~/whisper-models/ggml-large-v3-turbo.bin` | Liegt das Modell woanders, bricht die Erkennung ab. |
| `WHISPERCPP_BIN` | `whisper-cli` | Muss im `PATH` liegen (Homebrew `whisper-cpp`). |
| `WHISPER_THREADS` | `8` | Auf kleineren Maschinen herunterdrehen. |
| `NEXTCLOUD_DECK` | *aus* | **Häufigste Verwirrung:** `DECK_TEILEN` und `DECK_STAPEL` stehen in der Vorlage, der Hauptschalter nicht. Ohne `NEXTCLOUD_DECK=1` entsteht kein Board. |
| `NEXTCLOUD_USERID` | = `NEXTCLOUD_USER` | Bei E-Mail-Login weicht die interne User-ID ab, dann zeigt der WebDAV-Pfad ins Leere. |
| `FREESWITCH_LOG` | `/opt/homebrew/var/log/freeswitch/…` | Apple-Silicon-Homebrew. Auf Intel-Macs `/usr/local/...`. Sonst bleiben die Telefon-Ereignisse im Leitstand leer. |
| `ABLAGE_LOKAL`, `TELEFON_ORDNER` | im Projektordner | Für eine Ablage auf einem anderen Volume hier umbiegen. |
| `WHISPER_PROMPT` | Praxis-Kontext | Steuert, wie zuverlässig Namen und Rufnummern erkannt werden. Bei anderem Einsatzzweck anpassen. |

`starten.sh` nutzt außerdem `FS_ETC` (Voreinstellung `/opt/homebrew/etc/freeswitch`)
und `FS_PORT` (`8099`, weil 8021 auf dem Ursprungsrechner belegt war). Auf einer
frischen Maschine ist 8021 meist frei — dann entweder `FS_PORT=8021` setzen oder
den Port in `autoload_configs/event_socket.conf.xml` auf 8099 lassen. Beides
muss zusammenpassen, sonst antwortet `fs_cli` nicht.

## 4. Prüfleiter — in dieser Reihenfolge

1. **Erkennung:** `python3 -m pipe.process_call test/termin.wav --nummer 0…`
   → `meta.json` enthält ein plausibles `transkript`.
2. **Kategorisierung:** `test/rezept.wav` → `kategorie: rezept`,
   `test/notfall.wav` → `dringlichkeit: notfall`. Bleibt `kategorie` leer,
   läuft Ollama nicht oder das Modell fehlt.
3. **Name und Rückrufnummer** stehen als eigene Felder in `meta.json` — nicht
   nur im Fließtext von `anliegen_kurz` (siehe Falle 1 unten).
4. **Nextcloud:** Zugangsdaten eintragen, erneut einen Anruf verarbeiten, dann
   `python3 -m pipe.resync` — meldet 0 Fehler.
5. **Deck:** `NEXTCLOUD_DECK=1` **und** `DECK_TEILEN` setzen. Ohne Freigabe legt
   die Pipe Karten an, die Praxis sieht ein leeres Board.
6. **Leitstand:** `python3 -m pipe.server`, dann <http://127.0.0.1:8088>.
7. **Telefonie** zuletzt: `./telefon/starten.sh` prüft selbst vor und bricht mit
   Klartext ab.

## 5. Fallen, die uns beim ersten Volltest getroffen haben

1. **Name und Rückrufnummer gingen still verloren.** Beide Felder fehlten in der
   `required`-Liste des JSON-Schemas; das Modell ließ sie einfach weg, obwohl es
   sie kannte. Behoben — aber beim Anpassen des Schemas in `pipe/categorize.py`
   sofort wieder einbaubar. Immer gegen `test/notfall.wav` gegenprüfen.
2. **WebDAV ohne Wiederholung.** Ein einzelner träger Antwortlauf schickt den
   Anruf in den manuellen Nachsync. Jetzt 3 Versuche mit Backoff, Timeout 60 s.
3. **Ordnername aus der geratenen statt der CLIP-Nummer.** Die Nummer kommt aus
   der Telefonanlage, nicht aus dem Transkript.
4. **Stumme ffmpeg-/whisper-Fehler.** Jetzt mit Klartext — bei eigenen
   Änderungen nicht wieder auf nackte `CalledProcessError` zurückfallen.
5. **Telephone.app streitet um dieselbe SIP-Registrierung.** Vor dem Start
   beenden; `starten.sh` prüft das.
6. **Rotierende Provider-Adressen.** Löst der SIP-Hostname auf mehrere IPs auf,
   bricht die Registrierung dauernd ab. `SIP_PROXY` auf einen Host setzen, der
   auf genau eine IP zeigt.
7. **Leerzeichen im Projektpfad brechen die Aufnahme.** FreeSWITCHs `record`
   zerlegt seine Argumente an Leerzeichen (`record <pfad> <zeitlimit>
   <stille-schwelle> <stille-treffer>`). Liegt das Projekt z. B. unter
   `~/Desktop/Server AI/…`, landet die Aufnahme als `…/Server.PCMA` neben dem
   Projekt, der Eingang bleibt leer und es sieht aus wie ein Watcher-Fehler —
   die Ansage läuft ja. `einrichten.sh` bricht deshalb bei Leerzeichen ab und
   nennt den Symlink-Ausweg. Getroffen auf der zweiten Maschine, 23.08.2026.
8. **FreeSWITCH liest aus dem Cellar**, wenn `-conf` fehlt — dann überschreibt
   jedes Homebrew-Upgrade Trunk und Dialplan.

## 6. Was bewusst noch fehlt (vor Produktivbetrieb klären)

- **Notfall löst keinen Alarm aus.** `dringlichkeit: notfall` bekommt ein rotes
  Label und steht im Dashboard oben — kein Ton, keine Push, keine Mail. Eine
  Pipe, die Notfälle stumm ablegt, ist für eine Praxis gefährlicher als gar
  keine. Das ist der nächste Schritt, vor der Telefonie.
- **Keine Löschfristen**, keine Verschlüsselung at rest.
- **Leitstand im Netz** (`LEITSTAND_HOST=0.0.0.0`) verlangt `LEITSTAND_PASS` —
  die Seiten zeigen Transkripte und Aufnahmen von Patienten.
- **Ansage** nennt 112 und weist auf die Aufzeichnung hin. Beides ist bei einer
  Arztpraxis Pflicht und darf beim Umtexten nicht wegfallen.
- **Mehrsprachigkeit:** `WHISPER_SPRACHE=de` ist fest. Für EN/AR auf `auto`.

## 7. Nützliche Rückmeldung

Wenn auf der anderen Seite etwas abweicht, sind vor allem diese Punkte für uns
hier interessant:

- andere Hardware/OS-Version und wie lange ein Anruf dann braucht (hier ~24 s),
- ob `qwen3.5:latest` bei 16 GB RAM neben laufender Telefonie noch passt,
- jede Stelle, an der README oder `.env.example` nicht gereicht haben — die
  gehört dann dort hinein, nicht in diese Datei.
