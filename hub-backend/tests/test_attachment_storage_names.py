"""Local attachment storage must accept any filename the upload routes allow."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import storage


class LocalAttachmentNameTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.enterContext(patch.object(storage, "_LOCAL_DIR", self.folder.name))
        self.enterContext(patch.object(storage, "backend_name", return_value="local"))

    def _round_trip(self, key):
        data = ("payload for " + key).encode("utf-8")
        self.assertEqual(storage.put(key, data, "application/octet-stream"), "local")
        self.assertEqual(storage.get(key, "local"), data)
        handle = storage.open_stream(key, "local")
        self.assertEqual(b"".join(storage.iter_stream(handle)), data)
        path = Path(storage._local_path(key))
        self.assertEqual(path.parent, Path(self.folder.name))
        self.assertEqual(path.read_bytes(), data)
        storage.delete(key, "local")
        self.assertFalse(path.exists())
        return path

    def test_short_names_keep_their_existing_location(self):
        key = "att/task-1/attachment-1/brief.pdf"
        path = self._round_trip(key)
        self.assertEqual(path.name, "att__task-1__attachment-1__brief.pdf")

    def test_long_multibyte_name_is_stored(self):
        # Upload routes keep up to 200 characters; 200 CJK characters are
        # 600 UTF-8 bytes, beyond the 255-byte filename limit on Linux.
        name = "华东区域客户满意度调研报告" * 15 + ".pdf"
        key = "att/3259e758-4ecf-41b3-b385-0f86b67c3867/37d15ab5-dccf-4e25-8d62-bea1b0e21063/" + name[-200:]
        path = self._round_trip(key)
        self.assertTrue(path.name.endswith(".pdf"))
        self.assertLessEqual(len(os.fsencode(path.name)), 255)

    def test_distinct_long_names_do_not_collide(self):
        prefix = "att/task-1/attachment-1/" + "长" * 190
        self.assertNotEqual(storage._local_path(prefix + "a.txt"), storage._local_path(prefix + "b.txt"))

    @unittest.skipUnless(os.name == "nt", "Windows filename rules")
    def test_windows_reserved_characters_are_stored_in_the_main_stream(self):
        key = "att/task-1/attachment-1/会议纪要: 11月17日.docx"
        path = self._round_trip(key)
        # Without hashing, NTFS treats ":" as an alternate data stream and
        # leaves an empty "att__…__会议纪要" file in the uploads directory.
        self.assertNotIn(":", path.name)
        self.assertEqual(sorted(os.listdir(self.folder.name)), [])

    def test_existing_object_under_the_legacy_name_is_still_found(self):
        key = "att/task-1/attachment-1/" + "x" * 150 + ".txt"
        legacy = Path(self.folder.name) / key.replace("/", "__")
        with patch.object(storage, "_native_name_ok", return_value=False):
            legacy.write_bytes(b"written before this change")
            self.assertEqual(Path(storage._local_path(key)), legacy)
            self.assertEqual(storage.get(key, "local"), b"written before this change")


if __name__ == "__main__":
    unittest.main()
