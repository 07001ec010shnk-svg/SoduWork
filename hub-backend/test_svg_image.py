"""Actual bounded SVG rendering and staged-upload integrity, without a model."""
import base64
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.services import runtime_images as images, svg_image as svg


def document(body='<rect width="40" height="20" fill="red"/>', dimensions='width="40" height="20"'):
    return ('<svg xmlns="http://www.w3.org/2000/svg" ' + dimensions + '>' + body + '</svg>').encode()


def pixel(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 255, 0)).save(stream, format=format)
    return stream.getvalue()


class SvgImageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="soduwork-svg-图片-")
        self.addCleanup(self.folder.cleanup)
        self.root = str(Path(self.folder.name).resolve())

    def test_real_renderer_produces_pixels_and_preserves_source(self):
        original = Path(self.root, "图片 原件.svg")
        original.write_bytes(document())
        before = (original.read_bytes(), original.stat().st_mtime_ns)
        cfg = images.prepare({}, [{"path": str(original), "name": original.name,
                                  "contentType": "image/svg+xml"}], self.root, agent="dsh")
        item = cfg[images.CFG_KEY][0]
        self.assertEqual(item["mimeType"], "image/png")
        self.assertEqual(item["source"]["path"], str(original))
        self.assertEqual((original.read_bytes(), original.stat().st_mtime_ns), before)
        with Image.open(item["path"]) as rendered:
            self.assertEqual(rendered.size, (40, 20))
            self.assertEqual(rendered.getpixel((10, 10)), (255, 0, 0, 255))

    def test_original_and_preview_are_both_checked_before_transport(self):
        source = Path(self.root, "input.svg")
        for changed in ("source", "preview"):
            with self.subTest(changed=changed):
                source.write_bytes(document())
                cfg = images.prepare({}, [{"path": str(source), "name": source.name}], self.root, agent="qwen")
                raw = images.environment(cfg, self.root)
                target = source if changed == "source" else Path(cfg["images"][0])
                target.write_bytes(b"changed")
                with self.assertRaisesRegex(images.ImageInputError, "变化"):
                    images.prompt("Look at this image", {"promptCapabilities": {"image": True}}, raw)

    def test_validated_cache_reuses_png_and_repairs_a_changed_cache(self):
        path, first = svg.derived_png(document(), self.root)
        with patch.object(svg, "_render", side_effect=AssertionError("Unexpected rerender")):
            again, second = svg.derived_png(document(), self.root)
        self.assertEqual((again, second), (path, first))
        Path(path).write_bytes(b"tampered")
        with patch.object(svg, "_render", wraps=svg._render) as render:
            _, fixed = svg.derived_png(document(), self.root)
        render.assert_called_once()
        self.assertEqual(fixed, first)

    @unittest.skipIf(os.name == 'nt', 'Creating directory symlinks may require Windows privileges')
    def test_cache_directory_replacement_cannot_redirect_output(self):
        original = Path(self.root, 'archive')
        original.mkdir()
        moved, outside = Path(self.root, 'moved'), Path(self.root, 'outside')
        outside.mkdir()
        rendered = svg._render(document())

        def replace_directory(_data):
            original.rename(moved)
            original.symlink_to(outside, target_is_directory=True)
            return rendered

        with patch.object(svg, '_render', side_effect=replace_directory), self.assertRaises(svg.SvgImageError):
            svg.derived_png(document(), str(original))
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Named pipes use a different API on Windows')
    def test_cache_named_pipe_is_replaced_without_waiting_for_a_writer(self):
        path, original = svg.derived_png(document(), self.root)
        for target in (Path(path), Path(path).with_suffix('.json')):
            with self.subTest(target=target.suffix):
                target.unlink()
                os.mkfifo(target)
                _, repaired = svg.derived_png(document(), self.root)
                self.assertEqual(repaired, original)
                self.assertTrue(target.is_file())

    def test_internal_references_and_inline_rasters_render(self):
        for mime, format in (("image/png", "PNG"), ("image/jpeg", "JPEG"),
                             ("image/gif", "GIF"), ("image/webp", "WEBP")):
            with self.subTest(mime=mime):
                encoded = base64.b64encode(pixel(format)).decode()
                source = document('<defs><rect id="shape" width="20" height="20" fill="red"/></defs>'
                    '<use href="#shape"/><image x="20" width="20" height="20" href="data:' + mime + ';base64,' + encoded + '"/>')
                path, _ = svg.derived_png(source, self.root)
                with Image.open(path) as image:
                    self.assertEqual(image.getpixel((5, 5)), (255, 0, 0, 255))
                    self.assertGreater(image.getpixel((30, 10))[1], 240)

    def test_fragment_paint_and_style_remain_available(self):
        source = document('<style>/* Palette */ .color {fill: url(#渐变)}</style>'
            '<defs><linearGradient id="渐变"><stop stop-color="red"/>'
            '<stop offset="1" stop-color="blue"/></linearGradient></defs>'
            '<rect class="color" width="40" height="20"/>')
        path, _ = svg.derived_png(source, self.root)
        with Image.open(path) as image:
            self.assertGreater(image.getpixel((2, 10))[0], image.getpixel((37, 10))[0])

    def test_bom_prolog_percent_dimensions_and_viewbox(self):
        source = b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>' + document(
            dimensions='width="100%" height="100%" viewBox="0 0 40 20"')
        path, _ = svg.derived_png(source, self.root)
        with Image.open(path) as image:
            self.assertEqual(image.size, (40, 20))
            self.assertEqual(image.getpixel((5, 5)), (255, 0, 0, 255))
        self.assertEqual(svg.validate(document(dimensions='width="80" viewBox="0 0 40 20"'))[1:], (80, 40))

    def test_large_canvas_downscales_with_preserved_aspect_ratio(self):
        text, width, height = svg.validate(document(dimensions='width="20000" height="10000"'))
        self.assertLessEqual(max(width, height), svg.MAX_EDGE)
        self.assertLessEqual(width * height, svg.MAX_PIXELS)
        self.assertAlmostEqual(width / height, 2, places=2)
        self.assertIn("svg", text)
        path, _ = svg.derived_png(document('<rect width="20000" height="10000" fill="red"/>',
                                         dimensions='width="100%" height="100%" viewBox="0 0 20000 10000"'), self.root)
        with Image.open(path) as image:
            self.assertEqual(image.size, (width, height))
            self.assertEqual(image.getpixel((width - 1, height - 1)), (255, 0, 0, 255))

    def test_external_resource_variants_are_rejected_before_worker_start(self):
        for target in ("https://example.invalid/private.png", "file:///private/secret.png",
                       "/private/secret.png", "../../secret.png", "C:\\private\\secret.png",
                       "//server/share/image.png", "#local-file.png"):
            with self.subTest(target=target), patch.object(svg, "_render") as render:
                with self.assertRaises(svg.SvgImageError):
                    svg.derived_png(document('<image href="' + target + '"/>'), self.root)
                render.assert_not_called()
        with self.assertRaises(svg.SvgImageError):
            svg.validate(document('<filter id="f"><feImage href="#missing-local-file.png"/></filter>'))

    def test_nested_svg_data_css_escape_and_active_content_are_rejected(self):
        embedded = base64.b64encode(document()).decode()
        cases = ['<image href="data:image/svg+xml;base64,' + embedded + '"/>',
                 '<style>@import "https://example.invalid/x.css";</style>',
                 '<style>@/**/IMPORT "file:///private/style.css";</style>',
                 '<style>@\\69mport "file:///private/style.css";</style>',
                 '<rect style="fill:u&#x72;l(file:///private/data.png)"/>',
                 '<rect style="fill:url(file:///private/data.png)"/>',
                 '<rect style="fill:u\\72l(file:///private/data.png)"/>',
                 '<rect style="fill:u/**/rl(file:///private/data.png)"/>',
                 '<script>alert(1)</script>', '<foreignObject><div>HTML</div></foreignObject>',
                 '<animate attributeName="x"/>', '<rect onclick="alert(1)"/>',
                 '<g xml:base="file:///private/"><image href="local.png"/></g>']
        for body in cases:
            with self.subTest(body=body), self.assertRaises(svg.SvgImageError):
                svg.validate(document(body))

    def test_filter_image_fragment_never_reads_a_same_named_local_file(self):
        for target in ('<metadata id="local.png"/>', '<foreign xmlns="urn:unused" id="local.png"/>',
                       '<rect id="local.png" display="none"/>', '<rect id="local.png" width="1" height="1"/>'):
            with self.subTest(target=target):
                source = document(target + '<filter id="f"><feImage href="#local.png"/></filter>'
                                  '<rect width="40" height="20" fill="red" filter="url(#f)"/>')
                Path(self.root, "#local.png").write_bytes(pixel())
                with patch.object(svg, "_render") as render, self.assertRaises(svg.SvgImageError):
                    svg.derived_png(source, self.root)
                render.assert_not_called()
                worker = subprocess.run([svg.sys.executable, str(Path(svg.__file__).resolve()), '--worker'],
                                        input=source, cwd=self.root, capture_output=True, timeout=10)
                self.assertNotEqual(worker.returncode, 0)
                self.assertEqual(worker.stdout, b'')

    def test_many_unterminated_css_comments_are_rejected_before_rendering(self):
        source = document('<style>' + '/*x' * 100000 + '</style>')
        with patch.object(svg, '_render') as render, self.assertRaises(svg.SvgImageError):
            svg.derived_png(source, self.root)
        render.assert_not_called()

    def test_dtd_entity_and_malformed_svg_are_rejected(self):
        for source in (b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///private/secret">]>' + document('&x;'),
                       b'<?xml-stylesheet href="file:///private/style.css"?>' + document(),
                       b'<svg><broken>', b'<html/>', b'\xff\xfe<svg/>', b''):
            with self.subTest(source=source), self.assertRaises(svg.SvgImageError):
                svg.validate(source)

    def test_source_node_depth_and_dimension_limits(self):
        for source in (document('<g>' * 130 + '</g>' * 130),
                       document('<g/>' * (svg.MAX_NODES + 1)),
                       document(dimensions='width="1000001" height="1"'),
                       document(dimensions='width="0" height="10"'),
                       document(dimensions='viewBox="0 0 NaN 1"'),
                       b' ' * (svg.MAX_SVG_BYTES + 1)):
            with self.subTest(size=len(source)), self.assertRaises(svg.SvgImageError):
                svg.validate(source)

    def test_malformed_inline_raster_fails_in_bounded_worker(self):
        encoded = base64.b64encode(pixel()[:-16]).decode()
        with self.assertRaises(svg.SvgImageError):
            svg.derived_png(document('<image href="data:image/png;base64,' + encoded + '"/>'), self.root)

    def test_worker_timeout_missing_dependency_and_invalid_output_do_not_degrade(self):
        with patch.object(svg.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 10)):
            with self.assertRaisesRegex(svg.SvgImageError, "超时"):
                svg.derived_png(document(), self.root)
        for code, output in ((7, b""), (3, b""), (0, b"not PNG"), (0, b"a" * (svg.MAX_PNG_BYTES + 1))):
            with self.subTest(code=code, size=len(output)), patch.object(svg.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, stdout=output, stderr=b"private detail")):
                with self.assertRaises(svg.SvgImageError) as failure:
                    svg.derived_png(document(), self.root)
                self.assertNotIn("private detail", str(failure.exception))

    def test_frozen_worker_uses_runner_entry_without_source_script(self):
        with patch.object(svg.sys, "frozen", True, create=True), patch.object(svg.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 7, stdout=b"", stderr=b"")) as worker:
            with self.assertRaises(svg.SvgImageError):
                svg._render(document())
        self.assertEqual(worker.call_args.args[0], [svg.sys.executable, "--soduwork-svg-raster-worker"])
        self.assertEqual(worker.call_args.kwargs["timeout"], 10)

    def test_pi_and_unspecified_agent_keep_existing_original_transport(self):
        source = Path(self.root, "input.svg")
        source.write_bytes(document())
        for agent in ("pi", "", "unknown"):
            with self.subTest(agent=agent), patch.object(svg, "derived_png") as render:
                cfg = images.prepare({}, [{"path": str(source), "name": source.name}], self.root, agent=agent)
                self.assertEqual(cfg[images.CFG_KEY][0]["mimeType"], "image/svg+xml")
                self.assertEqual(cfg["images"], [str(source)])
                render.assert_not_called()

    def test_symlink_and_nonarchive_sources_are_rejected_before_rendering(self):
        source = Path(self.root, "input.svg")
        source.write_bytes(document())
        with patch.object(images.os.path, "islink", return_value=True), patch.object(svg, "derived_png") as render:
            with self.assertRaises(images.ImageInputError):
                images.prepare({}, [{"path": str(source), "name": source.name}], self.root, agent="kimi")
            render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
