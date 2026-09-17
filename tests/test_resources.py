import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import SiteHandler
from database import Database
from github_lfs import github_lfs_status, github_pdf_path, github_pdf_record, normalize_pdf_path
from tools import check_resource_pdfs


class ResourceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize({"meta": {}, "honors": [], "training": []})

    def tearDown(self):
        self.temporary.cleanup()

    def test_resources_keep_drafts_private_and_preserve_search_metadata(self):
        public_id = self.database.save_resource({
            "title": "最短路专题",
            "resourceType": "github",
            "category": "图论",
            "difficulty": "intermediate",
            "description": "Dijkstra 与势能最短路",
            "tags": ["图论", "模板"],
            "url": "https://github.com/example/shortest-path",
            "published": True,
        }, created_by="admin")
        draft_id = self.database.save_resource({
            "title": "未发布讲义", "resourceType": "link", "category": "其他", "difficulty": "all",
            "tags": [], "url": "https://example.com/draft", "published": False,
        })

        public = self.database.list_resources()
        self.assertEqual([item["id"] for item in public], [public_id])
        self.assertEqual(public[0]["tags"], ["图论", "模板"])
        self.assertNotIn("objectKey", public[0])
        all_items = self.database.list_resources(include_drafts=True)
        self.assertEqual({item["id"] for item in all_items}, {public_id, draft_id})
        self.assertEqual(next(item for item in all_items if item["id"] == public_id)["createdBy"], "admin")

        changed = {**all_items[0], "title": "草稿已发布", "published": True}
        self.database.save_resource(changed, resource_id=changed["id"])
        self.assertEqual(len(self.database.list_resources()), 2)
        removed = self.database.delete_resource(public_id)
        self.assertEqual(removed["title"], "最短路专题")
        self.assertIsNone(self.database.get_resource(public_id, include_drafts=True))

    def test_resource_payload_validation_rejects_unsafe_links_and_bad_tags(self):
        valid = SiteHandler._resource_body({
            "title": "仓库", "resourceType": "github", "category": "模板", "difficulty": "beginner",
            "description": "", "tags": ["C++", "C++", "入门"], "url": "https://github.com/dlut/example", "published": True,
        })
        self.assertEqual(valid["tags"], ["C++", "入门"])
        for url in ("javascript:alert(1)", "https://github.com.attacker.example/repo", "https://example.com/repo"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SiteHandler._resource_body({**valid, "resourceType": "github", "url": url})
        with self.assertRaises(ValueError):
            SiteHandler._resource_body({**valid, "tags": ["x"] * 13})


class GitHubLfsTests(unittest.TestCase):
    def test_pdf_path_builds_and_recovers_scoped_github_url(self):
        values = {"RESOURCE_GITHUB_REPOSITORY": "Code92007/dlut-cpc", "RESOURCE_GITHUB_BRANCH": "main"}
        with patch.dict(os.environ, values, clear=False):
            record = github_pdf_record("resources/pdfs/图论/网络流 讲义.pdf")
            self.assertEqual(record["originalFilename"], "网络流 讲义.pdf")
            self.assertIn("%E5%9B%BE%E8%AE%BA", record["url"])
            self.assertEqual(github_pdf_path(record["url"]), record["pdfPath"])
            status = github_lfs_status()
        self.assertEqual(status["repository"], "Code92007/dlut-cpc")
        self.assertEqual(status["directory"], "resources/pdfs")

    def test_pdf_paths_are_confined_to_resource_directory(self):
        for path in (
            "notes.pdf", "resources/notes.pdf", "resources/pdfs/../private.pdf",
            "/resources/pdfs/notes.pdf", "resources\\pdfs\\notes.pdf", "resources/pdfs/notes.txt",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_pdf_path(path)

    def test_invalid_repository_configuration_is_rejected(self):
        with patch.dict(os.environ, {"RESOURCE_GITHUB_REPOSITORY": "bad repository"}, clear=False), \
             self.assertRaisesRegex(ValueError, "REPOSITORY"):
            github_lfs_status()

    def test_lfs_pointer_uses_represented_size_and_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_root = root / "resources" / "pdfs"
            pdf_root.mkdir(parents=True)
            pointer = pdf_root / "notes.pdf"
            pointer.write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                f"oid sha256:{'a' * 64}\nsize 123456\n",
                encoding="ascii",
            )
            self.assertEqual(check_resource_pdfs.represented_size(pointer), 123456)
            with patch.object(check_resource_pdfs, "ROOT", root), \
                 patch.object(check_resource_pdfs, "PDF_ROOT", pdf_root), \
                 patch.object(check_resource_pdfs, "STORAGE_LIMIT_BYTES", 100_000), \
                 patch.object(check_resource_pdfs, "historical_pdf_objects", return_value={}), \
                 self.assertRaisesRegex(ValueError, "9 GB"):
                check_resource_pdfs.check_resource_pdfs()


if __name__ == "__main__":
    unittest.main()
