"""Static local producer dependencies, usable by the stdlib-only build scheduler."""

import ast
import hashlib
import os
from functools import lru_cache
from pathlib import Path


def selected_source(source, roots):
    """Include referenced local definitions, constants, defaults and imports."""
    tree = ast.parse(source)
    bindings = {}
    for index, node in enumerate(tree.body):
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.asname or alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [item.id for target in targets for item in ast.walk(target)
                     if isinstance(item, ast.Name)]
        for name in names:
            bindings.setdefault(name, []).append(index)
    pending, selected = list(roots), set()
    while pending:
        name = pending.pop()
        for index in bindings.get(name, ()):
            if index in selected:
                continue
            selected.add(index)
            pending.extend(node.id for node in ast.walk(tree.body[index])
                           if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load))
    if not all(name in bindings for name in roots):
        raise ValueError(f"missing source definitions: {roots}")
    return ast.dump(ast.Module(body=[tree.body[i] for i in sorted(selected)], type_ignores=[]),
                    include_attributes=False).encode()


# Names whose use makes a module's bindings unknowable from its syntax alone.
DYNAMIC = frozenset({"globals", "eval", "exec", "__import__", "import_module"})
# Non-Python resources a producer opens by a literal file name.
RESOURCES = (".json", ".mjs", ".js", ".toml", ".csv", ".txt", ".yml", ".yaml")


