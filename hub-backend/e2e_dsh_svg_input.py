"""Verify SVG pixels through an explicitly selected installed DSH ACP runtime.

Uses disposable session data, a synthetic credential and a loopback model. The
model fixture returns a single answer without tools; it cannot assess design
quality, but rejects anything other than the actual prepared PNG image bytes.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services import dsh_gateway, runtime_images
from app.services.acp_process import OwnedProcessGroup, close_process

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="2">'
       b'<rect width="2" height="2" fill="#ff0000"/>'
       b'<rect x="2" width="2" height="2" fill="#0000ff"/></svg>')
KEY, MODEL = "soduwork-svg-loopback-fixture", "svg-fixture"


def run_case(args, root):
    for name in ("home", "tmp", "work", "dsh", "inputs"):
        (root / name).mkdir()
    source = root / "inputs" / "design.svg"
    source.write_bytes(SVG)
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    cfg = runtime_images.prepare({}, [{"name": "design.svg", "path": str(source),
        "contentType": "image/svg+xml"}], str(source.parent), agent="dsh")
    png = Path(cfg["images"][0]).read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n"), "Conversion did not create PNG bytes"
    received, failures = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions", "Unexpected model path"
                assert self.headers.get("Authorization") == "Bearer " + KEY
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                image_parts = [part for message in body.get("messages", [])
                    if isinstance(message.get("content"), list) for part in message["content"]
                    if part.get("type") == "image_url"]
                if body.get("tools"):
                    assert len(image_parts) == 1, "Native agent did not send one visual image"
                    url = image_parts[0]["image_url"]["url"]
                    assert url.startswith("data:image/png;base64,"), "SVG MIME reached the model"
                    pixels = base64.b64decode(url.split(",", 1)[1], validate=True)
                    assert pixels == png, "Native transport changed or omitted the rendered pixels"
                    received.append({"mimeType": "image/png", "size": len(pixels),
                        "sha256": hashlib.sha256(pixels).hexdigest()})
                    assert len(received) == 1, "Single-answer image task unexpectedly repeated"
                message = {"role": "assistant", "content": "SVG_VISUAL_INPUT_RECEIVED"}
                common = {"id": "svg-fixture-response", "created": 1, "model": MODEL}
                if body.get("stream"):
                    chunks = [json.dumps({**common, "object": "chat.completion.chunk", "choices": [
                        {"index": 0, "delta": delta, "finish_reason": reason}]})
                        for delta, reason in ((message, None), ({}, "stop"))]
                    data = ("".join("data: " + chunk + "\n\n" for chunk in chunks)
                            + "data: [DONE]\n\n").encode()
                    kind = "text/event-stream"
                else:
                    data = json.dumps({**common, "object": "chat.completion", "choices": [
                        {"index": 0, "message": message, "finish_reason": "stop"}]}).encode()
                    kind = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                failures.append(str(exc))
                self.send_error(500, "Synthetic image fixture failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    guard = root / "loopback-only.cjs"
    guard.write_text("""'use strict';
const net = require('node:net');
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const options = first && typeof first === 'object' ? first : {
    port: first, host: typeof args[1] === 'string' ? args[1] : 'localhost'
  };
  if (options.path || !['localhost', '127.0.0.1', '::1'].includes(String(options.host || 'localhost')))
    throw Error('E2E_EXTERNAL_CONNECTION_BLOCKED');
  return connect.apply(this, args);
};
""", encoding="utf-8")
    env = {"PATH": str(args.node.parent) + os.pathsep + os.defpath,
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
        "DSH_HOME": str(root / "dsh"), "DSH_PATH": str(args.runtime_root),
        "DSH_TELEMETRY_DISABLED": "1", "DSH_PERMISSION_MODE": "workspace-write",
        "DSH_ACP_CACHE_DIR": str(root / "cache"), "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "NODE_OPTIONS": "--require=" + json.dumps(str(guard)),
        runtime_images.ENV_KEY: runtime_images.environment(cfg, str(source.parent))}
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA"):
        env[name] = str(root / "home" / name)
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    dsh_gateway.inject_env(env, {"base": f"http://127.0.0.1:{server.server_port}/v1",
        "key": KEY, "model": MODEL, "contextWindow": 32768, "maxOutputTokens": 1024,
        "imageRouting": "native", "localOnly": True})
    command = [sys.executable, str(Path(__file__).parent / "app/services/acp_bridge.py"),
        "--cwd", str(root / "work"), "--agent-name", "dsh", "--", str(args.node),
        str(args.adapter_entry), *dsh_gateway.bundle_args()]
    thread.start()
    proc = owner = None
    try:
        proc = subprocess.Popen(command, cwd=root / "work", env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            start_new_session=os.name != "nt")
        owner = OwnedProcessGroup(proc.pid) if os.name != "nt" else None
        stdout, stderr = proc.communicate("Review the attached SVG design. Reply once without tools.\n"
            "Original SVG source: " + str(source), timeout=45)
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        assert proc.returncode == 0, "Native image turn failed: " + stderr[-3000:] + stdout[-3000:]
        assert not failures, failures
        assert len(received) == 1, "No model request contained visual image input"
        assert not any(event.get("error") for event in events), "Bridge emitted an image error"
        assert "SVG_VISUAL_INPUT_RECEIVED" in stdout, "Missing native final response"
        assert (source.read_bytes(), source.stat().st_mtime_ns) == before, "Source SVG was changed"
        assert not list((root / "work").iterdir()), "No-tool fixture changed its workspace"
        return {"ok": True, "image": received[0], "originalSvgPreserved": True,
                "nativeTurnCompleted": True, "modelRequestsWithImage": len(received)}
    finally:
        if proc is not None:
            close_process(proc, [], owner)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-entry", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("adapter_entry", "runtime_root", "node"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    metadata = args.adapter_entry.parent.parent / "package.json"
    runtime_metadata = args.runtime_root / "node_modules/@deepseek-ai/dsh/package.json"
    sources = [args.adapter_entry, metadata, runtime_metadata]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    with tempfile.TemporaryDirectory(prefix="soduwork-dsh-svg-") as temporary:
        report = run_case(args, Path(temporary).resolve())
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report.update(adapterVersion=json.loads(metadata.read_text())["version"],
                  runtimeVersion=json.loads(runtime_metadata.read_text())["version"],
                  selectedRuntimeFilesUnchanged=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
