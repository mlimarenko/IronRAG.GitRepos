"""Thin async wrapper around the ``git`` CLI.

We shell out to ``git`` rather than depend on ``pygit2``/``GitPython``
to keep the dependency surface small and the container image lean.
All commands run under :func:`asyncio.create_subprocess_exec` so the
sync loop's bounded concurrency translates directly into bounded
git invocation parallelism.

Repository state lives in two flavors:

* **Remote-tracked**: bare clone at ``<clone_root>/<repo_slug>.git``.
  ``ensure_repo`` clones on first call, then ``git fetch --prune``
  before every enumeration.
* **Local path**: enumerated directly from the working tree. The
  current ``HEAD`` of each requested branch is read via
  ``git rev-parse``. No clone, no fetch.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path

from .observability import get_logger
from .repos import RepoSpec

log = get_logger(__name__)


class GitCommandError(RuntimeError):
    """Raised when a ``git`` invocation exits non-zero."""

    def __init__(self, cmd: list[str], code: int, stderr: str) -> None:
        super().__init__(
            f"git command failed (exit {code}): {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"stderr: {stderr.strip()}"
        )
        self.cmd = cmd
        self.code = code
        self.stderr = stderr


@dataclass(frozen=True)
class TreeEntry:
    path: str
    blob_sha: str
    size: int


class GitClient:
    """Per-process git executor with a single clone root."""

    def __init__(
        self,
        clone_root: Path,
        *,
        fetch_timeout_seconds: float = 120.0,
        command_timeout_seconds: float = 60.0,
    ) -> None:
        self._clone_root = clone_root
        self._fetch_timeout = fetch_timeout_seconds
        self._cmd_timeout = command_timeout_seconds
        self._clone_root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def _git_dir(self, spec: RepoSpec) -> Path:
        if spec.local_path is not None:
            # Local working tree: git operations work against
            # `local_path` directly; .git is auto-resolved.
            return spec.local_path
        return self._clone_root / f"{spec.name}.git"

    def _is_bare(self, spec: RepoSpec) -> bool:
        return spec.local_path is None

    async def _run(
        self,
        cwd: Path,
        args: list[str],
        *,
        timeout: float | None = None,
        capture_stdout: bool = True,
    ) -> bytes:
        cmd = ["git", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE if capture_stdout else None,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout or self._cmd_timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise GitCommandError(cmd, -1, "timeout") from None
        if proc.returncode != 0:
            raise GitCommandError(
                cmd,
                proc.returncode or -1,
                stderr.decode("utf-8", errors="replace"),
            )
        return stdout or b""

    def _lock_for(self, spec: RepoSpec) -> asyncio.Lock:
        # Serialize fetch/clone for one repo across concurrent sweeps.
        return self._locks.setdefault(spec.name, asyncio.Lock())

    async def ensure_repo(self, spec: RepoSpec) -> None:
        """Clone if missing, fetch otherwise. No-op for `local_path` repos."""
        if spec.local_path is not None:
            if not spec.local_path.is_dir():
                raise GitCommandError(
                    ["git", "rev-parse", "--git-dir"],
                    -1,
                    f"local_path {spec.local_path} does not exist",
                )
            return

        async with self._lock_for(spec):
            git_dir = self._git_dir(spec)
            if not git_dir.exists():
                log.info("gitrepos.clone.start", repo=spec.name, url=spec.url)
                await self._run(
                    self._clone_root,
                    [
                        "clone",
                        "--bare",
                        "--filter=blob:none",
                        spec.url or "",
                        str(git_dir),
                    ],
                    timeout=self._fetch_timeout,
                )
                log.info("gitrepos.clone.done", repo=spec.name)
            else:
                log.info("gitrepos.fetch.start", repo=spec.name)
                await self._run(
                    git_dir,
                    ["fetch", "--prune", "--prune-tags", "origin"],
                    timeout=self._fetch_timeout,
                )
                log.info("gitrepos.fetch.done", repo=spec.name)

    async def head_sha(self, spec: RepoSpec, branch: str) -> str | None:
        """Resolve ``branch`` to a commit sha. ``None`` if branch missing."""
        ref = self._resolve_ref(spec, branch)
        try:
            out = await self._run(self._git_dir(spec), ["rev-parse", ref])
        except GitCommandError as exc:
            if "unknown revision" in exc.stderr.lower() or "ambiguous" in exc.stderr.lower():
                return None
            raise
        return out.decode().strip() or None

    async def list_tree(self, spec: RepoSpec, branch: str) -> list[TreeEntry]:
        """Return every blob reachable from ``branch``'s tree."""
        ref = self._resolve_ref(spec, branch)
        # `ls-tree -r -l <ref>` emits one row per blob:
        # <mode> SP <type> SP <sha> SP <size> TAB <path>
        # Trees and submodules are excluded by the `-r` recurse + we
        # filter on type=blob defensively.
        out = await self._run(
            self._git_dir(spec),
            ["ls-tree", "-r", "-l", "-z", ref],
        )
        entries: list[TreeEntry] = []
        for chunk in out.split(b"\x00"):
            if not chunk:
                continue
            try:
                meta, path_bytes = chunk.split(b"\t", 1)
            except ValueError:
                continue
            parts = meta.split()
            if len(parts) < 4:
                continue
            _mode, otype, sha, size = parts[0], parts[1], parts[2], parts[3]
            if otype != b"blob":
                continue
            try:
                size_int = int(size) if size != b"-" else 0
            except ValueError:
                size_int = 0
            try:
                path = path_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # Skip files whose paths are not valid UTF-8 — IronRAG
                # external_keys must be text. We could base64 them, but
                # that's a rare edge case and logging loud is more useful.
                log.warning(
                    "gitrepos.tree.path_not_utf8",
                    repo=spec.name,
                    branch=branch,
                )
                continue
            entries.append(
                TreeEntry(path=path, blob_sha=sha.decode(), size=size_int)
            )
        return entries

    async def read_blob(self, spec: RepoSpec, sha: str) -> bytes:
        return await self._run(self._git_dir(spec), ["cat-file", "blob", sha])

    def _resolve_ref(self, spec: RepoSpec, branch: str) -> str:
        """For bare clones use ``refs/remotes/origin/<branch>``, local tree uses raw branch."""
        if self._is_bare(spec):
            return f"refs/remotes/origin/{branch}"
        return branch
