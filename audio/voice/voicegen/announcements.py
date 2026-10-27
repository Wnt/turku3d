"""Extra clips for runtime phrase pools and the published delivery address pool."""

import json
import os
from pathlib import Path

from .content import Line

SPEAKERS = {
    "order": "pizzeria",
    "double": "pizzeria",
    "second": "pizzeria",
    "hint_permit": "narrator",
    "hint_pizzeria": "narrator",
    "hint_carry": "pizzeria",
    "customer_fast": "customer",
    "customer_ok": "customer",
    "customer_slow": "customer",
    "pizzeria_greet": "pizzeria",
    "market_greet": "market",
    "dealer_greet": "dealer",
    "permit_0": "narrator",
    "permit_1": "narrator",
    "permit_2": "narrator",
    "pizzeria_back": "pizzeria",
    "pizzeria_after_fail": "pizzeria",
    "pizzeria_no_permit": "pizzeria",
    "market_greet_expired": "market",
    "dealer_no_permit": "dealer",
    "market_sold": "market",
    "delivery_failed": "narrator",
    "market_no_money": "market",
    "pizzeria_carrying": "pizzeria",
    "pizzeria_slice": "pizzeria",
    "pizzeria_tip_rules": "pizzeria",
    "pizzeria_no_money": "pizzeria",
    "dealer_bought_back": "dealer",
    "dealer_no_money": "dealer",
    "dealer_sold_scooter": "dealer",
    "dealer_sold_car": "dealer",
    "dealer_sold_truck": "dealer",
}
SCRIPTED = {
    "linnankatu-13a": "Linnankatu 13a",
    "aurakatu-1": "Aurakatu 1",
    "lantinen-rantakatu-3": "Läntinen Rantakatu 3",
    "kaskenkatu-4a": "Kaskenkatu 4a",
}


def extra_lines(repo):
    phrases = json.loads((repo / "game/src/gameplay/content/phrases.json").read_text())
    for kind, pool in phrases.items():
        if kind not in SPEAKERS or len(pool) != 12 or len(set(pool)) != 12:
            raise ValueError(f"phrase pool {kind} must have twelve distinct lines and a speaker")
    lines = [
        Line(f"phrase_{kind}_{i + 1}", SPEAKERS[kind], text)
        for kind, pool in phrases.items()
        for i, text in enumerate(pool)
    ]
    lines.append(Line("address_then", "pizzeria", "Then"))
    data = Path(os.environ.get("TURKU3D_DATA", Path.home() / "turku3d/data"))
    work = Path(os.environ.get("TURKU3D_WORK", data))
    addresses = dict(SCRIPTED)
    found_pool = False
    for root in (data, work):
        tiles = root / "dist/tiles"
        manifest = tiles / "manifest.json"
        if not manifest.exists():
            continue
        entry = json.loads(manifest.read_text()).get("addresses")
        if entry:
            found_pool = True
            for row in json.loads((tiles / entry["url"]).read_text())["addresses"]:
                addresses[row["id"]] = row["address"]
    if not found_pool:
        # Source-less CI checks keep the committed pool as their inventory; a tile build replaces it.
        committed = repo / "game/public/assets/voice/manifest.json"
        if committed.exists():
            for row in json.loads(committed.read_text())["lines"]:
                if row["id"].startswith("address_") and row["id"] != "address_then":
                    addresses[row["id"][len("address_") :].replace("_", "-")] = row["text"]
    lines.extend(
        Line(f"address_{id.replace('-', '_')}", "pizzeria", address)
        for id, address in sorted(addresses.items())
    )
    return lines
