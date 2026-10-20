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
    # iPhone-kuva (HEIC): suunta, EXIF ja tallennus JPEG:ksi
    import pillow_heif
    pillow_heif.register_heif_opener()
    ex = Image.Exif()
    ex[0x010F] = "iPhone"
    src3 = d / "IMG_0001.HEIC"
    Image.fromarray(a).save(src3, format="HEIF", quality=90, exif=ex.tobytes())
    wd = m.tyokansio(src3)
    an = kv.analysoi_kuva(src3, wd)
    geo = kv.geometria_kuva(an, {"skaala": "2"})
    ulos = m.ulostulo_polku(src3, d / "ulos", kv.ulostulo_paate(an))
    r = kv.kasittele_kuva(src3, an, geo, {"asetus": 2}, wd, L, ulos)
    im = Image.open(r["ulos"])
    print(f"::notice title=torchtesti::HEIC {an['leveys']}x{an['korkeus']} -> {im.format} {im.size}, EXIF {im.getexif().get(0x010F)}")
    assert im.format == "JPEG" and im.size == (160, 120) and im.getexif().get(0x010F) == "iPhone"
else:
    print("::warning title=torchtesti::ffmpeg ei käytettävissä, kuvien käsittelytesti ohitetaan")

# 5) kohinanpoisto (DRUNet) vastaa alkuperäistä KAIR-toteutusta
import subprocess  # noqa: E402
from pixeli import lisamallit  # noqa: E402
kair = Path(tempfile.mkdtemp(prefix="kair_"))
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/cszn/KAIR", str(kair)], check=True)
sys.path.insert(0, str(kair))
from models.network_unet import UNetRes  # noqa: E402
mp = lisamallit.varmista("kohina")
verkko = UNetRes(in_nc=4, out_nc=3, nc=[64, 128, 256, 512], nb=4, act_mode="R",
                 downsample_mode="strideconv", upsample_mode="convtranspose", bias=False)
verkko.load_state_dict(torch.load(mp, map_location="cpu"), strict=True)
verkko.eval()
sd = kuva.lataa_pth(mp)
for (hh, ww) in ((48, 64), (37, 50)):
    xk = m.testikuva(hh, ww)
    with torch.inference_mode():
        xt = torch.from_numpy(xk)
        ph, pw = (8 - hh % 8) % 8, (8 - ww % 8) % 8
        xp = torch.nn.functional.pad(xt, (0, pw, 0, ph), mode="replicate")
        kartta = torch.full((1, 1, hh + ph, ww + pw), 10.0 / 255.0)
        viite = verkko(torch.cat([xp, kartta], 1))[:, :, :hh, :ww].numpy()
        oma_t = ot.numpy(kuva.Kohinanpoisto(ot, sd)(ot.tensor(xk), 10.0))
    oma_n = kuva.Kohinanpoisto(on, sd)(xk, 10.0)
    e1, e2 = float(np.abs(oma_t - viite).max()), float(np.abs(oma_n - viite).max())
    print(f"::notice title=torchtesti::DRUNet {hh}x{ww}: torch {e1:.2e}, numpy {e2:.2e} (KAIR)")
    assert e1 < 1e-4 and e2 < 1e-4, (e1, e2)
