# Git Repositories ↔ IronRAG connector — Changelog

## 0.0.3 — 2026-05-30

- Binary blobs are no longer ingested. Detection is content-based — a
  blob is binary iff it carries a NUL byte in its first sniff window
  (the same heuristic git uses), so every text extension is ingested
  (including unknown ones) while images, archives, and compiled
  artifacts are skipped. No extension blocklist is consulted.
- `.gitignore` is honoured implicitly: enumeration walks the committed
  tree via `git ls-tree`, which never lists ignored/untracked files.
- Unknown text files now default to `text/plain` instead of
  `application/octet-stream`.
- Build against framework `v0.0.3` (content-addressed idempotency keys,
  single-request external-key lookup, cursor document-id persistence).

## 0.0.1 — 2026-05-23

Initial public release on top of the
[IronRAG Connector Template](https://github.com/mlimarenko/IronRAG.ConnectorTemplate).

### Sync behavior

- Tracks an arbitrary number of git repositories (remote SSH/HTTPS or
  local working trees). Each repo can mirror one or more branches; the
  connector emits one IronRAG document per `(repo, branch, path)`
  triple.
- Periodic sweep (default 1800s) fetches every remote repo
  (`git fetch --prune`), walks each tracked branch's tree
  (`git ls-tree -r -l -z`), and ships files whose blob SHA advanced
  since the last successful push.
- Bare clones with `--filter=blob:none` keep the local cache small;
  blobs stream in only when a file is actually fetched.
- One external-key namespace, `gitrepos:file:<repo>:<branch>:<path>`,
  URL-encoded so colons in branch or path names cannot break the
  external-key parse.
- Routing YAML maps `repo`, `branch`, `path`, `dir`, `ext`, `top_dir`,
  and custom `facts:` keys from `repos.yaml` to `(workspace, library)`
  pairs. Per-kind policies override the env-var defaults — files
  default to `on_missing: delete` so renames and deletions reflect in
  IronRAG.

### Configuration

- `repos.yaml` describes every tracked repository: name, source
  (`url` or `local_path`), branches, include/exclude globs, and
  arbitrary fact tags. Common dependency / build directories
  (`node_modules`, `__pycache__`, `target/{debug,release}`, …) are
  excluded by default.
- Per-file size ceiling is configurable both globally
  (`GITREPOS_MAX_FILE_BYTES`) and per-repo (`max_file_bytes`).
- The connector itself stores no vendor credentials: mount your SSH
  key or git credential helper at the container level.
