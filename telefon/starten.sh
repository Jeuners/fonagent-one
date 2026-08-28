#!/bin/bash
# Startet die Telefonannahme: FreeSWITCH nimmt Anrufe an, pipe.watch verarbeitet
# die Aufnahmen. Prueft vorher, was erfahrungsgemaess schiefgeht.
set -uo pipefail
PROJEKT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJEKT"

# Werte aus der .env uebernehmen (FS_PORT, FS_ETC, OLLAMA_URL, LEITSTAND_PORT).
# Wie in pipe/config.py gewinnt eine bereits gesetzte Umgebungsvariable - so
# bleibt ein einmaliges "FS_PORT=8021 ./telefon/starten.sh" moeglich.
if [ -f .env ]; then
  while IFS= read -r zeile; do
    case "$zeile" in ''|'#'*) continue ;; *=*) : ;; *) continue ;; esac
    schluessel="${zeile%%=*}"
    wert="${zeile#*=}"
    wert="${wert%\"}"; wert="${wert#\"}"
    [ -n "${!schluessel:-}" ] || export "$schluessel=$wert"
  done < .env
fi

fehler=0
melde() { printf '  %-38s %s\n' "$1" "$2"; }

echo "Vorabpruefung"

# Ollama - ohne Kategorisierung bleibt jeder Anruf liegen.
if curl -s --max-time 5 "${OLLAMA_URL:-http://127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
  melde "Ollama" "laeuft"
else
  melde "Ollama" "FEHLT - 'ollama serve &' starten"; fehler=1
fi

# Ansage
if [ -f telefon/ansage.wav ]; then
  melde "Ansage" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 telefon/ansage.wav | cut -c1-4) s"
else
  melde "Ansage" "FEHLT - telefon/ansage_bauen.sh ausfuehren"; fehler=1
fi

# Trunk-Konfiguration
FS_ETC="${FS_ETC:-/opt/homebrew/etc/freeswitch}"
FS_PORT="${FS_PORT:-8099}"   # Event-Socket; 8021 ist auf diesem Mac belegt
if [ -f "$FS_ETC/sip_profiles/external/plusnet.xml" ]; then
  melde "Trunk-Konfiguration" "eingerichtet"
else
  melde "Trunk-Konfiguration" "FEHLT - telefon/freeswitch/einrichten.sh"; fehler=1
fi

# Telephone.app streitet sich mit uns um dieselbe SIP-Registrierung.
if pgrep -xq Telephone; then
  melde "Telephone.app" "LAEUFT - beenden, sonst Registrierungskonflikt"; fehler=1
else
  melde "Telephone.app" "beendet"
fi

[ $fehler -eq 0 ] || { echo; echo "Abbruch - erst die Punkte oben klaeren."; exit 1; }

echo
if pgrep -xq freeswitch; then
  echo "FreeSWITCH laeuft bereits."
else
  echo "Starte FreeSWITCH ..."
  # -conf ist noetig: FreeSWITCH liest sonst aus dem Cellar-Verzeichnis, das
  # jedes Homebrew-Upgrade ueberschreibt. $FS_ETC bleibt bestehen.
  # -rp: Echtzeit-Priorität. Auf einem Rechner, der nebenher Whisper und ein
  # Sprachmodell laufen lässt, kommt FreeSWITCH sonst nicht rechtzeitig dran und
  # das Audio stückelt - RTP will alle 20 ms ein Paket losschicken.
  freeswitch -nc -rp -nonatmap \
    -conf "$FS_ETC" \
    -log /opt/homebrew/var/log/freeswitch \
    -db /opt/homebrew/var/lib/freeswitch/db \
    -run /opt/homebrew/var/run/freeswitch
  # FreeSWITCH braucht nach dem Backgrounding ~40 s, bis Sofia die Profile hat.
  sleep 15
fi

# Auf die Registrierung beim Provider warten.
# Quelle ist fs_cli, nicht das Log: das Log wird zum Testen geleert und taugt
# dann nicht mehr als Zustandsquelle.
echo -n "Warte auf Trunk-Registrierung "
status=""
for i in $(seq 1 30); do
  wert=$(fs_cli -P "$FS_PORT" -x "sofia status gateway plusnet" 2>/dev/null \
         | awk '/^Status/{print $2}')
  case "$wert" in
    UP)   status="UP"; break ;;
    DOWN) : ;;   # noch im Aufbau, weiter warten
  esac
  echo -n "."; sleep 2
done
echo
fs_cli -P "$FS_PORT" -x "sofia status gateway plusnet" 2>/dev/null \
  | grep -E '^(Name|State|Status)' || echo "  (fs_cli antwortet nicht)"

if [ "${status:-}" = "UP" ]; then
  # Watcher und Leitstand mitstarten, falls sie nicht schon laufen.
  if pgrep -f "[p]ipe.watch" >/dev/null; then
    echo "Verarbeitung laeuft bereits."
  else
    # nice 10: Transkription und Kategorisierung dürfen warten, ein laufendes
    # Telefonat nicht. Auf 16 GB RAM konkurrieren beide sonst um die Maschine.
    nohup nice -n 10 python3 -u -m pipe.watch > telefon/watcher.log 2>&1 &
    echo "Verarbeitung gestartet (Log: telefon/watcher.log)"
  fi
  if pgrep -f "[p]ipe.server" >/dev/null; then
    echo "Leitstand laeuft bereits."
  else
    nohup python3 -u -m pipe.server > telefon/leitstand.log 2>&1 &
    sleep 2
    head -1 telefon/leitstand.log
  fi
  if pgrep -f "[p]ipe.monitor" >/dev/null; then
    echo "Stoerungswache laeuft bereits."
  else
    nohup python3 -u -m pipe.monitor > telefon/monitor.log 2>&1 &
    echo "Stoerungswache gestartet (Log: telefon/monitor.log)"
  fi
  sleep 2
  echo
  ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
  echo "Bereit. Leitstand: http://${ip:-127.0.0.1}:${LEITSTAND_PORT:-8088}"
else
  echo
  echo "Trunk ist NICHT registriert. Log:"
  echo "    tail -f /opt/homebrew/var/log/freeswitch/freeswitch.log"
  exit 1
fi
