"""Candidate registry of the soundboard: what is shown, in which order, with which Finnish text.

Titles and descriptions are Finnish (the board's reader); identifiers and code are English. Sources live outside
git under ~/turku3d/loops/sounds-fable/. Optional parts (recordings, resynthesis, generated music) appear once their
manifests exist, so the board can be rebuilt at any stage.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.home() / "turku3d/loops/sounds-fable"
WORK = ROOT / "work"
GAME_AUDIO = Path(__file__).resolve().parents[3] / "game/public/assets/audio"
OWN = "Lähde: oma synteesi, ei äänitteitä. Lisenssi: CC0."


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def gull_items() -> list[dict]:
    current = [{
        "id": "f-cur-gulls", "current": True, "copy": str(GAME_AUDIO / "gulls.ogg"), "loop": "file", "match_rms": -20,
        "title": "Pelin nykyinen lokki (gulls.ogg)",
        "desc": "Kaksi puhdasta siniääntä, jotka liukuvat alas (1250→450 Hz ja 2400→1280 Hz), sama huuto tasan 4 s välein, "
                "mono 24 kHz. Soi yhdestä pisteestä 16 m joen yläpuolella. Proseduraalinen, CC0.",
    }]
    syn = WORK / "gulls-syn"
    synth = [
        ("gull-syn-herring-keow", "Synteesi: harmaalokin yksittäishuudot", "Kolme eri yksilön \"kjau\"-huutoa.", 2),
        ("gull-syn-herring-longcall", "Synteesi: harmaalokin naurusarja", "Pitkä huuto: matalat alkuäänet, 2–3 pitkää kirkaisua ja hidastuva \"kjau-kjau\"-sarja; joka kerta eri pituinen.", 2),
        ("gull-syn-herring-alarm", "Synteesi: harmaalokin varoitus", "\"Gag-ag-ag\": lyhyitä matalia ääniä 3–4 ryhmissä.", 2),
        ("gull-syn-common-mew", "Synteesi: kalalokin naukaisu", "Kaksi pitkää, valittavaa \"mjäy\"-ääntä; korkeampi ja ohuempi kuin harmaalokki.", 2),
        ("gull-syn-common-longcall", "Synteesi: kalalokin naurusarja", "Sama kielioppi kuin harmaalokilla, 45 % korkeammalta.", 2),
        ("gull-syn-blackheaded-kree", "Synteesi: naurulokin rääkäisyt ja kina", "Karhea, pärisevä \"krrriä\" (amplitudin pärinä 42–60 Hz, vahvat puoliharmoniset).", 2),
        ("gull-syn-distance", "Synteesi: sama huuto 15 m, 80 m ja 300 m päästä", "Etäisyyskäsittely veden yli: 1/r-vaimennus, ilman absorptio, heijastus vedenpinnasta ja etäisyyden mukana kasvava kaiku. "
                                                                              "Sama käsittely sopii myös äänitteille.", 3),
    ]
    method = "Menetelmä: lähde–suodin-malli (harmoninen lähde, värinä, puoliharmoninen rahina, hengityskohina, formantit) ja huutojen kielioppi. "
    items = [{"id": "f-" + name, "src": str(syn / f"{name}.wav"), "title": title, "desc": f"{what} {method}{OWN}", "rating": rating,
              "norm": ("peak", -3), "image_seconds": 10} for name, title, what, rating in synth if (syn / f"{name}.wav").exists()]
    loop = syn / "gull-syn-harbour-loop.wav"
    if loop.exists():
        items.append({"id": "f-gull-syn-harbour-loop", "src": str(loop), "loop": "body", "norm": ("rms", -24), "bitrate": "96k", "rating": 2,
                      "title": "Synteesi: satamaluuppi (48 s)", "image_seconds": 48,
                      "desc": "Vesi (kohina, liplatus, kuplat), tuuli, kaukainen yhdyskunta (46 huutoa 140–420 m päässä) ja muutama lähilintu, jotka vastaavat toisilleen. "
                              "Rakennettu ympyräaikajanalle, joten sauma on täsmälleen saumaton. " + OWN})
    return [{"items": current}, {"title": "Oma synteesi (ei äänitteitä)", "items": items,
                                 "note": "Ajossa laskettava malli: jokainen huuto on erilainen, eikä tarvita yhtään tiedostoa. Lokin tunnistaa, mutta aidon linnun rosoisuus jää vajaaksi."}]


def tyre_items() -> list[dict]:
    tyre = WORK / "tyre"
    current = [
        {"id": "f-cur-screech", "current": True, "copy": str(GAME_AUDIO / "screech.ogg"), "loop": "file", "match_rms": -20,
         "title": "Pelin nykyinen vinkuna (screech.ogg, auto ja skootteri)",
         "desc": "Yksi 1400 Hz:n siniääni, 33 Hz:n värinä ja valkoista kohinaa. Sävelkorkeus ei muutu koskaan; vain voimakkuus seuraa luistoa. Proseduraalinen, CC0."},
        {"id": "f-cur-roll", "current": True, "copy": str(GAME_AUDIO / "roll.ogg"), "loop": "file", "match_rms": -20,
         "title": "Pelin nykyinen rengaskohina (roll.ogg)",
         "desc": "Ruskeaa kohinaa (alle 120 Hz) ja 3 Hz:n tremolo; voimakkuus seuraa nopeutta. Proseduraalinen, CC0."},
    ]
    truck = tyre / "current-truck-screech.wav"
    if truck.exists():
        current.append({"id": "f-cur-truck-screech", "current": True, "src": str(truck), "loop": ("take", 4.0, 0.5), "norm": ("rms", -20), "mono": True,
                        "title": "Pelin nykyinen vinkuna (trophy truck, ajonaikainen synteesi)",
                        "desc": "1050 Hz:n siniääni, 6,5 Hz:n vibrato (±40 Hz) ja kapea kohinakaista. Renderöity pelin kaavalla (engine/chassis.ts) täydellä tasolla. CC0."})
    method = "Menetelmä: stick-slip-malli (9 kulutuspinnan palaa, jousi + Coulomb/Stribeck-kitka, 4× ylinäytteistys), hankauskohina ja vierintäkohina. "
    synth = [
        ("chirp-short", "Synteesi: lyhyt sirkaisu", "Rengas irtoaa 0,18 s ajaksi (vaihto, reunakivi, nopea ohjausliike). Sävelkorkeus nousee ja laskee luiston mukana.", None, 3),
        ("chirp-double", "Synteesi: kaksoissirkaisu", "Kaksi peräkkäistä irtoamista, toinen kevyempi.", None, 3),
        ("skid-stop", "Synteesi: lukkojarrutus 60 → 0 km/h", "Luistonopeus laskee auton mukana; ääni madaltuu ja karhenee loppua kohti.", None, 3),
        ("drift-corner", "Synteesi: drifti mutkan läpi", "Sisääntulo, kasvu, pito ohjauskorjauksineen ja ote takaisin; kuorma heiluu.", None, 3),
        ("drift-light-long", "Synteesi: kevyt luisto, luuppi", "Luistonopeus 2 m/s, kuorma 1,0: rengas juuri pidon rajalla.", ("take", 8.0, 1.0), 3),
        ("drift-mid-long", "Synteesi: tasainen luisto, luuppi", "Luistonopeus 4,2 m/s, kuorma 1,25.", ("take", 8.0, 1.0), 3),
        ("drift-hard-long", "Synteesi: täysi drifti, luuppi", "Luistonopeus 8,5 m/s, kuorma 1,6.", ("take", 8.0, 1.0), 3),
        ("sweep-slip-load", "Synteesi: luisto 0 → 10 → 0 m/s kolmella kuormalla", "Kolme 6 s:n ajoa: kevyt (0,6×), normaali (1×) ja raskas (1,7×) kuorma. Raskas rengas murisee matalammalta.", None, 3),
        ("surfaces", "Synteesi: sama luisto neljällä pinnalla", "4 s kutakin: kuiva asfaltti, sileä maalattu betoni (parkkihalli), märkä asfaltti (ei vinkunaa, vain suhina), sora (rapina).", None, 3),
    ]
    items = []
    for name, title, what, loop, rating in synth:
        path = tyre / f"{name}.wav"
        if path.exists():
            items.append({"id": "f-tyre-syn-" + name.replace("-long", "-loop"), "src": str(path), "title": title, "desc": f"{what} {method}{OWN}", "rating": rating,
                          "norm": ("rms", -20), "image_seconds": 18, **({"loop": loop} if loop else {})})
    return [{"items": current},
            {"title": "Oma synteesi: fysikaalinen stick-slip-malli", "items": items,
             "note": "Nämä ovat saman ajonaikaisen mallin offline-renderöintejä. Yllä olevassa säätimellisessä demossa sama koodi pyörii selaimessa AudioWorklet-solmuna."}]


def music_items() -> list[dict]:
    cur = WORK / "music-current"
    current = []
    for name, title, what in (
        ("music-current-explore", "Pelin nykyinen musiikki: vapaa ajo (viisi kappaletta peräkkäin)",
         "Aura Neon, Market Sun, River Dub, Ring Road ja Harbour Glass, 6–12 tahtia kutakin. Kaikissa sama kuvio: yksi siniääni tahdissa, basso ja \"rumpu\" (65/160 Hz:n siniääni) joka toisella iskulla, yksi johtoääni tahdissa."),
        ("music-current-mission", "Pelin nykyinen musiikki: tilaus ja kiire (Ring Road)",
         "4 tahtia vapaata ajoa, sitten tilaus: toimitusaika kuluu 0 → 100 %, tempo nousee 18 % ja mukaan tulee perkussio- ja paniikkikerros sekä loppukello."),
    ):
        path = cur / f"{name}.wav"
        if path.exists():
            current.append({"id": "f-cur-" + name.replace("-current", ""), "current": True, "kind": "music", "src": str(path), "norm": ("rms", -20), "bitrate": "96k", "mono": True,
                            "title": title, "desc": what + " Renderöity pelin sekvensserin kaavalla (music/index.ts); pelissä taso on noin −41 dB RMS, tässä nostettu vertailua varten. CC0."})
    return [{"items": current}]


def groups() -> list[dict]:
    return [{"id": "gulls", "subgroups": gull_items()}, {"id": "tyres", "subgroups": tyre_items()}, {"id": "music", "subgroups": music_items()}]


def figures() -> list[Path]:
    """Research figures copied to img/ (names start with fig-)."""
    return sorted((WORK / "figures").glob("fig-*.png")) if (WORK / "figures").exists() else []
