"""Assemble one static Finnish page with same-origin audio and reproducible source."""
from audio_common import *
from catalogue import *
from html import escape
import datetime
import shutil

HERE=Path(__file__).resolve().parent
media={row['id']:row for name in ['effects','music'] for row in json.loads((WORK/'work'/f'{name}.json').read_text())}
measurements=json.loads((WORK/'evidence/measurements.json').read_text())
catalogue=[]
def duration(s):return f'{int(s)//60}:{int(s)%60:02}'
def card(row,area):
    id,title,method,key,rating,recommended=row
    item=dict(media[id]);source,url,licence=SOURCES[key]
    item.update(title=title,area=area,method=method,source=source,source_url=url,licence=licence,rating=rating)
    # Preserve intensity changes in tyre sweeps and the intentionally quiet harbour bed.
    item['gain']=1
    if area=='music':
        item['gain']=round(10**((-24-measurements[id]['integrated_lufs'])/20),4)
    if id in ['current-gulls','current-screech','current-roll']:
        item['gain']=round(10**((-22-measurements[id]['active_rms_dbfs'])/20),4)
    catalogue.append(item)
    is_current=id.startswith('current-')
    badge='<span class="badge original">PELISSÄ NYT</span>' if is_current else '<span class="badge">KOKEILE ENSIN</span>' if recommended else ''
    seam=f'<button class="seam" data-seam="{id}" title="Aloita 1,5 sekuntia ennen silmukan rajaa">↻ Kuuntele sauma</button>' if item['loop'] else '<span></span>'
    return f'''<article class="card{' current' if is_current else ''}" data-id="{id}">
      <div class="card-top"><span>{escape(id.upper())}</span>{badge}</div>
      <h3>{escape(title)}</h3><p class="method">{escape(method)}</p>
      <p class="credit">Lähde: <a href="{escape(url)}" target="_blank" rel="noopener">{escape(source)}</a><br><span class="license">{escape(licence)}</span> · {'silmukka' if item['loop'] else 'kertatoisto'} · {duration(item['seconds'])}</p>
      <div class="player"><button class="play" data-play="{id}" aria-label="Toista: {escape(title)}">▶</button><div class="timeline">
      <input class="seek" data-seek="{id}" type="range" min="0" max="{item['seconds']}" step="0.05" value="0" aria-label="Toistokohta: {escape(title)}">
      <div class="timing"><span class="elapsed">0:00</span><span>{duration(item['seconds'])}</span></div></div></div>
      <div class="card-actions"><label class="pick"><input data-pick="{id}" type="checkbox"> Valitse</label>{seam}<span class="rating">arvio {rating}/10</span></div></article>'''

def measurement_table(ids):
    out='<div class="table-scroll"><table><thead><tr><th>Näyte</th><th>Spektrin painopiste</th><th>Energia &gt; 3 kHz</th><th>Aktiivinen aika</th></tr></thead><tbody>'
    for id,label in ids:
        m=measurements[id];out+=f'<tr><td>{label}</td><td>{m["centroid_hz"]} Hz</td><td>{m["energy_over_3khz_pct"]} %</td><td>{m["active_pct"]} %</td></tr>'
    return out+'</tbody></table></div>'

