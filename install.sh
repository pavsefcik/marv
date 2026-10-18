#!/bin/sh
# marv installer — a single script, three ways to run it:
#
#   1. One line, from any directory:
#        curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv/main/install.sh | sh
#
#   2. One line, and grab a model on the way out:
#        curl -fsSL .../install.sh | sh -s -- --model mlx-community/Qwen3.5-4B-MLX-4bit
#
#   3. From a git clone / checkout:
#        git clone https://github.com/pavsefcik/marv && sh marv/install.sh
#
# marv is the harness half of the MARV family. This script:
#   1. preflights the machine (Apple Silicon + macOS new enough for MLX),
#   2. installs uv from Astral's standalone installer (no compiler, no CLT, no brew;
#      an existing uv is reused),
#   3. installs marv-mlx, the recommended runtime (via its own installer, which
#      brings gum + mlx-vlm the same brew-free way),
#   4. installs marv itself as a `uv tool` (latest release, or this checkout),
#   5. puts uv's bin dirs on PATH in ~/.zshrc,
#   6. optionally downloads a model and makes it the default.
#
# Safe to re-run: each step skips what is already present. Re-running refreshes
# marv to the latest release (the install uses --force).
#
# Environment:
#   MARV_VERSION            release version to install (default: latest, e.g. 0.111.0 or v0.111.0)
#   MARV_REF                git ref for the source fallback when no release is available (default main)
#   MARV_MODEL              model id(s) to download after installing (space/comma separated)
#   MARV_SKIP_MLX=1         skip installing the marv-mlx runtime
#   MARV_SKIP_HARDWARE_CHECK=1
#                           skip the Apple Silicon / macOS / memory preflight
#                           (for non-MLX providers such as openai)
#   MARV_NO_ZSH=1           do not modify ~/.zshrc
#   MARV_MLX_DIR            where the marv-mlx installer materializes its source (default $HOME/.marv-mlx)
#   MARV_MLX_FORCE=1        passed through to the marv-mlx installer to refresh its source
#   MARV_DRY_RUN=1          preflight and print the plan without installing anything
#
# Flags:
#   --model <id>   download a model after installing and make it marv's default
#                  (repeatable; without it marv starts with its model picker)
#   --skip-mlx     harness only
#   --dry-run, --no-zsh, --skip-hardware-check, --help

set -e

step() { printf '\n==> %s\n' "$1"; }
says() { printf '    %s\n' "$1"; }
warn() { printf '    ! %s\n' "$1"; }
die()  { printf 'install.sh: %s\n' "$1" >&2; exit 1; }

# Run a mutating command, or print it under MARV_DRY_RUN=1.
run() {
  if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

usage() {
  sed -n '2,44p' "$0" 2>/dev/null | sed -n 's/^# \{0,1\}//p' || \
    printf 'usage: install.sh [--model <id>]... [--skip-mlx] [--dry-run] [--no-zsh]\n'
}

# ---- Args → env --------------------------------------------------------------
models="${MARV_MODEL:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      [ $# -ge 2 ] || die "--model needs a model id"
      models="$models $2"; shift 2 ;;
    --model=*) models="$models ${1#*=}"; shift ;;
    --skip-mlx) MARV_SKIP_MLX=1; shift ;;
    --dry-run) MARV_DRY_RUN=1; shift ;;
    --no-zsh)  MARV_NO_ZSH=1; shift ;;
    --skip-hardware-check) MARV_SKIP_HARDWARE_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done
export MARV_SKIP_MLX MARV_DRY_RUN MARV_NO_ZSH MARV_SKIP_HARDWARE_CHECK

# ---- PATH helpers ------------------------------------------------------------
# uv and gum both land in the user's bin dir (no brew involved). Put it on PATH
# for the rest of this script, and later for the user's shells.
LOCAL_BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
path_add() {
  case ":$PATH:" in
    *":$1:"*) : ;;
    *) PATH="$1:$PATH"; export PATH ;;
  esac
}
path_add "$LOCAL_BIN"

# ---- Resolve whether we are running from a checkout --------------------------
# When piped through curl there is no checkout, and `$0` is just "sh". If a real
# script sits next to pyproject.toml + src/marv, install from that directory so a
# developer's local tree wins over the latest release.
self_dir="$(dirname "$0" 2>/dev/null)"
repo_dir=""
if [ -n "$self_dir" ] && [ -f "$self_dir/pyproject.toml" ] && [ -d "$self_dir/src/marv" ]; then
  repo_dir="$(cd "$self_dir" && pwd)"
fi

