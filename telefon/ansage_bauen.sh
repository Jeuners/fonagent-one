#!/bin/bash
# Erzeugt die Telefonansage aus telefon/ansage.txt.
# Ergebnis: telefon/ansage.wav in Telefonqualitaet (8 kHz mono, PCM16).
#
# Zwei Sprachsynthesen, beide lokal:
#   piper  (Standard, natuerlicher) - Modelle unter ~/piper-voices
#   say    (macOS-Bordmittel, Rueckfall wenn Piper fehlt)
#
# Steuerung ueber Umgebungsvariablen bzw. .env:
#   ANSAGE_TTS=piper|say
#   ANSAGE_STIMME=<Piper-Modellname oder say-Stimme>
#   ANSAGE_EMOTION=<Sprecher-ID, nur bei thorsten_emotional: 0=amused,
#                   1=angry, 2=disgusted, 3=drunk, 4=neutral, 5=sleepy,
#                   6=surprised, 7=whisper>
#   ANSAGE_TEMPO=<1.0 = normal, groesser = langsamer>
set -euo pipefail
cd "$(dirname "$0")/.."
# .env laden, aber bereits gesetzte Umgebungsvariablen behalten Vorrang -
# sonst lässt sich das Skript nicht mehr für einen Einzellauf umstellen.
if [ -f .env ]; then
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    case "$zeile" in ''|'#'*) continue ;; esac
    schluessel=${zeile%%=*}
    wert=${zeile#*=}
    case "$schluessel" in *[!A-Za-z0-9_]*|'') continue ;; esac
    wert=${wert%\"}; wert=${wert#\"}
    if [ -z "${!schluessel:-}" ]; then export "$schluessel=$wert"; fi
  done < .env
fi

STIMMEN_ORDNER="${PIPER_STIMMEN:-$HOME/piper-voices}"
STIMME="${ANSAGE_STIMME:-de_DE-thorsten_emotional-medium}"
EMOTION="${ANSAGE_EMOTION:-0}"
TEMPO="${ANSAGE_TEMPO:-1.0}"

# Backend bestimmen: Piper nur, wenn Paket und Modell wirklich da sind.
TTS="${ANSAGE_TTS:-}"
if [ -z "$TTS" ]; then
  if python3 -c "import piper" 2>/dev/null && [ -f "$STIMMEN_ORDNER/$STIMME.onnx" ]; then
    TTS=piper
  else
    TTS=say
  fi
fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

case "$TTS" in
  piper)
    MODELL="$STIMMEN_ORDNER/$STIMME.onnx"
    [ -f "$MODELL" ] || { echo "Piper-Stimme fehlt: $MODELL" >&2; exit 1; }
    tr '\n' ' ' < telefon/ansage.txt \
      | python3 -m piper -m "$MODELL" -s "$EMOTION" --length-scale "$TEMPO" \
                -f "$TMP/sprache_roh.wav"
    ffmpeg -v error -y -i "$TMP/sprache_roh.wav" -ar 8000 -ac 1 "$TMP/sprache.wav"
    BESCHREIBUNG="piper/$STIMME (Sprecher $EMOTION)"
    ;;
  say)
    SAY_STIMME="${ANSAGE_STIMME:-Anna}"
    say -v "$SAY_STIMME" -r 180 -f telefon/ansage.txt -o "$TMP/sprache.aiff"
    ffmpeg -v error -y -i "$TMP/sprache.aiff" -ar 8000 -ac 1 "$TMP/sprache.wav"
    BESCHREIBUNG="say/$SAY_STIMME"
    ;;
  *) echo "Unbekanntes TTS-Backend: $TTS" >&2; exit 1 ;;
esac

# Signalton: 1000 Hz, 0,4 s - danach 0,3 s Stille, damit der Anrufer einsetzt.
ffmpeg -v error -y -f lavfi -i "sine=frequency=1000:duration=0.4" \
       -f lavfi -t 0.3 -i anullsrc=r=8000:cl=mono \
       -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" \
       -ar 8000 -ac 1 "$TMP/ton.wav"

# -3 dB Kopfraum: Piper gibt randvoll aus (max ~0 dB). Verstärkt die Anlage
# oder der Provider noch etwas, verzerrt das auf der Leitung.
ffmpeg -v error -y -i "$TMP/sprache.wav" -i "$TMP/ton.wav" \
       -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1,volume=-3dB" \
       -ar 8000 -ac 1 -c:a pcm_s16le telefon/ansage.wav

DAUER=$(ffprobe -v error -show_entries format=duration -of csv=p=0 telefon/ansage.wav)
printf 'telefon/ansage.wav erzeugt (%.1f s, %s)\n' "$DAUER" "$BESCHREIBUNG"
