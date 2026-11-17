"""Portable TLS trust for the standalone macOS Runner control connection."""
import sys


def websocket_options(url: str) -> dict:
    if sys.platform != "darwin" or not url.lower().startswith("wss://"):
        return {}
    from .runtime_tls import client_context

    # Share HTTPS trust rules: supplement default roots for frozen builds,
    # but keep explicit CA overrides exclusive and fail closed when invalid.
    return {"ssl": client_context()}
