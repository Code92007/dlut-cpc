import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import SiteHandler
from database import Database
from object_storage import ObjectStorage


class FakeStorageResponse:
    def __init__(self, payload: str, status: int = 200):
        self.payload = payload.encode()
        self.status = status

    def read(self, maximum: int) -> bytes:
        return self.payload[:maximum]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


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


class ObjectStorageTests(unittest.TestCase):
    def storage(self):
        return ObjectStorage(
            endpoint="https://account.r2.cloudflarestorage.com",
            bucket="dlut-cpc-resources",
            region="auto",
            access_key_id="access-key",
            secret_access_key="secret-key",
            max_file_bytes=1024 * 1024,
            storage_limit_bytes=10_000_000,
        )

    def test_upload_is_direct_and_uses_short_lived_signature(self):
        fixed = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)
        with patch("object_storage.dt.datetime") as clock:
            clock.now.return_value = fixed
            upload = self.storage().create_upload("讲义.pdf", 2048, "application/pdf")
        parsed = urlsplit(upload["uploadUrl"])
        query = parse_qs(parsed.query)
        self.assertRegex(upload["objectKey"], r"^resources/2026/09/[0-9a-f]{32}\.pdf$")
        self.assertEqual(query["X-Amz-Expires"], ["900"])
        self.assertEqual(query["X-Amz-SignedHeaders"], ["content-length;content-type;host"])
        self.assertNotIn("secret-key", upload["uploadUrl"])
        self.assertEqual(upload["headers"], {"Content-Type": "application/pdf"})

    def test_storage_configuration_is_all_or_nothing(self):
        keys = [
            "RESOURCE_S3_ENDPOINT", "RESOURCE_S3_BUCKET", "RESOURCE_S3_REGION",
            "RESOURCE_S3_ACCESS_KEY_ID", "RESOURCE_S3_SECRET_ACCESS_KEY", "RESOURCE_MAX_FILE_BYTES",
            "RESOURCE_STORAGE_LIMIT_BYTES",
        ]
        with patch.dict(os.environ, {}, clear=False):
            for key in keys:
                os.environ.pop(key, None)
            self.assertIsNone(ObjectStorage.from_env())
            os.environ["RESOURCE_S3_BUCKET"] = "only-one-setting"
            with self.assertRaisesRegex(ValueError, "配置不完整"):
                ObjectStorage.from_env()

    def test_storage_limit_cannot_be_configured_above_free_tier_buffer(self):
        values = {
            "RESOURCE_S3_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "RESOURCE_S3_BUCKET": "resources",
            "RESOURCE_S3_ACCESS_KEY_ID": "access",
            "RESOURCE_S3_SECRET_ACCESS_KEY": "secret",
            "RESOURCE_STORAGE_LIMIT_BYTES": "9000000001",
        }
        with patch.dict(os.environ, values, clear=True), self.assertRaisesRegex(ValueError, "9 GB"):
            ObjectStorage.from_env()

    def test_rejects_non_pdf_and_oversized_files(self):
        storage = self.storage()
        for name, size, media_type in (("notes.txt", 10, "text/plain"), ("notes.pdf", 2 * 1024 * 1024, "application/pdf")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                storage.create_upload(name, size, media_type)

    def test_bucket_usage_follows_pagination_and_sums_every_object(self):
        first = """<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>true</IsTruncated><NextContinuationToken>next/+ token</NextContinuationToken>
          <Contents><Key>resources/a.pdf</Key><Size>120</Size></Contents>
        </ListBucketResult>"""
        second = """<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>false</IsTruncated><Contents><Key>other.bin</Key><Size>80</Size></Contents>
        </ListBucketResult>"""
        with patch("object_storage.urllib.request.urlopen", side_effect=[FakeStorageResponse(first), FakeStorageResponse(second)]) as request:
            usage = self.storage().bucket_usage()
        self.assertEqual(usage["usedBytes"], 200)
        self.assertEqual(usage["objectCount"], 2)
        self.assertEqual(usage["_objectKeys"], {"resources/a.pdf", "other.bin"})
        self.assertIn("list-type=2", request.call_args_list[0].args[0].full_url)
        self.assertIn("continuation-token=next%2F%2B%20token", request.call_args_list[1].args[0].full_url)

    def test_prepare_upload_rejects_over_limit_and_fails_closed(self):
        storage = self.storage()
        usage = {"usedBytes": storage.storage_limit_bytes - 100, "objectCount": 1, "_objectKeys": set()}
        with patch.object(ObjectStorage, "bucket_usage", return_value=usage):
            with self.assertRaisesRegex(ValueError, "硬限制"):
                storage.prepare_upload("large.pdf", 101, "application/pdf")
        with patch.object(ObjectStorage, "bucket_usage", side_effect=OSError("R2 offline")):
            with self.assertRaisesRegex(OSError, "R2 offline"):
                storage.prepare_upload("small.pdf", 10, "application/pdf")

    def test_pending_uploads_are_reserved_against_the_limit(self):
        storage = self.storage()
        usage = {"usedBytes": storage.storage_limit_bytes - 200, "objectCount": 1, "_objectKeys": set()}
        first_key = "resources/2026/09/11111111111111111111111111111111.pdf"
        second_key = "resources/2026/09/22222222222222222222222222222222.pdf"
        with patch.object(ObjectStorage, "bucket_usage", return_value=usage):
            status = storage.reserve_upload(first_key, 100)
            self.assertEqual(status["remainingBytes"], 100)
            with self.assertRaisesRegex(ValueError, "硬限制"):
                storage.reserve_upload(second_key, 101)


if __name__ == "__main__":
    unittest.main()
