import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]


class DockerPackagingTests(unittest.TestCase):
    def test_image_copy_manifest_can_initialize_and_report_official_import(self):
        # Run only the literal files packaged by the Dockerfile, not the checkout.
        with tempfile.TemporaryDirectory() as temporary:
            image_root = Path(temporary) / "app"
            image_root.mkdir()
            for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
                if not line.startswith("COPY "):
                    continue
                instruction, source, destination = shlex.split(line)
                destination = image_root / PurePosixPath(destination).relative_to("/app")
                if (ROOT / source).is_dir():
                    shutil.copytree(ROOT / source, destination)
                else:
                    shutil.copy2(ROOT / source, destination)
            database = image_root / "runtime/site.sqlite3"
            environment = {**os.environ, "PYTHONPATH": "", "DATABASE_PATH": str(database),
                           "SITE_DATA_PATH": str(image_root / "data/site.json"), "PYTHONDONTWRITEBYTECODE": "1"}
            check = subprocess.run([sys.executable, "app.py", "--check"], cwd=image_root, env=environment,
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertIn("ok:", check.stdout)
            report = image_root / "runtime/import-report.json"
            merge = subprocess.run([sys.executable, "tools/merge_ccpc.py", "--database", str(database), "--report", str(report)],
                                   cwd=image_root, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(result["alreadyImported"])
            self.assertEqual(result["added"] + result["merged"] + result["conflict"], 50)


if __name__ == "__main__":
    unittest.main()
