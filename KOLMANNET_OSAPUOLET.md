# Kolmansien osapuolten osat / Third-party components

## AI-mallit (app/mallit) / AI models

| Malli / Model | Tekijä / Author | Lisenssi / License | Lähde / Source |
|---|---|---|---|
| 2xLiveActionV1_SPAN_490000.pth | jcj83429 | Apache License 2.0 | https://github.com/jcj83429/upscaling |
| 2xVHS2HD-RealPLKSR.pth | asterixcool (Starinspace) | CC BY-SA 4.0 | https://openmodeldb.info/models/2x-VHS2HD |
| 2x_AniSD_AC_G6i2b_SPAN_190K.pth | Sirosky | CC BY-NC 4.0 (ei kaupalliseen käyttöön / non-commercial) | https://github.com/Sirosky/Upscale-Hub |

| drunet_color.pth (kohinanpoisto / denoising) | Kai Zhang ym. (KAIR) | MIT | https://github.com/cszn/KAIR – ladataan ensimmäisellä käynnistyksellä / downloaded on first launch |

| GFPGANv1.4.pth (kasvojen palautus / face restoration) | Tencent ARC (GFPGAN) | Apache 2.0 (arkkitehtuuri: GFPGANv1Clean, ilman NVIDIA-lisenssin CUDA-osia / without the NVIDIA-licensed CUDA ops) | https://github.com/TencentARC/GFPGAN – ladataan ensimmäisellä käytöllä / downloaded on first use |
| detection_Resnet50_Final.pth (kasvojen tunnistus / face detection) | Xintao Wang (facexlib), RetinaFace | MIT | https://github.com/xinntao/facexlib – ladataan ensimmäisellä käytöllä / downloaded on first use |
| bopbtl_naarmut.pth (naarmujen tunnistus / scratch detection) | Microsoft (Bringing Old Photos Back to Life) | MIT | https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life – ladataan ensimmäisellä käytöllä / downloaded on first use |
| big_lama.pth (täyttö / inpainting) | Samsung AI Center (LaMa, Suvorov ym.) | Apache 2.0 | https://github.com/advimman/lama – ladataan ensimmäisellä käytöllä / downloaded on first use |
| realbasicvsr_ema.pth ("Terävin"-asetus / "Sharpest" preset) | OpenMMLab (RealBasicVSR, Chan ym.) | Apache 2.0 | https://github.com/open-mmlab/mmagic – ladataan asennuksessa / downloaded during setup |
| ddcolor_modelscope_fp16.pth (väritys / colorization) | DAMO Academy (DDColor, Kang ym.) | Apache 2.0 | https://github.com/piddnad/DDColor – ladataan ensimmäisellä käytöllä / downloaded on first use |

Malleja ei ole koulutettu uudelleen. Neljästä viimeisestä on julkaisuun poimittu pelkät verkon painot
(tarkistuspisteen optimoijan tila ja opetuksen apuverkot poistettu; DDColor puolitarkkuudella).
/ The models have not been retrained. For the last four, only the network weights were extracted
(optimizer state and training-only networks removed; DDColor in half precision).

Mallien arkkitehtuurit (SPAN, RealPLKSR) on toteutettu uudelleen tiedostossa `app/pixeli/kuva.py` alkuperäisten julkaisujen ja spandrel-kirjaston (MIT) perusteella, ja toteutus on tarkistettu mallien ONNX-vientejä vastaan.

Muut arkkitehtuurit (DRUNet, RetinaFace, GFPGAN, RealBasicVSR, naarmujen tunnistuksen UNet, LaMa ja DDColor) on toteutettu uudelleen tiedostoissa `app/pixeli/kuva.py`, `kasvot.py`, `vsr.py`, `korjaus.py` ja `vari.py`; jokainen rakennus vertaa tuloksia alkuperäisiin toteutuksiin (`installer/torchtesti.py`). / The other architectures were reimplemented and every build compares them against the original implementations.

## Soittimen suurennusvarjostimet / Player upscaling shaders (app/shaders)

| Tiedosto / File | Tekijä / Author | Lisenssi / License | Lähde / Source |
|---|---|---|---|
| FSRCNNX_x2_16-0-4-1.glsl | igv | LGPL-3.0 (LICENSE-FSRCNNX-LGPL-3.0.txt, GPL-3.0.txt) | https://github.com/igv/FSRCNN-TensorFlow/releases/tag/1.1 |
| Anime4K_*.glsl (Mode C HQ) | bloc97 | MIT (LICENSE-Anime4K-MIT.txt) | https://github.com/bloc97/Anime4K |

Varjostimet ovat muuttamattomia. / The shaders are unmodified.

## Algoritmit / Algorithms

- DeHalo_alpha – Didée (AviSynth/VapourSynth-yhteisö), toteutettu uudelleen.

## Ohjelmistot / Software

| Osa / Component | Lisenssi / License | Huom. / Note |
|---|---|---|
| Python 3.12 (embeddable) | PSF License | mukana asennusohjelmassa / bundled |
| NumPy | BSD-3-Clause | mukana / bundled |
| pywebview, pythonnet | BSD-3-Clause, MIT | mukana / bundled |
| PyTorch | BSD-3-Clause | ladataan ensimmäisellä käynnistyksellä / downloaded on first launch |
| pillow-heif, Pillow (HEIC-kuvat / iPhone photos) | BSD-3-Clause, MIT-CMU; mukana libheif ja libde265 (LGPL-3.0) | ladataan ensimmäisellä käynnistyksellä PyPI:stä / downloaded on first launch from PyPI |
| FFmpeg (BtbN builds) | GPL | ladataan ensimmäisellä käynnistyksellä / downloaded on first launch; https://github.com/BtbN/FFmpeg-Builds |
| mpv (shinchiro/mpv-winbuild-cmake; sis. libdvdnav, libdvdread, libdvdcss, libbluray) | GPL-2.0+ / LGPL | ladataan ensimmäisellä toistolla / downloaded on first playback; https://mpv.io, https://github.com/shinchiro/mpv-winbuild-cmake |
| pywinrt (winrt-runtime, winrt-Windows.*) | MIT | ladataan ensimmäisellä tekstitysten tunnistuksella / downloaded on first subtitle recognition; tekstintunnistus on Windowsin oma (Windows.Media.Ocr) |
| MakeMKV | oma lisenssi / proprietary | ei mukana – käytetään käyttäjän itse asentamaa ohjelmaa / not included – the user's own installation is used; https://www.makemkv.com |
| Barlow-kirjasin / typeface | SIL Open Font License 1.1 | `app/web/fontit/OFL.txt` |