# 6) kasvojen palautus: RetinaFace (facexlib) ja GFPGAN v1.4 (clean) vastaavat alkuperäisiä
import types  # noqa: E402
from pixeli import kasvot as kv2  # noqa: E402
torch.set_grad_enabled(False)
# RetinaFace: verkon raakatulokset ja koko tunnistus (matala kynnys, jotta purku ja NMS tulevat testatuiksi)
from facexlib.detection.retinaface import RetinaFace  # noqa: E402
tp = lisamallit.varmista("tunnistin")
rf = RetinaFace(network_name="resnet50", half=False, device=torch.device("cpu"))
sd_t = torch.load(tp, map_location="cpu")
rf.load_state_dict({(k[7:] if k.startswith("module.") else k): v for k, v in sd_t.items()}, strict=True)
rf.eval()
rng = np.random.default_rng(7)
kuva_t = (m.testikuva(96, 128)[0].transpose(1, 2, 0) * 255).astype(np.uint8)
kuva_t[20:70, 40:90] = rng.integers(0, 255, (50, 50, 3))
sd_np = kuva.lataa_pth(tp)
for nimi_, ops_ in (("torch", ot), ("numpy", on)):
    T = kv2.Tunnistin(ops_, sd_np)
    x_in = torch.from_numpy(np.ascontiguousarray(kuva_t[:, :, ::-1].astype(np.float32) - T.KESKIARVO).transpose(2, 0, 1))[None]
    l0, c0, m0 = rf(x_in)
    l1, c1, m1 = T._verkko(ops_.tensor(x_in.numpy()))
    e = max(float(np.abs(l0[0].numpy() - l1).max()), float(np.abs(m0[0].numpy() - m1).max()))
    p0 = rf.detect_faces(kuva_t[:, :, ::-1].copy(), conf_threshold=0.02)
    p1 = T.tunnista(kuva_t, kynnys=0.02)
    print(f"::notice title=torchtesti::RetinaFace {nimi_}: verkko {e:.2e}, tunnistuksia {len(p0)}/{len(p1)}")
    assert e < 2e-3, e
    assert len(p0) == len(p1) and (len(p0) == 0 or float(np.abs(p0 - p1).max()) < 0.05), (p0[:3], p1[:3])
# GFPGAN: alkuperäiset arkkitehtuuritiedostot (basicsr-riippuvuudet korvattu tyngillä)
gfp = Path(tempfile.mkdtemp(prefix="gfpgan_"))
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/TencentARC/GFPGAN", str(gfp / "repo")], check=True)
for mod in ("basicsr", "basicsr.utils", "basicsr.utils.registry", "basicsr.archs", "basicsr.archs.arch_util"):
    sys.modules[mod] = types.ModuleType(mod)
sys.modules["basicsr.utils.registry"].ARCH_REGISTRY = types.SimpleNamespace(register=lambda *a, **k: (lambda c: c))
sys.modules["basicsr.archs.arch_util"].default_init_weights = lambda *a, **k: None
for f in ("stylegan2_clean_arch.py", "gfpganv1_clean_arch.py"):
    koodi = (gfp / "repo" / "gfpgan" / "archs" / f).read_text(encoding="utf-8")
    (gfp / f).write_text(koodi.replace("from .stylegan2_clean_arch", "from stylegan2_clean_arch"), encoding="utf-8")
sys.path.insert(0, str(gfp))
from gfpganv1_clean_arch import GFPGANv1Clean  # noqa: E402
gp = lisamallit.varmista("kasvot")
verkko_g = GFPGANv1Clean(out_size=512, num_style_feat=512, channel_multiplier=2, decoder_load_path=None,
                         fix_decoder=False, num_mlp=8, input_is_latent=True, different_w=True, narrow=1, sft_half=True)
sd_g = torch.load(gp, map_location="cpu")
verkko_g.load_state_dict(sd_g["params_ema"] if "params_ema" in sd_g else sd_g, strict=True)
verkko_g.eval()
xg = (np.clip(m.testikuva(512, 512), 0, 1) - 0.5) / 0.5
viite_g = verkko_g(torch.from_numpy(xg), return_rgb=False, randomize_noise=False)[0].numpy()
G = kv2.GFPGAN(ot, kuva.lataa_pth(gp))
oma_g = ot.numpy(G(ot.tensor(xg)))
eg = float(np.abs(oma_g - viite_g).max())
print(f"::notice title=torchtesti::GFPGAN v1.4 torch: suurin ero {eg:.2e} (alkuperäinen)")
assert eg < 2e-3, eg

# 7) naarmujen tunnistus vastaa Bringing Old Photos Back to Life -projektin UNetia
from pixeli import korjaus as kj  # noqa: E402
bop = Path(tempfile.mkdtemp(prefix="bopbtl_"))
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life",
                str(bop)], check=True)
