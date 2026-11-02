"""One physical opening layout shared by texture paint and arched relief."""


def openings(width, height, bottom, d, tower):
    proposed = []
    if tower:
        for low, w, h, count in d["tower"]["opening_rows"]:
            proposed.extend(
                (width * (i + 1) / (count + 1), low, w, h, "gothic" in d["style"])
                for i in range(int(count))
            )
        # Only synthesize the belfry opening when no explicit row describes it.
        level = d["tower"]["copper_start_m"] + 1
        if d["roof"]["material"] == "copper" and not any(
            abs(row[0] - level) < 4 for row in d["tower"]["opening_rows"]
        ):
            proposed.append((width / 2, level, width * 0.42, 6, False))
    else:
        w = d["windows"]
        pointed = (
            True
            if any(k in w["type"] for k in ("gothic", "pointed", "lancet"))
            else False
            if "arch" in w["type"]
            else None
        )
        count = max(0, int(width / w["spacing_m"]))
        for low in w.get("rows_m", [w["sill_m"]]):
            proposed.extend(
                (width * (i + 0.5) / count, low, w["width_m"], w["height_m"], pointed)
                for i in range(count)
            )
    if d.get("show_entrance"):
        e = d["entrance"]
        proposed.append((width / 2, e.get("sill_m", 0.1), e["width_m"], e["height_m"], True))
    return [
        row
        for row in proposed
        if row[2] + 0.32 <= width
        and row[1] >= bottom + 0.3
        and row[1] + row[3] <= bottom + height - 0.3
    ]
