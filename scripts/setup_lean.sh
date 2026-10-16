#!/usr/bin/env bash
# Install elan + Lean 4 and build lean_project/ with the pinned Mathlib (~7 GB).
# Works on Linux, macOS and Windows (Git Bash). Run from the repository root.
#
# If lean_project/ already exists (cloned repo), its lean-toolchain and
# lake-manifest.json pin the exact Lean version and Mathlib commit (RNF-2);
# otherwise a new project is created from the Mathlib `math` template.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.elan/bin:$PATH"

if ! command -v elan >/dev/null 2>&1; then
  echo ">> Installing elan (Lean version manager)"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      tmp="$(mktemp -d)"
      curl -sSfL -o "$tmp/elan-init.ps1" \
        https://raw.githubusercontent.com/leanprover/elan/master/elan-init.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -Command \
        "& '$(cygpath -w "$tmp/elan-init.ps1")' -NoPrompt \$true -DefaultToolchain none"
      ;;
    *)
      curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
        | sh -s -- -y --default-toolchain none
      ;;
  esac
fi

cd "$ROOT"
if [ ! -f lean_project/lakefile.toml ]; then
  echo ">> Creating lean_project/ from the Mathlib template"
  lake +leanprover-community/mathlib4:lean-toolchain new lean_project math
fi

cd lean_project
echo ">> Downloading precompiled Mathlib cache"
lake exe cache get
echo ">> Building"
lake build

echo ">> Versions"
lean --version
echo "Mathlib commit: $(git -C .lake/packages/mathlib rev-parse HEAD)"
