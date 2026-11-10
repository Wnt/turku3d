"""Candidate registry of the soundboard: what is shown, in which order, with which Finnish text.

Titles and descriptions are Finnish (the board's reader); identifiers and code are English. Sources live outside
git under ~/turku3d/loops/sounds-fable/. Optional parts (recordings, resynthesis, generated music) appear once their
files exist, so the board can be rebuilt at any stage.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path.home() / "turku3d/loops/sounds-fable"
WORK = ROOT / "work"
GAME_AUDIO = Path(__file__).resolve().parents[3] / "game/public/assets/audio"
OWN = "Lähde: oma synteesi, ei äänitteitä. Lisenssi: CC0."
USED_SOURCES: dict[str, set[str]] = {}  # source id -> how it was used ("ääni", "mittaus")


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


SOURCES = {e["id"]: e for e in load_json(ROOT / "sources/manifest.json", [])}
LICENCE_FI = {"CC0-1.0": "CC0", "CC-BY-4.0": "CC BY 4.0"}


def credit(source_ids: list[str], use: str = "ääni") -> str:
    """Finnish source line with links; records the use for the sources table."""
    parts = []
    for sid in source_ids:
        entry = SOURCES.get(sid)
        if not entry:
            continue
        USED_SOURCES.setdefault(sid, set()).add(use)
        label = "xeno-canto XC" + sid.split("-")[0][2:] if sid.startswith("xc") else "BigSoundBank " + sid.split("-")[1] if sid.startswith("bsb") else sid
        author = entry["author"].replace("_", " ").split(" (")[0]
        parts.append(f'<a href="{html.escape(entry["source_page"])}">{label}</a> ({html.escape(author)}, {LICENCE_FI.get(entry["licence"], entry["licence"])})')
    return "Lähde: " + ", ".join(parts) + "."


def gull_items() -> list[dict]:
    current = [{
        "id": "f-cur-gulls", "current": True, "copy": str(GAME_AUDIO / "gulls.ogg"), "loop": "file", "match_rms": -20,
        "title": "Pelin nykyinen lokki (gulls.ogg)",
        "desc": "Kaksi puhdasta siniääntä, jotka liukuvat alas (1250→450 Hz ja 2400→1280 Hz), sama huuto tasan 4 s välein, "
                "mono 24 kHz. Soi yhdestä pisteestä 16 m joen yläpuolella. Proseduraalinen, CC0.",
    }]
    groups = [{"items": current}]
    cand = WORK / "gulls-cand"

    # Recordings: cleaned cuts.
    rec_meta = {e["id"]: e for e in load_json(cand / "rec.json", [])}
    clean = "Menetelmä: äänitteestä leikattu, ylipäästö 280 Hz ja spektrinen kohinanpoisto (enintään −15 dB), häivytykset. "
    rec = [
        ("gull-rec-herring-keow", "Äänite: harmaalokin yksittäishuudot", "Neljä huutoa läheltä (signaali-kohinasuhde 37–40 dB). Perusääni noin 0,85–0,95 kHz, kerrannaisia 8 kHz:iin.", 5),
        ("gull-rec-herring-alarm", "Äänite: harmaalokin varoitus", "\"Gag-ag-ag\"-sarja, viisi lyhyttä ääntä.", 4),
        ("gull-rec-herring-longcall-a", "Äänite: harmaalokin naurusarja A", "Koko pitkä huuto alkuäänineen läheltä: perusääni 1,35–1,65 kHz laskee jokaisessa äänessä, noin 1,3 ääntä sekunnissa.", 5),
        ("gull-rec-herring-longcall-b", "Äänite: harmaalokin naurusarja B (lennossa)", "12 ääntä, MP3-lähde.", 4),
        ("gull-rec-herring-flight", "Äänite: harmaalokin lentohuudot", "Kolme pitkää yksittäishuutoa.", 4),
        ("gull-rec-herring-series", "Äänite: harmaalokin pitkä naurusarja pesimäluodolla", "16 s yhtäjaksoista sarjaa, taustalla muuta yhdyskuntaa. MP3-lähde.", 3),
        ("gull-rec-common-longcall-a", "Äänite: kalalokin naurusarja A (Tromssa)", "Täydellinen pitkä huuto: kaarevat äänet kiihtyvät kahteen pitkään kirkaisuun ja hiipuvat. Signaali-kohinasuhde 37 dB.", 5),
        ("gull-rec-common-longcall-b", "Äänite: kalalokin naurusarja B (Lidingö, Itämeri)", "Itämeren rannikolta, useampi lintu vastaa.", 4),
        ("gull-rec-common-calls", "Äänite: kalalokin huutosarja ja yksittäishuutoja (Lidingö)", "Sarja ja kaksi erillistä huutoa.", 4),
        ("gull-rec-common-mew", "Äänite: kalalokin lyhyet naukaisut lennossa", "Kolme 0,13 s:n kaarevaa \"kli\"-ääntä. MP3-lähde.", 3),
        ("gull-rec-blackheaded-kree", "Äänite: naurulokin rääkäisyt", "Kolme karheaa lentohuutoa: tiheä kerrannaisrakenne 8 kHz:iin.", 4),
        ("gull-rec-blackheaded-calls", "Äänite: naurulokin pitkä huuto ja sarja", "MP3-lähde.", 4),
        ("gull-rec-lbb-alarm", "Äänite: selkälokin varoitushuudot", "Matalampi ja täyteläisempi kuin harmaalokki; signaali-kohinasuhde 36–42 dB. MP3-lähde.", 4),
        ("gull-rec-lbb-longcall", "Äänite: selkälokin naurusarja", "10 ääntä. MP3-lähde.", 4),
        ("gull-rec-gbb-longcall", "Äänite: merilokin naurusarja (Tromssa)", "Suurin lokki, matalin ääni; satamaan ja ulkosaaristoon.", 3),
    ]
    items = []
    for name, title, what, rating in rec:
        path = cand / f"{name}.wav"
        if path.exists() and name in rec_meta:
            items.append({"id": "f-" + name, "src": str(path), "title": title, "rating": rating, "norm": ("peak", -3), "mono": True, "image_seconds": 16,
                          "desc": f"{what} {clean}{credit(rec_meta[name]['sources'])}"})
    if items:
        groups.append({"title": "Äänitteet: yksittäiset huudot ja sarjat", "items": items,
                       "note": "Oikeita lintuja, vapaasti käytettävistä kenttä-äänitteistä leikattuina ja puhdistettuina. Peliin näistä tehtäisiin 20–40 lyhyen näytteen sarja, "
                               "jota soitetaan satunnaisesti, hieman eri korkeuksilta ja eri paikoista."})

    # Scenes and loops.
    loops = []
    mix = cand / "gull-mix-harbour-loop.wav"
    if mix.exists() and "gull-mix-harbour-loop" in rec_meta:
        loops.append({"id": "f-gull-mix-harbour-loop", "src": str(mix), "loop": "body", "norm": ("rms", -24), "bitrate": "112k", "rating": 4, "image_seconds": 60,
                      "title": "Satamaluuppi äänitteistä (60 s)",
                      "desc": "Oikea veden liplatus laiturilla, kaukainen lokkiparvi (400 m) ja 40 yksittäistä huutoa sijoiteltuna 30–380 m:n päähän etäisyyskäsittelyllä "
                              "(vaimennus, ilman absorptio, vedenpinnan heijastus, kaiku). Ympyräaikajana: sauma on saumaton. Tällaisen kohtauksen peli voisi koota ajossa itse. "
                              + credit(rec_meta["gull-mix-harbour-loop"]["sources"])})
    syn = WORK / "gulls-syn"
    if (syn / "gull-syn-harbour-loop.wav").exists():
        loops.append({"id": "f-gull-syn-harbour-loop", "src": str(syn / "gull-syn-harbour-loop.wav"), "loop": "body", "norm": ("rms", -24), "bitrate": "96k", "rating": 2,
                      "title": "Satamaluuppi puhtaalla synteesillä (48 s)", "image_seconds": 48,
                      "desc": "Vesi (kohina, liplatus, kuplat), tuuli, kaukainen yhdyskunta (46 huutoa 140–420 m päässä) ja muutama lähilintu, jotka vastaavat toisilleen. "
                              "Ympyräaikajana, saumaton. " + OWN})
    for name, title, what, rating in (
        ("gull-rec-herring-roost-loop", "Äänite: yli 2400 harmaalokin yöpymisparvi, luuppi (44 s)", "Tiheä kaukainen kuoro: sopii sataman tai kaatopaikan taustaksi. Vain ylipäästö, 3 s:n ristihäivytys. MP3-lähde.", 3),
        ("gull-rec-blackheaded-colony-loop", "Äänite: naurulokkiyhdyskunta pesimäpaikalla, luuppi (36 s)", "Jatkuva lähikuoro; Aurajoen ja torin kesä-ääni. Vain ylipäästö, 3 s:n ristihäivytys.", 4),
    ):
        path = cand / f"{name}.wav"
        if path.exists() and name in rec_meta:
            loops.append({"id": "f-" + name, "src": str(path), "loop": "body", "norm": ("rms", -22), "bitrate": "96k", "rating": rating, "title": title, "image_seconds": 44,
                          "desc": f"{what} {credit(rec_meta[name]['sources'])}"})
    if loops:
        groups.append({"title": "Satama- ja yhdyskuntaluupit", "items": loops})

    # Resynthesis from measurements.
    resyn_meta = {e["id"]: e for e in load_json(cand / "resyn.json", [])}
    method = "Menetelmä: harmoninen + kohina -malli: äänitteestä mitataan 5 ms:n välein perustaajuus, 32 kerrannaisen voimakkuudet ja kerrannaisten välinen kohina; ääni soitetaan oskillaattoripankilla ja muotoillulla kohinalla. "
    resyn = [
        ("gull-resyn-herring-keow", "Uudelleensynteesi: harmaalokin yksittäishuudot", "Sama kohta kuin äänitteessä yllä, mutta jokainen näyte on laskettu mitatuista parametreista.", 3),
        ("gull-resyn-herring-keow-birds", "Uudelleensynteesi: yksi mitattu huuto viitenä eri lintuna", "Sävelkorkeus 0,90–1,13×, kesto 0,90–1,12×, formantit 0,94–1,08× toisistaan riippumatta: iso hidas lintu, alkuperäinen, pieni nopea ja kaksi muuta.", 4),
        ("gull-resyn-herring-longcall", "Uudelleensynteesi: harmaalokin naurusarja", "Vertaa äänitteeseen A.", 3),
        ("gull-resyn-common-longcall", "Uudelleensynteesi: kalalokin naurusarja", "Vertaa äänitteeseen A (Tromssa).", 3),
        ("gull-resyn-common-longcall-birds", "Uudelleensynteesi: kalalokin naurusarja kahtena muuna lintuna", "Matalampi ja hitaampi, sitten korkeampi ja nopeampi.", 3),
        ("gull-resyn-blackheaded-kree", "Uudelleensynteesi: naurulokin rääkäisyt", "Karhea ääni on mallille vaikein: osa pärinästä siirtyy kohinaosaan.", 2),
    ]
    items = []
    for name, title, what, rating in resyn:
        path = cand / f"{name}.wav"
        if path.exists() and name in resyn_meta:
            items.append({"id": "f-" + name, "src": str(path), "title": title, "rating": rating, "norm": ("peak", -3), "mono": True, "image_seconds": 20,
                          "desc": f"{what} {method}{credit(resyn_meta[name]['sources'], 'mittaus')} Tulos on omaa synteesiä (CC0)."})
    if items:
        groups.append({"title": "Uudelleensynteesi mitatuista huudoista", "items": items,
                       "note": "Äänitteestä otetaan vain mittaustulokset, ei näytteitä. Etu: yhdestä huudosta saa rajattomasti eri yksilöitä, ja yksi huuto vie muistia noin 2–4 kt. "
                               "Haitta: nopeimmat alukkeet ja karhein rahina pehmenevät."})

    # Pure synthesis.
    synth = [
        ("gull-syn-herring-keow", "Synteesi: harmaalokin yksittäishuudot", "Kolme eri yksilön \"kjau\"-huutoa.", 2),
        ("gull-syn-herring-longcall", "Synteesi: harmaalokin naurusarja", "Pitkä huuto: matalat alkuäänet, 2–3 pitkää kirkaisua ja hidastuva \"kjau-kjau\"-sarja; joka kerta eri pituinen.", 2),
        ("gull-syn-herring-alarm", "Synteesi: harmaalokin varoitus", "\"Gag-ag-ag\": lyhyitä matalia ääniä 3–4 ryhmissä.", 2),
        ("gull-syn-common-mew", "Synteesi: kalalokin naukaisu", "Kaksi pitkää, valittavaa \"mjäy\"-ääntä.", 2),
        ("gull-syn-common-longcall", "Synteesi: kalalokin naurusarja", "Sama kielioppi kuin harmaalokilla, eri äänielimistö.", 2),
        ("gull-syn-blackheaded-kree", "Synteesi: naurulokin rääkäisyt ja kina", "Karhea, pärisevä \"krrriä\" (amplitudin pärinä, vahvat puoliharmoniset).", 2),
        ("gull-syn-distance", "Synteesi: sama huuto 15 m, 80 m ja 300 m päästä", "Etäisyyskäsittely veden yli: 1/r-vaimennus, ilman absorptio, heijastus vedenpinnasta ja etäisyyden mukana kasvava kaiku. "
                                                                              "Sama käsittely toimii äänitteille.", 3),
    ]
    method = "Menetelmä: lähde–suodin-malli (harmoninen lähde, värinä, puoliharmoninen rahina, hengityskohina, formantit) ja huutojen kielioppi. "
    items = [{"id": "f-" + name, "src": str(syn / f"{name}.wav"), "title": title, "desc": f"{what} {method}{OWN}", "rating": rating,
              "norm": ("peak", -3), "image_seconds": 10} for name, title, what, rating in synth if (syn / f"{name}.wav").exists()]
    if items:
        groups.append({"title": "Puhdas synteesi (ei äänitteitä, ei mittauksia)", "items": items,
                       "note": "Ajossa laskettava malli, jossa jokainen huuto on erilainen eikä tarvita yhtään tiedostoa. Lokin tunnistaa, mutta aidon linnun rosoisuus jää vajaaksi; "
                               "ensimmäisen version sävelkorkeudet arvasin kirjallisuuden pohjalta, ja mittaus osoitti naurusarjan olevan oikeasti noin 1,6 kertaa korkeampi."})
    return groups


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
    groups = [{"items": current}]

    rec_dir = WORK / "tyre-cand"
    rec_meta = {e["id"]: e for e in load_json(rec_dir / "rec.json", [])}
    items = []
    for name, meta in rec_meta.items():
        path = rec_dir / f"{name}.wav"
        if path.exists():
            items.append({"id": "f-" + name, "src": str(path), "title": meta["title_fi"], "rating": meta.get("rating", 4), "norm": ("rms", -20), "image_seconds": 18,
                          "desc": f"{meta['desc_fi']} {credit(meta['sources'])}", **({"loop": "body"} if meta.get("loop") else {})})
    if items:
        groups.append({"title": "Äänitteet ja niistä tehdyt luupit", "items": items, "note": rec_meta and next(iter(rec_meta.values())).get("group_note_fi")})

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
    groups.append({"title": "Oma synteesi: fysikaalinen stick-slip-malli", "items": items,
                   "note": "Nämä ovat saman ajonaikaisen mallin offline-renderöintejä. Yllä olevassa säätimellisessä demossa sama koodi pyörii selaimessa AudioWorklet-solmuna."})
    return groups


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
    groups = [{"items": current}]
    for key, title, note in (("own", "Omat sävellykset: uusi proseduraalinen moottori", None), ("gen", "Paikallisella generatiivisella mallilla tehdyt kappaleet", None)):
        meta = load_json(WORK / f"music-{key}" / "tracks.json", {})
        items = []
        for name, track in meta.get("tracks", {}).items():
            path = WORK / f"music-{key}" / track.get("file", f"{name}.wav")
            if path.exists():
                items.append({"id": "f-" + name, "kind": "music", "src": str(path), "norm": ("rms", -18), "bitrate": track.get("bitrate", "128k"), "title": track["title_fi"],
                              "desc": track["desc_fi"], "rating": track.get("rating")})
        if items:
            groups.append({"title": title, "items": items, "note": meta.get("note_fi", note)})
    return groups


def groups() -> list[dict]:
    return [{"id": "gulls", "subgroups": gull_items()}, {"id": "tyres", "subgroups": tyre_items()}, {"id": "music", "subgroups": music_items()}]


def sources_table() -> str:
    """Finnish HTML table of every downloaded input that a candidate or a measurement used."""
    if not USED_SOURCES:
        return ""
    rows = []
    for sid in sorted(USED_SOURCES, key=lambda s: (SOURCES[s]["area"], s)):
        entry = SOURCES[sid]
        author = entry["author"].replace("_", " ")
        rows.append(f'<tr><td class="id">{sid}</td><td><a href="{html.escape(entry["source_page"])}">{html.escape(entry["title"])}</a></td><td>{html.escape(author)}</td>'
                    f'<td><a href="{entry["licence_url"]}">{LICENCE_FI.get(entry["licence"], entry["licence"])}</a></td><td>{", ".join(sorted(USED_SOURCES[sid]))}</td></tr>')
    return ("<h3>Käytetyt äänitteet</h3><p class=\"dim\">Kaikki ladattu ilman kirjautumista; CC BY -lähteiden tekijät on nimettävä myös pelissä, jos niitä käytetään. "
            "Tiedostot ovat koneella hakemistossa <code>~/turku3d/loops/sounds-fable/sources/</code> (ei gitissä).</p>"
            "<table><tr><th>Tunniste</th><th>Äänite</th><th>Tekijä</th><th>Lisenssi</th><th>Käyttö</th></tr>" + "".join(rows) + "</table>")


def figures() -> list[Path]:
    """Research figures copied to img/ (names start with fig-)."""
    return sorted((WORK / "figures").glob("fig-*.png")) if (WORK / "figures").exists() else []
