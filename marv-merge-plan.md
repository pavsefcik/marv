# MARV merge plan — `ymlx → marv-mlx`

Status: **executed** (Phases 0–4 and 6 landed; see §12 for what shipped and what
remains optional).
Scope: rename + namespace + contract. No code-level git merge in this plan.
Source revisions at time of writing: `ymlx` `v0.135.1` (40 commits), `marv` `v0.107.0` (36 commits).

---

## 0. Naming convention (locked)

| Layer | Name | Notes |
|---|---|---|
| Full system | **MARV** | Modular Agent Runtime Valve · German: *Modulare Agenten Regelventil* |
| Harness / interface layer | **marv** | lowercase for CLI/package/repo context. No suffix. |
| LLM runtime / backend layer | **marv-mlx** | lowercase, technical layer. Was `ymlx`. |

Rules going forward:

- Avoid `-harness`, `-work`, `-code`, `-agent` suffixes entirely.
- `marv` = the cowork harness (agent loop, tools, sessions, TUI). It keeps its
  current name, repo, package, binary and paths.
- `marv-mlx` = the local LLM runtime (model catalog, download, run/swap,
  `mlx_vlm.server`, OpenAI endpoint). It is the rename target of `ymlx`.
- The "valve" metaphor is the guiding image: **marv is the valve** that routes
  and controls agent flow, sitting on top of the **marv-mlx** runtime.

## 0.1 Layering at a glance

```
                ┌──────────────────────────────────────────────────┐
                │                      MARV                        │
                │          Modular Agent Runtime Valve             │
                │                                                  │
   user ──────► │  ┌────────────────────────────────────────────┐  │
                │  │  marv   (harness / interface layer)        │  │
                │  │   agent loop · tools · sessions · skills   │  │
                │  │   prompts · extensions · Textual TUI       │  │
                │  └───────────────┬────────────────────────────┘  │
                │                  │ OpenAI-compatible HTTP        │
                │                  │ (:11500) + lifecycle CLI      │
                │                  ▼                               │
                │  ┌────────────────────────────────────────────┐  │
                │  │  marv-mlx   (runtime / backend layer)      │  │
                │  │   catalog · download · run/swap · status   │  │
                │  │   mlx_vlm.server · TUI · pi extension      │  │
                │  └───────────────┬────────────────────────────┘  │
                │                  │                               │
                └──────────────────┼───────────────────────────────┘
                                   ▼
                     Hugging Face hub  +  Apple Silicon (MLX)
```

**Seam A (rename-time):** the harness self-manages `mlx_vlm.server`; `marv-mlx`
is the recommended manager but not a runtime dependency.
**Seam B (follow-up, flagged):** the harness delegates lifecycle to `marv-mlx`.

---

## 1. Goal

Rename the runtime into the `marv` family and define a clean seam, without
touching the harness's identity:

- **`marv-mlx`** (was **ymlx**) — the local LLM **runtime**: browsing,
  downloading, running and serving local MLX models; OpenAI-compatible endpoint
  on `:11500`; the human-facing TUI; the pi extension.
- **`marv`** (unchanged) — the coding-agent **harness**: Textual TUI + Typer CLI,
  agent loop, tools, sessions, skills, prompts, extensions.

Two repos first (not a monorepo). A monorepo can be revisited later, but the
rename is cheap and reversible while a merge is not.

---

## 2. Decisions (locked)

| # | Decision |
|---|---|
| 1 | Two separate repos first: `pavsefcik/marv-mlx` (runtime) and `pavsefcik/marv` (harness, unchanged). Monorepo deferred. |
| 2 | `marv-mlx` / `marv mlx` launches the runtime (today's ymlx). `marv` launches the harness (today's marv). No `marv harness` suffix. |
| 3 | Seam **A** (independent, harness self-manages `mlx_vlm.server`) at rename time. Seam **B** (harness delegates lifecycle to `marv-mlx`) as a follow-up behind a config flag. |
| 4 | Runtime state moves under the shared marv cache root: `~/.cache/marv/mlx/`. The harness keeps `~/.cache/marv/` (its `sessions/`, `logs/`, `state.toml` are already subfoldered there). |
| 5 | Provider-name "collision" is not a collision: the harness provider id `marv-mlx` correctly names the marv-mlx runtime backend. Ambiguity is removed by splitting env-var namespaces (`MARV_MLX_*` belongs to the runtime; the harness uses `AGENT_*`). See §4. |

