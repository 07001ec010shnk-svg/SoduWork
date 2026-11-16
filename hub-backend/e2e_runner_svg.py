"""Freeze the actual Runner recipe and verify SVG pixels without any model or account."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image
from PyInstaller.archive.readers import CArchiveReader

BACKEND = Path(__file__).resolve().parent


def verify(binary: Path) -> dict:
    raster = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 255, 0)).save(raster, format="PNG")
    encoded = base64.b64encode(raster.getvalue()).decode()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="2">'
           '<rect width="2" height="2" fill="red"/>'
           '<image x="2" width="2" height="2" href="data:image/png;base64,'
           + encoded + '"/></svg>').encode()
    command = [str(binary), "--soduwork-svg-raster-worker"]
    result = subprocess.run(command, input=svg, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Frozen SVG worker failed: " + result.stderr.decode(errors="replace")[:1000])
    with Image.open(io.BytesIO(result.stdout)) as image:
        assert image.size == (4, 2)
        pixels = image.convert("RGBA")
        assert pixels.getpixel((0, 0)) == (255, 0, 0, 255)
        assert pixels.getpixel((3, 0)) == (0, 255, 0, 255)
    rejected = subprocess.run(command,
        input=b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///outside.png"/></svg>',
        capture_output=True, timeout=30)
    assert rejected.returncode != 0 and not rejected.stdout
    archive = CArchiveReader(str(binary))
    entries = list(archive.toc)
    for dependency in ("resvg_py-0.5.0.dist-info", "pillow-"):
        assert any(dependency in name.lower() and "license" in name.lower()
                   for name in entries), "Missing wheel license: " + dependency
    manifest = json.loads((BACKEND.parent / "third-party/manifest.json").read_text(encoding="utf-8"))
    for override in manifest.get("pythonNoticeOverrides", []):
        if override["name"].replace("-", "_") == "resvg_py":
            notice = next(item for item in manifest["notices"] if item["id"] == override["notice"])
            matches = [name for name in entries if Path(notice["path"]).name in name]
            assert len(matches) == 1, "Missing or ambiguous native renderer notices"
            original = archive.extract(matches[0]).decode("utf-8").replace("\r\n", "\n")
            assert hashlib.sha256(original.encode("utf-8")).hexdigest() == notice["sha256"], "Stale native renderer notices"
    return {"ok": True, "platform": sys.platform, "frozenRunner": True,
            "size": [4, 2], "pngBytes": len(result.stdout), "embeddedRasterDecoded": True,
            "pixelsVerified": True, "externalResourceRejected": True, "licensesIncluded": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soduwork-svg-package-") as folder:
        root = Path(folder).resolve()
        with (args.output / "build.log").open("wb") as log:
            built = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--distpath", str(root / "dist"), "--workpath", str(root / "build"),
                str(BACKEND / "runner.spec")], cwd=BACKEND,
                env={**os.environ, "SODUWORK_INTERNAL_BUILD": "1"},
                stdout=log, stderr=subprocess.STDOUT, timeout=600)
        if built.returncode:
            raise RuntimeError("Runner freeze failed; inspect build.log")
        binary = root / "dist" / ("soduwork-runner.exe" if os.name == "nt" else "soduwork-runner")
        report = verify(binary)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
