# Changelog

## v1.0.0 — 2026-08-25

Erste stabile Version. Läuft produktiv (mit Testdaten) auf macOS/Apple
Silicon, Telefonie steht, Pipeline läuft Ende-zu-Ende.

### Kernpipeline
- Audio → Transkript (whisper.cpp/faster-whisper) → Kategorisierung
  (Ollama/OpenRouter) → strukturierte Ablage, lokal und optional Nextcloud
- FreeSWITCH nimmt Anrufe über den SIP-Trunk der Praxis an
- Leitstand (`pipe/server.py`): Live-Kanban-Board, Status-Ampeln, Verlauf

### Zuverlässigkeit & Sicherheit
- Notfall-Sicherheitsnetz: deterministischer Code-Check gegen die im Prompt
  genannten Notfall-Symptome (Atemnot, Brustschmerz, Bewusstlosigkeit,
  starke Blutung) — eskaliert unabhängig vom LLM, nur nach oben
- Bekannte Whisper-Halluzinationen (z. B. "Untertitelung des ZDF") werden
  gefiltert statt als echtes Anliegen kategorisiert
- Failover auf lokales Ollama-Modell, wenn OpenRouter ausfällt oder
  zeitüberschreitet
- Unveränderliches, append-only Audit-Log (`telefon/audit.log`,
  `chflags uappnd`) — zusätzlich zum rotierenden UI-Verlauf
- Automatische Aufbewahrungsfrist (`LOESCHFRIST_TAGE`) ohne Cron —
  `pipe.watch` prüft selbst alle 24h; unbearbeitete Anrufe bleiben immer
  unangetastet
- E-Mail-Extraktion mit deterministischer @-Reparatur (Whisper transkribiert
  gesprochenes "at" manchmal als Punkt) — als Vermutung markiert, nicht
  stillschweigend übernommen

### Leitstand-UI
- Sicher-Modus: Rufnummern (Karten + Verlauf-Log) blurren für
  Screenshots/Aufnahmen
- Notfall-Alarm: pulsierender Rahmen, Banner, minütlicher Ton
- Verbindungsverlust-Anzeige, wenn der Server nicht mehr antwortet
- Modell-Dropdown: Kategorisierungs-Backend/-Modell live umschaltbar
  (Ollama lokal oder OpenRouter zum Testen), inkl. Dauer/Tokens/Kosten im
  Live-Log
- Bewertungssystem: Melde-Button pro Karte, Freitext-Modal mit
  Kategorie/Status-Auswahl, Export als JSONL-Trainingsdaten
- Kartendesign überarbeitet (weniger Badges, klarere Struktur), Logo,
  zweizeiliger Header, Footer mit Projekt-/Spendenlink

### Bekannte offene Punkte (nicht Teil von v1.0)
- Ansage-Text erfüllt die Pflichtangaben für den echten Praxisbetrieb noch
  nicht (Notruf-Hinweis, Aufnahme-Einwilligung) — siehe README
- Verschlüsselung at rest noch nicht umgesetzt
- Monitoring/Alerting bei Diensteausfall nur über die Leitstand-Ampel
- Echtzeit-Sprachdialog (statt Aufnahme+Nachbearbeitung) bewusst nicht in
  dieser Version — größere Architekturänderung (Streaming-STT, Live-Audio
  über FreeSWITCH, schnelles Dialog-LLM), siehe Diskussion in der
  Projekt-Historie
