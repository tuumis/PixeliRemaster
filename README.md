# PixeliRemaster

**AI-skaalain DVD-elokuville, vanhoille videoille ja kuville** · *AI upscaler for DVDs, old videos and images* ([English below](#english))

PixeliRemaster parantaa DVD-elokuvien, TV-tallenteiden, vanhojen videotiedostojen ja kuvien laatua tekoälyllä ja suurentaa ne kuvasuhdetta vääristämättä. Valmis video toimii missä tahansa soittimessa ja valmis kuva missä tahansa kuvankatselussa.

## Lataa

Uusin asennusohjelma: **[Releases](https://github.com/tuumis/PixeliRemaster/releases/latest)** → `PixeliRemaster-x.y.z-Setup.exe`

Vaatimukset:
- Windows 10 tai 11 (64-bit)
- NVIDIA GeForce- tai RTX-näytönohjain (suositus RTX 2060 tai uudempi) ja ajantasainen ajuri
- Noin 8 Gt vapaata levytilaa; ensimmäisellä käynnistyksellä ladataan noin 3 Gt tekoälykomponentteja

Asennusohjelmaa ei ole allekirjoitettu maksullisella varmenteella, joten Windows saattaa näyttää varoituksen "Windows suojasi tietokoneesi". Valitse **Lisätietoja → Suorita silti**.

## Käyttö

1. Vedä video- tai kuvatiedostot ikkunaan tai valitse **Lisää tiedostoja**. DVD-levyn saat tiedostoksi esimerkiksi [MakeMKV](https://www.makemkv.com/)-ohjelmalla.
2. Ohjelma analysoi videon (kuvanopeus, lomitus, mustat palkit, kuvasuhde), mittaa laadun 16 kohdasta, tunnistaa piirretyt ja suosittelee asetusta. Esikatselun kohtia voi siirtää aikajanalta; kolmas kohta on videon vaikein kohta.
3. Vertaa asetuksia esikatselussa: vedä jakajaa tai paina **välilyöntiä** vaihtaaksesi alkuperäisen ja paranneltun välillä. **Katso liikkeessä** tekee lyhyen videopätkän.
4. Valitse **Käsittele**. Käsittelyn voi keskeyttää ja jatkaa myöhemmin samasta kohdasta.

Valmis tiedosto `Nimi [Remaster].mkv` tallentuu alkuperäisen viereen. Mukana ovat kaikki ääniraidat, tekstitykset ja kappaleet sekä videon vieressä oleva `.srt`-tekstitys; halutessasi voit valita raidat ja pienemmän tiedostokoon.

### Suositellut säädöt

Esikatselun yhteydessä ohjelma mittaa lähteen kohinan, pakkausjäljet ja terävyyden ja sovittaa jokaisen asetuksen hienosäädöt niihin: pakkausjälkiä siivotaan maltillisesti (kohinanpoisto, DRUNet), hyvin pehmeissä lähteissä tekoälyn osuutta pienennetään ja filmiraetta lisätään sen verran, ettei pinta muutu muoviseksi. Säätöjä voi muuttaa, ja **Käytä näitä asetuksia myös muihin** kopioi ne kaikkiin listan tiedostoihin.

### Kuvat

JPEG-, PNG-, HEIC- (iPhone), WebP-, BMP- ja TIFF-kuvat käsitellään samalla tekoälyllä kuin videot. Valitse koko: **1×** (vain parannus), **2×**, **4×** (tekoäly ajetaan kahdesti) tai **Näytön kokoon**. Kuvan voi kääntää ja tallennusmuodon valita. Isoista kuvista esikatselu näyttää kolme kohtaa täydessä koossa.

**Vanhat valokuvat:** naarmujen, taitteiden ja pölyn poisto (tunnistus + täyttö tekoälyllä), mustavalkoisen tai seepiakuvan väritys (kirkkaus ja yksityiskohdat säilyvät alkuperäisinä) ja kasvojen palautus. Mallit ladataan ensimmäisellä käytöllä.

- JPEG-kuvasta tulee `Nimi [Remaster].jpg`, muista `Nimi [Remaster].png`.
- Puhelinkuvien suunta korjataan, ja EXIF-tiedot (esim. kuvausaika ja kamera) sekä väriprofiili säilyvät. PNG-kuvien läpinäkyvyys säilyy.
- Kuvat käsitellään videon rinnalla, joten ne eivät jää odottamaan pitkää elokuvaa.
- Suurin koko on 64 megapikseliä.

### Asetukset

| Video | Kuva | Käyttö |
|---|---|---|
| Alkuperäinen | Alkuperäinen | Tavallinen skaalaus ilman tekoälyä (vertailukohta) |
| DVD-elokuva | Valokuva | Hyvälaatuiset DVD-levyt ja valokuvat |
| Heikko lähde | Pakattu kuva | Netistä ladatut, AVI-tiedostot, TV-tallenteet, voimakkaasti pakatut kuvat |
| VHS / pehmeä | Pehmeä / vanha | Hyvin heikot, haamuiset ja epätarkat lähteet (hidas) |
| Piirretty | Piirros | Animaatio, piirrokset, sarjakuvat ja logot |
| Terävin – kokeellinen | – | Monikuvamalli (RealBasicVSR): taustat ja tekstuurit selvästi tarkemmiksi naapurikuvien avulla. Liikkuvissa kasvoissa voi näkyä repeämiä; hyvin hidas |

## Miten se toimii

- **Analyysi:** lomitettu video puretaan, NTSC-telesiini palautetaan, toistetut kuvat (esim. 24 → 30 kuvaa/s venytys) poistetaan ja mustat palkit rajataan. Anamorfinen DVD-kuva skaalataan oikeaan muotoon.
- **Tekoäly:** videolle koulutetut 2x-mallit (SPAN, RealPLKSR). Ennen mallia voidaan poistaa haloja (DeHalo_alpha), ja mallin osuutta sekä hienoa filmiraetta voi säätää.
- **Laatu:** 10-bittinen HEVC (NVENC), BT.709-värit, 4:2:0-värit oikein sijoitettuna.
- **Varmistus:** ensimmäisellä käynnistyksellä itsetesti laskee saman kuvan näytönohjaimella ja prosessorilla ja vertaa tuloksia. Mallien rakenteet on tarkistettu mallien tekijöiden ONNX-vientejä vastaan.

## Kehittäjille

```
app/
  kaynnista.py        käynnistys (yksi instanssi, ikkuna)
  pixeli/kuva.py      laskenta: mallit, halonpoisto, skaalaus, rae, YUV (PyTorch + numpy-referenssi)
  pixeli/moottori.py  videon analyysi, purku/pakkaus ffmpegillä, käsittely osissa
  pixeli/kuvat.py     kuvat: purku (EXIF-suunta), koko, esikatselu, JPEG/PNG-tallennus
  pixeli/tyojono.py   jono ja taustasäikeet
  pixeli/asennus.py   ensimmäisen käynnistyksen asennus
  pixeli/palvelin.py  paikallinen HTTP-rajapinta käyttöliittymälle
  pixeli/ikkuna.py    pywebview-ikkuna (varalla Edgen sovellustila)
  web/                käyttöliittymä (HTML/CSS/JS, suomi + englanti)
  mallit/             AI-mallit
installer/            asennusohjelman rakennus (Inno Setup), testit ja kuvakkeen piirto (kuvake.py)
```

Kehitysajo ilman näytönohjainta: `PIXELI_LAITE=numpy PIXELI_IKKUNA=selain python app/kaynnista.py` (hidas, vain testaukseen).

Uusi versio: päivitä `VERSIO` tiedostossa `app/pixeli/moottori.py` ja lisää tagi, esim. `git tag v1.0.1 && git push --tags`. GitHub Actions rakentaa asennusohjelman ja julkaisee sen.

## Lisenssit

Ohjelmakoodi: MIT (ks. [LICENSE](LICENSE)). Mukana olevat AI-mallit ja muut osat on lueteltu tiedostossa [KOLMANNET_OSAPUOLET.md](KOLMANNET_OSAPUOLET.md). Piirrettyjen malli (AniSD) on lisensoitu vain ei-kaupalliseen käyttöön.

---

## English

PixeliRemaster improves DVD movies, TV recordings, old video files and images with AI and enlarges them without distorting the aspect ratio.

**Download:** [Releases](https://github.com/tuumis/PixeliRemaster/releases/latest) → `PixeliRemaster-x.y.z-Setup.exe`

**Requirements:** Windows 10/11 64-bit, an NVIDIA GeForce/RTX graphics card with a current driver, about 8 GB free disk space (about 3 GB of AI components are downloaded on first launch). The installer is not code-signed, so Windows may show a SmartScreen warning: choose **More info → Run anyway**.

**Usage:** drag videos or images into the window, compare presets in the preview (drag the divider or press **space**), then choose **Process**. Video processing can be paused and resumed. The result `Name [Remaster].mkv` is saved next to the original with all audio tracks, subtitles and chapters. For old photos, the program can remove scratches and dust, colorize black-and-white photos and restore faces (models are downloaded on first use).

**Images:** JPEG, PNG, WebP, BMP and TIFF, enlarged 2×, 4× (the AI runs twice) or to fit the screen. JPEGs are saved as `Name [Remaster].jpg`, others as `.png`. Photo orientation is corrected, EXIF data and color profiles are kept, and PNG transparency is preserved. Images are processed alongside videos, up to 64 megapixels.

**License:** code MIT; bundled AI models have their own licenses, see [KOLMANNET_OSAPUOLET.md](KOLMANNET_OSAPUOLET.md). The animation model (AniSD) is licensed for non-commercial use only.
