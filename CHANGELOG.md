# Changelog

## v1.1.0 — 2026-08-30

Branchen-Profile machen das Projekt für andere Einsatzzwecke als die
Hausarztpraxis konfigurierbar (z. B. 24h-Notdienst), dazu mehrere
Leitstand-Härtungen und Aufräumarbeiten.

### Branchen-Profile (neu)
- Projekt ist jetzt per `PROFIL`-Wahl für andere Einsatzzwecke konfigurierbar
  (Praxis, Aufzug-Notdienst, …) statt hart auf die Arztpraxis zugeschnitten
- aktives Branchen-Profil im Leitstand/Dashboard sichtbar
- README-Einleitung an den Profil-Umbau angepasst

### Leitstand & Zugriff
- mehrere gleichzeitige Leitstand-Zugänge (`LEITSTAND_ZUGAENGE`), z. B. ein
  eigenes Notdienst-Konto neben dem Praxis-Konto
- Abmelden-Button im Leitstand, zusätzlich per `?logoff` in der URL auslösbar
- eingeloggter Nutzer im Header sichtbar; Verlauf zeigt jetzt, wer eine Karte
  verschoben hat
- Modell-Dropdown listet lokale Ollama-Modelle live statt fest im Code
- Dringlichkeitsfarben getauscht und zentralisiert; `/api/anrufe` und
  `/api/verlauf` werden jetzt gecacht (Performance)

### Testzugang & Sicherheit
- signierte, 1h gültige Testzugangslinks für den Leitstand ("agent-one
  test"-Kommando)
- Sicher-Modus bei Testzugang erzwungen, nicht abschaltbar
- Rufnummern bei Testzugang serverseitig maskiert statt nur per CSS geblurrt
  (vorher im Rohdaten-Response sichtbar)

### Zuverlässigkeit
- Störungswache (`pipe.monitor`) + Verschlüsselung-at-rest dokumentiert
- Log-Rotation für Rohlogs + Aufräumen alter Verlauf-Archivkopien
- Linux-Setup-Hinweis in der README ergänzt

### Sonstiges
- echte Aufnahme statt Sprachsynthese als Ansage möglich (`ANSAGE_TTS=datei`)
- Nextcloud-Profil, LinkedIn und Telefonnummer (internationales Format) im
  Footer

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
