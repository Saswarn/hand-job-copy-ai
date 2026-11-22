#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "$0")/../.." && pwd)
check_dir="$repo_dir/backend/engine-check"
engine_dir="$check_dir/.venv"
engine_revision=8358d4e3ad61ac8ed757b3bc739401b09d967395
python_bin=${PYTHON_BIN:-python3.11}

source_dir=$(mktemp -d)
trap 'rm -rf -- "$source_dir"' EXIT
git -C "$source_dir" init --quiet
git -C "$source_dir" fetch --quiet --depth 1 \
    https://github.com/YABYABuwu/handclean-pi.git "$engine_revision"
git -C "$source_dir" checkout --quiet --detach FETCH_HEAD

"$python_bin" -m venv "$engine_dir"
engine_python="$engine_dir/bin/python"
"$engine_python" -m pip install --only-binary=:all: --no-deps -r "$check_dir/requirements.txt"
"$engine_python" -m pip install --no-build-isolation --no-deps "$source_dir/engine"
"$engine_python" -m pip check

# C++ GrabCut (about 4x faster captures). build.sh uses -march=native, so build on the machine that runs the engine.
bash "$source_dir/native/grabcut/build.sh"
mkdir -p "$check_dir/lib"
cp "$source_dir/native/grabcut/build/libhcgrabcut.so" "$check_dir/lib/libhcgrabcut.so"

# The worker must load the real engine with the C++ GrabCut (engine.toml sets method = "grabcut_cpp").
ready=$(cd "$check_dir" && "$engine_python" -I worker.py real </dev/null | head -n 1)
"$engine_python" -I -c 'import json, sys; ready = json.loads(sys.argv[1]); assert ready["mask_backend"] == "grabcut_cpp", ready; print("engine ready:", ready)' "$ready"

# Run the real-engine smoke test directly so missing native dependencies fail explicitly.
"$engine_python" -m pytest -q "$source_dir/engine/tests/test_engine_parts.py::test_engine_black_image_no_hand"
"$engine_python" -m pytest -q -rs "$source_dir/engine/tests"
