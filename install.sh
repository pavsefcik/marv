#!/bin/sh
# marv installer — a single script, three ways to run it:
#
#   1. One line, from any directory:
#        curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv/main/install.sh | sh
#
#   2. From a git clone / checkout:
#        git clone https://github.com/pavsefcik/marv && sh marv/install.sh
#
# marv is the harness half of the MARV family. This script:
#   1. preflights the machine (Apple Silicon + macOS new enough for MLX),
#   2. installs the build tools marv needs (Xcode CLT, Homebrew, uv, Python 3.14),
#   3. installs marv-mlx, the recommended runtime (via its own installer, which
#      brings gum + mlx-vlm),
#   4. installs marv itself as a `uv tool` (latest release, or this checkout),
#   5. puts uv's tool bin on PATH in ~/.zshrc.
#
# Safe to re-run: each step skips what is already present. Re-running refreshes
# marv to the latest release (the install uses --force).
#
# Environment:
#   MARV_VERSION            release version to install (default: latest, e.g. 0.111.0 or v0.111.0)
#   MARV_REF                git ref for the source fallback when no release is available (default main)
#   MARV_SKIP_MLX=1         skip installing the marv-mlx runtime
#   MARV_SKIP_HARDWARE_CHECK=1
#                           skip the Apple Silicon / macOS / memory preflight
#                           (for non-MLX providers such as openai)
#   MARV_NO_ZSH=1           do not modify ~/.zshrc
#   MARV_MLX_DIR            where the marv-mlx installer materializes its source (default $HOME/.marv-mlx)
#   MARV_MLX_FORCE=1        passed through to the marv-mlx installer to refresh its source
#   MARV_DRY_RUN=1          preflight and print the plan without installing anything

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

# ---- 2. Xcode Command Line Tools --------------------------------------------
step "Checking Xcode Command Line Tools…"
if xcode-select -p >/dev/null 2>&1; then
  says "present ($(xcode-select -p))"
else
  die "Xcode CLT not installed. Run 'xcode-select --install', finish the GUI prompt, then re-run this script."
fi

# ---- 3. Homebrew ------------------------------------------------------------
step "Checking Homebrew…"
if ! command -v brew >/dev/null 2>&1 && [ -x /opt/homebrew/bin/brew ]; then
  # brew may exist without being on PATH in a non-login shell.
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
if command -v brew >/dev/null 2>&1; then
  says "present ($(brew --version | head -n1))"
else
  die "Homebrew missing — install it:
    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
  then re-run this script."
fi

# ---- 4. uv ------------------------------------------------------------------
step "Installing uv…"
if command -v uv >/dev/null 2>&1; then
  says "present ($(uv --version | head -n1))"
else
  run brew install uv
fi
command -v uv >/dev/null 2>&1 || die \
  "uv is not on PATH after install — open a new terminal and re-run this script."

# ---- 5. Python 3.14 (uv-managed) --------------------------------------------
step "Ensuring Python 3.14…"
if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  run uv python install 3.14
elif uv python install 3.14 >/dev/null 2>&1; then
  says "Python 3.14 ready"
else
  says "uv will fetch Python 3.14 during the marv install"
fi

# ---- 6. marv-mlx runtime (recommended) --------------------------------------
# marv self-manages mlx_vlm.server, so the runtime is not required — but it is
# the recommended way to manage models, and this installer brings it in.
if [ "${MARV_SKIP_MLX:-0}" = "1" ]; then
  step "Skipping marv-mlx (MARV_SKIP_MLX=1)"
else
  step "Installing marv-mlx (runtime + mlx-vlm)…"
  mlx_dir="${MARV_MLX_DIR:-$HOME/.marv-mlx}"
  if [ -f "$mlx_dir/marv-mlx.zsh" ] && [ "${MARV_MLX_FORCE:-0}" != "1" ]; then
    says "already installed at $mlx_dir (set MARV_MLX_FORCE=1 to refresh)"
  elif [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    says "[dry-run] curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv-mlx/main/install.sh | sh"
  else
    curl -fsSL https://raw.githubusercontent.com/pavsefcik/marv-mlx/main/install.sh | sh
    says "marv-mlx installed at $mlx_dir"
  fi
fi

# ---- 7. marv (uv tool) -------------------------------------------------------
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

# ---- 8. PATH in ~/.zshrc -----------------------------------------------------
step "Wiring marv onto PATH in ~/.zshrc…"
if [ "${MARV_NO_ZSH:-0}" = "1" ]; then
  says "skipped (MARV_NO_ZSH=1) — add this line yourself: export PATH=\"$UV_TOOL_BIN:\$PATH\""
else
  zrc="$HOME/.zshrc"
  if grep -qF "$UV_TOOL_BIN" "$zrc" 2>/dev/null; then
    says "$UV_TOOL_BIN already referenced in ~/.zshrc"
  elif [ "${MARV_DRY_RUN:-0}" = "1" ]; then
    says "[dry-run] append 'export PATH=\"$UV_TOOL_BIN:\$PATH\"' to ~/.zshrc"
  else
    {
      printf '\n# marv (installed by install.sh)\n'
      printf 'export PATH="%s:$PATH"\n' "$UV_TOOL_BIN"
    } >> "$zrc"
    says "appended $UV_TOOL_BIN to ~/.zshrc"
  fi
fi

if [ "${MARV_DRY_RUN:-0}" = "1" ]; then
  step "Dry run complete — nothing was installed."
  says "Re-run without MARV_DRY_RUN=1 to apply."
  exit 0
fi

# ---- 9. Verification ---------------------------------------------------------
step "Verifying…"
ok=1
for c in uv; do
  command -v "$c" >/dev/null 2>&1 || { warn "MISSING: $c"; ok=0; }
done
if [ -x "$UV_TOOL_BIN/marv" ]; then
  says "marv $("$UV_TOOL_BIN/marv" --version 2>&1)"
elif command -v marv >/dev/null 2>&1; then
  says "marv $(marv --version 2>&1)"
else
  warn "MISSING: marv (expected at $UV_TOOL_BIN/marv)"; ok=0
fi
if [ "${MARV_SKIP_MLX:-0}" != "1" ]; then
  mlx_dir="${MARV_MLX_DIR:-$HOME/.marv-mlx}"
  [ -f "$mlx_dir/marv-mlx.zsh" ] || { warn "MISSING: $mlx_dir/marv-mlx.zsh"; ok=0; }
  command -v mlx_vlm.server >/dev/null 2>&1 || [ -x "$UV_TOOL_BIN/mlx_vlm.server" ] || \
    { warn "MISSING: mlx_vlm.server"; ok=0; }
fi
if [ "$ok" -eq 1 ]; then
  says "all checks passed ✓"
else
  warn "verification found problems above — fix them and re-run, or install manually (see README)."
  exit 1
fi

step "Done."
says "Open a NEW terminal (or run: source ~/.zshrc), then type: marv"
[ "${MARV_SKIP_MLX:-0}" = "1" ] || says "Browse models with: marv-mlx   ·   health-check with: marv doctor"
