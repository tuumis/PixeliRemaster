"""PyTorch-toteutuksen testi (GitHub Actions, prosessori-PyTorch erillisessä Pythonissa).

Tarkistaa, että TorchOps laskee samat tulokset kuin numpy-referenssi myös uusissa kuvien
poluissa: paloittelu (SPAN ja RealPLKSR, GroupNorm kaistoittain), kaksi kierrosta (4x)
ja uint8-muunnos. Jos ffmpeg on käytettävissä, ajetaan lisäksi koko kuvan käsittely
(EXIF-suunta, JPEG- ja PNG-tallennus, läpinäkyvyys) Windowsissa.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=torchtesti::{rivit}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe
juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "app"))
os.environ["PIXELI_DATA"] = tempfile.mkdtemp(prefix="pixeli_t_")

import numpy as np  # noqa: E402
import torch  # noqa: E402

from pixeli import kuva, moottori as m  # noqa: E402

torch.set_num_threads(max(1, os.cpu_count() or 1))
on, ot = kuva.NumpyOps(), kuva.TorchOps("cpu", puolitarkkuus=False)
print("torch", torch.__version__)

with torch.inference_mode():
    # 1) paloittelu vastaa koko kuvan laskentaa, torch vastaa numpya
    x = m.testikuva(70, 90)
    for nimi in ("live", "vhs"):
        sd = kuva.lataa_pth(m.MALLIT / m.MALLITIEDOSTOT[nimi])
        mn, mt = kuva.Malli(on, sd), kuva.Malli(ot, sd)
        koko = mn(x)
        mt.raja, mt.pala = 100, 32
        osat = []
        mt.palaute = osat.append
        pal = ot.numpy(mt(ot.tensor(x)))
        ero = float(np.abs(koko - pal).max())
        print(f"::notice title=torchtesti::{nimi}: paloittain torch vs numpy {ero:.2e}")
        assert ero < 2e-4, (nimi, ero)
        assert osat and abs(osat[-1] - 1.0) < 1e-6

    # 2) GroupNorm kaistoittain
    t = np.random.default_rng(3).normal(0.3, 2.0, (1, 64, 37, 41)).astype(np.float32)
    w_, b_ = np.linspace(0.5, 1.5, 64).astype(np.float32), np.linspace(-1, 1, 64).astype(np.float32)
    viite = on.group_norm(t, 4, w_, b_)
    tt = ot.tensor(t)
    ot.kaista_alkiot = 16 * 41 * 5            # useita kaistoja
    tulos = ot.numpy(ot.group_norm_suuri(tt, 4, ot.param(w_), ot.param(b_)))
    ero = float(np.abs(viite - tulos).max())
    print(f"::notice title=torchtesti::group_norm_suuri {ero:.2e}")
    assert ero < 1e-4, ero

    # 3) kaksi kierrosta + AI-osuus + uint8
    sd = kuva.lataa_pth(m.MALLIT / m.MALLITIEDOSTOT["live"])
    a_n = {"malli": kuva.Malli(on, sd), "ai": 0.85, "halo": 0.8, "kierrokset": 2}
    a_t = {"malli": kuva.Malli(ot, sd), "ai": 0.85, "halo": 0.8, "kierrokset": 2}
    xs = m.testikuva(24, 32)
    yn = kuva.Ketju(on, 24, 32, 90, 120, a_n, yuv=False).rgb_lopullinen(xs)
    yt = kuva.Ketju(ot, 24, 32, 90, 120, a_t, yuv=False).rgb_lopullinen(ot.tensor(xs))
    un, ut = on.uint8_hwc(yn), ot.uint8_hwc(yt)
    ero = int(np.abs(un.astype(int) - ut.astype(int)).max())
    print(f"::notice title=torchtesti::4x-ketju uint8 ero {ero}")
    assert ut.shape == (90, 120, 3) and ero <= 1

# 4) koko kuvan käsittely (vaatii ffmpegin)
if m.ffmpeg_ok():
    from PIL import Image
    from pixeli import kuvat as kv
    d = Path(tempfile.mkdtemp(prefix="pixeli_kuvat_"))
    yy, xx = np.mgrid[0:60, 0:80]
    a = np.stack([128 + 100 * np.sin(xx / 6), 128 + 80 * np.cos(yy / 5), (xx * 3) % 255], -1).astype(np.uint8)
    a[0:8, 0:16] = [255, 0, 0]
    ex = Image.Exif()
    ex[0x0112] = 6
    src = d / "pysty äö.jpg"
    Image.fromarray(a).save(src, quality=80, exif=ex.tobytes())
    rgba = np.dstack([a, np.where(xx < 40, 255, 0).astype(np.uint8)])
    src2 = d / "läpinäkyvä.png"
    Image.fromarray(rgba, "RGBA").save(src2)
    L = m.Laite("cpu")
    for s_, n in ((src, 2), (src2, 5)):
        wd = m.tyokansio(s_)
        an = kv.analysoi_kuva(s_, wd)
        geo = kv.geometria_kuva(an, {"skaala": "2"})
        koe = kv.koekuvat_kuva(s_, an, geo, wd, L)
        ulos = m.ulostulo_polku(s_, d / "ulos", kv.ulostulo_paate(an))
        r = kv.kasittele_kuva(s_, an, geo, {"asetus": n}, wd, L, ulos)
        im = Image.open(r["ulos"])
        print(f"::notice title=torchtesti::{s_.name} {an['leveys']}x{an['korkeus']} {an['tyyppi']} -> {im.size} {im.mode}")
        if s_ == src:
            assert im.size == (120, 160), im.size                      # suunta 6: pystykuva
            assert im.getexif().get(0x0112) == 1
            px = np.asarray(im.convert("RGB")).astype(int)
            assert px[4:24, -14:-3, 0].mean() > 180 and px[4:24, -14:-3, 1].mean() < 90   # punainen oikealla ylhäällä
        else:
            assert im.mode == "RGBA" and im.size == (160, 120)
            al = np.asarray(im)[..., 3]
            assert al[:, :70].mean() > 240 and al[:, 90:].mean() < 15
else:
    print("::warning title=torchtesti::ffmpeg ei käytettävissä, kuvien käsittelytesti ohitetaan")
print("TORCHTESTI OK")