gull_research=f'''<p><strong>Ongelma:</strong> nykyinen 7,9 s tiedosto toistaa lähes identtisen huudon kahdesti. Koodissa on vain kaksi siniaaltoa ja symmetrinen vaippa. Vaiheeseen kirjoitettu taajuus tuottaa pääliukuun noin 1 250 → 450 Hz, ei rikasta linnun ääntöelimistön sointia. Sauma on jo häivytetty; suurin ongelma on toiston tunnistettavuus.</p>
<p><strong>Oikea lintu:</strong> pitkä kutsu sisältää johdannon, voimakkaamman huudon ja nauravan tavusarjan. Mew on venyvä, kek-hälytys lyhyt ja matalampi. Eurooppalaisen harmaalokin hälytyksiä tutkineessa aineistossa kekin perustaajuus oli noin 360 Hz ja kyow'n noin 1 400 Hz; kekissä näkyi selviä resonanssialueita. Yhtä vihellystä ei kannata käyttää kaikkiin tilanteisiin. <a href="https://www.researchgate.net/publication/260719270_Anatomical_bases_of_sex-_and_size-related_acoustic_variation_in_herring_gull_alarm_calls" target="_blank" rel="noopener">Lähde: hälytysäänten akustiikkatutkimus</a>.</p>
<p>Kalalokin pitkä kutsu on erilainen, valittavampi sarja; yhteysäänet voivat muistuttaa sen johdantoa. <a href="https://gullstothehorizon.wordpress.com/2012/12/28/common-visitors/" target="_blank" rel="noopener">Maarten van Kleinween omat havainnot ja sonogrammit</a> olivat tutkimuslähde, eivät tämän sivun äänimateriaalia. G03 on lisensoitu sekaympäristöäänite, eikä sen jokaista tavua ole lajivarmennettu.</p>
<p><strong>Peliin:</strong> 12–24 erillistä näytettä per laji, useita oikeita yksilöitä, epätasaiset tauot ja vastaukset. Lähiäänet pistelähteiksi; kauas hiljainen yhdyskuntakerros. Veden yllä suora ääni ja muutama heijastus, ei jatkuvaa hallikaikua. Etäisyys pienentää tasoa ja korkeita taajuuksia. Nykyinen koodi seuraa vain kolmea Aura-keskilinjan pistettä, häipyy 140 metrissä ja sijoittaa lokin 16 m korkeuteen. Satama ja meri tarvitsevat omat elinympäristöalueensa.</p>
{measurement_table([('current-gulls','Nykyinen'),('g02-herring-series','Äänitetty sarja')])}<img src="analysis-gulls.png" alt="Nykyisen lokkisynteesin ja äänitetyn harmaalokin aaltomuoto, spektrogrammi ja keskimääräinen spektri" loading="lazy">'''

tyre_research=f'''<p><strong>Ongelma:</strong> auton 3,9 s silmukan sävy pysyy samana: 1 400 Hz kantoaalto, 33 Hz modulaatio ja vakio kohinakerros. Truck on jo monipuolisempi, mutta sen rengaskerroksen päätaajuus huojuaa aina 1 050 ± 40 Hz 6,5 kertaa sekunnissa. Molemmat kaipaavat vaihtelevaa karkeutta ja pintaeroja.</p>
<p><strong>Oikea rengas:</strong> kosketuksen tarttuminen ja irtoaminen voi virittää kapeita värähtelyalueita; taustalla on leveämpää tien ja kumikuvion kohinaa. Luisto, kuorma, kumi ja pinta vaikuttavat siihen, syntyykö vinkunaa. Taajuudelle ei ole yleistä sääntöä ”kaksinkertainen luisto = kaksinkertainen sävel”. Lyhyt kirskahdus tarvitsee nopean alun ja loppumisen, driftissä ääni elää sekunteja. <a href="https://www.ksp.kit.edu/books/2499/files/004383e1-b60d-4abf-8480-4ef083b2f1a8.pdf" target="_blank" rel="noopener">Pinay: renkaan ja tien äänen kokeellinen tutkimus</a>.</p>
<p><strong>Nykyinen ohjaus:</strong> kaikki pyörät kulkevat TyreSlipFilterin kautta. Mukana ovat sivu- ja pitkittäisluisto, nopeus, kontakti ja hyväksytty pinta. Kontakti odottaa 80 ms ja luiston alku vielä 180 ms; loppuodotus on 100 ms. Auton ääni käyttää pyörien suurinta luistoa ja nopeuskerrointa. Truck erottaa myös asfalttia, soraa ja maata. Kuorma ei ohjaa vinkunan sävyä suoraan. Lyhyt aito kirskahdus voi jäädä nykyisen aloitusviiveen alle.</p>
<p><strong>Peliin:</strong> erillinen nopea kirskahdustapahtuma + jatkuva rengasmalli, jota ohjaavat pyöräkohtainen liukunopeus, pitkittäinen luistosuhde, normaalivoima ja materiaali. Älä muuta fysiikkaa äänen takia. Säilytä kontaktin häiriösuoja, mutta erottele lyhyt ääni pitkästä savu-/jälkiviiveestä. T08 on äänihavaintoon perustuva neljän moodin luonnos, ei mitattu fysikaalinen rengasratkaisija. Sen luistosäädin on yhdistetty 0–1 voimakkuus, ei todellinen luistosuhde.</p>
{measurement_table([('current-screech','Nykyinen'),('t01-recorded-chirp','Äänitetty rengas')])}<img src="analysis-tyres.png" alt="Nykyisen vinkunan ja äänitetyn renkaan aaltomuoto sekä spektri" loading="lazy">'''

