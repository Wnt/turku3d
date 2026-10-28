"""Data-lane input identity and duplicate scheduling/publication decisions."""


def release_id(commit, key, builder=None):
    return f"{commit}-{key[:12]}" + (f"-{builder[:12]}" if builder else "")


def already_live(name, record, commit, key, builder=None):
    """Recognise published inputs even before advisories append the ledger row.

    The name fallback covers releases made before input identity was recorded.
    A stale-tile refresh also needs the same tile builder.
    """
    return name == release_id(commit, key, builder) or (
        record.get("commit") == commit
        and record.get("data_key") == key
        and (builder is None or record.get("tiles_commit") == builder)
    )


def taken(rows, published, commit, key):
    """A failed attempt settles its base; a publication also settles its destination.

    A data build can carry a newer code-lane game, so its successful `to` may differ
    from `from`. Superseded/failed runs never settle a destination they did not publish.
    """
    return any(
        row.get("published") == str(published)
        and row.get("lane") == "data"
        and row.get("data_key") == key
        and (
            row.get("from") == commit or (row.get("result") == "green" and row.get("to") == commit)
        )
        for row in rows
    )
