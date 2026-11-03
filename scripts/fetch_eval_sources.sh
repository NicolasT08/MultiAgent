#!/usr/bin/env bash
# Download the pinned sources of data/eval.jsonl into data/raw/ (not in git).
# The commits must match data/curation/criteria.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."

MINIF2F_COMMIT=2c4ba9119eef74d0d611f494261b2c5bae98c69a
PUTNAM_COMMIT=a635813f5edf297d928f9bd5ef8324a26bdde09c

mkdir -p data/raw
curl -sSfL -o data/raw/minif2f.jsonl \
  "https://raw.githubusercontent.com/deepseek-ai/DeepSeek-Prover-V1.5/${MINIF2F_COMMIT}/datasets/minif2f.jsonl"

if [ ! -d data/raw/putnam ]; then
  git clone --quiet https://github.com/trishullab/PutnamBench.git data/raw/putnam
fi
git -C data/raw/putnam checkout --quiet "${PUTNAM_COMMIT}"
