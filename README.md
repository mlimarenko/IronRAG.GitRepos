<h1 align="center">IronRAG ↔ Git Repositories connector</h1>
<p align="center"><b>Mirror one or many git repositories — remote or local, single- or multi-branch — into <a href="https://github.com/mlimarenko/IronRAG">IronRAG</a>: bare-clone + periodic <code>git fetch</code>, blob-sha diff, per-file routing.</b></p>

<p align="center">
  <a href="https://github.com/mlimarenko/IronRAG.GitRepos/releases"><img src="https://img.shields.io/github/v/release/mlimarenko/IronRAG.GitRepos?style=flat-square&label=release" alt="Release"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License"></a>
  <a href="https://hub.docker.com/r/pipingspace/ironrag.gitrepos"><img src="https://img.shields.io/docker/pulls/pipingspace/ironrag.gitrepos?style=flat-square&label=docker%20pulls" alt="Docker pulls"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square" alt="Python">
</p>

---

Built on the [IronRAG Connector Template](https://github.com/mlimarenko/IronRAG.ConnectorTemplate) — this repo only owns git-specific code (subprocess wrapper around `git`, `repos.yaml` schema, blob-sha change detection, file-path routing facts).

## What it does

- Tracks N repositories described in `repos.yaml` (remote SSH/HTTPS
  *or* local working trees).
- For each repo, follows one or more branches and emits one IronRAG
  document per `(repo, branch, path)` triple.
- Detects file changes by **blob SHA** — content-addressed, so a
  no-op rebase or merge does not re-ship the same content.
- Honors `include` / `exclude` globs per repo (with `**` matching
  any path depth) and a configurable per-file size ceiling.
- Routes files to `(workspace, library)` via `routing.yaml` rules
  matched against `repo`, `branch`, `path`, `dir`, `ext`, `top_dir`,
  and any custom `facts:` tags you set in `repos.yaml`.
- Cleans up deletions: files removed from a tracked branch (or
  whole repos / branches dropped from `repos.yaml`) become orphans
  and the framework's reaper deletes them from IronRAG.

## Quick start

```bash
git clone git@github.com:mlimarenko/IronRAG.GitRepos.git
cd IronRAG.GitRepos
cp .env.example .env.local            # set IRONRAG_BASE_URL, IRONRAG_API_TOKEN, ADMIN_BEARER_TOKEN
cp repos.yaml.example repos.yaml      # describe the repositories you want to track
cp routing.yaml.example routing.yaml  # map them to workspace/library UUIDs

uv sync --all-extras
uv run pytest
uv run gitrepos-connector             # FastAPI on http://localhost:8088
```

### Docker

```bash
docker build -t pipingspace/ironrag.gitrepos:latest .
docker run -d \
    --name ironrag-gitrepos \
    --env-file .env.local \
    -v $(pwd)/routing.yaml:/app/routing.yaml:ro \
    -v $(pwd)/repos.yaml:/app/repos.yaml:ro \
    -v ironrag_gitrepos_state:/var/lib/ironrag-connector \
    -v $HOME/.ssh:/root/.ssh:ro \
    -p 8088:8088 \
    pipingspace/ironrag.gitrepos:latest
```

The official image is published to Docker Hub as
[`pipingspace/ironrag.gitrepos`](https://hub.docker.com/r/pipingspace/ironrag.gitrepos)
on every GitHub release.

## Configuration

Two YAML files:

* `repos.yaml` — what to track. Repository name, source
  (`url` or `local_path`), branches, include/exclude globs, and
  arbitrary fact tags. See [`repos.yaml.example`](./repos.yaml.example).
* `routing.yaml` — where it goes. Rules match against
  `routing_facts` and resolve to `(workspace_id, library_id)` pairs.
  See [`routing.yaml.example`](./routing.yaml.example).

Plus a small `.env.local` for the IronRAG endpoint, the connector's
HTTP bearer, and the sync-loop knobs the framework owns.

## Endpoints

| Route                     | Purpose                                                |
|---------------------------|--------------------------------------------------------|
| `GET  /health`            | Liveness — no auth.                                    |
| `POST /sync/run`          | Force a sweep now. Requires `Authorization: Bearer …`. |

A periodic sweep also runs in the background per `RUN_MODE` and
`SYNC_INTERVAL_SECONDS`.

## Routing facts emitted per file

| Key         | Example                | Notes                                       |
|-------------|------------------------|---------------------------------------------|
| `repo`      | `docs-public`          | `name` from `repos.yaml`                    |
| `branch`    | `main`                 | branch from `repos.yaml`                    |
| `path`      | `docs/intro.md`        | full path inside the tree                   |
| `dir`       | `docs`                 | parent directory of `path` (empty for root) |
| `ext`       | `md`                   | extension without dot, lowercase            |
| `top_dir`   | `docs`                 | first path segment                          |
| `repo_url`  | `git@github.com:…`     | populated for remote repos                  |
| *anything*  | …                      | every key under `facts:` in `repos.yaml`    |

## Docs

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — sweep flow,
  external-key layout, failure modes specific to git.
- [CHANGELOG.md](./CHANGELOG.md) — release notes.

## Deploy with Docker Compose

```bash
cp .env.example .env.local             # IRONRAG_* + ADMIN_BEARER_TOKEN
cp repos.yaml.example repos.yaml       # repos + branches + include/exclude
cp routing.yaml.example routing.yaml   # map files → (workspace, library)
docker compose up -d
docker compose logs -f
```

[`docker-compose.yml`](docker-compose.yml) pulls the released image and mounts
`repos.yaml` + `routing.yaml` read-only. The bare clones and the SQLite cursor
share one named volume, so a restart reuses the clone cache and ships only the
diff. For private repositories over SSH, uncomment the deploy-key mount in the
compose file.

## Related

- [IronRAG](https://github.com/mlimarenko/IronRAG) — the RAG backend these connectors feed.
- [Connector Template](https://github.com/mlimarenko/IronRAG.ConnectorTemplate) — the framework every connector builds on.
- Connectors: [Confluence](https://github.com/mlimarenko/IronRAG.Confluence) · [BookStack](https://github.com/mlimarenko/IronRAG.BookStack) · [Git Repositories](https://github.com/mlimarenko/IronRAG.GitRepos)

## License

MIT — see [LICENSE](./LICENSE).
