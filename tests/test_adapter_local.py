"""End-to-end adapter tests against real local git repositories.

These tests do not touch the network and do not need IronRAG running:
they exercise the adapter's iter_items + fetch against repos created
in tmp dirs, which is the slice of the connector that actually
interacts with git.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gitrepos_connector.adapter import GitReposAdapter
from gitrepos_connector.mapping import KIND_FILE, file_external_key, parse_file_item_id

from .conftest import make_settings, write_repos_yaml

pytestmark = pytest.mark.asyncio


async def _collect(adapter: GitReposAdapter):
    return [ref async for ref in adapter.iter_items()]


async def test_iter_local_repo_single_branch(tmp_repo_factory, state_root: Path) -> None:
    repo = tmp_repo_factory("alpha")
    repo.write("README.md", "# alpha\n")
    repo.write("src/main.py", "print('hello')\n")
    repo.commit("init")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "alpha",
                "local_path": str(repo.path),
                "branches": ["main"],
                "include": ["**/*.md", "**/*.py"],
                "facts": {"project": "alpha"},
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)

    refs = await _collect(adapter)
    paths = sorted(r.routing_facts["path"] for r in refs)
    assert paths == ["README.md", "src/main.py"]
    for ref in refs:
        assert ref.kind == KIND_FILE
        assert ref.routing_facts["repo"] == "alpha"
        assert ref.routing_facts["branch"] == "main"
        assert ref.routing_facts["project"] == "alpha"
        assert ref.change_token  # blob sha must be present


async def test_multibranch_emits_one_ref_per_branch(
    tmp_repo_factory, state_root: Path
) -> None:
    repo = tmp_repo_factory("multi")
    repo.write("shared.md", "common")
    repo.commit("init")
    repo.switch("feature", create=True)
    repo.write("feature-only.md", "feature")
    repo.commit("feature branch addition")
    repo.switch("main")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "multi",
                "local_path": str(repo.path),
                "branches": ["main", "feature"],
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)

    refs = await _collect(adapter)
    by_branch: dict[str, set[str]] = {}
    for ref in refs:
        by_branch.setdefault(ref.routing_facts["branch"], set()).add(
            ref.routing_facts["path"]
        )
    assert by_branch == {
        "main": {"shared.md"},
        "feature": {"shared.md", "feature-only.md"},
    }


async def test_include_exclude_filters(tmp_repo_factory, state_root: Path) -> None:
    repo = tmp_repo_factory("filtered")
    repo.write("docs/intro.md", "intro")
    repo.write("docs/drafts/notes.md", "drafts")
    repo.write("src/server.py", "py")
    repo.write("dist/bundle.js", "compiled")
    repo.commit("init")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "filtered",
                "local_path": str(repo.path),
                "branches": ["main"],
                "include": ["docs/**/*.md", "src/**/*.py"],
                "exclude": ["docs/drafts/**"],
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)

    refs = await _collect(adapter)
    paths = sorted(r.routing_facts["path"] for r in refs)
    # docs/drafts is excluded; dist/* never matched include; .git/* is in DEFAULT_EXCLUDES.
    assert paths == ["docs/intro.md", "src/server.py"]


async def test_fetch_returns_blob_payload(tmp_repo_factory, state_root: Path) -> None:
    repo = tmp_repo_factory("payload")
    repo.write("hello.md", "# Hi\nThe quick brown fox.")
    repo.commit("init")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "payload",
                "local_path": str(repo.path),
                "branches": ["main"],
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)

    refs = await _collect(adapter)
    ref = next(r for r in refs if r.routing_facts["path"] == "hello.md")
    item = await adapter.fetch(ref)
    assert item is not None
    assert item.payload == b"# Hi\nThe quick brown fox."
    assert item.mime_type == "text/markdown"
    assert item.file_name == "hello.md"
    assert item.title == "payload:main — hello.md"
    assert item.document_hint == "payload:main:hello.md"


async def test_change_token_advances_on_content_change(
    tmp_repo_factory, state_root: Path
) -> None:
    repo = tmp_repo_factory("evolving")
    repo.write("doc.md", "v1")
    repo.commit("v1")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "evolving",
                "local_path": str(repo.path),
                "branches": ["main"],
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)

    refs_v1 = await _collect(adapter)
    blob_v1 = refs_v1[0].change_token
    assert blob_v1

    # Empty commit must NOT change the blob sha — same content, same blob.
    repo.commit("touch")
    refs_after_empty = await _collect(GitReposAdapter(settings))
    assert refs_after_empty[0].change_token == blob_v1

    # Real content change must change the blob sha.
    repo.write("doc.md", "v2 — new content")
    repo.commit("update")
    refs_v2 = await _collect(GitReposAdapter(settings))
    assert refs_v2[0].change_token != blob_v1


async def test_max_file_bytes_skips_large_files(
    tmp_repo_factory, state_root: Path
) -> None:
    repo = tmp_repo_factory("sized")
    repo.write("small.md", "ok")
    repo.write("big.bin", b"x" * 4096)
    repo.commit("init")

    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "sized",
                "local_path": str(repo.path),
                "branches": ["main"],
                "max_file_bytes": 1024,
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)
    refs = await _collect(adapter)
    paths = sorted(r.routing_facts["path"] for r in refs)
    assert paths == ["small.md"]


async def test_routing_facts_payload(tmp_repo_factory, state_root: Path) -> None:
    repo = tmp_repo_factory("facts-rich")
    repo.write("apps/web/main.tsx", "x")
    repo.commit("init")
    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [
            {
                "name": "facts-rich",
                "local_path": str(repo.path),
                "branches": ["main"],
                "facts": {"team": "frontend", "tier": "production"},
            }
        ],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)
    refs = await _collect(adapter)
    assert len(refs) == 1
    facts = refs[0].routing_facts
    assert facts["repo"] == "facts-rich"
    assert facts["branch"] == "main"
    assert facts["path"] == "apps/web/main.tsx"
    assert facts["dir"] == "apps/web"
    assert facts["ext"] == "tsx"
    assert facts["top_dir"] == "apps"
    assert facts["team"] == "frontend"
    assert facts["tier"] == "production"
    # repo_url omitted for local-path repos
    assert "repo_url" not in facts


async def test_external_key_round_trip_through_adapter(
    tmp_repo_factory, state_root: Path
) -> None:
    repo = tmp_repo_factory("rt")
    repo.write("nested/path/file.txt", "hi")
    repo.commit("init")
    repos_yaml = write_repos_yaml(
        state_root / "repos.yaml",
        [{"name": "rt", "local_path": str(repo.path), "branches": ["main"]}],
    )
    settings = make_settings(state_root=state_root, repos_yaml=repos_yaml)
    adapter = GitReposAdapter(settings)
    refs = await _collect(adapter)
    [ref] = refs
    expected_key = file_external_key("rt", "main", "nested/path/file.txt")
    assert ref.external_key == expected_key
    parsed = adapter.parse_external_key(ref.external_key)
    assert parsed is not None
    kind, item_id = parsed
    assert kind == KIND_FILE
    assert parse_file_item_id(item_id) == ("rt", "main", "nested/path/file.txt")
