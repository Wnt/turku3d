"""Board registry (Finnish texts) for the renders of the new procedural engine.
Usage: python -I own_registry.py WORK_MUSIC_OWN_DIR"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

METHOD = ("Menetelmä: oma sekvensseri ja syntetisaattori (kaistarajoitetut oskillaattorit, unisono, resonoivat suotimet verhokäyrineen, FM-sähköpiano ja -kellot, "
          "rumpusynteesi, ping-pong-viive, kaiku, sidechain). Sama koodi pyörii yllä olevassa demossa selaimessa. Lähde: oma sävellys ja synteesi. Lisenssi: CC0.")
TRACKS = {
    "music-own-aura-neon": ("Oma moottori: Aura Neon (synthwave, 108 BPM, a-molli)", 3,
                            "Sama nimi, tempo ja sävellaji kuin pelin nykyisellä kappaleella, mutta oikeilla soinnuilla (Am9–Fmaj7–Cadd9–G–Em7), kirjoitetulla melodialla, "
                            "arpeggiolla, bassolla ja rummuilla. Muoto: intro, A, A + melodia, tauko, B, A, B, outro."),
    "music-own-ring-road": ("Oma moottori: Ring Road, tilaus ja kiire (132 → 150 BPM, e-molli)", 3,
                            "Adaptiivinen: 12 s rauhallista ajoa, sitten toimitusaika kuluu 50 sekunnissa. Kiireen kasvaessa mukaan tulevat 16-osahihatit, sekvenssilinja, "
                            "oktaavituplaus, tomifillit, neljäsosabasari, kellon tikitys ja kello; tempo nousee 14 % ja suotimet aukeavat."),
    "music-own-river-dub": ("Oma moottori: River Dub (dub techno, 120 BPM, d-molli)", 3,
                            "Sointuiskut (Dm9–Gm7–Bbmaj7–Am7) pitkään ping-pong-viiveeseen ja kaikuun, pehmeä basari, sub-basso, hihatit takapotkulla. Muoto: intro, pääosa, tauko, pääosa, outro."),
    "music-own-harbour-glass": ("Oma moottori: Harbour Glass, valikkoteema (72 BPM, G-duuri)", 3,
                                "Sähköpianon murtosoinnut (Gmaj9–Em9–Cmaj7–D6/9), pehmeä matto ja harva kellomelodia; ei rumpuja."),
}
out = Path(sys.argv[1])
tracks = {name: {"file": f"{name}.wav", "title_fi": title, "rating": rating, "bitrate": "128k", "desc_fi": f"{what} {METHOD}"}
          for name, (title, rating, what) in TRACKS.items() if (out / f"{name}.wav").exists()}
note = ("Nämä näyttävät, mihin ajonaikainen synteesi yltää, kun moottorissa on soittimet, tila ja sävellys. Etu generoituun tai äänitettyyn verrattuna: "
        "musiikki reagoi peliin iskun tarkkuudella (kiire, tempo, kerrokset, vaimennus) eikä vie latauskokoa. En ole kuullut näitä: miksauksen tasot on asetettu mittaamalla.")
sfx.write_json(out / "tracks.json", {"note_fi": note, "tracks": tracks})
print(len(tracks), "tracks")
