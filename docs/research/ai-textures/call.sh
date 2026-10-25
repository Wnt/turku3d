#!/bin/bash
# usage: call.sh <name> <model> <sandbox> <prompt-file> [images...]
set -u
R=$(cd "$(dirname "$0")" && pwd); U="python3 $R/../../codex-usage.py --percent"
name=$1; model=$2; sb=$3; pf=$4; shift 4
pct=$($U); if [ "${pct%.*}" -ge 70 ]; then echo "CAP $pct"; exit 3; fi
d=$(mktemp -d /tmp/aitex.XXXX); for f in "$@"; do cp "$f" $d/; done
imgs=(); for f in "$@"; do imgs+=(-i "$d/$(basename $f)"); done
t0=$(date +%s)
codex exec -m "$model" -C "$d" --skip-git-repo-check --sandbox "$sb" --json - "${imgs[@]}" < "$pf" > $R/$name.jsonl 2> $R/$name.err
rc=$?; t1=$(date +%s)
mkdir -p $R/$name.out; cp -R $d/. $R/$name.out/
echo "$name rc=$rc secs=$((t1-t0)) tmp=$d before=$pct after=$($U)"
