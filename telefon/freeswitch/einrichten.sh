#!/bin/bash
# Traegt Trunk und Dialplan in die FreeSWITCH-Konfiguration ein.
# Das SIP-Passwort kommt aus der .env und landet nur in der FreeSWITCH-Config
# (Rechte 600), nie im Git.
set -euo pipefail
PROJEKT="$(cd "$(dirname "$0")/../.." && pwd)"
FS_ETC="${FS_ETC:-/opt/homebrew/etc/freeswitch}"

[ -d "$FS_ETC" ] || { echo "FreeSWITCH-Konfiguration nicht gefunden: $FS_ETC" >&2; exit 1; }
[ -f "$PROJEKT/.env" ] || { echo ".env fehlt in $PROJEKT" >&2; exit 1; }

# .env einlesen, ohne die Werte auszugeben.
set -a; source "$PROJEKT/.env"; set +a
for v in SIP_USER SIP_PASS SIP_DOMAIN; do
  [ -n "${!v:-}" ] || { echo "$v fehlt in .env" >&2; exit 1; }
done
[ -f "$PROJEKT/telefon/ansage.wav" ] || {
  echo "Ansage fehlt - erst telefon/ansage_bauen.sh ausfuehren" >&2; exit 1; }

mkdir -p "$FS_ETC/sip_profiles/external" "$FS_ETC/dialplan/public"

fuelle() {  # <vorlage> <ziel>
  sed -e "s|@@SIP_USER@@|$SIP_USER|g" \
      -e "s|@@SIP_PASS@@|$SIP_PASS|g" \
      -e "s|@@SIP_DOMAIN@@|$SIP_DOMAIN|g" \
      -e "s|@@SIP_PROXY@@|${SIP_PROXY:-$SIP_DOMAIN}|g" \
      -e "s|@@PROJEKT@@|$PROJEKT|g" "$1" > "$2"
}

fuelle "$PROJEKT/telefon/freeswitch/plusnet.xml.tpl" "$FS_ETC/sip_profiles/external/plusnet.xml"
chmod 600 "$FS_ETC/sip_profiles/external/plusnet.xml"   # enthaelt das SIP-Passwort
fuelle "$PROJEKT/telefon/freeswitch/praxis_ab.xml.tpl" "$FS_ETC/dialplan/public/00_praxis_ab.xml"

echo "Trunk    -> $FS_ETC/sip_profiles/external/plusnet.xml (Rechte 600)"
echo "Dialplan -> $FS_ETC/dialplan/public/00_praxis_ab.xml"
echo "Ansage   -> $PROJEKT/telefon/ansage.wav"
echo "Eingang  -> $PROJEKT/telefon/eingang"
