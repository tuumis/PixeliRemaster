# Kolmansien osapuolten osat / Third-party components

## AI-mallit (app/mallit) / AI models

| Malli / Model | Tekijä / Author | Lisenssi / License | Lähde / Source |
|---|---|---|---|
| 2xLiveActionV1_SPAN_490000.pth | jcj83429 | Apache License 2.0 | https://github.com/jcj83429/upscaling |
| 2xVHS2HD-RealPLKSR.pth | asterixcool (Starinspace) | CC BY-SA 4.0 | https://openmodeldb.info/models/2x-VHS2HD |
| 2x_AniSD_AC_G6i2b_SPAN_190K.pth | Sirosky | CC BY-NC 4.0 (ei kaupalliseen käyttöön / non-commercial) | https://github.com/Sirosky/Upscale-Hub |

Malleja ei ole muokattu. / The models are unmodified.

Mallien arkkitehtuurit (SPAN, RealPLKSR) on toteutettu uudelleen tiedostossa `app/pixeli/kuva.py` alkuperäisten julkaisujen ja spandrel-kirjaston (MIT) perusteella, ja toteutus on tarkistettu mallien ONNX-vientejä vastaan.

## Algoritmit / Algorithms

- DeHalo_alpha – Didée (AviSynth/VapourSynth-yhteisö), toteutettu uudelleen.

## Ohjelmistot / Software

| Osa / Component | Lisenssi / License | Huom. / Note |
|---|---|---|
| Python 3.12 (embeddable) | PSF License | mukana asennusohjelmassa / bundled |
| NumPy | BSD-3-Clause | mukana / bundled |
| pywebview, pythonnet | BSD-3-Clause, MIT | mukana / bundled |
| PyTorch | BSD-3-Clause | ladataan ensimmäisellä käynnistyksellä / downloaded on first launch |
| FFmpeg (BtbN builds) | GPL | ladataan ensimmäisellä käynnistyksellä / downloaded on first launch; https://github.com/BtbN/FFmpeg-Builds |
| Barlow-kirjasin / typeface | SIL Open Font License 1.1 | `app/web/fontit/OFL.txt` |
