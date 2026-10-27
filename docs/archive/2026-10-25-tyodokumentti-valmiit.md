# Arkisto: työdokumentin valmiit kohdat (25.–26.10.2026)

Siirretty jaetusta työdokumentista
[Turku 3D -peli: tilanne ja seuraavat askeleet](https://claude.ai/artifact/DvG8xm73Wf3cqY13Q2DL9c) 26.10.2026.
Dokumentissa jäljellä vain keskeneräiset asiat.

## Vaihe 0: työkalupohja (valmis 25.10.)

Tehty vielä Clauden workflow-agenteilla, putki palvelimella. Commitit b59566b (pohja, 414 tiedostoa) ja 759a198
(katselmoinnin korjaukset).

| Työlinja | Tulos | Testit |
| --- | --- | --- |
| Pohja: repo, config, specit | Valmis | 17 pytest-testiä |
| Datanhaku ja putken ympäristö | Toimistokorttelin data haettu | 33 palvelimella, 31 Macilla |
| Törmäysformaatti .col ja validoija | Valmis | 15 pytest + 22 node |
| Pelin runko Chromeen | WebGPU/WebGL2, Rapier workerissa, hahmo, auto, HUD | 18 vitest + Playwright |
| Codex-tekstuurit | Pilotti 2 julkisivulla | 29 |
| Astra-vertailu (QA) | Testattu tynkärenderöinnillä | 17 + 4 |
| Integraatio + katselmointi | `make tile` koko ketju ~75 s | kaikki vihreänä |

## Checklist A: vaihe 0 loppuun (valmis)

- [x] Integraatio: koko ketju tynkädatalla yhdellä komennolla, ensimmäinen commit
- [x] Kolme katselmoijaa ja korjaukset, toinen commit

## Checklist B: työkalut ja säännöt Codex-työskentelyyn (valmis)

- [x] Omat toteutukset Codex-tehtävien ajoon, visuaaliseen tuomariin ja kiintiön seurantaan (ideoina palvelimen
      codex-task.sh, codex-visual-judge.sh, codex-usage; ei kopioitu 1:1): `scripts/dev/codex-task`, `scripts/dev/watch`
- [x] AGENTS.md ainoa ohjetiedosto, CLAUDE.md tuo sen
- [x] `scripts/dev/wt`: agenttikohtainen worktree, data- ja välimuistihakemistot, portit, selainprofiili
- [x] Laatuportti `scripts/dev/gate` (ESLint, tsc, knip, ruff, shellcheck, kokobudjetti, generoitujen tiedostojen
      tarkistus) ja hookit
- [x] Vain muutokseen liittyvät testit, rinnakkaisajo; portti nopeutettu (fix-gate-speed: yhdistämisportit 2–12 s)
- [x] Lippurekisteri (≤ 5 lippua, poistoehto ≤ 10 yhdistettyä muutosta) ja kuolleen koodin siivous
- [x] Putki ajetaan Macilla, raakadata siirretty palvelimelta, 3 × 3 -työalue haettu
- [x] Agenttien koordinaatio: postilaatikko, ilmoitustaulu, `codex-task msg/inbox/ack/board`

## Wave 0 ja Wave 1: yhdistetyt Codex-tehtävät (25.–26.10.)

| Tehtävä | Sisältö | Yhdistetty |
| --- | --- | --- |
| W0-A | Agenttien eristys (wt), codex-task, here, AGENTS.md | 3431e92 |
| W0-B | Laatuportti, hookit, lippurekisteri | b3e34d8 |
| W0-C | Putki Macilla, 3 × 3 -alue | 7218b14 |
| W0-D | Postilaatikko ja ilmoitustaulu | badc89f |
| W1-06 | Syöte ja Xbox-ohjain, hahmo, portaat, foot IK | 9523b80 |
| W1-07 | Auto, reunakivitörmäys, CC0-auto | 2fd13b0 |
| W1-08 | Valaistus, varjot, jälkikäsittely, joki | 04037fc |
| W1-11 | 20 vertailukuvaa, Astra-arviointi, regressio | ea0509d |
| W1-04 | Ruutujen kokoaja, pakkaus, latauksen virheensieto | 2d75e2c |
| Erä 1 | W1-04b tekstuurisidonta, W1-03 rakennukset (81), W1-09 julkisivuputki, W1-01 maasto, auton haamukuvan korjaus | aaf46d1 |
| W1-03 korjaus | Rakennuskerros yhteisellä v1-manifestilla | 8c1f9a5 |
| fix-gate-speed | Portin reititys ja nopeutus, kuormankestävä stop | dfb9204 |
| W1-13 | Lidar-työkalut `turku3d.lidar`, reunakivien tunnistus | fe6cf3a |
| W1-15 | Katukalusteet ja liikennemerkit | b35347f |
| W1-14 | Puut (GPU-instansoitu kasvillisuus) | 1626ead |
| W2-02 | Alueajuri ja kustannusarvio | 215ade0 |
| W1-16 | Pysäköidyt autot | 8e940fc |
| fix-integration-1 | Tarkat ruudut latautuvat, piirtokutsut 3 468 → 355, 60 fps | 9d9eba5 |
| fix-integration-1 | Aloituspiste jalkakäytävälle, auto julkiselle paikalle | f53dcad |
| W1-18 | Paikkasidonnaiset äänet, CC0-äänipankki | 637bdfb |

## Päätökset ja opit (tiivistelmä)

- Reunakivet siirrettiin seuraavaan iteraatioon; ensin koko kortteli rakennuksineen pelattavaksi.
- Yli 30 min kestävät tehtävät arvioidaan uudelleen; ohuet, arvioitavat viipaleet.
- Testien pitää toimia kuormitetulla koneella; rinnakkaisuus rajataan agenttia kohden (`TURKU3D_JOBS`).
- Muisti, ei suoritin, oli pullonkaula: ~15 agenttia täytti 64 Gt RAMin.
- Sandboxien riippuvuuksien ajantasaisuus ja puuttuvat riippuvuusmäärittelyt kaatoivat useita yhdistämisiä;
  korjaus tool-load-robust-gate-tehtävässä.
- Codexin viikkokiintiö täyttyi ensimmäisenä iltana kerran (nollattu).
