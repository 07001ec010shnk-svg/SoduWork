"""Render staged SVGs into bounded PNG previews without loading outside resources.

The original attachment is never modified. The worker reads SVG bytes from stdin
and emits PNG bytes only; it imports no application configuration or credentials.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import subprocess
import sys
import tempfile
import warnings
import xml.etree.ElementTree as ET

RENDER_VERSION = "resvg-0.5.0-policy-1"
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_NODES = 20000
MAX_DEPTH = 128
MAX_EDGE = 4096
MAX_PIXELS = 8 * 1024 * 1024
TIMEOUT_SECONDS = 10
_SVG = "http://www.w3.org/2000/svg"
_XLINK = "http://www.w3.org/1999/xlink"
_FRAGMENT = re.compile(r"#(?:[^\W\d]|_)[\w.:-]*\Z")
_LENGTH = re.compile(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))(px|pt|pc|in|cm|mm|q)?\Z", re.I)
_UNITS = {"": 1, "px": 1, "pt": 96 / 72, "pc": 16, "in": 96,
          "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6}
_FORBIDDEN = {"script", "foreignobject", "animate", "animatetransform", "animatemotion",
              "set", "discard", "audio", "video", "iframe"}


class SvgImageError(ValueError):
    pass


def _bad(message="SVG 含有无法安全预览的内容，请导出为 PNG 后重新发送；原附件已保留"):
    raise SvgImageError(message)


def _dimensions(width, height):
    if (not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
            or width > 1_000_000 or height > 1_000_000):
        _bad("SVG 尺寸无效或过大，请调整后重新发送；原附件已保留")
    scale = min(1.0, MAX_EDGE / width, MAX_EDGE / height,
                math.sqrt(MAX_PIXELS / (width * height)))
    return max(1, int(width * scale)), max(1, int(height * scale))


def _raster_size(data: bytes, mime: str):
    if mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33:
        if data[12:16] != b"IHDR":
            _bad()
        return struct.unpack(">II", data[16:24])
    if mime == "image/gif" and data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X" and len(data) >= 30:
            return (1 + int.from_bytes(data[24:27], "little"),
                    1 + int.from_bytes(data[27:30], "little"))
        if data[12:16] == b"VP8L" and len(data) >= 25 and data[20] == 0x2f:
            packed = int.from_bytes(data[21:25], "little")
            return (1 + (packed & 0x3fff), 1 + ((packed >> 14) & 0x3fff))
        if data[12:16] == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3fff, height & 0x3fff
    if mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff"):
        position = 2
        while position + 4 <= len(data):
            if data[position] != 0xff:
                break
            while position < len(data) and data[position] == 0xff:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
                continue
            if position + 2 > len(data):
                break
            size = int.from_bytes(data[position:position + 2], "big")
            if size < 2 or position + size > len(data):
                break
            if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
                if size < 8:
                    break
                height, width = struct.unpack(">HH", data[position + 3:position + 7])
                return width, height
            position += size
    _bad("SVG 内嵌图片格式无效，请导出为 PNG 后重新发送；原附件已保留")


def _href(value: str, totals: dict, *, decode_images=False):
    value = value.strip()
    if _FRAGMENT.fullmatch(value):
        return
    match = re.fullmatch(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        _bad("SVG 引用了外部或嵌套资源，请导出为独立 PNG 后重新发送；原附件已保留")
    try:
        data = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error):
        _bad()
    width, height = _raster_size(data, match[1])
    totals["bytes"] += len(data)
    totals["pixels"] += width * height
    if (not width or not height or width > MAX_EDGE or height > MAX_EDGE
            or totals["bytes"] > MAX_SVG_BYTES or totals["pixels"] > MAX_PIXELS):
        _bad("SVG 内嵌图片过大，请缩小后重新发送；原附件已保留")
    if decode_images:
        from PIL import Image
        formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF", "image/webp": "WEBP"}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data), formats=[formats[match[1]]]) as image:
                    if image.size != (width, height):
                        _bad()
                    image.verify()
                with Image.open(io.BytesIO(data), formats=[formats[match[1]]]) as image:
                    frames = int(getattr(image, "n_frames", 1))
                    totals["pixels"] += width * height * (frames - 1)
                    if frames > 1000 or totals["pixels"] > MAX_PIXELS:
                        _bad()
                    for index in range(frames):
                        image.seek(index)
                        image.load()
        except Exception as exc:
            raise SvgImageError("SVG 内嵌图片无法解码；原附件已保留") from exc


def _css(value: str):
    # Reject CSS escapes rather than guessing whether an escaped resource name
    # will be decoded into a file reference by the native renderer.
    # Advance past each comment once: a lazy wildcard regex retries every
    # unterminated opener and can block the parent Runner before its deadline.
    chunks, position = [], 0
    while True:
        start = value.find("/*", position)
        if start < 0:
            chunks.append(value[position:])
            break
        chunks.append(value[position:start])
        end = value.find("*/", start + 2)
        if end < 0:
            _bad()
        position = end + 2
    value = "".join(chunks)
    if "\\" in value or re.search(r"@(?:import|font-face)\b", value, re.I):
        _bad()
    stripped = re.sub(r"url\s*\(\s*(['\"]?)(#(?:[^\W\d]|_)[\w.:-]*)\1\s*\)", "", value, flags=re.I)
    if re.search(r"url\s*\(", stripped, re.I):
        _bad("SVG 样式引用了外部资源，请导出为独立 PNG 后重新发送；原附件已保留")


def validate(data: bytes, *, decode_images=False) -> tuple[str, int, int]:
    if not data or len(data) > MAX_SVG_BYTES:
        _bad("SVG 附件不能超过 2 MB，请简化后重新发送；原附件已保留")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        _bad("SVG 必须使用 UTF-8 编码，请重新导出；原附件已保留")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b|<\?xml-stylesheet\b", text, re.I):
        _bad()
    try:
        root = ET.fromstring(text)
    except (ValueError, ET.ParseError):
        _bad("SVG 文件内容无效，请重新导出；原附件已保留")
    if root.tag not in ("svg", "{" + _SVG + "}svg"):
        _bad("附件内容不是有效 SVG；原附件已保留")
    stack, count, totals = [(root, 1)], 0, {"pixels": 0, "bytes": 0}
    while stack:
        element, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            _bad("SVG 结构过于复杂，请简化后重新发送；原附件已保留")
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in _FORBIDDEN:
            _bad("SVG 含脚本、动画或 HTML 内容，无法生成可靠静态预览；请导出为 PNG，原附件已保留")
        if tag == "style":
            _css(element.text or "")
        for name, value in element.attrib.items():
            local = name.rsplit("}", 1)[-1].lower()
            if local.startswith("on") or name == "{http://www.w3.org/XML/1998/namespace}base":
                _bad()
            if local == "href":
                if name not in ("href", "{" + _XLINK + "}href"):
                    _bad()
                if value.strip().startswith("#"):
                    # Both image resolvers can treat '#name' as a local file.
                    # XML IDs do not prove usvg created a renderable target.
                    if tag in {"image", "feimage"}:
                        _bad()
                elif tag not in {"image", "feimage"}:
                    _bad()
                _href(value, totals, decode_images=decode_images)
            else:
                _css(value)
        stack.extend((child, depth + 1) for child in element)
    view_box = None
    if "viewBox" in root.attrib:
        try:
            values = [float(value) for value in re.split(r"[\s,]+", root.attrib["viewBox"].strip())]
        except ValueError:
            _bad("SVG viewBox 无效；原附件已保留")
        if len(values) != 4 or not all(math.isfinite(value) for value in values) or min(values[2:]) <= 0:
            _bad("SVG viewBox 无效；原附件已保留")
        view_box = values
    sizes = []
    for index, key in enumerate(("width", "height")):
        value = root.get(key, "").strip()
        if not value or value.endswith("%"):
            sizes.append(None)
            continue
        match = _LENGTH.fullmatch(value)
        if not match:
            _bad("SVG 尺寸单位暂不支持，请导出为 PNG；原附件已保留")
        sizes.append(float(match[1]) * _UNITS[(match[2] or "").lower()])
    if sizes == [None, None]:
        sizes = view_box[2:] if view_box else [300, 150]
    elif sizes[0] is None:
        sizes[0] = sizes[1] * view_box[2] / view_box[3] if view_box else 300
    elif sizes[1] is None:
        sizes[1] = sizes[0] * view_box[3] / view_box[2] if view_box else 150
    width, height = _dimensions(*sizes)
    # Canonical XML also removes processing instructions and fixes byte encoding.
    return ET.tostring(root, encoding="unicode"), width, height


def _png(data: bytes) -> bool:
    if not data or len(data) > MAX_PNG_BYTES:
        return False
    try:
        width, height = _raster_size(data, "image/png")
    except SvgImageError:
        return False
    return (0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and width * height <= MAX_PIXELS
            and data.endswith(b"IEND\xaeB`\x82"))


def worker_main() -> int:
    try:
        data = sys.stdin.buffer.read(MAX_SVG_BYTES + 1)
        text, width, height = validate(data, decode_images=True)
        try:
            import resvg_py
        except ImportError:
            return 7
        if resvg_py.__version__ != "0.5.0":
            return 7
        png = resvg_py.svg_to_bytes(svg_string=text, width=width, height=height,
                                   resources_dir=None, log_information=False)
        if not _png(png):
            return 3
        sys.stdout.buffer.write(png)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:  # Native renderer panics must not expose attachment contents.
        return 3


def _render(data: bytes) -> bytes:
    command = ([sys.executable, "--soduwork-svg-raster-worker"] if getattr(sys, "frozen", False)
               else [sys.executable, str(Path(__file__).resolve()), "--worker"])
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=TIMEOUT_SECONDS, **flags)
    except subprocess.TimeoutExpired as exc:
        raise SvgImageError("SVG 预览生成超时，请简化图片后重新发送；原附件已保留") from exc
    except OSError as exc:
        raise SvgImageError("无法启动 SVG 预览组件，请更新执行机后重试；原附件已保留") from exc
    if result.returncode == 7:
        _bad("SVG 预览组件缺失或版本不匹配，请更新执行机后重试；原附件已保留")
    if result.returncode or not _png(result.stdout):
        _bad("SVG 无法转换为有效图片，请导出为 PNG 后重新发送；原附件已保留")
    return result.stdout


def _read_regular(path: Path, limit: int, directory_fd=None) -> bytes:
    if directory_fd is not None:
        handle = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise OSError("Invalid cached preview")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise OSError("Oversized cached preview")
            return data
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise OSError("Invalid cached preview")
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Invalid cached preview")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise OSError("Oversized cached preview")
        return data


def _atomic(path: Path, data: bytes, directory_fd=None):
    if directory_fd is not None:
        temporary = ".svg-writing-" + secrets.token_hex(16)
        try:
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".svg-writing-", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def derived_png(data: bytes, root: str) -> tuple[str, bytes]:
    """Input bytes have already passed the staged-upload containment gate."""
    validate(data)
    directory = Path(root)
    if not directory.is_dir() or directory.is_symlink() or os.path.realpath(root) != os.path.abspath(root):
        _bad("SVG 附件存档目录无效；原附件已保留")
    identity = directory.stat()
    directory_fd = None
    try:
        if os.name != "nt":
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(directory_fd)
            if (identity.st_dev, identity.st_ino) != (info.st_dev, info.st_ino):
                _bad("SVG 附件存档目录已变化，请重试；原附件已保留")
        return _derived_png(data, directory, identity, directory_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _derived_png(data, directory, directory_identity, directory_fd):
    def check_directory():
        current = directory.stat()
        if (directory.is_symlink() or os.path.realpath(directory) != str(directory)
                or (current.st_dev, current.st_ino) != (directory_identity.st_dev, directory_identity.st_ino)):
            _bad("SVG 附件存档目录已变化，请重试；原附件已保留")

    source = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256((RENDER_VERSION + ":" + source).encode()).hexdigest()
    path = directory / (".svg-preview-" + identity + ".png")
    receipt = path.with_suffix(".json")
    try:
        check_directory()
        saved = json.loads(_read_regular(receipt, 1024, directory_fd))
        png = _read_regular(path, MAX_PNG_BYTES, directory_fd)
        if (saved == {"renderer": RENDER_VERSION, "source": source,
                     "sha256": hashlib.sha256(png).hexdigest()} and _png(png)):
            return str(path), png
    except (OSError, ValueError):
        pass
    png = _render(data)
    check_directory()
    _atomic(path, png, directory_fd)
    check_directory()
    _atomic(receipt, json.dumps({"renderer": RENDER_VERSION, "source": source,
                                "sha256": hashlib.sha256(png).hexdigest()}).encode(), directory_fd)
    return str(path), png


if __name__ == "__main__":
    raise SystemExit(worker_main() if sys.argv[1:] == ["--worker"] else 2)
