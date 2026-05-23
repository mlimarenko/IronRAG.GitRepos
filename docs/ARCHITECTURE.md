# Architecture

## Overview

```
                          repos.yaml                  routing.yaml
                              │                            │
                              ▼                            ▼
git remote / local tree ──► GitReposAdapter ──► ironrag_connector framework ──► IronRAG
       (git CLI)                                  (routing / state / sync)        HTTP
```

The adapter is the only piece of code that touches git. Everything
else — the cursor, the per-kind policy, the orphan reaper, the
HTTP server — is the connector framework.

## Lifecycle of one file

```
ensure_repo(spec)              ── clone bare or git fetch --prune for every spec
   │
   ▼
list_tree(spec, branch)        ── git ls-tree -r -l -z <ref>
   │
   ▼
yield SourceItemRef            ── change_token = blob sha; item_id = <repo>\0<branch>\0<path>
   │
   ▼
Router.resolve                 ── facts include repo, branch, path, dir, ext, top_dir, facts.*
   │
   ▼
StateStore.get                 ── cursor row keyed on (kind, item_id)
   │
 ┌─┴─────────────┐
 │               │
unchanged   change_token advanced (or new row)
 │               │
 ▼               ▼
noop      fetch(ref) → SourceItem (payload = git cat-file blob <sha>)
                 │
                 ▼
        Orchestrator.push_item
                 │
        ┌────────┼────────┐
        │        │        │
   not present  present  on 409 duplicate
        │        │        │
        ▼        ▼        ▼
   on_new    on_changed   on_duplicate_content
        │        │        │
        ▼        ▼        ▼
   created   replaced   skipped_duplicate_content
                 │
                 ▼
        StateStore.upsert (kind, item_id, change_token=blob_sha, doc_id)
```

After enumeration completes successfully, the reaper lists IronRAG
documents under the `gitrepos:file:` prefix and deletes anything not
seen during the sweep, subject to the `file` kind's `on_missing`
policy in `routing.yaml`.

## External-key layout

```
gitrepos:file:<repo>:<branch>:<path>
```

Repo, branch, and path are URL-encoded (RFC 3986 unreserved). Reasons:

1. Colons inside branch names (rare but allowed by git for refs) or
   inside paths (forbidden by git on the working tree but
   theoretically possible) would otherwise break parsing.
2. `parse_external_key` is the round-trip the reaper needs to map an
   IronRAG document back to a `(kind, item_id)` for cursor cleanup.

## Change-token choice: blob sha

git exposes two natural identities for "this file at this revision":

* **commit sha** — advances on every commit that touches the file.
* **blob sha** — advances *only* when content actually changes.

A connector that re-shipped a file on every commit would burn the
embedding / graph pipeline whenever an unrelated commit touched the
tree (a Makefile bump, a comment-only rebase). We pick blob sha
deliberately: only real content changes reach IronRAG.

## Why git CLI instead of pygit2 / GitPython

* No native library means a smaller image (`git` is ~50 MB,
  `pygit2`'s libgit2 build chain is ~150 MB on top of that).
* git's own SSH / credential-helper / submodule handling is the
  authoritative implementation; libgit2 lags on edge cases.
* Each command is a clean subprocess — no in-process state to
  pollute, no thread-safety questions for the async sweep.

The trade-off is one fork+exec per `ls-tree` and per `cat-file`.
For reasonable repo sizes (≲ 50k files) this is negligible; for
multi-million-file monorepos it would warrant batching.

## Failure modes

| Symptom                                                  | Behavior                                                                                                                |
|----------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| `git clone` fails (auth, network)                        | `gitrepos.repo.ensure_failed` logged; the sweep skips that repo's branches and keeps going. Reaper is not run.          |
| `git fetch` fails on an existing clone                   | Same as clone failure: log, skip this repo's branches, continue with the next repo.                                     |
| Branch absent from a remote (typo, deleted upstream)     | `git ls-tree` fails with `unknown revision`; that branch's files are skipped, others continue.                          |
| File larger than `max_file_bytes`                        | `gitrepos.file.skipped_too_large` logged; file is not pushed and not reaped (cursor row left untouched).                |
| Path with invalid UTF-8                                  | `gitrepos.tree.path_not_utf8` logged; file is skipped. Rare in practice.                                                |
| Vendor `git@host:` URL with custom port                  | Set up via `~/.ssh/config` inside the container; the connector itself has no custom-port flag.                          |
| Repo removed from `repos.yaml` after some files shipped  | All files under that repo become orphans on the next clean sweep and are reaped per the `file` `on_missing` policy.     |

## Operational notes

- The clone cache lives under `GITREPOS_CLONE_ROOT` (default
  `./repo-cache`). Mount a docker volume so clones survive restarts.
- For remote repos the cache uses `--filter=blob:none` partial clones:
  trees come down with the initial fetch, blob bodies stream on
  demand at `git cat-file`. This keeps the cache size proportional
  to *changed* files, not to the full repository history.
- For local-path repos the connector reads the working tree's
  current branch tips. If you want the connector to follow a
  rebase, point it at a checkout you push your branches into; do
  not point it at a developer's daily working copy where uncommitted
  changes would be ignored.
- Sweep concurrency (`SYNC_CONCURRENCY`) is the bound for parallel
  IronRAG uploads, not for parallel git operations. Git commands
  per repository are serialized via a per-repo asyncio lock to
  avoid `git fetch` racing itself.
