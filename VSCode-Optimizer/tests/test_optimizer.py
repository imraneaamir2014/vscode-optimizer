import tempfile
import unittest
from pathlib import Path

from optimizer import VSCodeOptimizer, CACHE_NAMES

class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.user_data = self.base / "UserData"
        self.extensions = self.base / "extensions"
        self.backups = self.base / "backups"
        self.user_data.mkdir()
        self.extensions.mkdir()
        self.app = VSCodeOptimizer(self.user_data, self.extensions, self.backups)
        self.app._check_vscode_running = lambda: False

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_only_reports_allowlisted_cache(self):
        cache = self.user_data / "Cache"
        cache.mkdir()
        (cache / "sample.bin").write_bytes(b"abc")
        (self.user_data / "User").mkdir()
        (self.user_data / "User" / "settings.json").write_text("keep")
        preview = self.app.preview_clean()
        self.assertEqual(preview["files_found"], 1)
        self.assertEqual(preview["bytes_found"], 3)
        self.assertEqual([p.name for p in preview["targets"]], ["Cache"])

    def test_clean_requires_confirmation(self):
        with self.assertRaises(ValueError):
            self.app.clean_cache()

    def test_clean_makes_verified_backup_and_removes_only_cache_files(self):
        cache = self.user_data / "GPUCache"
        cache.mkdir()
        payload = cache / "data.bin"
        payload.write_bytes(b"cache-data")
        protected = self.user_data / "User"
        protected.mkdir()
        settings = protected / "settings.json"
        settings.write_text('{"editor.fontSize": 14}')
        result = self.app.clean_cache(confirm=True)
        self.assertTrue(result.backup_path.exists())
        self.assertFalse(payload.exists())
        self.assertTrue(settings.exists())
        self.assertEqual(result.files_removed, 1)
        self.assertEqual(result.bytes_removed, len(b"cache-data"))

    def test_extension_analysis_never_deletes_extensions(self):
        ext = self.extensions / "publisher.extension-1"
        ext.mkdir()
        (ext / "extension.js").write_bytes(b"x" * 1024)
        result = self.app.analyze_extensions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, ext.name)
        self.assertTrue((ext / "extension.js").exists())

    def test_running_vscode_blocks_cleanup(self):
        cache = self.user_data / "Cache"
        cache.mkdir()
        (cache / "x").write_text("x")
        self.app._check_vscode_running = lambda: True
        with self.assertRaises(RuntimeError):
            self.app.clean_cache(confirm=True)

    def test_unknown_folders_are_never_targets(self):
        for name in ("logs", "CachedExtensions", "workspaceStorage", "User"):
            (self.user_data / name).mkdir()
        self.assertEqual(self.app.preview_clean()["targets"], [])

if __name__ == "__main__":
    unittest.main()