---

## 3. Naming map

### 3.1 Repos / packages / artifacts

| Today | After |
|---|---|
| `pavsefcik/ymlx` | `pavsefcik/marv-mlx` |
| `pavsefcik/marv` | **unchanged** `pavsefcik/marv` |
| `pavsefcik/ymlx-curator` | `pavsefcik/marv-curator` |
| `pavsefcik/homebrew-ymlx` (`Formula/ymlx.rb`) | `pavsefcik/homebrew-marv-mlx` (`Formula/marv-mlx.rb`) |
| Python package `marv` (in `src/marv/`) | **unchanged** |
| zsh script `ymlx.zsh` | `marv-mlx.zsh` |
| pi extension `ymlx-sync.ts` | `marv-mlx-sync.ts` |
| pi package name `ymlx` | `marv-mlx` |

### 3.2 Binaries / commands

| Today | After | Notes |
|---|---|---|
| `ymlx` | `marv-mlx` | primary runtime command |
| `marv` | **unchanged** `marv` | primary harness command |
| (none) | `marv mlx …` | runtime alias: the harness CLI passes through to `marv-mlx` (§7.2) |

The runtime keeps its existing CLI verb set unchanged in meaning:
`run | chat | stop | status | list | info | endpoint | download | curated | version`
(plus hidden `serve`/`curator`/`ls`), all `--json`-capable with the stable
`0/1/2` exit contract.

### 3.3 Env-var namespaces (see §4 for why this split is the fix)

| Owner | Prefix today | After |
|---|---|---|
| Runtime (ymlx) | `YMLX_*` | `MARV_MLX_*` |
| Harness | `AGENT_*` (keep) + `MARV_MLX_*` + `MARV_PROCTITLE` | `AGENT_*` (keep) + `AGENT_MLX_*` + `MARV_PROCTITLE` (keep) |

Renames inside the harness (small, two variables):
- `MARV_MLX_BASE_URL` → `AGENT_MLX_BASE_URL`
- `MARV_MLX_MAX_OUTPUT_TOKENS` → `AGENT_MLX_MAX_OUTPUT_TOKENS`

Runtime internal `YMLX_*` (menu state, `YMLX_QUICK_*`, `YMLX_CHAT_FLAGS`,
`YMLX_SERVER_FLAGS`, `YMLX_DEBUG`, `YMLX_HUB_DIR`, `YMLX_PROVIDER`, …) →
`MARV_MLX_*`. The pi-extension knobs
`YMLX_PI_PROVIDER`/`YMLX_HUB_DIR`/`YMLX_BIN`/`YMLX_REPO`/`YMLX_ZSH`/`YMLX_STABLE_DIR`
→ `MARV_MLX_*`.

### 3.4 Filesystem layout (per decision 4)

| Purpose | Today | After |
|---|---|---|
| Harness state (sessions/state/logs) | `~/.cache/marv/{sessions,state.toml,logs}` | **unchanged** |
| Harness global config | `~/.marv/` | **unchanged** |
| Harness project config | `./.marv/` | **unchanged** |
| Runtime state | `~/.cache/ymlx/` | `~/.cache/marv/mlx/` |
| Runtime config | `~/.cache/ymlx/config.zsh` | `~/.cache/marv/mlx/config.zsh` |
| Runtime chats/logs/curated | `~/.cache/ymlx/{chats,logs,curated-llms.md,latest-version,venvs}` | `~/.cache/marv/mlx/…` |
| Runtime install dir (standalone) | `~/.ymlx` | `~/.marv-mlx` |
| Runtime stable copy (pi) | `~/.local/share/ymlx/` | `~/.local/share/marv-mlx/` |
| pi wrapper | `~/.pi/agent/bin/ymlx` | `~/.pi/agent/bin/marv-mlx` |
| Homebrew wrapper/shim | `bin/ymlx`, `libexec/ymlx-launcher.zsh` | `bin/marv-mlx`, `libexec/marv-mlx-launcher.zsh` |

