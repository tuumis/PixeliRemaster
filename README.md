# PixeliRemaster

**AI-videoskaalain DVD-elokuville ja vanhoille videoille** · *AI video upscaler for DVDs and old videos* ([English below](#english))

PixeliRemaster parantaa DVD-elokuvien, TV-tallenteiden ja vanhojen videotiedostojen kuvaa tekoälyllä ja skaalaa ne näyttösi tarkkuuteen kuvasuhdetta vääristämättä. Valmis tiedosto toimii missä tahansa soittimessa.

## Lataa

Uusin asennusohjelma: **[Releases](https://github.com/tuumis/PixeliRemaster/releases/latest)** → `PixeliRemaster-x.y.z-Setup.exe`

Vaatimukset:
- Windows 10 tai 11 (64-bit)
- NVIDIA GeForce- tai RTX-näytönohjain (suositus RTX 2060 tai uudempi) ja ajantasainen ajuri
- Noin 8 Gt vapaata levytilaa; ensimmäisellä käynnistyksellä ladataan noin 3 Gt tekoälykomponentteja

Asennusohjelmaa ei ole allekirjoitettu maksullisella varmenteella, joten Windows saattaa näyttää varoituksen "Windows suojasi tietokoneesi". Valitse **Lisätietoja → Suorita silti**.

## Käyttö

1. Vedä videotiedostot ikkunaan tai valitse **Lisää videoita**. DVD-levyn saat tiedostoksi esimerkiksi [MakeMKV](https://www.makemkv.com/)-ohjelmalla.
2. Ohjelma analysoi videon (kuvanopeus, lomitus, mustat palkit, kuvasuhde) ja suosittelee asetusta.
3. Vertaa asetuksia esikatselussa: vedä jakajaa tai paina **välilyöntiä** vaihtaaksesi alkuperäisen ja paranneltun välillä. **Katso liikkeessä** tekee lyhyen videopätkän.
4. Valitse **Käsittele**. Käsittelyn voi keskeyttää ja jatkaa myöhemmin samasta kohdasta.

Valmis tiedosto `Nimi [Remaster].mkv` tallentuu alkuperäisen viereen. Mukana ovat kaikki ääniraidat, tekstitykset ja kappaleet sekä videon vieressä oleva `.srt`-tekstitys.

### Asetukset

| Asetus | Käyttö |
|---|---|
| Alkuperäinen | Tavallinen skaalaus ilman tekoälyä (vertailukohta) |
| DVD-elokuva | Hyvälaatuiset DVD-levyt |
| Heikko lähde | Netistä ladatut, AVI-tiedostot, TV-tallenteet |
| VHS / pehmeä | Hyvin heikot ja haamuiset lähteet (hidas) |
| Piirretty | Animaatio |

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
  pixeli/moottori.py  analyysi, purku/pakkaus ffmpegillä, käsittely osissa
  pixeli/tyojono.py   jono ja taustasäikeet
  pixeli/asennus.py   ensimmäisen käynnistyksen asennus
  pixeli/palvelin.py  paikallinen HTTP-rajapinta käyttöliittymälle
  pixeli/ikkuna.py    pywebview-ikkuna (varalla Edgen sovellustila)
  web/                käyttöliittymä (HTML/CSS/JS, suomi + englanti)
  mallit/             AI-mallit
installer/            asennusohjelman rakennus (Inno Setup) ja savutesti
```

Kehitysajo ilman näytönohjainta: `PIXELI_LAITE=numpy PIXELI_IKKUNA=selain python app/kaynnista.py` (hidas, vain testaukseen).

Uusi versio: päivitä `VERSIO` tiedostossa `app/pixeli/moottori.py` ja lisää tagi, esim. `git tag v1.0.1 && git push --tags`. GitHub Actions rakentaa asennusohjelman ja julkaisee sen.

## Lisenssit

Ohjelmakoodi: MIT (ks. [LICENSE](LICENSE)). Mukana olevat AI-mallit ja muut osat on lueteltu tiedostossa [KOLMANNET_OSAPUOLET.md](KOLMANNET_OSAPUOLET.md). Piirrettyjen malli (AniSD) on lisensoitu vain ei-kaupalliseen käyttöön.

---

## English

PixeliRemaster improves DVD movies, TV recordings and old video files with AI and scales them to your screen's resolution without distorting the aspect ratio.

**Download:** [Releases](https://github.com/tuumis/PixeliRemaster/releases/latest) → `PixeliRemaster-x.y.z-Setup.exe`

**Requirements:** Windows 10/11 64-bit, an NVIDIA GeForce/RTX graphics card with a current driver, about 8 GB free disk space (about 3 GB of AI components are downloaded on first launch). The installer is not code-signed, so Windows may show a SmartScreen warning: choose **More info → Run anyway**.

**Usage:** drag videos into the window, compare presets in the preview (drag the divider or press **space**), then choose **Process**. Processing can be paused and resumed. The result `Name [Remaster].mkv` is saved next to the original with all audio tracks, subtitles and chapters.

**License:** code MIT; bundled AI models have their own licenses, see [KOLMANNET_OSAPUOLET.md](KOLMANNET_OSAPUOLET.md). The animation model (AniSD) is licensed for non-commercial use only.