music_research=f'''<p><strong>Ongelma:</strong> viisi nimeä ja 84–120 BPM, mutta sama neljän tahdin rytmikaava. Pad on yksi siniaalto, basso kolmio, rummut yksittäisiä 65/160 Hz sinisäveliä. Sointujuuri muuttuu, mutta täysiä sointukäännöksiä ja pidempää fraseerausta ei ole. Se kuulostaa helposti käyttöliittymän piippaukselta. Saumaklikit eivät ole pääsyy: musiikki syntyy ajossa eikä kierrä äänitiedostoa.</p>
<p><strong>Milloin mikäkin soi:</strong> aloitus, tauko ja tallennus/lataus pyytävät menu-tilaa, joka on nykykoodissa <strong>hiljainen</strong>. Vapaa ajo käyttää explore-tilaa. Hyväksytty tilaus käyttää mission-tilaa luovutukseen saakka. Kiire lisää tempoa ja kerroksia; valitut kappaleet säilyvät tehtävän ajan. Vapaassa ajossa kappale vaihtuu 64 tahdin välein, luovutuksen jälkeen ja N-näppäimellä. Intro ja kauppadialogi vaimentavat eniten, ajossa muffle on 0,6. Erillistä takaa-ajokappaleen valintaa ei nykykoodissa ole.</p>
<p><strong>Isoin hyppy:</strong> sävelletyt 8–16 tahdin fraasit, oikeat soinnut ja vastamelodiat, uskottavat iskut, hiljaisuus sekä ero päivä-, yö- ja toimintamusiikin välillä. Pelissä toistetaan valmiita stereokappaleita radiota varten; tilanteeseen reagoiva musiikki kannattaa jakaa samoissa tahdeissa oleviin basso-, rumpu-, harmonia- ja jännityskeroksiin. Vaihda kerroksia tai osia tahdin/aiheen rajalla. <a href="https://dev.epicgames.com/documentation/en-us/fortnite/vertical-composition" target="_blank" rel="noopener">Kerrostavan pelimusiikin periaate</a>.</p>
<p>M01–M04 ovat uusia omia sävellyksiä, eivät generatiivisen mallin tuloksia. <a href="https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B" target="_blank" rel="noopener">ACE-Step v1:n painot ovat Apache-2.0-lisensoituja</a>; malli arvioitiin dokumentaatiosta, ei ajettu tässä versiossa. Se on lupaava tapa saada radiosoittoon rikkaampi instrumenttisointi, mutta rakenteen ja saumojen editointi jää tehtäväksi. <a href="https://huggingface.co/facebook/musicgen-small" target="_blank" rel="noopener">MusicGenin CC-BY-NC-painot</a> rajattiin pois.</p>
{measurement_table([('current-music-aura-neon','Nykyinen Aura Neon'),('m01-river-afterhours','Aurajoen jälkeen')])}<img src="analysis-music.png" alt="Nykyisen musiikin ja uuden downtempo-kappaleen aaltomuoto, ensimmäiset 20 sekuntia ja keskimääräinen spektri" loading="lazy">'''

