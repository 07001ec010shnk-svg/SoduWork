"""Native system TLS trust for Runner control and model-gateway connections."""
import os
import ssl


def client_context(url: str) -> ssl.SSLContext | None:
    if not url.lower().startswith(("https://", "wss://")):
        return None
    import truststore

    # Use Security.framework, CryptoAPI or the installed Linux system CA store,
    # not a bundled root list. Native trust failures must propagate; never retry
    # with a different trust policy.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cafile = os.environ.get("SSL_CERT_FILE") or None
    capath = os.environ.get("SSL_CERT_DIR") or None
    if cafile or capath:
        context.load_verify_locations(cafile=cafile, capath=capath)
    return context


def websocket_options(url: str) -> dict:
    if not url.lower().startswith("wss://"):
        return {}
    context = client_context(url)
    return {"ssl": context} if context is not None else {}