# ver_ge A B -> success when A >= B (dot-separated numeric versions).
ver_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, "."); nb = split(b, y, ".")
    n = (na > nb ? na : nb)
    for (i = 1; i <= n; i++) {
      xi = (i <= na ? x[i] + 0 : 0)
      yi = (i <= nb ? y[i] + 0 : 0)
      if (xi > yi) exit 0
      if (xi < yi) exit 1
    }
    exit 0
  }'
}

# ---- 1. Hardware preflight ---------------------------------------------------
step "Checking hardware (Apple Silicon / MLX)…"
if [ "${MARV_SKIP_HARDWARE_CHECK:-0}" = "1" ]; then
  says "skipped (MARV_SKIP_HARDWARE_CHECK=1)"
else
  [ "$(uname -s 2>/dev/null)" = "Darwin" ] || die \
    "marv targets macOS — this system is $(uname -s). Set MARV_SKIP_HARDWARE_CHECK=1 to install anyway (non-MLX providers only)."

  arch="$(uname -m 2>/dev/null)"
  [ "$arch" = "arm64" ] || die \
    "MLX needs an Apple Silicon Mac (M-series); this machine reports '$arch'. Set MARV_SKIP_HARDWARE_CHECK=1 to install for non-MLX providers."

  [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] || die \
    "hw.optional.arm64 is not set — MLX is unavailable on this machine."

  osver="$(sw_vers -productVersion 2>/dev/null)"
  [ -n "$osver" ] || die "Could not read the macOS version (sw_vers)."
  ver_ge "$osver" "14.0" || die \
    "macOS $osver is too old — MLX requires macOS 14.0 (Sonoma) or later. Upgrade and re-run."

  model="$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
  mem_bytes="$(sysctl -n hw.memsize 2>/dev/null)"
  if [ -n "$mem_bytes" ]; then
    mem_gb=$(( mem_bytes / 1024 / 1024 / 1024 ))
    says "${model:-Apple Silicon} — macOS $osver, ${mem_gb} GB unified memory"
    [ "$mem_gb" -ge 16 ] || warn "16 GB is recommended; ${mem_gb} GB can only run small models comfortably."
  else
    says "${model:-Apple Silicon} — macOS $osver"
  fi
fi

# ---- 2. uv (standalone binary; reused when already present) ------------------
step "Installing uv…"
if command -v uv >/dev/null 2>&1; then
  says "present ($(uv --version | head -n1))"
