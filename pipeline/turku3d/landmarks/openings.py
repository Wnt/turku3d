"""Measured opening details painted in physical scale, without image assets."""

def panelled_door(paint, bump, box, spec, sx, sy):
    """Glazed upper leaves, recessed lower panels and measured metal grille."""
    x0, y0, x1, y1 = box
    leaves = spec.get("columns", 2)
    wood = tuple(spec.get("colour", [91, 80, 62]))
    glass = tuple(spec.get("glass_colour", [38, 45, 41]))
    metal = tuple(spec.get("metal_colour", [128, 122, 100]))
    edge = tuple(min(255, c + 17) for c in wood)
    recess = tuple(max(0, c - 20) for c in wood)
    split = y0 + (y1 - y0) * spec.get("glazed_fraction", .46)
    stile, rail = .09 * sx, .09 * sy
    for leaf in range(leaves):
        left = x0 + (x1 - x0) * leaf / leaves
        right = x0 + (x1 - x0) * (leaf + 1) / leaves
        pane = (left + stile, y0 + rail, right - stile, split)
        paint.rectangle(pane, fill=glass)
        bump.rectangle(pane, fill=-.08)
        # Separate grille bars from the structural timber stiles and rails.
        for bar in range(1, 4):
            x = pane[0] + (pane[2] - pane[0]) * bar / 4
            paint.line((x, pane[1], x, pane[3]), fill=metal, width=max(1, round(.018 * sx)))
            for fraction in (.25, .75):
                y = pane[1] + (pane[3] - pane[1]) * fraction
                paint.ellipse((x - .035*sx, y - .035*sy, x + .035*sx, y + .035*sy), outline=metal)
        panel = (left + stile, split + 2*rail, right - stile, y1 - 2*rail)
        paint.rectangle(panel, fill=recess, outline=edge, width=max(1, round(.04 * sx)))
        bump.rectangle(panel, fill=-.07, outline=-.025, width=max(1, round(.04 * sx)))
        handle = right - .055*sx if leaf == 0 else left + .055*sx
        paint.line((handle, split + rail, handle, split + rail + .22*sy), fill=metal,
                   width=max(1, round(.025*sx)))
