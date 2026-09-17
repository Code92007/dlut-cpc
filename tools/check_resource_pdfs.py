#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDF_ROOT = ROOT / "resources" / "pdfs"
STORAGE_LIMIT_BYTES = 9_000_000_000
LFS_POINTER_PATTERN = re.compile(
    rb"\Aversion https://git-lfs\.github\.com/spec/v1\n"
    rb"oid sha256:([0-9a-f]{64})\nsize (\d+)\n?\Z"
)


def represented_object(payload: bytes) -> tuple[str, int]:
    match = LFS_POINTER_PATTERN.fullmatch(payload) if len(payload) <= 1024 else None
    if match:
        return match.group(1).decode(), int(match.group(2))
    return hashlib.sha256(payload).hexdigest(), len(payload)


def represented_file(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    if size <= 1024:
        return represented_object(path.read_bytes())
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), size


def represented_size(path: Path) -> int:
    return represented_file(path)[1]


def historical_pdf_objects() -> dict[str, int]:
    output = subprocess.run(
        ["git", "rev-list", "--objects", "--all", "--", "resources/pdfs"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    blobs = {}
    for line in output:
        object_id, _, path = line.partition(" ")
        if path.casefold().endswith(".pdf"):
            blobs[object_id] = path
    objects = {}
    for object_id in blobs:
        blob_size = int(subprocess.run(
            ["git", "cat-file", "-s", object_id], cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip())
        if blob_size > 1024:
            objects[object_id] = blob_size
            continue
        payload = subprocess.run(
            ["git", "cat-file", "blob", object_id], cwd=ROOT, check=True, capture_output=True,
        ).stdout
        oid, size = represented_object(payload)
        objects[oid] = size
    return objects


def check_resource_pdfs() -> tuple[int, int]:
    files = sorted(path for path in PDF_ROOT.rglob("*.pdf") if path.is_file() and not path.is_symlink())
    objects = historical_pdf_objects()
    for path in files:
        oid, size = represented_file(path)
        objects[oid] = size
    total = sum(objects.values())
    if total > STORAGE_LIMIT_BYTES:
        raise ValueError(f"PDF 历史对象总大小 {total} 字节超过 9 GB 硬上限")
    if files:
        relative = [str(path.relative_to(ROOT)) for path in files]
        attributes = subprocess.run(
            ["git", "check-attr", "filter", "--", *relative], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        if any(not line.endswith(": filter: lfs") for line in attributes):
            raise ValueError("存在未由 Git LFS 跟踪的 PDF")
    return len(files), total


def main() -> None:
    count, total = check_resource_pdfs()
    print(f"ok: {count} PDFs, {total} / {STORAGE_LIMIT_BYTES} bytes")


if __name__ == "__main__":
    main()
