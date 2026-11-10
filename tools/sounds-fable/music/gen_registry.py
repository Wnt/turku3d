"""Board registry (Finnish texts) for the tracks generated with the local model.

Usage: python -I gen_registry.py GEN_OUT_DIR WORK_MUSIC_GEN_DIR
Reads GEN_OUT_DIR/manifest.json (written by gen/generate.py) and optional vocals.json (Demucs vocal-leak check),
measures each track (analyse.py) and writes WORK_MUSIC_GEN_DIR/tracks.json for board/candidates.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analyse  # noqa: E402
import sfx  # noqa: E402

STYLES = {
    "synthwave": ("synthwave, yöajo", "Radio: yö ja moottoritie"),
    "iskelma": ("iskelmä/tango, haitari", "Radio: paikallisväriä; malli tulkitsi tyylin osin itäeurooppalaiseksi kansanmusiikiksi"),
    "funk": ("70-luvun poliisisarjafunk", "Radio: ajo ja takaa-ajo"),
    "dnb": ("liquid drum and bass", "Tilaus ja kiire"),
    "lofi": ("lo-fi hiphop", "Kävely ja vapaa ajo"),
    "dubtechno": ("dub techno", "Radio: joki yöllä"),
    "surf": ("surf- ja garagerock", "Tilaus ja kiire (pizzakuski)"),
    "menu": ("valikkoteema, piano ja jouset", "Valikko"),
    "metal": ("melodinen metalli", "Radio: suomalainen erikoisuus"),
    "citypop": ("jazz-funk / city pop", "Radio: kesäilta"),
}


def main() -> None:
    gen, out = Path(sys.argv[1]), Path(sys.argv[2])
    manifest = json.loads((gen / "manifest.json").read_text())
    vocals = {v["file"]: v for v in json.loads((gen / "vocals.json").read_text())} if (gen / "vocals.json").exists() else {}
    tracks = {}
    rows = []
    for entry in sorted(manifest, key=lambda e: (list(STYLES).index(e["slug"]) if e["slug"] in STYLES else 99, str(e["seed"]))):
        slug = entry["slug"]
        path = gen / entry["file"]
        if slug not in STYLES or not path.exists():
            continue
        style, use = STYLES[slug]
        requested = entry["settings"].get("bpm")
        m = analyse.measure(str(path), requested)
        rows.append(m)
        model = entry["model"]
        leak = vocals.get(entry["file"])
        leak_text = ""
        if leak is not None:
            leak_text = " Lauluvuoto (Demucs): " + ("ei havaittu." if not leak.get("vocals_suspected") else f"mahdollinen, laulustemmin taso {leak.get('vocals_rel_db', '?')} dB.")
        tempo = f"pyydetty {requested} BPM, mitattu {m['tempo_bpm']:.0f}" if requested else f"mitattu {m['tempo_bpm']:.0f} BPM"
        tracks[f"music-gen-{slug}-s{entry['seed']}"] = {
            "file": str(path), "title_fi": f"Generoitu: {style} (siemen {entry['seed']})", "rating": 4, "bitrate": "128k",
            "desc_fi": (f"{use}. Kehote: <i>{entry['prompt']}</i> Malli: {model['name']} ({model['dit']}, {entry['settings']['steps']} askelta), paikallisesti M1 Maxilla "
                        f"{entry['wall_time_s']['generate']:.0f} sekunnissa; koodi ja painot MIT-lisenssillä. Tempo: {tempo}.{leak_text}"),
        }
    note = ("Tekstikehotteesta kokonaisen tuotetun kappaleen tekevä avoin malli (ACE-Step 1.5). Kappaleet ovat ensimmäisiä otoksia, ei valikoituja: "
            "samasta kehotteesta saa uuden version noin 40 sekunnissa. En ole kuullut näitä; tähdet kertovat vain, että tiedosto on ehjä ja tempo osuu.")
    sfx.write_json(out / "tracks.json", {"note_fi": note, "tracks": tracks})
    sfx.write_json(out / "analysis.json", rows)
    print(len(tracks), "tracks")


if __name__ == "__main__":
    main()
