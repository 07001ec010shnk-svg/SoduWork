"""Local library objects must be writable under a deep data directory."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import library_storage


class LocalLibraryLongPathTests(unittest.TestCase):
    def test_round_trip_when_object_and_staging_paths_exceed_max_path(self):
        with tempfile.TemporaryDirectory() as folder:
            # The default Windows root is ~90 characters, for example
            # C:\Users\<name>\AppData\Local\SoduWork\source-service\application\hub-backend\uploads\library.
            # Pad the temporary root so object paths cross 260 characters.
            root = Path(folder) / ("r" * max(1, 120 - len(folder)))
            with patch.object(library_storage, "_local_root", return_value=root):
                library_storage.ensure_ready("local")
                key = library_storage.object_key("owner-1", "3259e758-4ecf-41b3-b385-0f86b67c3867")
                plain = root.resolve() / key
                self.assertGreater(len(str(plain)) + len(".part-") + 32, 260)
                library_storage.put_bytes(key, b"hello", "text/plain", backend="local")
                self.assertEqual(library_storage.read_bytes(key, backend="local"), b"hello")
                library_storage.delete(key, backend="local")
                with self.assertRaises(FileNotFoundError):
                    library_storage.open_stream(key, backend="local")

    def test_short_paths_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(library_storage, "_local_root", return_value=Path(folder)):
                path = library_storage._local_path("library/users/abc/item/object")
        self.assertEqual(path, Path(folder).resolve() / "library/users/abc/item/object")

    @unittest.skipUnless(os.name == "nt", "Windows path syntax")
    def test_windows_long_paths_use_the_extended_length_prefix(self):
        long_root = Path("C:/") / ("d" * 160)
        with patch.object(library_storage, "_local_root", return_value=long_root):
            path = library_storage._local_path("library/users/" + "a" * 64 + "/item/object")
        self.assertTrue(str(path).startswith("\\\\?\\C:\\"))


if __name__ == "__main__":
    unittest.main()