The HF hub (`~/.cache/huggingface/hub`) is **not** touched — both layers share it
as they do today.

---

## 4. The provider-name question — resolution (decision 5)

**The apparent collision.** The bare token `marv-mlx` names (a) the runtime
repo/binary and (b) the harness's local-MLX provider id
(`provider = "marv-mlx"` in `~/.marv/config.toml`, in `state.toml`, in sessions,
in `src/marv/llm/models.py`'s `Provider` literal, and in ~63 references across
13 files).

**Why it is not a semantic clash.** The harness provider *is* the marv-mlx-served
backend: it talks to the model server that `marv-mlx` runs, and under seam B it
will delegate lifecycle to the `marv-mlx` CLI. One concept, one name. This is
now a feature of the naming convention, not a bug: `provider = "marv-mlx"` reads
as "the marv-mlx runtime backend", exactly as `apple-fm` reads as "the Apple
Foundation Model backend".

**Resolution — keep the id, split the namespaces.** Mechanical ambiguity is
eliminated by three rules:

1. **Reserve the bare token `marv-mlx` for the runtime** (repo, binary, pi
   package, tap, state subfolder `~/.cache/marv/mlx`). It is never an env-var
   prefix.
2. **Provider ids are a separate value namespace.** `provider = "marv-mlx"` is
   read only in `provider = …` config/CLI/state position, alongside `apple-fm`,
   `openai-compat`, `ollama`. Nothing resolves a provider id to a filesystem
   path or binary, so there is no lookup ambiguity.
3. **Env vars are split by owner, not shared.** The runtime takes `MARV_MLX_*`;
   the harness keeps `AGENT_*` and takes `AGENT_MLX_*`. No variable is owned by
   both. (This is the change that actually matters — today both repos would
   want `MARV_MLX_*`.)

**Migration for the provider id:** none required. `provider = "marv-mlx"`
continues to resolve exactly as it does today, so existing `state.toml`, configs
and sessions keep working. Only the two *env var* names move, handled with a
deprecation read of the old names for one release.

**Rejected alternatives.**
- Rename the harness provider to `mlx` / `local-mlx`: churn in 13 files, a
  synonym for one concept, breaks stored `state.toml`/configs unless aliased.
- Rename the runtime to something else to free the token: defeats decision 1.

---

## 5. Current-state facts the plan relies on

- Runtime (`ymlx`): zsh, single big `ymlx.zsh` (~2.3k lines) + `lib/`
  (`ymlx-helpers.zsh`, `ymlx_repl.py`, `sitecustomize.py`), `install.sh`,
  `Makefile` (release + Homebrew formula hash), `package.json` (pi package),
  `extensions/ymlx-sync.ts`, `tests/` (2 python unittest + 2 zsh), state
  `~/.cache/ymlx`, env `YMLX_*`. ~708 `ymlx` string occurrences across 18
  tracked files.
- Harness (`marv`): Python 3.14, `src/marv/` (~15k LOC), `pyproject.toml`
  (hatchling, `VERSION` regex, console script `marv = marv.cli:main`),
  `Makefile`, Textual TUI + Typer CLI, tests mirroring `src/`, CI + release
  workflows, state `~/.cache/marv` + config `~/.marv`, env `AGENT_*`/`MARV_*`.