sections=[]
for area,number,title,lead,research in [
 ('gulls','01','Lokit','Suurin välitön parannus: oikea lintu, oma tila ja tauko. Aloita harmaalokin sarjasta ja satamasta.',gull_research),
 ('tyres','02','Renkaat','Kirskahdus on tapahtuma. Drifti on jatkuvasti muuttuva kosketus. Kuuntele molemmat ja kokeile säätimiä.',tyre_research),
 ('music','03','Musiikki','Neljä erilaista käyttötarkoitusta ja neljä omaa sävellystä. Tässä kuunnellaan suuntaa, ei vielä lopullista pelimiksausta.',music_research)]:
    cards=''.join(card(row,area) for row in CANDIDATES[area])
    extra=''
    if area=='music':
        extra='<details class="extra-baselines"><summary>Kuuntele myös nykyisen pelin neljä muuta kappaletta ja tehtäväversio</summary><div class="cards">'+''.join(card((id,title,method,'gamemusic',3,False),area) for id,title,method in EXTRA_BASELINES)+'</div></details>'
    if area=='tyres':
        extra='''<div class="lab" id="tyreLab"><div><div class="eyebrow">T08-LIVE · oma CC0-synteesi</div><h3>Pidon rajalla</h3><p>Sama malli kuin T02–T06. Säädä ääntä vapaasti: kuorma muuttaa energiaa, pinta sävelen ja kohinan suhdetta. Ei fysiikkasimulaatio.</p><button class="live-button" id="liveStart">Käynnistä rengasmalli</button><div class="presets"><button class="small-button" data-preset="grip">Pito</button><button class="small-button" data-preset="light">Kevyt</button><button class="small-button" data-preset="drift">Drifti</button><button class="small-button" data-preset="gravel">Sora</button></div><label class="pick" style="margin-top:18px"><input type="checkbox" data-pick="t08-live"> Valitse tämä malli · arvio 7/10</label></div><div class="controls"><label>Luisto <span id="slipValue"></span><input id="slip" type="range" min="0" max="100" value="55"></label><label>Suhteellinen kuorma <span id="loadValue"></span><input id="load" type="range" min="0" max="180" value="100"></label><label>Nopeus <span id="speedValue"></span><input id="speed" type="range" min="0" max="140" value="55"></label><label>Pinta<select id="surface"><option value="0">Kuiva asfaltti</option><option value="1">Märkä pinta</option><option value="2">Sora</option></select></label></div></div>'''
    sections.append(f'''<section id="{area}"><div class="section-head"><div class="section-title"><span>{number}</span><h2>{title}</h2></div><p>{lead}</p></div><div class="cards">{cards}</div>{extra}<details class="research"><summary>Miksi nykyinen kuulostaa keinotekoiselta? Tutkimus, mittaukset ja peliehdotus</summary><div class="research-body">{research}<p class="chart-note">Mittaus toimitetuista Opus-tiedostoista, 48 kHz dekoodaus. Aktiivisuus: 10 ms RMS-ikkuna yli 10 % näytteen suurimmasta RMS-arvosta. Spektrin painopiste ei ole perustaajuus. Tasoja on tasattu, joten kuva ei mittaa äänenvoimakkuutta luonnossa.</p></div></details></section>''')

size=sum(p.stat().st_size for p in (BOARD/'audio').glob('*'))/1e6
count=sum(len(v)-sum(row[0].startswith('current-') for row in v) for v in CANDIDATES.values())+1
source_html=''
for key in ['herring','common','beach','tyre1','tyre3']:
    name,url,lic=SOURCES[key];source_html+=f'<p><a href="{url}" target="_blank" rel="noopener">{name}</a><br>{lic}. Muutokset: leikkaus, suodatus ja tason tasaus; harmaalokin lähteestä lisäksi tilallinen koostaminen.</p>'

