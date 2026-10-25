"""Content-addressed successful stage results, local to one checkout."""

import fnmatch
import hashlib
import json
import os
import subprocess
import tempfile


def versions(env):
    result = {}
    for command in (
        [env["PIPELINE_PY"], "--version"],
        ["node", "--version"],
        ["npm", "--version"],
        ["shfmt", "--version"],
        ["shellcheck", "--version"],
        [
            env["PIPELINE_PY"],
            "-c",
            "import importlib.metadata as m; print(sorted((d.metadata['Name'], d.version) for d in m.distributions()))",
        ],
        ["toktx", "--version"],
        ["gltfpack", "-v"],
        ["roofer", "--version"],
    ):
        try:
            with tempfile.TemporaryDirectory(dir=env.get("TMPDIR")) as directory:
                result[" ".join(command)] = subprocess.check_output(
                    command, env=env, cwd=directory, stderr=subprocess.STDOUT
                ).decode()
        except (OSError, subprocess.SubprocessError) as exc:
            result[" ".join(command)] = str(exc)
    return result


def stage_key(root, stage, files, scope, env, tools):
    # Hash full routed dependency domains, not just the edited files. Missing/deleted
    # files change the inventory. Scope separates partial tests/lints from full passes.
    patterns = stage.get("inputs", []) + stage.get("triggers", []) + stage.get("sources", [])
    patterns += [
        "scripts/dev/gate*",
        "scripts/dev/env_sync.py",
        "scripts/dev/resources.py",
        "tools/dev/*",
        "*package*.json",
        "*pyproject.toml",
        "*requirements*.txt",
        "*config*",
        "*.lock",
        "pipeline/env/*",
    ]
    if stage.get("cwd"):
        patterns.append(stage["cwd"] + "/*")
    h = hashlib.sha256(
        json.dumps(
            {
                "stage": stage,
                "scope": scope,
                "tools": tools,
                "env": {
                    k: v for k, v in env.items() if k.startswith(("TURKU3D_", "GATE_", "FIXTURE_"))
                },
            },
            sort_keys=True,
        ).encode()
    )
    for name in files:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            h.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return h.hexdigest()


def cached_execute(root, stage, key, execute):
    path = root / ".cache/gate-results" / stage["name"] / (key + ".json")
    try:
        record = json.loads(path.read_text())
        if record["key"] == key and record["result"]["status"] == "PASS":
            return {**record["result"], "cached": True, "seconds": 0}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    result = execute()
    if result["status"] == "PASS":
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps({"key": key, "result": result}))
        temp.replace(path)
    return result