dm = types.ModuleType("detection_models")
dm.__path__ = [str(bop / "Global" / "detection_models")]
sys.modules["detection_models"] = dm
sb = types.ModuleType("detection_models.sync_batchnorm")
sb.DataParallelWithCallback = lambda m_, *a, **k: m_
sys.modules["detection_models.sync_batchnorm"] = sb
from detection_models.networks import UNet  # noqa: E402
np_ = lisamallit.varmista("naarmut")
unet = UNet(in_channels=1, out_channels=1, depth=4, conv_num=2, wf=6, padding=True, batch_norm=True,
            up_mode="upsample", with_tanh=False, sync_bn=False, antialiasing=True)
unet.load_state_dict(torch.load(np_, map_location="cpu"), strict=True)
unet.eval()
yk = m.testikuva(64, 96)[:, :1]
viite_n = torch.sigmoid(unet((torch.from_numpy(yk) - 0.5) / 0.5)).numpy()
sd_n = kuva.lataa_pth(np_)
for nimi_, ops_ in (("torch", ot), ("numpy", on)):
    oma = ops_.numpy(kj.Naarmuntunnistin(ops_, sd_n)(ops_.tensor(yk)))
    e = float(np.abs(oma - viite_n).max())
    print(f"::notice title=torchtesti::naarmuntunnistin {nimi_}: suurin ero {e:.2e} (alkuperäinen)")
    assert e < 1e-4, e

# 8) täyttö vastaa alkuperäistä Big LaMa -generaattoria (ffc.py, riippuvuudet korvattu tyngillä)
lama = Path(tempfile.mkdtemp(prefix="lama_"))
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/advimman/lama", str(lama)], check=True)
koodi = (lama / "saicinpainting" / "training" / "modules" / "ffc.py").read_text(encoding="utf-8")
tynga = """
import torch.nn as nn
def get_activation(kind='tanh'):
    return {'tanh': nn.Tanh(), 'sigmoid': nn.Sigmoid(), False: nn.Identity()}[kind]
class BaseDiscriminator(nn.Module):
    pass
class LearnableSpatialTransformWrapper(nn.Module):
    pass
class SELayer(nn.Module):
    pass
def get_shape(t):
    return t.shape
"""
koodi = "\n".join(r for r in koodi.splitlines() if not r.startswith("from saicinpainting"))
ffc = types.ModuleType("lama_ffc")
exec(compile(tynga + koodi, "ffc.py", "exec"), ffc.__dict__)
gen = ffc.FFCResNetGenerator(4, 3, ngf=64, n_downsampling=3, n_blocks=18, add_out_act="sigmoid",
                             init_conv_kwargs={"ratio_gin": 0, "ratio_gout": 0, "enable_lfu": False},
                             downsample_conv_kwargs={"ratio_gin": 0, "ratio_gout": 0, "enable_lfu": False},
                             resnet_conv_kwargs={"ratio_gin": 0.75, "ratio_gout": 0.75, "enable_lfu": False})
lp = lisamallit.varmista("taytto")
gen.load_state_dict(torch.load(lp, map_location="cpu"), strict=True)
gen.eval()
xl = m.testikuva(64, 80)
mk = np.zeros((1, 1, 64, 80), np.float32)
mk[:, :, 20:30, 10:70] = 1.0
viite_l = gen(torch.cat([torch.from_numpy(xl * (1 - mk)), torch.from_numpy(mk)], 1)).numpy()
sd_l = kuva.lataa_pth(lp)
for nimi_, ops_ in (("torch", ot), ("numpy", on)):
    oma = ops_.numpy(kj.LaMa(ops_, sd_l)(ops_.tensor(xl), ops_.tensor(mk)))
    e = float(np.abs(oma - viite_l).max())
    print(f"::notice title=torchtesti::LaMa {nimi_}: suurin ero {e:.2e} (alkuperäinen)")
    assert e < 1e-3, e