html='''<!doctype html><html lang="fi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><meta name="description" content="Astra-äänilaboratorio: lokit, renkaat ja musiikki Turku3D:lle. Kuuntele, vertaa ja valitse."><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline' blob:; worker-src blob:; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'"><title>Turku3D · Astra-äänilaboratorio</title><style>__CSS__</style></head><body>
<div class="wrap"><header class="mast"><div class="wordmark">TURKU3D / ASTRA</div><div class="edition">ÄÄNILABORATORIO 01<br>10.11.2026 · ITSENÄINEN EHDOTUS</div></header>
<div class="hero"><div><div class="eyebrow">Lokit / renkaat / musiikki</div><h1>Kymmenen kertaa<br><em>elävämpi Turku.</em></h1><p class="intro">Mikä tekisi äänistä parempia? Kuuntele nykyinen peli, kokeile uusia suuntia ja poimi parhaat. Tämä on erillinen kuuntelupöytä — valinnat eivät muuta peliä.</p></div><div class="hero-art" aria-hidden="true"><svg viewBox="0 0 460 270"><defs><linearGradient id="wave"><stop stop-color="#d4ef86"/><stop offset="1" stop-color="#e7a578"/></linearGradient></defs>__WAVES__</svg><small>01 LOKKI · 02 KOSKETUS · 03 RYTMI</small></div></div>
<div class="stats"><div><strong>__COUNT__</strong>uutta vaihtoehtoa</div><div><strong>4</strong>omaa sävellystä</div><div><strong>__SIZE__ Mt</strong>ääntä · Opus</div></div>
<nav class="nav" aria-label="Ääniryhmät"><a href="#gulls"><span>01</span>Lokit</a><a href="#tyres"><span>02</span>Renkaat</a><a href="#music"><span>03</span>Musiikki</a><a href="#recommendations">Suositus</a><div class="push"></div><label class="volume">Taso <input id="volume" type="range" min="0" max="100" value="55" aria-label="Äänenvoimakkuus"><span id="volumeLabel">55 %</span></label><button id="stop" class="small-button">Pysäytä kaikki</button></nav>
<p class="instructions"><strong>Aloita nykyisestä, vaihda ehdokkaaseen.</strong> Vain yksi ääni soi kerrallaan. Silmukat jatkuvat, kunnes pysäytät ne; ”Kuuntele sauma” aloittaa juuri ennen rajaa. Musiikki on tasattu toistossa −24 LUFS -tasolle, tehosteet aktiivisen RMS-tason mukaan. Luisto-, kuorma- ja etäisyysdemoissa tarkoituksellinen dynamiikka säilyy. Arviot ovat Astran alustavia suunnitteluarvioita, eivät kuuntelupaneelin pisteitä.</p><div id="status" class="status" role="status" aria-live="polite">Valmis kuunteluun.</div>
<div id="copyFallback" hidden><label>Valitut tunnisteet<textarea readonly class="copy-area" aria-label="Kopioitavat tunnisteet"></textarea></label></div>
__SECTIONS__
<section id="recommendations"><div class="eyebrow">Mitä veisin peliin seuraavaksi</div><h2>Kolme erilaista ratkaisua.</h2><div class="recommend"><div><h3>1 / Lokit: näytepankki</h3><p><strong>Ensiksi G02 + G05.</strong> Suurin varma laadunlisä tulee oikeista äänistä. Lisää yksilöitä ja erillinen kalalokkipankki, sitten joen, sataman ja meren alueet. Synteesi jää erikoistilanteiden tutkimukseksi.</p></div><div><h3>2 / Renkaat: yhdistelmä</h3><p><strong>Ensiksi T01 + T08.</strong> Lyhyt nauhoitettu kirskahdus tuo tunnistettavan alun. Parametrinen jatkuva ääni pitää pitkän driftin elävänä. Kalibroi kuorma ja pintamiksaukset oikeilla ajotallenteilla.</p></div><div><h3>3 / Musiikki: sävellys ensin</h3><p><strong>M01 vapaaseen ajoon, M02 valikkoon, M03 toimintaan.</strong> M04 on toinen radioväri. Vie valmiit kappaleet radioon; tee tilanteeseen reagoivasta musiikista tahdistetut kerrokset. Lisää sävellyksiä ennen uusia satunnaissääntöjä.</p></div></div>
<div class="explain-box"><p class="fine muted">Toteutusarvio hyväksytyn suunnan jälkeen: lokit noin 1–2 työpäivää näytebankin ja alueohjauksen rakentamiseen; renkaat 2–4 päivää pyöräkohtaisen telemetrian, nopeiden tapahtumien ja kuuntelusäädön yhdistämiseen; musiikki 2–3 päivää kerrostoistimeen ja tilasiirtymiin. Arviot eivät sisällä uusia kenttä-äänityksiä tai lopullista musiikkituotantoa. Pelin lähdekoodia tai julkaisua ei muutettu.</p></div>
<details class="research"><summary>Lähteet, lisenssit ja tarkistuksen rajat</summary><div class="research-body"><div class="license-list">__SOURCES__</div><p>Erityinen nimeäminen: <strong>”lakeside birds 1.wav” — jus, Freesound, CC-BY 4.0</strong>. <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">Lisenssi</a>. G03 on muokattu versio. Muiden nauhoitteiden lisenssi on CC0. Freesoundista käytettiin julkisia MP3-esikuuntelutiedostoja ilman tiliä; uusia äänityksiä ei väitetä tehdyiksi Turussa.</p><p>Oma uusi koodi, synteesi ja sävellykset: <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">CC0 1.0</a>. Nykyisen pelin vertailut on tehty tämän työhaaran koodista, eristettyinä muusta pelimiksauksesta. Äänitteiden lähdesivut ja SHA-256-tunnisteet on tallennettu. Mallipainoja tai ladattuja äänitteitä ei ole gitissä.</p><p><a href="sources.json">Lähdemanifesti</a> · <a href="catalogue.json">Ääniluettelo ja tiedostotunnisteet</a> · <a href="measurements.json">Kaikki mittaukset</a> · <a href="source/README.txt">Toistettavat työvaiheet</a></p><p>Kaikki toimitetut äänet dekoodataan tarkistuksessa. Uusien silmukoiden rajahyppy tarkistetaan näytteen sisäisiin muutoksiin nähden ja ”sauma”-painike auttaa kuuntelemaan sen. Se ei korvaa Jonniin kuuntelua: biologinen lajivarmennus, rengasmallin fyysinen kalibrointi ja lopullinen musiikkimaku jäävät avoimiksi. Sivulla ei ole ulkoisia resursseja, analytiikkaa eikä automaattista toistoa. Lähdelinkit avautuvat vain niitä painettaessa.</p></div></details></section>
<footer>ASTRA / TURKU3D · __STAMP__ UTC · Riippumaton vertailu, ei muutoksia peliin.<br>Valinnat säilyvät tässä selaimessa. Tunnisteet pysyvät samoina seuraavissa julkaisuissa.</footer></div>
<aside class="selection-bar" aria-label="Omat valinnat"><div class="wrap selection-inner"><div><strong id="selectedCount">0 valittua</strong><small id="selectionHint">Valitse kiinnostavat äänet.</small></div><button id="showSelected" class="small-button" aria-pressed="false">Näytä valitut</button><button id="copy" class="copy">Kopioi valinnat</button></div></aside>
<script>const CATALOGUE=__CATALOGUE__;const TYRE_CORE=__TYRECORE__;__JS__</script></body></html>'''
waves=''
for i in range(3):
    points=[]
    for x in range(460):
        env=np.exp(-((x-(150+i*50))/95)**2)
        y=60+i*65+env*(np.sin(x*.15+i)*19+np.sin(x*.31)*8)
        points.append(f'{x},{y:.2f}')
    waves+=f'<polyline points="{" ".join(points)}" fill="none" stroke="url(#wave)" stroke-width="1.3" opacity="{1-i*.2}"/>'
