"""Lokaler macOS-Alarm (Systembenachrichtigung + Ton) für pipe.monitor.

Wirft nie - eine fehlgeschlagene Benachrichtigung darf die Wache nicht zum
Absturz bringen. Nutzt osascript (stdlib-Unterprozess, kein Framework, keine
Abhängigkeit von einem laufenden Nextcloud/Cloud-Dienst).
"""
from __future__ import annotations

import subprocess

from . import config


def _quote(text: str) -> str:
    """AppleScript-String-Literal - Anführungszeichen/Backslashes escapen."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sende(titel: str, text: str) -> None:
    skript = (
        f"display notification {_quote(text)} with title {_quote(titel)} "
        f"sound name {_quote(config.MONITOR_TON)}"
    )
    try:
        subprocess.run(["osascript", "-e", skript], capture_output=True, timeout=10)
    except Exception as fehler:
        print(f"  Alarm konnte nicht zugestellt werden: {fehler}")
