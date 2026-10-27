"""Repository-only policy checks: no downloads, generators or external state."""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import tomllib
from pathlib import Path

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".glb",
    ".wasm",
    ".zip",
    ".pdf",
    ".col",
}


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def flags_in_code(path, text):
    """Literal, unaliased calls only, so registry uses are mechanically auditable."""
    names, errors = set(), []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return names, [f"{path}: {exc}"]
        for node in ast.walk(tree):
            if isinstance(node, ast.alias) and node.name == "feature_flag" and node.asname:
                errors.append(f"{path}: do not alias feature_flag")
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name != "feature_flag":
                    continue
                if (
                    len(node.args) != 1
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    errors.append(f"{path}:{node.lineno}: feature_flag requires one literal name")
                else:
                    names.add(node.args[0].value)
    else:
        # The canonical spelling is part of the contract. Comments cannot keep a flag alive.
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
        if re.search(r"\bfeatureFlag\s+as\b", text):
            errors.append(f"{path}: do not alias featureFlag")
        for call in re.finditer(r"\bfeatureFlag\s*\(([^)]*)\)", text):
            literal = re.fullmatch(r"\s*(['\"])([a-z][a-z0-9_]*)\1\s*", call[1])
            if literal:
                names.add(literal[2])
            else:
                errors.append(f"{path}: featureFlag requires one literal name")
    # The reserved namespace must only be read by the two language adapters.
    if "TURKU3D_AB_" in text or re.search(r"['\"]ab\.", text):
        errors.append(f"{path}: read A/B settings through the canonical flag module")
    return names, errors


def check_flags(root, files, config):
    rules = config["flags"]
    data = tomllib.loads((root / rules["registry"]).read_text())
    entries = data.get("flags", [])
    if not isinstance(entries, list):
        return ["flags registry: expected [[flags]] entries"]
    errors, registered, used = [], set(), set()
    count = int(
        subprocess.check_output(
            ["git", "-C", str(root), "rev-list", "--first-parent", "--count", rules["main"]],
            stderr=subprocess.PIPE,
        )
    )
    if len(entries) > 5:
        errors.append("flags registry: at most five experiments")
    for entry in entries:
        name = entry.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in registered:
            errors.append(f"flags registry: invalid or duplicate name {name!r}")
        registered.add(name)
        for key in ("owner", "reason"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                errors.append(f"flag {name}: missing {key}")
        added, duration = entry.get("added_at_merge"), entry.get("remove_after_merges")
        if (
            type(added) is not int
            or type(duration) is not int
            or not 0 <= added <= count
            or not 1 <= duration <= 10
        ):
            errors.append(f"flag {name}: invalid merge lifetime (main={count}, max duration=10)")
        elif count >= added + duration:
            errors.append(f"flag {name}: expired at main merge {added + duration} (now {count})")
    for path in files:
        if not matches(path, rules["sources"]) or path in rules["readers"]:
            continue
        if any(part in ("tests", "test") for part in Path(path).parts):
            continue
        names, problems = flags_in_code(path, (root / path).read_text())
        used.update(names)
        errors.extend(problems)
    errors.extend(f"flag {name}: unregistered use" for name in sorted(used - registered))
    errors.extend(
        f"flag {name}: registry entry has no use in code" for name in sorted(registered - used)
    )
    return errors


def check_policy(root, files, config):
    rules = config["budgets"]
    exceptions = rules.get("exceptions", {})
    errors = []
    for path in files:
        file = root / path
        data = file.read_bytes()
        suffix = file.suffix.lower()
        binary = suffix in BINARY_SUFFIXES or b"\0" in data[:8192]
        if binary:
            if len(data) > rules["binary_kib"] * 1024 and not matches(
                path, rules["binary_allowed"]
            ):
                errors.append(
                    f"{path}: binary {len(data)} bytes exceeds {rules['binary_kib']} KiB; store outside git"
                )
            continue
        limit = rules["lines"].get(suffix.lstrip("."))
        if limit is None:
            continue
        lines = len(data.splitlines())
        if lines > limit and not exceptions.get(path, "").strip():
            errors.append(
                f"{path}: {lines} lines exceeds {limit}; split the file or document a budget exception"
            )
    for path, reason in exceptions.items():
        if path not in files or not reason.strip():
            errors.append(f"budget exception {path}: missing file or reason")
    errors.extend(check_flags(root, files, config))
    return errors