def _bound(node, declared=False):
    """Module-level names one top-level statement binds, including inside if/try/with blocks."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names = [node.name]
        if declared:  # A function may assign module globals through a global declaration.
            names += [name for item in ast.walk(node) if isinstance(item, ast.Global) for name in item.names]
        return names
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [alias.asname or alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name)]
    names = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            names += _bound(child, declared)
        elif isinstance(child, ast.excepthandler):
            names += [child.name] if child.name else []
            names += [n for stmt in child.body for n in _bound(stmt, declared)]
        elif isinstance(child, ast.withitem) and child.optional_vars is not None:
            names += [item.id for item in ast.walk(child.optional_vars) if isinstance(item, ast.Name)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        names += [item.id for item in ast.walk(node.target) if isinstance(item, ast.Name)]
    return names


@lru_cache(maxsize=1024)
def _parsed(source):
    tree = ast.parse(source)
    declared = b"global " in source
    bindings = {}
    for index, node in enumerate(tree.body):
        for name in _bound(node, declared):
            bindings.setdefault(name, []).append(index)
    return tree, bindings


@lru_cache(maxsize=1 << 16)
def _dump(source, index):
    return ast.dump(_parsed(source)[0].body[index], include_attributes=False).encode()


def _references(node):
    """Loaded names, imports, literal strings and dynamic lookups inside one statement."""
    names, imports, strings, dynamic = set(), [], set(), False
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            dynamic |= item.id in DYNAMIC
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            dynamic |= item.attr in DYNAMIC
        elif isinstance(item, ast.ImportFrom):
            imports.append((item.level, item.module, tuple(alias.name for alias in item.names)))
        elif isinstance(item, ast.Import):
            imports.append((None, None, tuple(alias.name for alias in item.names)))
        elif isinstance(item, ast.Constant) and isinstance(item.value, str):
            strings.add(item.value)
    return frozenset(names - DYNAMIC), tuple(imports), frozenset(strings), dynamic


@lru_cache(maxsize=1 << 16)
def _top_references(source, index):
    return _references(_parsed(source)[0].body[index])


class Closure:
    """Static dependency closure of seed statements over the local package tree.

    Follows module-level names, ``from x import name`` into the referenced definitions,
    whole modules for module objects, star imports, dynamic lookups or unknown names,
    and literal resource file names. Code outside ``root`` (numpy, shapely, ...) is external.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.modules = {}
        self.resources = set()
        self.missing = set()
        self.pending = []
        self.listings = {}
        self.seen = set()

    def module(self, path):
        found = self.modules.get(path)
        if found is None:
            source = path.read_bytes()
            tree, bindings = _parsed(source)
            found = self.modules[path] = dict(
                source=source, tree=tree, bindings=bindings, selected=set(), whole=False,
                package=path.relative_to(self.root).parent.parts,
            )
        return found

    def seed(self, path, node):
        """A statement the caller hashes itself; only its references join the closure."""
        self.pending.append((Path(path), node))
        self._drain()

    def name(self, path, name):
        self._name(Path(path), name)
        self._drain()

    def _name(self, path, name):
        found = self.module(path)
        indices = found["bindings"].get(name)
        if indices is None:
            return self._whole(path)
        for index in indices:
            self._select(path, found, index)

    def _whole(self, path):
        found = self.module(path)
        if not found["whole"]:
            found["whole"] = True
            for index in range(len(found["tree"].body)):
                self._select(path, found, index)

    def _select(self, path, found, index):
        if index not in found["selected"]:
            found["selected"].add(index)
            self.pending.append((path, _top_references(found["source"], index)))

    def _drain(self):
        while self.pending:
            path, references = self.pending.pop()
            if isinstance(references, ast.AST):
                references = _references(references)
            self._visit(path, references)

    def _visit(self, path, references):
        found = self.module(path)
        names, imports, strings, dynamic = references
        if dynamic:
            self._whole(path)
        bindings = found["bindings"]
        for name in names:
            for index in bindings.get(name, ()):
                self._select(path, found, index)
        for level, module, aliases in imports:
            if level is None:
                for alias in aliases:
                    target = self._resolve(tuple(alias.split(".")))
                    if target is not None:
                        self._whole(target)
            else:
                self._import_from(found, level, module, aliases)
        for value in strings:
            self._resource(path, value)

    def _entries(self, directory):
        """Exact-case directory names, so case-insensitive file systems resolve like Linux."""
        found = self.listings.get(directory)
        if found is None:
            found = self.listings[directory] = set(os.listdir(directory)) if directory.is_dir() else set()
        return found

    def _resolve(self, parts):
        """The local module or package file for dotted parts, None for external code."""
        if not parts or parts[0] not in self._entries(self.root):
            return None
        directory = self.root.joinpath(*parts[:-1])
        entries = self._entries(directory)
        if f"{parts[-1]}.py" in entries:
            return directory / f"{parts[-1]}.py"
        if parts[-1] in entries and "__init__.py" in self._entries(directory / parts[-1]):
            return directory / parts[-1] / "__init__.py"
        self.missing.add(".".join(parts))
        return None

    def _submodule(self, package, name):
        entries = self._entries(package)
        return f"{name}.py" in entries or (
            name in entries and "__init__.py" in self._entries(package / name))

    def _import_from(self, found, level, module, aliases):
        parts = tuple(module.split(".")) if module else ()
        if level:
            package = found["package"]
            parts = package[: len(package) - level + 1] + parts
        target = self._resolve(parts)
        if target is None:
            return
        for alias in aliases:
            if alias == "*":
                self._whole(target)
            elif target.name == "__init__.py" and self._submodule(target.parent, alias):
                self._whole(self._resolve(parts + (alias,)))
            else:
                self._name(target, alias)

    def _resource(self, path, value):
        if len(value) > 200 or "\n" in value or not value.endswith(RESOURCES):
            return
        if (path.parent, value) in self.seen:
            return
        self.seen.add((path.parent, value))
        for candidate in (path.parent / value, self.root / value, self.root.parent / value):
            if candidate.is_file():
                self.resources.add(candidate.resolve())

    def digest(self):
        """Selected definitions per module in source order; comments and layout are ignored."""
        h = hashlib.sha256(b"closure-v1")
        for path in sorted(self.modules, key=lambda p: p.relative_to(self.root).as_posix()):
            found = self.modules[path]
            if not found["selected"] and not found["whole"]:
                continue
            h.update(path.relative_to(self.root).as_posix().encode() + b"\0")
            h.update(b"whole" if found["whole"] else b"selected")
            for index in sorted(found["selected"]):
                part = _dump(found["source"], index)
                h.update(len(part).to_bytes(8, "little") + part)
        top = self.root.resolve().parent
        for path in sorted(self.resources):
            data = path.read_bytes()
            name = path.relative_to(top).as_posix() if path.is_relative_to(top) else path.name
            h.update(name.encode() + b"\0" + len(data).to_bytes(8, "little") + data)
        for name in sorted(self.missing):
            h.update(b"missing\0" + name.encode())
        return h.digest()
