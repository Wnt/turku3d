"""Static local producer dependencies, usable by the stdlib-only build scheduler."""

import ast


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
