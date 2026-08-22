#!/bin/bash
# Erzeugt die Test-Anrufe, auf die README und MITGABE verweisen. Die Audios
# selbst sind gitignoriert (Format: reale Aufnahmen waeren Patientendaten),
# deshalb entstehen sie hier lokal per macOS "say" - kein Download, keine
# Zusatz-Abhaengigkeit. Alle Namen, Nummern und Anliegen sind erfunden.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v say >/dev/null 2>&1; then
  echo "say (macOS) nicht gefunden - auf anderen Systemen z. B. mit Piper" \
       "erzeugen (siehe telefon/ansage_bauen.sh als Vorlage)." >&2
  exit 1
fi

erzeuge() {  # <datei> <stimme> <text>
  local datei="$1" stimme="$2" text="$3"
  say -v "$stimme" -o "$datei" --file-format=WAVE --data-format=LEI16@16000 "$text"
  echo "  $datei"
}

echo "Erzeuge Test-Anrufe (erfundene Daten) ..."

erzeuge rezept.wav Anna \
  "Guten Tag, hier ist Erika Musterfrau. Ich bräuchte ein neues Rezept für mein Blutdruckmedikament, Ramipril fünf Milligramm. Bitte rufen Sie mich zurück unter null zwei eins null drei, eins zwei drei vier fünf sechs sieben. Vielen Dank."

erzeuge termin.wav Markus \
  "Hallo, hier spricht Klaus Allofs. Ich hätte gerne einen Termin zur Vorsorgeuntersuchung, am liebsten nächste Woche vormittags. Meine Nummer ist null eins fünf einhundertsiebenundzwanzig, drei vier fünf sechs sieben acht neun. Danke schön."

erzeuge notfall.wav Petra \
  "Hallo, hier ist Sabine Winter, es geht um meinen Vater. Er hat seit einer Stunde starke Schmerzen in der Brust und ist ganz blass. Bitte rufen Sie so schnell wie möglich zurück unter null eins sieben zwei, neun acht siebenunddreißig sechzehn."

echo "Fertig. Beispielaufruf: python3 -m pipe.process_call test/rezept.wav --nummer 021031234567"
