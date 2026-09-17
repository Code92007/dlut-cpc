from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import PurePosixPath


DEFAULT_REPOSITORY = "Code92007/dlut-cpc-resources"
DEFAULT_BRANCH = "main"
LEGACY_REPOSITORIES = ("Code92007/dlut-cpc",)
PDF_ROOT = ("resources", "pdfs")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def repository_config() -> tuple[str, str]:
    repository = os.environ.get("RESOURCE_GITHUB_REPOSITORY", DEFAULT_REPOSITORY).strip()
    branch = os.environ.get("RESOURCE_GITHUB_BRANCH", DEFAULT_BRANCH).strip()
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("RESOURCE_GITHUB_REPOSITORY 无效")
    if not BRANCH_PATTERN.fullmatch(branch):
        raise ValueError("RESOURCE_GITHUB_BRANCH 无效")
    return repository, branch


def normalize_pdf_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError("Git LFS PDF 路径无效")
    raw = value.strip()
    if "\\" in raw or raw.startswith("/"):
        raise ValueError("Git LFS PDF 路径必须使用仓库内的相对路径")
    path = PurePosixPath(raw)
    if path.parts[:2] != PDF_ROOT or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("PDF 必须放在 resources/pdfs 目录中")
    if len(path.parts) < 3 or path.suffix.casefold() != ".pdf":
        raise ValueError("资源文件必须是 PDF")
    return path.as_posix()


def github_pdf_record(value: str) -> dict:
    path = normalize_pdf_path(value)
    repository, branch = repository_config()
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_branch = urllib.parse.quote(branch, safe="")
    return {
        "url": f"https://github.com/{repository}/blob/{encoded_branch}/{encoded_path}?raw=1",
        "pdfPath": path,
        "originalFilename": PurePosixPath(path).name,
    }


def github_pdf_path(url: str) -> str:
    repository, branch = repository_config()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}:
        raise ValueError("PDF GitHub 地址无效")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("PDF GitHub 地址无效")
    parts = urllib.parse.unquote(parsed.path).strip("/").split("/")
    repositories = (repository, *LEGACY_REPOSITORIES)
    prefixes = []
    for candidate in repositories:
        owner, repo = candidate.split("/", 1)
        prefixes.append([owner.casefold(), repo.casefold(), "blob", branch])
    actual = [part.casefold() for part in parts[:3]] + parts[3:4]
    if len(parts) < 7 or actual not in prefixes:
        raise ValueError("PDF 地址不属于配置的 GitHub 仓库或分支")
    return normalize_pdf_path("/".join(parts[4:]))


def github_lfs_status() -> dict:
    repository, branch = repository_config()
    return {
        "repository": repository,
        "branch": branch,
        "directory": "/".join(PDF_ROOT),
        "repositoryUrl": f"https://github.com/{repository}/tree/{urllib.parse.quote(branch, safe='')}/resources/pdfs",
    }
