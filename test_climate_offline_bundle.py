import json
import tempfile
import unittest
from pathlib import Path

from build_climate_offline_bundle import FILES, build_bundle


class OfflineBundleTests(unittest.TestCase):
    def test_bundle_contains_local_json_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            expected = {}
            for name in FILES:
                expected[name] = {"schemaVersion": 1, "source": name}
                (root / name).write_text(json.dumps(expected[name]), encoding="utf-8")
            text = build_bundle(root).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("window.CResistClimateOffline="))
            self.assertEqual(json.loads(text.removeprefix("window.CResistClimateOffline=").removesuffix(";\n")), expected)

    def test_rejects_unsupported_schema_without_overwriting_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            output = root / "data" / "climate-offline.js"
            output.write_text("original", encoding="utf-8")
            for name in FILES:
                (root / name).write_text('{"schemaVersion":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                build_bundle(root)
            self.assertEqual(output.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
