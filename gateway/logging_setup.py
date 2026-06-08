"""Structured-ish console logging used across the gateway."""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers[:] = [handler]
    # zeep/werkzeug are chatty; quiet them unless debugging.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("zeep").setLevel(logging.WARNING)
    _CONFIGURED = True
