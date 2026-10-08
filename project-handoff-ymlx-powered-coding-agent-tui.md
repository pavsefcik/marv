# Project Handoff — YMLX-powered Coding Agent TUI

> **Historical / archived.** This brief predates the shipped app: the name was
> chosen (`marv`), the backend was renamed `ymlx` → `marv-mlx`, and the YMLX CLI
> dependency was dropped in favour of a self-managed `mlx_vlm.server`. It also
> contains open questions that are all resolved now. Kept for context only — for
> current behaviour see [`README.md`](README.md), [`docs/`](docs/), and
> [`CHANGELOG.md`](CHANGELOG.md).

This brief is for an AI coding agent continuing this project. It captures the goal, decisions already made, and the work that remains.

## 1. Goal

Build a personal, terminal-based AI coding agent driven by **YMLX** (the user's existing tool that manages and runs MLX models on Apple Silicon). The app is a fork of **eddmann/my-own-coding-agent** (Python, MIT-free check below), stripped of unneeded parts, rebranded, with the user's existing YMLX config repurposed as the app's config backbone. The final app name is **not yet chosen** — propose options and let the user pick before hardcoding names/paths.

## 2. Decisions already made

- **Base codebase:** https://github.com/eddmann/my-own-coding-agent — chosen over alternatives (pie/Rust ~70–90k LOC too large; rasbt/mini-coding-agent single-file but Ollama-only, no TUI, no config layer; smolagents is a library, not a forkable harness).
- **Why this base:** provider-agnostic `llm/` adapter layer, Textual TUI included, config layering (global → project → env), skills/extensions system, JSONL sessions with resume/fork, MIT-licensed style repo with modular layout (`runtime/ llm/ config/ tools/ skills/ prompts/ extensions/ tui/ cli/ web/`).
- **Model backend:** YMLX replaces Ollama/OpenRouter as the (primary) provider.
- **TUI:** keep the Textual TUI essentially as-is; only light customization of behavior/output.
- **Config:** rename/reuse the user's ymlx config format as the app's own config — it becomes the "bolts and wheels" (model list, defaults, endpoints).

## 3. Reference material

- Base repo README and architecture docs: see the repo's `docs/` folder (`docs/llm.md` documents how to add a provider — this is the template for the YMLX adapter).
- Conceptual reference: https://github.com/rasbt/mini-coding-agent — six-component breakdown (repo context, prompt shape/caching, structured tools + approvals, context reduction, transcript/memory, delegation) to understand what to keep when stripping.

## 4. Work plan

1. **Fork & verify.** Clone the base repo, confirm its license permits renaming/redistribution (keep attribution if required), get `make deps && make run` working with an existing OpenAI-compatible local server.
2. **Add YMLX provider.** New adapter in the `llm/` layer following the existing Ollama/OpenAI-compatible adapter pattern. YMLX exposes/serves MLX models; the adapter needs: base URL, model listing (so the model picker shows YMLX-managed models), streaming chat completions, and tool-call support. Check YMLX's actual API surface (ask the user or read YMLX source) before assuming endpoints.
3. **Repurpose the ymlx config.** Design the app config schema: start from the existing ymlx config's fields (models, ports, defaults) and extend with agent settings (default model, approval mode, system prompt, context compaction limits). Support versioned config (`version: 1`). Wire it into the base repo's config layering (global → project → env).
4. **Strip unwanted parts.** Candidates for removal (confirm with user first): `web/` delivery (FastAPI + WebSocket), extensions API if unused, web-search tool if present. Keep: TUI, skills, sessions/resume, context compaction, approval gates.
5. **Rebrand.** New name (ask user), rename binary, session paths, config paths, TUI title. Do this **after** the name is chosen to avoid rework.
6. **Polish TUI behavior.** Light adjustments to status line / output formatting per user preference (gather specifics from user).

## 5. Open questions for the user

- Final app name (needed before step 5).
- YMLX API details: exact endpoints for listing/running models, whether it's OpenAI-compatible or custom.
- Which parts of the base repo to strip (web delivery? extensions?).
- Python version: base repo requires 3.14+ and `uv` — acceptable on the user's machine?

## 6. Acceptance criteria

- `make run` starts the TUI and can chat with a YMLX-served MLX model, streaming output and executing tools (read/write/edit/bash).
- Model picker lists models managed by YMLX, read from the renamed config.
- Sessions save/resume under the new app's directory.
- No references to the original repo's name in user-facing strings; license attribution preserved if required.
- `make test` and `make lint` pass after modifications.