"""Signierte, zeitlich begrenzte Testzugangs-Links zum Leitstand.

Gegenstueck zu dilles_agent_one_test_command() in dillenberg.net's
dilles-agent.php ("agent-one test"-Kommando im Alice-Chat). Beide Seiten
teilen sich TESTZUGANG_SECRET (config.py) - der Link wird komplett auf der
PHP-Seite erzeugt, ohne Live-Aufruf hierher; hier wird nur die Signatur
geprueft. Stateless: kein Server-seitiger Speicher fuer ausgegebene Tokens,
die Gueltigkeit steckt komplett im signierten Ablauf-Zeitstempel.

Token-Format: "<ablauf-unixzeit>.<hmac-sha256-hexdigest>"
"""
from __future__ import annotations

import hmac
import time
from hashlib import sha256

from . import config


def gueltig(token: str) -> bool:
    """Prueft ein Token aus ?zugang=... oder dem zugang-Cookie. Wirft nie."""
    if not config.TESTZUGANG_SECRET or not token:
        return False
    try:
        ablauf_str, signatur = token.split(".", 1)
        ablauf = int(ablauf_str)
    except (ValueError, AttributeError):
        return False
    erwartet = hmac.new(
        config.TESTZUGANG_SECRET.encode("utf-8"), ablauf_str.encode("utf-8"), sha256
    ).hexdigest()
    if not hmac.compare_digest(erwartet, signatur):
        return False
    return time.time() < ablauf