# paloittainen täyttö: saumat eivät näy ja maskin ulkopuoli säilyy
kj.PALA, kj.LIMITYS = 48, 16
iso = (np.clip(m.testikuva(100, 140)[0].transpose(1, 2, 0), 0, 1) * 255).astype(np.uint8)
mi = np.zeros((100, 140), bool)
mi[40:44, 5:135] = True
tay = kj.tayta(ot, kj.LaMa(ot, sd_l), iso, mi)
assert (tay[~mi] == iso[~mi]).all()
print(f"::notice title=torchtesti::LaMa paloittain: {int(mi.sum())} pikseliä täytetty")

# 9) väritys vastaa alkuperäistä DDColoria; Lab-muunnos vastaa OpenCV:tä
import cv2  # noqa: E402
from pixeli import vari as vr  # noqa: E402
rgb_t = np.random.default_rng(5).random((40, 50, 3)).astype(np.float32)
e_lab = float(np.abs(vr.rgb_lab(rgb_t) - cv2.cvtColor(rgb_t, cv2.COLOR_RGB2Lab)).max())
lab_t = cv2.cvtColor(rgb_t, cv2.COLOR_RGB2Lab)
e_rgb = float(np.abs(np.clip(vr.lab_rgb(lab_t), 0, 1) - np.clip(cv2.cvtColor(lab_t, cv2.COLOR_Lab2RGB), 0, 1)).max())
print(f"::notice title=torchtesti::Lab vs OpenCV: {e_lab:.2e} / {e_rgb:.2e}")
# OpenCV laskee sRGB-gamman taulukoidulla splinillä: ero alle 1 Lab-yksikön (ei näkyvä)
assert e_lab < 1.0 and e_rgb < 2e-3, (e_lab, e_rgb)
ddc = Path(tempfile.mkdtemp(prefix="ddcolor_"))
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/piddnad/DDColor", str(ddc)], check=True)
for mod, polku_ in (("basicsr", ddc / "basicsr"), ("basicsr.archs", ddc / "basicsr" / "archs")):
    mm = sys.modules.get(mod) or types.ModuleType(mod)
    mm.__path__ = [str(polku_)]
    sys.modules[mod] = mm
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("ddcolor_malli", ddc / "ddcolor" / "model.py")
ddm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ddm)
vp = lisamallit.varmista("vari")
dd = ddm.DDColor(encoder_name="convnext-l", decoder_name="MultiScaleColorDecoder", input_size=[256, 256],
                 num_output_channels=2, last_norm="Spectral", do_normalize=False, num_queries=100, num_scales=3,
                 dec_layers=9)
sd_v = torch.load(vp, map_location="cpu")
puuttuu = dd.load_state_dict({k: v.float() for k, v in sd_v.items()}, strict=False)
assert not puuttuu.missing_keys, puuttuu.missing_keys[:5]
dd.eval()
xv = np.repeat(m.testikuva(256, 256)[:, :1], 3, axis=1)
viite_v = dd(torch.from_numpy(xv)).numpy()
sd_vn = kuva.lataa_pth(vp)
oma_v = ot.numpy(vr.DDColor(ot, sd_vn)(ot.tensor(xv)))
e = float(np.abs(oma_v - viite_v).max())
print(f"::notice title=torchtesti::DDColor torch: suurin ero {e:.2e} ab-yksikköä (alkuperäinen, |ab| < {float(np.abs(viite_v).max()):.0f})")
assert e < 0.05, e
xv_p = np.repeat(m.testikuva(64, 64)[:, :1], 3, axis=1)
e2 = float(np.abs(vr.DDColor(on, sd_vn)(xv_p) - ot.numpy(vr.DDColor(ot, sd_vn)(ot.tensor(xv_p)))).max())
print(f"::notice title=torchtesti::DDColor numpy vs torch: {e2:.2e}")
assert e2 < 0.05, e2
print("TORCHTESTI OK")