replacements={'__CSS__':(HERE/'board.css').read_text(),'__JS__':(HERE/'board.js').read_text(),'__TYRECORE__':json.dumps((HERE/'tyre-core.js').read_text()),'__CATALOGUE__':json.dumps(catalogue,ensure_ascii=False),
              '__SECTIONS__':''.join(sections),'__SOURCES__':source_html,'__COUNT__':str(count),'__SIZE__':f'{size:.1f}'.replace('.',','),'__WAVES__':waves,'__STAMP__':datetime.datetime.now(datetime.timezone.utc).strftime('%d.%m.%Y %H:%M')}
for a,b in replacements.items():html=html.replace(a,b)
(BOARD/'index.html').write_text(html)
(BOARD/'catalogue.json').write_text(json.dumps(catalogue,ensure_ascii=False,indent=2))
shutil.copyfile(WORK/'inputs/sources.json',BOARD/'sources.json')
source_dir=BOARD/'source';source_dir.mkdir(exist_ok=True)
for src in HERE.iterdir():
    if src.suffix in ['.py','.js','.mjs','.css','.txt']:shutil.copyfile(src,source_dir/src.name)
for src,name in [(ROOT/'game/scripts/prepare-audio.mjs','prepare-audio.mjs'),(ROOT/'game/src/audio/music/index.ts','music-index.ts'),(ROOT/'game/src/audio/engine/chassis.ts','chassis.ts')]:shutil.copyfile(src,source_dir/name)
print(f'Built {len(catalogue)} audio cards + live synth, {size:.2f} MB audio, {len(html)} HTML characters')
