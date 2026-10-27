#!/usr/bin/env bash
# Build the Lean REPL (leanprover-community/repl) used by src/formal_mas/lean/runner.py.
# Works on Linux, macOS and Windows (Git Bash). Run from the repository root after
# scripts/setup_lean.sh. Takes ~1 min; the binary lands in lean_repl/.lake/build/bin/.
#
# The tag must match lean_project/lean-toolchain, and the commit is pinned (RNF-2):
# if the tag is ever moved upstream, the build stops instead of using other code.
set -euo pipefail

REPL_URL="https://github.com/leanprover-community/repl"
REPL_TAG="v4.36.0-rc1"
REPL_COMMIT="a85a1d2f4bdeab19c3d34be979ea336c9ee9b3ed"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/lean_repl"
export PATH="$HOME/.elan/bin:$PATH"

if [ ! -d "$DEST/.git" ]; then
  echo ">> Cloning $REPL_URL at $REPL_TAG"
  git clone --quiet --depth 1 --branch "$REPL_TAG" "$REPL_URL" "$DEST"
fi

commit="$(git -C "$DEST" rev-parse HEAD)"
if [ "$commit" != "$REPL_COMMIT" ]; then
  echo "ERROR: lean_repl/ is at $commit, expected $REPL_COMMIT ($REPL_TAG)." >&2
  echo "Delete lean_repl/ and run this script again." >&2
  exit 1
fi

if [ -n "$(git -C "$DEST" status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: lean_repl/ has local changes; delete it and run this script again." >&2
  exit 1
fi

project_toolchain="$(cat "$ROOT/lean_project/lean-toolchain")"
repl_toolchain="$(cat "$DEST/lean-toolchain")"
if [ "$project_toolchain" != "$repl_toolchain" ]; then
  echo "ERROR: REPL toolchain $repl_toolchain != lean_project toolchain $project_toolchain" >&2
  exit 1
fi

cd "$DEST"
echo ">> Building the REPL ($repl_toolchain)"
lake build repl

echo ">> REPL $REPL_TAG ($commit) built in lean_repl/.lake/build/bin/"
