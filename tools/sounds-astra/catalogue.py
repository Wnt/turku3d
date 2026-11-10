"""Finnish listener copy; stable candidate IDs survive republishes."""
SOURCES={
 'herring':('Canardo55 · Herring Gull 1.wav','https://freesound.org/people/Canardo55/sounds/538016/','CC0-1.0'),
 'common':('jus · lakeside birds 1.wav','https://freesound.org/people/jus/sounds/73616/','CC-BY-4.0'),
 'beach':('squashy555 · Seagull on beach','https://freesound.org/people/squashy555/sounds/353416/','CC0-1.0'),
 'tyre1':('Dorian CLAIR · Screeching Tires #1','https://bigsoundbank.com/crissement-de-pneus-1-s2368.html','CC0-1.0'),
 'tyre3':('Dorian CLAIR · Screeching Tires #3','https://bigsoundbank.com/crissement-de-pneus-3-s2370.html','CC0-1.0'),
 'synth':('Astra · oma synteesi','source/generate-effects.py','CC0-1.0'),
 'tyresynth':('Astra · oma parametrinen malli','source/tyre-core.js','CC0-1.0'),
 'compose':('Astra · oma sävellys ja synteesi','source/compose.py','CC0-1.0'),
 'game':('Turku3D · nykyinen äänipankki','source/prepare-audio.mjs','CC0-1.0'),
 'gamemusic':('Turku3D · nykyinen sekvensseri','source/music-index.ts','Projektin oma vertailuäänite'),
 'gametruck':('Turku3D · ChassisSynth','source/chassis.ts','CC0-1.0'),
}
# id, Finnish title, method/intent, source key, provisional rating, highlighted
CANDIDATES={
 'gulls':[
 ('current-gulls','Nykyinen · lokkisilmukka','Kaksi laskettavaa siniaaltoa, 7,9 sekunnin toisto. Alkuperäinen pelitiedosto; kuunteluvoimakkuutta tasattu.','game',2,False),
 ('g01-herring-single','Harmaalokki · yksittäinen huuto','Lähiäänitteen leikkaus, kevyt ylipäästösuodatus ja reunahäivytys. Laji äänittäjän ilmoittama; Alankomaat.','herring',8,True),
 ('g02-herring-series','Harmaalokki · naurava sarja','Yksi ehjä 4,5 s kutsujakso. Säilytetty hengitys, karheus ja epätasainen rytmi; ei koneellista tavutoistoa.','herring',9,True),
 ('g03-common-lakeside','Kalalokki · järvenranta','23,7 s leikkaus kenttä-äänitteestä. Lähteen mukaan myös talitiainen ja kalatiiroja: ei puhdas yhden lajin näyte.','common',7,False),
 ('g04-herring-beach','Harmaalokki · rannan vastaus','Toinen yksilö ja ympäristö. Rajaus, ylipäästö ja reunahäivytys; lähteen lajimerkintä, ei Turusta.','beach',7,False),
 ('g05-harbour','Satama · tilaa ja vastauksia','60 s koostettu stereosilmukka: harmaalokin näytteet, oma hiljainen vesikohina ja lyhyet heijastukset. Ei paikan kenttä-äänite.','herring',8,True),
 ('g06-alarm-model','Hälytys · kek-kek-malli','Oma lähde–suodin-synteesi: 330–390 Hz perustaajuus ja neljä resonanssialuetta. Biologinen luonnos, ei lajivarmennettu.','synth',5,False),
 ('g07-mew-model','Mew · synteesiluonnos','Oma liukuva harmoninen ääni, hengityskohina ja epäsymmetrinen vaippa. Tarkoitus on tutkia ohjattavuutta.','synth',5,False),
 ('g08-distance','Sama lintu · lähellä, keskellä, kaukana','Kolme esimerkkiä noin 0,3 / 6 / 11,7 s kohdalla. Taso ja kirkkaus laskevat; heijastukset jäävät lyhyiksi.','herring',8,False),
 ],
 'tyres':[
 ('current-screech','Nykyinen · auton vinkuna','1 400 Hz kantoaalto, 33 Hz vaihemodulaatio ja valkoinen kohina. Alkuperäinen 3,9 s pelisilmukka.','game',3,False),
 ('current-truck','Nykyinen · trophy truck','Pelistä suoraan renderöity rengaskerros: 1 050 ± 40 Hz sekä kaistanpäästökohina. Vakio-ohjaus 0,8; moottori pois.','gametruck',4,False),
 ('current-roll','Nykyinen · vierintä','Alkuperäinen suodatettu kohinasilmukka. Tämä soi pelissä vinkunan alla nopeuden mukaan.','game',3,False),
 ('t01-recorded-chirp','Äänitetty · pidon irtoaminen','Aito renkaiden kirskahdus, kevyt 230 Hz ylipäästö ja reunahäivytys. Hyvä lähtökohta lyhyiden tapahtumien pankkiin.','tyre1',8,True),
 ('t07-recorded-skid','Äänitetty · toinen luisto','Toinen ulkona äänitetty rengastapahtuma. Säilytetty epätasainen hyökkäys ja luonnollinen lopetus.','tyre3',8,False),
 ('t02-chirp','Synteesi · nopea kirskahdus','Luisto nousee ja laskee 0,33 sekunnissa. Sama malli kuin alla; ääni reagoi ilman pitkää silmukkaa.','tyresynth',6,False),
 ('t03-drift-light','Synteesi · kevyt jatkuva luisto','11 s saumaton silmukka: neljä värähtelymoodia, epäsäännöllinen huojunta ja erillinen kohinakerros.','tyresynth',6,False),
 ('t04-drift-heavy','Synteesi · raskas jatkuva luisto','11 s saumaton silmukka. Enemmän karheutta ja energiaa; sama vahvistus kuin kevyessä versiossa.','tyresynth',7,True),
 ('t05-slip-load-sweep','Synteesi · luisto ja kuorma','0–12 s: luisto 0 → 100 → 0 %. 12–24 s: kuorma 0,2 → 1,8 → 0,2, luisto 55 %. Nopeus 55 km/h.','tyresynth',7,False),
 ('t06-surface-sweep','Synteesi · kolme pintaa','0–8 s kuiva asfaltti, 8–16 s märkä pinta, 16–24 s sora. Sama liike; sävelen ja kohinan suhde vaihtuu.','tyresynth',6,False),
 ],
 'music':[
 ('current-music-aura-neon','Nykyinen · Aura Neon','Nykyisen Web Audio -koodin 16 tahtia, vapaa ajo, ei auton vaimennusta. Kuunteluvoimakkuus tasattu.','gamemusic',3,False),
 ('m01-river-afterhours','Aurajoen jälkeen','96 BPM · yöajo / downtempo. Omat sähköpianot, sointukäännökset, synkopoitu basso, A/B-osat ja hengittävä tauko.','compose',7,True),
 ('m02-harbour-light','Sataman valo','72 BPM · valikko / rauhallinen kävely. Harvat lasimaiset sävelet ja hitaat sointukentät; 32 tahdin ympyrä.','compose',7,True),
 ('m03-ringroad-pursuit','Kehätie','124 BPM · kierros / takaa-ajo. Basso ja rummut kantavat, melodia käy välillä etualalla. 64 tahtia, neljä intensiteettijaksoa.','compose',7,True),
 ('m04-market-day','Toripäivä','108 BPM · päiväajo / toinen radiokanava. Oma kielisynteesi, kevyt funk-rytmi ja sähköpianon vastaukset.','compose',6,False),
 ],
}
EXTRA_BASELINES=[
 ('current-music-market-sun','Nykyinen · Market Sun','102 BPM · vapaa ajo'),
 ('current-music-river-dub','Nykyinen · River Dub','92 BPM · vapaa ajo'),
 ('current-music-ring-road','Nykyinen · Ring Road','120 BPM · vapaa ajo'),
 ('current-music-harbour-glass','Nykyinen · Harbour Glass','84 BPM · vapaa ajo'),
 ('current-music-mission','Nykyinen · tehtäväversio','Aura Neon · kiireen sointikerrokset, deadline 0,85; tässä vertailussa vakio 108 BPM'),
]