- The harness already contains a **copy** of the runtime's logic:
  `llm/mlx_models.py` says it "Mirrors the logic in YMLX's `ymlx-helpers.zsh`";
  `llm/model_download.py` bundles a fallback of the curated list; provider
  lifecycle lives in `llm/{local_server,server_lifecycle,server_process}.py`.
  Seam B later collapses this duplication.
- Historical note: the harness originally had `llm/ymlx.py` and invoked the
  ymlx CLI; commit `6d06362` removed the dependency in favour of self-managing
  `mlx_vlm.server`. Seam B is, in effect, restoring that dependency
  deliberately and behind a flag.

---

## 6. Ordering rationale

Because the harness keeps its name, repo, package and root state path, there is
**no forced ordering** between the two codebases. The only shared namespace is
`~/.cache/marv/`, and the runtime simply takes a new `mlx/` subfolder inside it,
which does not exist today — so the runtime's migration is additive.

Recommended order: rename the runtime first (Phase 1), then align the harness
(Phase 2). Doing the runtime first means the harness's doc/README references can
point at the final name immediately.

---

## 7. Phased execution

Each phase ends in a green gate. Do the runtime rename as **one atomic commit**
so review reads as a rename, not a rewrite.

### Phase 0 — Freeze and prep

- Tag rollback points: `ymlx-v0.135.1` and `marv-v0.107.0` (both repos).
- Confirm clean trees; snapshot `git remote -v`, `VERSION`, and the current
  `.zshrc` launcher line.
- Back up state dirs: `~/.cache/ymlx`, `~/.cache/marv`, `~/.marv`.
- **Gate:** backups exist; tags pushed.

### Phase 1 — Runtime rename: `ymlx → marv-mlx`

1. GitHub: rename `pavsefcik/ymlx` → `pavsefcik/marv-mlx`; rename
   `ymlx-curator` → `marv-curator` and `homebrew-ymlx` → `homebrew-marv-mlx`
   (`Formula/marv-mlx.rb`). Confirm old URLs redirect.
2. `ymlx.zsh` → `marv-mlx.zsh`; `ymlx()` → `marv-mlx()`; `_ymlx_*` helpers →
   `_marv_mlx_*`; `_YMLX_*` globals → `_MARV_MLX_*`; `ymlx-launcher.zsh` →
   `marv-mlx-launcher.zsh`; the `source "$_YMLX_SRC_DIR/lib/ymlx-helpers.zsh"`
   path.
3. `lib/`: `ymlx-helpers.zsh` → `marv-mlx-helpers.zsh`; `ymlx_repl.py` →
   `marv_mlx_repl.py`; update `sitecustomize.py` proctitle name.
4. `install.sh`: `YMLX_DIR`/`YMLX_REF`/`YMLX_FORCE` → `MARV_MLX_*`; default
   install dir `~/.ymlx` → `~/.marv-mlx`; pi extension copy target
   `marv-mlx-sync.ts`; wrapper `~/.pi/agent/bin/marv-mlx`; `~/.zshrc` line
   `marv-mlx-launcher.zsh`; final messages. Keep `sh -n` clean and POSIX-sh
   compatible; keep the uv-dir invariants documented in `AGENTS.md`.
5. State: `~/.cache/ymlx` → `~/.cache/marv/mlx` (`marv-mlx.zsh` line 22,
   `README.md`, `tests/test_cli.zsh` ×2). Config inside stays `config.zsh`;
   managed-block markers `# >>> ymlx-managed …` → `# >>> marv-mlx-managed …`
   (update the reader/writer regexes and the default-config heredoc).
6. Env: all `YMLX_*` → `MARV_MLX_*` (§3.3), including the extension knobs and
   the curated-fetch/self-update internals.
7. Curated source: URL and mirror to `pavsefcik/marv-curator`;
   `ymlx-curator.md` → `marv-curator.md` in the curator repo; cache file stays
   `curated-llms.md` (or rename — open question Q2).
