**Uutta 1.5.0:** tarkempi videoanalyysi. Laatu mitataan 16 kohdasta koko videon matkalta, ja säädöt sovitetaan niiden mediaaniin. Piirretyt tunnistetaan automaattisesti. Esikatselun kolmas kohta on videon vaikein kohta, ja ohjelma kertoo, jos laatu vaihtelee kohtauksittain. Esikatselukohtia voi siirtää aikajanalta mihin tahansa kohtaukseen.
**New in 1.5.0:** more thorough video analysis. Quality is measured at 16 points across the whole video and the settings are tuned to their median. Animation is detected automatically. The third preview point is the hardest spot in the video, and the program tells you if quality varies between scenes. Preview points can be moved anywhere on the timeline.

**Korjattu 1.4.1:** esikatselussa saattoi näkyä toisen kuvan säädetty versio, jos tiedostoa vaihtoi ennen kuin esikatselu valmistui. Päivityksen jälkeen vanhentuneet esikatselut lasketaan uudelleen vasta, kun tiedoston avaa.
**Fixed in 1.4.1:** the preview could show another image's adjusted version when switching files before the preview was ready. After an update, outdated previews are recalculated only when the file is opened.

**Uutta 1.4.0:** videoille uusi asetus "Terävin – kokeellinen": monikuvamalli (RealBasicVSR) hakee tarkkuutta naapurikuvista, joten taustat ja tekstuurit tarkentuvat selvästi. Kohtausten vaihdot tunnistetaan, ja esikatselu laskee naapurikuvat mukaan. Nopeasti liikkuvissa kasvoissa voi näkyä repeämiä, ja käsittely on hyvin hidasta – tavallinen asetus on edelleen suositus.
**New in 1.4.0:** a new video preset "Sharpest – experimental": a multi-frame model (RealBasicVSR) gathers detail from neighboring frames, making backgrounds and textures clearly sharper. Scene changes are detected and the preview includes neighboring frames. Fast-moving faces may show tearing and processing is very slow – the regular presets remain the recommendation.

**Uutta 1.3.0:** vanhojen valokuvien korjaus: naarmujen, taitteiden ja pölyn poisto sekä mustavalkoisten ja seepiakuvien väritys tekoälyllä (kirkkaus ja yksityiskohdat säilyvät alkuperäisinä). Ohjelma huomaa mustavalkoisen kuvan ja ehdottaa väritystä.
**New in 1.3.0:** old photo restoration: removing scratches, creases and dust, and colorizing black-and-white and sepia photos with AI (brightness and detail stay as in the original). The program notices black-and-white photos and suggests colorizing them.

**Uutta 1.2.0:** kohinanpoisto ja pakkausjälkien siivous, lähteen laatuun sovitetut oletussäädöt (luonnollinen, ei muovinen tulos), kuvien kasvojen palautus (valinnainen), iPhonen HEIC-kuvat, kokonaisen kansion lisäys ja asetukset kerralla kaikille, kuvien kierto, 1×-parannus ja tallennusmuoto, videoiden ääni- ja tekstitysraitojen valinta sekä pienemmän tiedoston vaihtoehto.
**New in 1.2.0:** noise and compression artifact removal, default settings tuned to each source's quality (natural, not plastic), optional face restoration for photos, iPhone HEIC photos, adding whole folders and applying settings to all, image rotation, 1× enhancement and output format, choosing video audio and subtitle tracks, and a smaller-file option.

**Uutta 1.1.0:** kuvien parannus (JPEG, PNG, WebP, BMP, TIFF) samalla tekoälyllä kuin videot: 2×, 4× tai näytön kokoon, EXIF-tiedot, väriprofiili ja läpinäkyvyys säilyvät. Kuvat käsitellään videon rinnalla, joten ne eivät jää odottamaan pitkää elokuvaa. Uusi, selkeämpi kuvake.
**New in 1.1.0:** image enhancement (JPEG, PNG, WebP, BMP, TIFF) with the same AI as videos: 2×, 4× or fit to screen, keeping EXIF data, color profile and transparency. Images are processed alongside videos, so they never wait for a long movie. New, clearer icon.

**Lataa / Download:** `PixeliRemaster-*-Setup.exe` (alla / below)

Vaatimukset: Windows 10/11, NVIDIA GeForce- tai RTX-näytönohjain. Ensimmäisellä käynnistyksellä ladataan noin 3 Gt tekoälykomponentteja.
Requirements: Windows 10/11, NVIDIA GeForce or RTX graphics card. About 3 GB of AI components are downloaded on first launch.

Windows saattaa varoittaa allekirjoittamattomasta ohjelmasta: valitse **Lisätietoja → Suorita silti**.
Windows may warn about an unsigned program: choose **More info → Run anyway**.