elif [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  says "[dry-run] curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh"
else
  # Astral's installer drops a prebuilt binary — no compiler, no CLT, no brew.
  # UV_NO_MODIFY_PATH keeps PATH wiring in this script (one line per bin dir in
  # ~/.zshrc) instead of letting the installer add its own.
  if curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh; then
    path_add "$LOCAL_BIN"
    # The installer still uses ~/.cargo/bin when that legacy dir exists.
    if [ -x "$HOME/.cargo/bin/uv" ]; then
      path_add "$HOME/.cargo/bin"
    fi
    command -v uv >/dev/null 2>&1 || die \
      "uv installed but not on PATH (looked in $LOCAL_BIN and ~/.cargo/bin) — open a new terminal and re-run."
    says "installed $(uv --version | head -n1)"
  elif command -v brew >/dev/null 2>&1; then
    says "standalone install failed — falling back to Homebrew"
    run brew install uv || die "brew install uv failed."
  else
    die "uv install failed — install it by hand (https://docs.astral.sh/uv/getting-started/installation/) and re-run."
  fi
fi
command -v uv >/dev/null 2>&1 || die \
  "uv is not on PATH after install — open a new terminal and re-run this script."

# ---- 3. Python 3.14 (uv-managed) --------------------------------------------
step "Ensuring Python 3.14…"
if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  run uv python install 3.14
elif uv python install 3.14 >/dev/null 2>&1; then
  says "Python 3.14 ready"
else
  says "uv will fetch Python 3.14 during the marv install"
fi

# ---- 4. marv-mlx runtime (recommended) --------------------------------------
# marv self-manages mlx_vlm.server, so the runtime is not required — but it is
# the recommended way to manage models, and this installer brings it in. Its own
# installer handles gum + mlx-vlm (brew-free), and inherits the env below so a
# single `--no-zsh`/`--dry-run` covers both layers.
if [ "${MARV_SKIP_MLX:-0}" = "1" ]; then
  step "Skipping marv-mlx (MARV_SKIP_MLX=1)"
else
  step "Installing marv-mlx (runtime + mlx-vlm + gum)…"
  mlx_dir="${MARV_MLX_DIR:-$HOME/.marv-mlx}"
  if [ -f "$mlx_dir/marv-mlx.zsh" ] && [ "${MARV_MLX_FORCE:-0}" != "1" ]; then
    says "already installed at $mlx_dir (set MARV_MLX_FORCE=1 to refresh)"
  elif [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    says "[dry-run] curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv-mlx/main/install.sh | sh"
  else
    curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv-mlx/main/install.sh \
      | MARV_MLX_DIR="$mlx_dir" \
        MARV_MLX_FORCE="${MARV_MLX_FORCE:-0}" \
        MARV_NO_ZSH="${MARV_NO_ZSH:-0}" \
        MARV_SKIP_HARDWARE_CHECK="${MARV_SKIP_HARDWARE_CHECK:-0}" \
        sh || die "marv-mlx install failed. Set MARV_SKIP_MLX=1 to install the harness only."
    says "marv-mlx installed at $mlx_dir"
  fi
fi

# ---- 5. marv (uv tool) -------------------------------------------------------
step "Installing marv…"
resolve_marv_requirement() {
  if [ -n "$repo_dir" ]; then
    printf '%s' "$repo_dir"
    return
  fi
  if [ -n "${MARV_VERSION:-}" ]; then
    v="${MARV_VERSION#v}"
    printf 'marv @ https://github.com/pavsefcik/marv/releases/download/v%s/marv-%s-py3-none-any.whl' "$v" "$v"
    return
  fi
  tag="$(curl -fsSL https://api.github.com/repos/pavsefcik/marv/releases/latest 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)"
  if [ -n "$tag" ]; then
    v="${tag#v}"
    printf 'marv @ https://github.com/pavsefcik/marv/releases/download/%s/marv-%s-py3-none-any.whl' "$tag" "$v"
  else
    # No API access / no releases: fall back to a source install.
    printf 'marv @ git+https://github.com/pavsefcik/marv@%s' "${MARV_REF:-main}"
  fi
}

requirement="$(resolve_marv_requirement)"
if [ "$requirement" = "$repo_dir" ]; then
  says "installing from checkout: $repo_dir"
else
  says "installing: $requirement"
fi

if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  run uv tool install --force --python 3.14 "$requirement"
else
  attempt=0
  while ! uv tool install --force --python 3.14 "$requirement"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 3 ]; then
      die "uv tool install marv failed after $attempt attempts."
    fi
    says "install failed — retrying (attempt $attempt)…"
  done
fi

# Resolve uv's tool bin dir the way uv itself does — its location is
# configurable (UV_TOOL_BIN_DIR, XDG_BIN_HOME, XDG_DATA_HOME).
UV_TOOL_BIN="$(uv tool dir --bin 2>/dev/null || printf '%s/.local/bin' "$HOME")"

# ---- 6. PATH in ~/.zshrc -----------------------------------------------------
# uv itself and gum live in LOCAL_BIN; the uv tool shims (marv, mlx_vlm.server)
# may live elsewhere. One guarded line per dir (uv's installer was told not to
# touch shell rc files, so this is the only writer).
step "Wiring PATH in ~/.zshrc…"
wire_zsh_path() {
  _dir="$1"
  if [ "${MARV_NO_ZSH:-0}" = "1" ]; then
    says "skipped ~/.zshrc (MARV_NO_ZSH=1) — add: export PATH=\"$_dir:\$PATH\""
  elif [ -f "$HOME/.zshrc" ] && grep -qF "$_dir" "$HOME/.zshrc" 2>/dev/null; then
    says "$_dir already referenced in ~/.zshrc"
  elif [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    says "[dry-run] append PATH line for $_dir to ~/.zshrc"
  else
    {
      printf '\n# marv (installed by install.sh)\n'
      printf 'export PATH="%s:$PATH"\n' "$_dir"
    } >> "$HOME/.zshrc"
    says "appended $_dir to ~/.zshrc"
  fi
}
wire_zsh_path "$LOCAL_BIN"
if [ "$UV_TOOL_BIN" != "$LOCAL_BIN" ]; then
  wire_zsh_path "$UV_TOOL_BIN"
fi

# ---- 7. Optional model: download + make it the default -----------------------
# `--model` makes this a one-shot setup: install everything, fetch the weights,
# and point marv at them so the first `marv` opens with that model instead of the
# picker. Without it nothing is downloaded (the default).
download_model() {
  _id="$1"
  _zsh="${MARV_MLX_DIR:-$HOME/.marv-mlx}/marv-mlx.zsh"
  if [ -f "$_zsh" ] && command -v zsh >/dev/null 2>&1; then
    zsh "$_zsh" download "$_id"
  else
    # Harness-only install (MARV_SKIP_MLX=1) or no runtime source yet: fetch the
    # weights the same way marv-mlx's headless `download` does.
    uvx --from mlx-vlm python3 -c "from mlx_vlm.utils import load; load('$_id')"
  fi
}

set_default_model() {
  _id="$1"
  _cfg="$HOME/.marv/config.toml"
  if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    says "[dry-run] set model = \"$_id\" in $_cfg"
    return 0
  fi
  mkdir -p "$HOME/.marv"
  if [ ! -f "$_cfg" ]; then
    {
      printf '# marv configuration — written by install.sh.\n'
      printf '# See config/default.toml for every option.\n'
      printf 'provider = "marv-mlx"\n'
      printf 'model = "%s"\n' "$_id"
    } > "$_cfg"
    says "created $_cfg with model = \"$_id\""
    return 0
  fi
  # Only the top-level `model` counts (the first line before any [section]).
  _current="$(awk '/^[[:space:]]*\[/{exit}
    /^[[:space:]]*model[[:space:]]*=/{
      if (match($0, /"[^"]*"/)) { print substr($0, RSTART + 1, RLENGTH - 2) }
      exit
    }' "$_cfg")"
  if [ -n "$_current" ]; then
    if [ "$_current" = "$_id" ]; then
      says "$_cfg already sets model = \"$_id\""
    else
      warn "$_cfg already sets model = \"$_current\" — left as is (edit it to switch)"
    fi
    return 0
  fi
  _tmp="$_cfg.install.$$"
  if awk -v id="$_id" '
      BEGIN { done = 0 }
      { if (!done && /^[[:space:]]*\[/) { printf "model = \"%s\"\n", id; done = 1 } print }
      END { if (!done) printf "model = \"%s\"\n", id }
    ' "$_cfg" > "$_tmp"; then
    mv "$_tmp" "$_cfg"
    says "set model = \"$_id\" in $_cfg"
  else
    rm -f "$_tmp"
    warn "could not update $_cfg — set model = \"$_id\" yourself"
    return 1
  fi
}

model_failed=0
if [ -n "$(printf '%s' "$models" | tr -d ' ,')" ]; then
  first_model=1
  for model in $(printf '%s' "$models" | tr ',' ' '); do
    case "$model" in
      "") continue ;;
      *[!A-Za-z0-9._/-]*) die "invalid model id: $model" ;;
    esac
    step "Downloading ${model}…"
    if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
      says "[dry-run] download $model"
    elif download_model "$model"; then
      says "downloaded $model"
    else
      warn "download failed for $model"
      model_failed=1
      continue
    fi
    if [ "$first_model" = "1" ]; then
      set_default_model "$model"
      first_model=0
    fi
  done
else
  step "Models…"
  says "none requested — marv starts with its model picker (pass --model <id> to preload one)"
fi

if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  step "Dry run complete — nothing was installed."
  says "Re-run without MARV_DRY_RUN=1 to apply."
  exit 0
fi

# ---- 8. Verification ---------------------------------------------------------
step "Verifying…"
ok=1
for c in uv; do
  command -v "$c" >/dev/null 2>&1 || { warn "MISSING: $c"; ok=0; }
done
if [ -x "$UV_TOOL_BIN/marv" ]; then
  says "$("$UV_TOOL_BIN/marv" --version 2>&1)"
elif command -v marv >/dev/null 2>&1; then
  says "$(marv --version 2>&1)"
else
  warn "MISSING: marv (expected at $UV_TOOL_BIN/marv)"; ok=0
fi
if [ "${MARV_SKIP_MLX:-0}" != "1" ]; then
  mlx_dir="${MARV_MLX_DIR:-$HOME/.marv-mlx}"
  [ -f "$mlx_dir/marv-mlx.zsh" ] || { warn "MISSING: $mlx_dir/marv-mlx.zsh"; ok=0; }
  command -v mlx_vlm.server >/dev/null 2>&1 || [ -x "$UV_TOOL_BIN/mlx_vlm.server" ] || \
    { warn "MISSING: mlx_vlm.server"; ok=0; }
  command -v gum >/dev/null 2>&1 || { warn "MISSING: gum"; ok=0; }
fi
if [ "$ok" -eq 1 ]; then
  says "all checks passed ✓"
else
  warn "verification found problems above — fix them and re-run, or install manually (see README)."
  exit 1
fi
if [ "$model_failed" = "1" ]; then
  warn "the install itself is complete, but one or more model downloads failed."
  step "Done."
  says "Open a NEW terminal (or run: source ~/.zshrc), then type: marv"
  exit 1
fi

step "Done."
says "Open a NEW terminal (or run: source ~/.zshrc), then type: marv"
[ "${MARV_SKIP_MLX:-0}" = "1" ] || says "Browse models with: marv-mlx   ·   health-check with: marv doctor"