8. `extensions/ymlx-sync.ts` → `marv-mlx-sync.ts`: commands `/ymlx-sync` →
   `/marv-mlx-sync`, `/ymlx-setup` → `/marv-mlx-setup`; hardcoded dev fallback
   `~/Dev/projects/ymlx` → `~/Dev/projects/marv-mlx`; clone path, stable dir,
   wrapper path, `YMLX_*` env; the pi provider registered stays `local`
   (unchanged) unless you want it renamed (Q3).
9. `package.json`: `name: "marv-mlx"`, description, extension path.
10. `Makefile`: `REPO := pavsefcik/marv-mlx`, `BREW := ../homebrew-marv-mlx`,
    `FORMULA := …/Formula/marv-mlx.rb`.
11. Homebrew formula: rename file, `desc`/`homepage`, `bin/"marv-mlx"`,
    `libexec` shim `marv-mlx-launcher.zsh`, keep the brew-aware launcher
    replacement, update the `test do` assertions.
12. **Migration shim** on first `marv-mlx` run:
    - if `~/.cache/ymlx` exists and `~/.cache/marv/mlx` does not, move it;
    - migrate the `YMLX_*` quick settings block in `config.zsh` if present;
    - rewrite the `~/.zshrc` launcher line from `ymlx-launcher.zsh` to
      `marv-mlx-launcher.zsh`;
    - regenerate/clean the stale `~/.pi/agent/bin/ymlx` wrapper and old
      extension copy.
13. Version bump + CHANGELOG entry. Note in `AGENTS.md` the raw.githubusercontent
    CDN-lag pitfall and the GH007 noreply-email requirement still apply; the
    curl one-liner moves to the `marv-mlx` raw URL.
14. **Gate:** `make test` (python unittests + `test_helpers.zsh` +
    `test_cli.zsh`); `sh -n install.sh`; fresh-`HOME` install test;
    `brew install pavsefcik/marv-mlx/marv-mlx` from the renamed tap;
    `marv-mlx --version`; `marv-mlx status --json`; pi extension loads and
    `/model` syncs.

### Phase 2 — Harness alignment: `marv` stays, points at `marv-mlx`

Small, no identity change.

1. Env: `MARV_MLX_BASE_URL` → `AGENT_MLX_BASE_URL`,
   `MARV_MLX_MAX_OUTPUT_TOKENS` → `AGENT_MLX_MAX_OUTPUT_TOKENS`
   (`llm/factory.py`, `llm/marv_mlx.py`). Keep `MARV_PROCTITLE`. Read the old
   names for one release with a deprecation warning.
2. Provider id (`marv-mlx`) and class (`MarvMlxProvider`): **unchanged** (§4).
   Optionally rename the module `llm/marv_mlx.py` → `llm/mlx_backend.py` for
   tidiness — no behavioural change, skip if churn is unwanted.
3. CLI: add a thin `marv mlx …` passthrough subcommand that `exec`s `marv-mlx`
   with the remaining args (prints a clear "install marv-mlx" hint when the
   binary is absent). This gives the `marv mlx` alias from decision 2 without a
   separate shim repo.
4. Docs: `docs/llm.md` currently says "no external model-manager CLI is
   involved" — update to name `marv-mlx` as the *recommended* runtime while
   stating the harness remains self-sufficient (seam A). README + `AGENTS.md`
   link to the `marv-mlx` repo. Mention the MARV acronym and the layering
   diagram in the README.
5. `VERSION` bump only if any user-visible behaviour changed; otherwise a docs
   release.
6. **Gate:** `make can-release` (ruff + mypy + pytest); `marv mlx status --json`
   passthrough works; headless smoke test against a running `mlx_vlm.server`;
   `provider = "marv-mlx"` still resolves.

### Phase 3 — Seam A hardening (rename-time baseline)

Nothing changes behaviourally, but make the boundary explicit:

- Add the layering diagram (§0.1) to both READMEs and the plan.
- Point both repos' READMEs at each other ("marv-mlx manages/serves models;
  marv runs the agent"), and both `AGENTS.md` at the seam.
