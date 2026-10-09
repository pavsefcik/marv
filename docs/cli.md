# CLI

The CLI lives in `src/marv/cli/` and is the lighter delivery surface alongside the TUI.

## Structure

The CLI is split by role rather than by framework detail:

- command surface and dispatch
- headless stdout delivery
- session utility commands

## Delivery modes

### Interactive handoff

`marv run` with no prompt/headless flag creates config + provider + session state, then hands off to the Textual TUI.

### Headless mode

`marv run --headless "..."` or `make run-headless` runs one prompt through the same runtime and renders:

- thinking markers
- streamed text
- tool activity
- system messages

directly to stdout.

## Session commands

The CLI also exposes delivery-level session utilities:

- `marv fork`
- `marv tree`
- `marv sessions`
- `marv config-show`

These are delivery commands over the same JSONL session model used by the runtime and TUI.

## Latency benchmark

`marv bench` measures time-to-first-token for the active model, because for a
local-LLM harness TTFT is the difference between an assistant and an
interruption. It runs a scripted multi-turn conversation through the real agent
loop (prompt assembly, tool schemas, transport included) and reports:

- TTFT p50 / p90
- median prefill cost (prompt + tool-schema tokens)
- **TTFT growth per turn** — a flat number means the prompt is not being
  re-prefilled in full each turn; a climbing one means it is, and that is the
  latency bug to fix first
- **prefill rate** — the learned `tokens/s` this machine prefills at, plus a
  `cold start` constant when cold turns were measured

Options: `--prompt` (repeatable, overrides the default prompts), `-n`/`--turns N`,
`--no-warmup` (include cold start), `--json`, and the usual `-m`/`-p` overrides.

### Seeding the calibration

Beyond reporting, `marv bench` **writes the calibration to `state.toml`**
(`remember_latency_fit`) for the active model. This is how a fresh machine gets
its prefill constants: the first benchmark seeds the fit that the wait predictor
and the model menu's latency tier then use. An ordinary session also refreshes
it from its own measured turns, so running `bench` is a way to seed it early
rather than a required step. A `--no-warmup` run measures the cold-start
constant too.

See [`llm.md`](llm.md#prefilldecode-calibration) for the prediction model and
[`coworking-plan.md`](coworking-plan.md) for how it feeds the latency work.

## Design role

The CLI owns:

- command parsing
- config override/application
- provider creation
- session selection
- delivery dispatch

It should not own:

- the runtime agent loop
- tool execution semantics
- session semantics themselves
- extension dispatch behavior

Those stay below the delivery boundary.
