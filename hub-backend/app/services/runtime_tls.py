"""Verified TLS trust for source and frozen Runner HTTP clients."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import ssl
import urllib.request

import certifi


def client_context() -> ssl.SSLContext:
    """Use explicit CA overrides exclusively, otherwise supplement default roots."""
    # Empty values mean no override. Do not strip paths: whitespace can be
    # part of a filename and an invalid explicit path must fail closed.
    cafile = os.environ.get("SSL_CERT_FILE") or None
    capath = os.environ.get("SSL_CERT_DIR") or None
    if cafile or capath:
        if cafile and not Path(cafile).is_file():
            raise FileNotFoundError(errno.ENOENT, "The configured TLS CA file is unavailable")
        if capath and not Path(capath).is_dir():
            raise NotADirectoryError(errno.ENOTDIR, "The configured TLS CA directory is unavailable")
        # A capath uses OpenSSL's hashed-directory format and loads on demand.
        # Supplying either override must not silently add other trust anchors.
        context = ssl.create_default_context(cafile=cafile, capath=capath)
    else:
        context = ssl.create_default_context()
        # Frozen OpenSSL can retain absent build-machine CA paths. certifi is
        # bundled with the Runner and supplements roots available at runtime.
        context.load_verify_locations(cafile=certifi.where())
    context.set_alpn_protocols(["http/1.1"])
    return context


class _RuntimeHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        # HTTPSHandler may eagerly build a context on newer Python versions.
        # Plain HTTP must not require valid TLS configuration.
        urllib.request.AbstractHTTPHandler.__init__(self)

    def https_open(self, request):
        handler = urllib.request.HTTPSHandler(
            debuglevel=self._debuglevel, context=client_context())
        return handler.https_open(request)


def https_handler() -> urllib.request.HTTPSHandler:
    """Apply verified trust at each HTTPS hop, including redirects from HTTP."""
    return _RuntimeHTTPSHandler()