- Curated list: keep the harness's bundled fallback, but note it is a snapshot
  of `marv-curator`.

### Phase 4 — Seam B behind a flag (follow-up)

Goal: let the harness delegate model lifecycle to `marv-mlx` when present.

- Add config `server_manager = "embedded" | "marv-mlx"` (default `embedded`);
  env `AGENT_SERVER_MANAGER`; CLI `marv run --server-manager …`.
- Implementation: in `llm/local_server.py` / `llm/marv_mlx.py`, when set to
  `marv-mlx`:
  - `list_models()` → `marv-mlx list --json` (fallback to the embedded hub scan
    if the binary is absent);
  - `ensure_running()` → `marv-mlx run <model>` (blocking until ready, or
    `--json` status poll);
  - `stop()` → `marv-mlx stop <model>`;
  - `is_serving()` → `marv-mlx status --json`;
  - thinking spec → `marv-mlx info <model> --json` (keeps families in one place).
- The runtime's CLI contract is already suitable: stdout data, stderr logs,
  `--json`, exit `0/1/2`.
- Keep the embedded path as the default and the fallback. Flip the default only
  after the runtime is a declared dependency.
- **Gate:** existing harness tests pass with both `embedded` and `marv-mlx`
  (the latter using a fake `marv-mlx` on `PATH`), plus a live smoke test.

### Phase 5 — Deduplicate shared logic (optional, after B is trusted)

- Single source for: hub discovery, Ministral Instruct/Reasoning pairing,
  thinking spec, curated catalog.
- If B lands, the harness consumes the runtime's `--json` rather than
  re-deriving; delete `llm/mlx_models.py`'s duplicated classification and the
  bundled curated fallback (or keep the fallback generated from
  `marv-curator`).
- If you prefer a shared Python package instead, extract `marv_mlx_core`
  consumed by the harness directly (runtime stays zsh and shells out to it, or
  the runtime gains a thin Python CLI).

### Phase 6 — Packaging / docs / CI / tap final pass

- Runtime: refresh `homebrew-marv-mlx` URL/sha via `make formula` **after**
  pushing the tag; update the curl one-liner; verify
  `pi install git:github.com/pavsefcik/marv-mlx`.
- Harness: unchanged packaging; add the README/`AGENTS.md` links and the
  `marv mlx` passthrough docs.
- Deprecation stub for one release: `ymlx` prints "renamed to marv-mlx". Remove
  after.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Large mechanical diff in the runtime (~708 strings) | One atomic rename commit; `make test` + `sh -n install.sh` as gates; no behaviour edits in the same commit. |
| `~/.cache/marv` sharing | Runtime takes a new `mlx/` subfolder; harness root is untouched; runtime migration is additive and copy-then-switch for one release. |
| pi coupling (`ymlx-sync.ts` hardcodes dev path, stable dir, wrapper) | Explicit Phase 1 step 8 + fresh-HOME and pi `/reload` verification. |
| raw.githubusercontent CDN lag | Pin test URLs to full commit SHA; verify content before trusting (`AGENTS.md` pitfall). |
| GitHub push rejected `GH007` | Keep the repo-local noreply email `187490479+pavsefcik@users.noreply.github.com`. |
| Homebrew formula/tap rename | Rename tap repo and formula file; Homebrew 6 tap trust still required; `brew trust pavsefcik/marv-mlx`. |
| Migration runs twice or on a half-renamed state | Make every shim idempotent and guard on target-absent + source-present. |
| Old `pi install`/cloned packages stale | Document the reinstall command; old GitHub URLs redirect but installed clones do not refresh. |
| Env-var rename silently ignored | Read old `MARV_MLX_*` names in the harness for one release with a deprecation warning, then drop. |
| Provider id confusion | §4 rules; the token `marv-mlx` is never an env prefix; provider ids are a separate value namespace. |
| Acronym drift (`MARV` vs `marv`) | Document §0 in both READMEs and both `AGENTS.md`; `MARV` is the system, `marv`/`marv-mlx` are the layers. |

