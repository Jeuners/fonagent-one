"""Spracherkennung: Audiodatei -> Transkript (lokal, kein Cloud-Aufruf).

Zwei Backends:
  - "whispercpp": nutzt das CLI `whisper-cli` mit einem vorhandenen ggml-Modell
                  (Apple-GPU-beschleunigt, kein Modell-Download).
  - "faster":     faster-whisper (CTranslate2); lädt sein Modell bei Bedarf.

Beliebige Eingabeformate/Abtastraten werden vor der Erkennung per ffmpeg auf
16 kHz mono normalisiert — passt auch für 8-kHz-Telefonaufnahmen.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config


@dataclass
class Transkript:
    text: str
    sprache: str
    sprache_wahrscheinlichkeit: float
    dauer: float
    segmente: list[dict] = field(default_factory=list)


def _letzte_zeile(ausgabe: str | None) -> str:
    """Letzte nicht-leere Zeile einer Fehlerausgabe (für knappe Meldungen)."""
    zeilen = [z for z in (ausgabe or "").strip().splitlines() if z.strip()]
    return zeilen[-1].strip() if zeilen else "keine Fehlerausgabe"


def _nach_16k_mono(audio: Path) -> Path:
    """Normalisiert beliebiges Audio auf 16 kHz mono WAV (Tempdatei)."""
    ziel = Path(tempfile.mkstemp(suffix=".wav", prefix="stt_")[1])
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(ziel)],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as fehler:
        ziel.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg wurde nicht gefunden (brew install ffmpeg).") from fehler
    except subprocess.CalledProcessError as fehler:
        ziel.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg konnte {audio.name} nicht lesen: {_letzte_zeile(fehler.stderr)}"
        ) from fehler
    return ziel


def transkribiere(audio: str | Path) -> Transkript:
    """Wandelt eine Audiodatei in Text um. FileNotFoundError, wenn sie fehlt."""
    pfad = Path(audio)
    if not pfad.is_file():
        raise FileNotFoundError(f"Audiodatei nicht gefunden: {pfad}")

    if config.STT_BACKEND == "faster":
        return _mit_faster_whisper(pfad)
    return _mit_whispercpp(pfad)


# --- Backend: whisper.cpp -----------------------------------------------------

def _mit_whispercpp(audio: Path) -> Transkript:
    wav = _nach_16k_mono(audio)
    ausgabe_basis = Path(tempfile.mkstemp(suffix="", prefix="stt_out_")[1])
    try:
        cmd = [
            config.WHISPERCPP_BIN,
            "-m", config.WHISPERCPP_MODELL,
            "-f", str(wav),
            "-l", config.WHISPER_SPRACHE,
            "-t", str(config.WHISPER_THREADS),
            "-oj", "-of", str(ausgabe_basis),
            "-np",
        ]
        if config.WHISPER_PROMPT:
            cmd += ["--prompt", config.WHISPER_PROMPT]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as fehler:
            raise RuntimeError(
                f"Spracherkennung '{config.WHISPERCPP_BIN}' nicht gefunden "
                f"(brew install whisper-cpp, oder STT_BACKEND=faster setzen)."
            ) from fehler
        except subprocess.CalledProcessError as fehler:
            raise RuntimeError(
                f"whisper-cli brach ab: {_letzte_zeile(fehler.stderr)}"
            ) from fehler
        roh = json.loads(Path(f"{ausgabe_basis}.json").read_text(encoding="utf-8"))
    finally:
        wav.unlink(missing_ok=True)
        Path(f"{ausgabe_basis}.json").unlink(missing_ok=True)
        ausgabe_basis.unlink(missing_ok=True)

    segmente = []
    for s in roh.get("transcription", []):
        text = (s.get("text") or "").strip()
        if text:
            off = s.get("offsets", {})
            segmente.append({
                "start": round(off.get("from", 0) / 1000, 2),
                "ende": round(off.get("to", 0) / 1000, 2),
                "text": text,
            })
    text_ganz = " ".join(s["text"] for s in segmente).strip()
    sprache = roh.get("result", {}).get("language", config.WHISPER_SPRACHE)
    dauer = segmente[-1]["ende"] if segmente else 0.0
    return Transkript(text=text_ganz, sprache=sprache,
                      sprache_wahrscheinlichkeit=1.0, dauer=dauer, segmente=segmente)


# --- Backend: faster-whisper --------------------------------------------------

@lru_cache(maxsize=1)
def _faster_modell():
    from faster_whisper import WhisperModel
    return WhisperModel(config.WHISPER_MODELL, device="cpu",
                        compute_type=config.WHISPER_COMPUTE)


def _mit_faster_whisper(audio: Path) -> Transkript:
    sprache = None if config.WHISPER_SPRACHE == "auto" else config.WHISPER_SPRACHE
    segmente, info = _faster_modell().transcribe(str(audio), language=sprache, vad_filter=True)
    teile, seg_liste = [], []
    for s in segmente:
        t = s.text.strip()
        if t:
            teile.append(t)
            seg_liste.append({"start": round(s.start, 2), "ende": round(s.end, 2), "text": t})
    return Transkript(
        text=" ".join(teile).strip(), sprache=info.language,
        sprache_wahrscheinlichkeit=round(info.language_probability, 3),
        dauer=round(info.duration, 2), segmente=seg_liste,
    )
