# IronRAG.GitRepos — agent + developer guide

Git-repository connector for IronRAG. Built on the
[IronRAG Connector Template](https://github.com/mlimarenko/IronRAG.ConnectorTemplate);
the framework handles routing / state / push / reap, this repo
implements one `SourceAdapter` that talks to git.

## Mental model

```
repos.yaml ─► GitReposAdapter ─► ironrag_connector framework ─► IronRAG
```

The adapter only knows git. Everything else is framework — if a
behavior is missing, edit the framework, do not duplicate it here.

## File map

```
src/gitrepos_connector/
  __init__.py       — public re-exports (GitReposAdapter, GitReposSettings)
  __main__.py       — uvicorn entrypoint
  config.py         — GitReposSettings (extends BaseConnectorSettings)
  repos.py          — repos.yaml schema, include/exclude globbing
  git_client.py     — subprocess wrappers around `git`
  mapping.py        — external_key encode/parse + item_id helpers
  adapter.py        — SourceAdapter implementation
  observability.py  — get_logger re-export

tests/              — pytest, real git repos in tmp dirs
docs/ARCHITECTURE.md — sweep flow, external-key layout, failure modes
```

## Agent rules

- Treat the framework as canonical. New per-kind policies belong in
  `ironrag_connector.policy`, not as ad-hoc flags here.
- Do not switch from the `git` CLI to a library binding without a
  concrete reason. The container image staying lean is part of the
  product surface.
- `change_token` is the blob sha. Do not switch to commit sha to "fix"
  diff sensitivity — that would re-push every file on every commit.
- `external_key` round-trips through `parse_external_key`. If you
  change the encoding, update both ends *and* `tests/test_mapping.py`.
- No vendor hostnames, repo URLs, workspace/library UUIDs belong in
  this repo's examples or tests. Use synthetic identifiers only.
- The connector stores no git credentials. SSH keys / credential
  helpers come from the container runtime. Do not add a
  `GIT_USERNAME` / `GIT_PASSWORD` env shim.