---

## 9. Open questions

1. **Curated cache filename in the runtime.** Keep `curated-llms.md` or rename
   to `marv-mlx-curated-llms.md`. Default: keep.
2. **pi provider name.** The extension registers provider `local` in pi's
   catalog. Leave as `local` (default) or rename to `marv-mlx`? Default: leave.
3. **`marv mlx` passthrough location.** Implement as a harness CLI subcommand
   (recommended, no extra repo) or as a separate `marv` shim script installed
   by the runtime? Default: harness subcommand.
4. **Module filename `llm/marv_mlx.py`.** Keep or rename to `llm/mlx_backend.py`
   for tidiness. Default: keep (no behavioural value).

---

## 10. Rollback

- Tags `ymlx-v0.135.1` and `marv-v0.107.0` mark the pre-rename state.
- The runtime rename is an atomic commit: `git revert` restores the previous
  tree. The harness identity is unchanged, so it has almost nothing to roll
  back.
- GitHub repo renames are reversible; old URLs and clones redirect until the new
  name is reused.
- State migration is copy-then-switch (never destructive) for one release, so
  `~/.cache/ymlx` remains intact.

---

## 11. Definition of done

- `marv-mlx` exists as the runtime repo, installs cleanly, and passes its gate
  (`make test`, install smoke tests, Homebrew formula test).
- `marv` keeps its name, repo, package, binary, config and state paths; only the
  two `AGENT_MLX_*` env renames and the `marv mlx` passthrough land.
- Runtime state lives under `~/.cache/marv/mlx/`; harness state stays under
  `~/.cache/marv/`; no path or env var is owned by both layers.
- `provider = "marv-mlx"` still resolves in the harness; existing sessions and
  configs survive.
- The pi extension (`marv-mlx-sync`) loads and syncs `/model`.
- Both READMEs explain the MARV family and the layering, and link the other
  layer; both `AGENTS.md` name the seam.
- Seam B is implemented behind `server_manager = "marv-mlx"` with `embedded` as
  the default and passing tests for both modes.

---

## 12. Execution log

All four repos renamed on GitHub and released:

| Repo | Renamed from | Latest |
|---|---|---|
| `pavsefcik/marv-mlx` | `pavsefcik/ymlx` | v0.136.2 |
| `pavsefcik/marv` | (unchanged) | v0.109.0 |
| `pavsefcik/marv-curator` | `pavsefcik/ymlx-curator` | — |
| `pavsefcik/homebrew-marv-mlx` | `pavsefcik/homebrew-ymlx` | 0.136.2 |

**Phase 0** — tags `ymlx-v0.135.1` and `marv-v0.107.0` pushed; state dirs, `~/.zshrc`
and the pi wrapper backed up.

**Phase 1** — runtime renamed in one atomic commit (`marv-mlx` 0.136.0), then the
`ymlx` deprecation stub (0.136.1) and a migration narrowing fix (0.136.2). Gate:
`make test` (21 Python + 2 zsh suites), `sh -n install.sh`, fresh-HOME install,
Homebrew install + formula test, CLI smoke tests.

**Phase 2** — harness aligned (`marv` 0.108.0): `AGENT_MLX_*` env names (legacy
read + warning) and the `marv mlx …` passthrough. Provider id unchanged.

**Phase 3** — layering diagram + cross-links added to both READMEs and `AGENTS.md`.

**Phase 4** — Seam B landed behind `server_manager` (`marv` 0.109.0); `embedded`
remains the default and the fallback, with tests for both modes.

**Phase 6** — formulas refreshed from real tag tarballs; deprecation stub shipped.

Not done (optional, per the plan): Phase 5 (deduplicate `llm/mlx_models.py` and the
bundled curated fallback) and flipping the Seam B default. Both wait until
`marv-mlx` is a declared dependency.

---

