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


# Every landing publishes main's tree to the public repository, so street-level imagery
# (Google Street View, Apple Look Around, Mapillary, Bing Streetside) must never be committed.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif"}
PUBLISHED_IMAGE_ROOTS = ["docs/*", "textures/*", "qa/*", "game/public/*", "tests/*", "*/tests/*"]
STREET_MARKERS = (
    "streetview",
    "street-view",
    "lookaround",
    "look-around",
    "mapillary",
    "streetside",
    "pano",
    "contact-",
    "reference",
    "refs/",
)
STREET_PROVIDERS = re.compile(
    r"\b(google|street.?view|apple|look.?around|mapillary|bing|streetside)\b", re.IGNORECASE
)
SIDECAR_SUFFIXES = {".json", ".txt", ".toml", ".yaml", ".yml"}
IMAGE_ALLOWLIST = "docs/published-images.toml"
IMAGE_MAX_KIB = 512
MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(<?([^)\s>]+)")


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def street_marker(text):
    """The first street-imagery marker in text; `_` and spaces count as `-`."""
    folded = re.sub(r"[_\s]+", "-", text.lower())
    return next((marker for marker in STREET_MARKERS if marker in folded), None)


def image_captions(root, files):
    """Alt texts of Markdown image embeds, keyed by the repo path of the embedded image."""
    captions = {}
    for path in files:
        if not path.endswith(".md"):
            continue
        text = (root / path).read_text(errors="replace")
        for alt, target in MARKDOWN_IMAGE.findall(text):
            if "://" in target:
                continue
            resolved = (Path(path).parent / target.split("#")[0]).as_posix()
            parts = []
            for part in resolved.split("/"):
                if part == "..":
                    parts = parts[:-1]
                elif part not in ("", "."):
                    parts.append(part)
            captions.setdefault("/".join(parts), []).append(alt)
    return captions


def check_published_images(root, files):
    """Images under published roots: no street-imagery markers and no large files unless listed.

    A marker in the path, in a sidecar next to the image (`<name>.<ext>.json`, `<stem>.txt`, ...)
    or in a Markdown embed's alt text marks it as street imagery. The allowlist records
    path, source, licence and a one-line reason; an entry admits a marked or large image.
    """
    errors, listed = [], {}
    # Google aerial comparisons are local-only, even outside the usual image roots
    # or when someone mistakenly adds an allowlist entry.
    for path in files:
        folded = re.sub(r"[_\s]+", "-", path.lower())
        if Path(path).suffix.lower() in IMAGE_SUFFIXES and any(
            marker in folded for marker in ("gmaps-compare", "google-maps", "googlemaps")
        ):
            errors.append(f"{path}: Google Maps comparison imagery must remain local-only")
    allowlist = root / IMAGE_ALLOWLIST
    entries = tomllib.loads(allowlist.read_text()).get("images", []) if allowlist.is_file() else []
    present = set(files)
    for entry in entries:
        path = entry.get("path", "")
        where = f"{IMAGE_ALLOWLIST} {path or '<no path>'}"
        if path in listed:
            errors.append(f"{where}: duplicate entry")
        for key in ("source", "licence", "reason"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
                errors.append(f"{where}: missing or multi-line {key}")
        if path not in present:
            errors.append(f"{where}: no such file")
        provider = STREET_PROVIDERS.search(f"{entry.get('source', '')} {entry.get('licence', '')}")
        if provider:
            errors.append(f"{where}: street imagery ({provider[0]}) cannot be published")
        listed[path] = entry
    images = [
        path
        for path in files
        if Path(path).suffix.lower() in IMAGE_SUFFIXES
        and matches(path, PUBLISHED_IMAGE_ROOTS)
        and path not in listed
    ]
    if not images:
        return errors
    captions = image_captions(root, files)
    sidecars = {}
    for other in files:
        if Path(other).suffix.lower() in SIDECAR_SUFFIXES:
            sidecars.setdefault(str(Path(other).parent), []).append(other)
    for path in images:
        file = Path(path)
        marks = [(f"path {path}", street_marker(path))]
        marks += [
            (f"sidecar {other}", street_marker((root / other).read_text(errors="replace")))
            for other in sidecars.get(str(file.parent), [])
            if Path(other).name.startswith((file.name + ".", file.stem + "."))
        ]
        marks += [(f"caption {alt!r}", street_marker(alt)) for alt in captions.get(path, [])]
        found = next(((where, marker) for where, marker in marks if marker), None)
        if found:
            errors.append(
                f"{path}: {found[0]} marks street imagery ({found[1]!r}); remove it, since "
                "street-level imagery never enters the public repository, or, if it is not "
                f"street imagery, record its source and licence in {IMAGE_ALLOWLIST}"
            )
        size = (root / path).stat().st_size
        if size > IMAGE_MAX_KIB * 1024:
            errors.append(
                f"{path}: image {size} bytes exceeds {IMAGE_MAX_KIB} KiB without an entry "
                f"in {IMAGE_ALLOWLIST}"
            )
    return errors


def flags_in_code(path, text):
    """Literal, unaliased calls only, so registry uses are mechanically auditable."""
    names, errors = set(), []
    if path.endswith(".py"):
        # Parsing is the policy stage's main cost; a file without the name has no call or
        # alias to check (python-lint reports syntax errors).
        empty = ast.Module(body=[], type_ignores=[])
        try:
            tree = ast.parse(text) if "feature_flag" in text else empty
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
    errors.extend(check_published_images(root, files))
    return errors
