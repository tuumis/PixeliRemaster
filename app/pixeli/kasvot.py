"""PixeliRemaster – kasvojen palautus valokuvissa.

  Tunnistin  – RetinaFace (ResNet-50), facexlib (Xintao Wang, MIT): kasvot ja 5 maamerkkiä
  Palauttaja – GFPGAN v1.4 "clean" (Tencent ARC, Apache 2.0): 512x512 kasvokuva -> palautettu

Kulku kuten GFPGANin omassa työkalussa: kasvot tunnistetaan lähdekuvasta, suoristetaan
maamerkkien avulla 512x512-kokoon, palautetaan ja sijoitetaan takaisin valmiiseen (jo
suurennettuun) kuvaan pehmeäreunaisella maskilla. Voimakkuus sekoittaa palautetun ja
tavallisen tuloksen (1 = pelkkä palautettu).

Molemmat verkot on kirjoitettu kuva.py:n ops-rajapinnan päälle (numpy-referenssi + PyTorch), ja
CI vertaa niitä alkuperäisiin toteutuksiin.
"""
import math

import numpy as np

from . import kuva

# GFPGANin 512x512-kasvopohja: silmät, nenä, suupielet
POHJA = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
                  [201.26117, 371.41043], [313.08905, 371.15118]], np.float32)
REUNAVARI = np.array([132, 133, 135], np.float32)       # cv2.warpAffine(borderValue=(135,133,132)) BGR -> RGB


# ================================================================ RetinaFace

def _bn_taitto(sd, conv, bn):
    """Konvoluutio + BatchNorm yhdeksi konvoluutioksi (päättelyssä täsmälleen sama)."""
    w = sd[conv + ".weight"]
    g, b, m, v = (sd[bn + k] for k in (".weight", ".bias", ".running_mean", ".running_var"))
    k = g / np.sqrt(v + 1e-5)
    b0 = sd.get(conv + ".bias", np.zeros_like(m))
    return (w * k[:, None, None, None]).astype(np.float32), ((b0 - m) * k + b).astype(np.float32)


class Tunnistin:
    """RetinaFace ResNet-50 (facexlib, testivaihe): palauttaa [x1,y1,x2,y2,pisteet, 10 maamerkkiä]."""

    MIN_KOOT = [[16, 32], [64, 128], [256, 512]]
    ASKELEET = [8, 16, 32]
    KESKIARVO = np.array([104.0, 117.0, 123.0], np.float32)       # BGR

    def __init__(self, ops, sd):
        self.o = o = ops
        sd = {(k[7:] if k.startswith("module.") else k): v for k, v in sd.items()}
        P = {}

        def cb(nimi, conv, bn):
            w, b = _bn_taitto(sd, conv, bn)
            P[nimi] = (o.param(w), o.param(b))

        cb("stem", "body.conv1", "body.bn1")
        self.kerrokset = []
        for li, n in zip((1, 2, 3, 4), (3, 4, 6, 3)):
            lohkot = []
            for bi in range(n):
                p = f"body.layer{li}.{bi}"
                for c in (1, 2, 3):
                    cb(f"{p}.c{c}", f"{p}.conv{c}", f"{p}.bn{c}")
                if f"{p}.downsample.0.weight" in sd:
                    cb(f"{p}.ds", f"{p}.downsample.0", f"{p}.downsample.1")
                askel = 2 if (bi == 0 and li > 1) else 1
                lohkot.append((p, askel, f"{p}.downsample.0.weight" in sd))
            self.kerrokset.append(lohkot)
        for nimi in ("output1", "output2", "output3", "merge1", "merge2"):
            cb("fpn." + nimi, f"fpn.{nimi}.0", f"fpn.{nimi}.1")
        for s in ("ssh1", "ssh2", "ssh3"):
            for c in ("conv3X3", "conv5X5_1", "conv5X5_2", "conv7X7_2", "conv7x7_3"):
                cb(f"{s}.{c}", f"{s}.{c}.0", f"{s}.{c}.1")
        for h in ("ClassHead", "BboxHead", "LandmarkHead"):
            for i in range(3):
                P[f"{h}.{i}"] = (o.param(sd[f"{h}.{i}.conv1x1.weight"]), o.param(sd[f"{h}.{i}.conv1x1.bias"]))
        self.P = P

    def _cb(self, x, nimi, pad, stride=1, act=True, leaky=0.0):
        y = self.o.conv2d(x, *self.P[nimi], pad, stride)
        if act:
            y = self.o.relu(y) if leaky == 0 else self.o.leaky(y, leaky)
        return y

    def _verkko(self, x):
        o = self.o
        x = self._cb(x, "stem", 3, 2)
        x = o.maxpool3s2(x)
        ulos = []
        for li, lohkot in enumerate(self.kerrokset):
            for p, askel, ds in lohkot:
                y = self._cb(x, p + ".c1", 0)
                y = self._cb(y, p + ".c2", 1, askel)
                y = self._cb(y, p + ".c3", 0, act=False)
                x = o.relu(y + (self._cb(x, p + ".ds", 0, askel, act=False) if ds else x))
            if li >= 1:
                ulos.append(x)
        o1 = self._cb(ulos[0], "fpn.output1", 0)
        o2 = self._cb(ulos[1], "fpn.output2", 0)
        o3 = self._cb(ulos[2], "fpn.output3", 0)
        o2 = self._cb(o2 + o.lahin(o3, o2.shape[2], o2.shape[3]), "fpn.merge2", 1)
        o1 = self._cb(o1 + o.lahin(o2, o1.shape[2], o1.shape[3]), "fpn.merge1", 1)
        piirteet = []
        for s, f in zip(("ssh1", "ssh2", "ssh3"), (o1, o2, o3)):
            c3 = self._cb(f, s + ".conv3X3", 1, act=False)
            c51 = self._cb(f, s + ".conv5X5_1", 1)
            c5 = self._cb(c51, s + ".conv5X5_2", 1, act=False)
            c72 = self._cb(c51, s + ".conv7X7_2", 1)
            c7 = self._cb(c72, s + ".conv7x7_3", 1, act=False)
            piirteet.append(o.relu(o.cat([c3, c5, c7])))
        tulos = {}
        for h, k in (("BboxHead", 4), ("ClassHead", 2), ("LandmarkHead", 10)):
            osat = []
            for i, f in enumerate(piirteet):
                t = o.numpy(o.conv2d(f, *self.P[f"{h}.{i}"], 0))[0]         # (A*k, h, w)
                osat.append(t.transpose(1, 2, 0).reshape(-1, k))
            tulos[h] = np.concatenate(osat, 0)
        return tulos["BboxHead"], tulos["ClassHead"], tulos["LandmarkHead"]

    def _priorit(self, h, w):
        a = []
        for k, askel in enumerate(self.ASKELEET):
            fh, fw = math.ceil(h / askel), math.ceil(w / askel)
            jj, ii = np.meshgrid(np.arange(fw), np.arange(fh))
            cx = ((jj + 0.5) * askel / w).reshape(-1)
            cy = ((ii + 0.5) * askel / h).reshape(-1)
            for m in self.MIN_KOOT[k]:
                a.append(np.stack([cx, cy, np.full_like(cx, m / w), np.full_like(cy, m / h)], 1))
            # järjestys: (i, j, min_koko) -> lomitetaan kuten alkuperäinen
            a[-2:] = [np.stack(a[-2:], 1).reshape(-1, 4)]
        return np.concatenate(a, 0).astype(np.float32)

    def tunnista(self, rgb_u8, kynnys=0.97, nms=0.4):
        """rgb (h,w,3) uint8 -> taulukko (n, 15): laatikko, pisteet, maamerkit (5 x [x, y])."""
        h, w, _ = rgb_u8.shape
        bgr = rgb_u8[:, :, ::-1].astype(np.float32) - self.KESKIARVO
        x = self.o.tensor(np.ascontiguousarray(bgr.transpose(2, 0, 1))[None])
        loc, conf, maam = self._verkko(x)
        e = np.exp(conf - conf.max(1, keepdims=True))
        pisteet = (e / e.sum(1, keepdims=True))[:, 1]
        pr = self._priorit(h, w)
        v0, v1 = 0.1, 0.2
        lt = np.concatenate([pr[:, :2] + loc[:, :2] * v0 * pr[:, 2:], pr[:, 2:] * np.exp(loc[:, 2:] * v1)], 1)
        lt[:, :2] -= lt[:, 2:] / 2
        lt[:, 2:] += lt[:, :2]
        lt *= np.array([w, h, w, h], np.float32)
        mp = np.concatenate([pr[:, :2] + maam[:, 2 * i:2 * i + 2] * v0 * pr[:, 2:] for i in range(5)], 1)
        mp *= np.array([w, h] * 5, np.float32)
        ok = pisteet > kynnys
        lt, mp, pisteet = lt[ok], mp[ok], pisteet[ok]
        jarj = np.argsort(-pisteet)
        lt, mp, pisteet = lt[jarj], mp[jarj], pisteet[jarj]
        pidetty = _nms(lt, nms)
        return np.concatenate([lt[pidetty], pisteet[pidetty, None], mp[pidetty]], 1)


def _nms(laatikot, kynnys):
    """Ei-maksimien vaimennus (kuten torchvision.ops.nms; laatikot jo pisteiden mukaan järjestyksessä)."""
    x1, y1, x2, y2 = laatikot.T
    alat = (x2 - x1) * (y2 - y1)
    jaljella = list(range(len(laatikot)))
    pidetty = []
    while jaljella:
        i = jaljella.pop(0)
        pidetty.append(i)
        jaljella = [j for j in jaljella if _iou(i, j, x1, y1, x2, y2, alat) <= kynnys]
    return np.array(pidetty, np.int64)


def _iou(i, j, x1, y1, x2, y2, alat):
    w = max(0.0, min(x2[i], x2[j]) - max(x1[i], x1[j]))
    h = max(0.0, min(y2[i], y2[j]) - max(y1[i], y1[j]))
    leik = w * h
    return leik / max(alat[i] + alat[j] - leik, 1e-9)


# ================================================================ GFPGAN v1.4 (clean)

class GFPGAN:
    """GFPGANv1Clean: out_size 512, channel_multiplier 2, input_is_latent, different_w, sft_half.
    Kohina otetaan mallin tallennetuista puskureista, joten tulos on toistettava."""

    def __init__(self, ops, sd):
        self.o = o = ops
        self.P = {k: o.param(v) for k, v in sd.items()}
        self.log = 9
        self._bil = {}

    def _p(self, k):
        return self.P[k]

    def _bilin(self, x, kerroin):
        o = self.o
        h, w = x.shape[2], x.shape[3]
        avain = (h, w, kerroin)
        if avain not in self._bil:
            self._bil[avain] = (o.param(kuva.bilineaari_matriisi(h, kerroin)), o.param(kuva.bilineaari_matriisi(w, kerroin)))
        return o.matresize(x, *self._bil[avain])

    def _conv(self, x, nimi, pad):
        return self.o.conv2d(x, self._p(nimi + ".weight"), self._p(nimi + ".bias"), pad)

    def _resblock(self, x, p, kerroin):
        o = self.o
        y = o.leaky(self._conv(x, p + ".conv1", 1), 0.2)
        y = self._bilin(y, kerroin)
        y = o.leaky(self._conv(y, p + ".conv2", 1), 0.2)
        s = o.conv2d(self._bilin(x, kerroin), self._p(p + ".skip.weight"), None, 0)
        return y + s

    def _modconv(self, x, p, tyyli, demod=True, ylos=False):
        o = self.o
        w = self._p(p + ".weight")                                   # (1, cout, cin, k, k)
        mod = o.linear(tyyli, self._p(p + ".modulation.weight"), self._p(p + ".modulation.bias"))  # (1, cin)
        cout, cin, k = w.shape[1], w.shape[2], w.shape[3]
        ww = w[0] * mod.reshape(1, cin, 1, 1)
        if demod:
            ww = ww * ((o.summa(ww * ww, (1, 2, 3)) + 1e-8) ** -0.5).reshape(cout, 1, 1, 1)
        if ylos:
            x = self._bilin(x, 2)
        return o.conv2d(x, ww, None, k // 2)

    def _styleconv(self, x, p, tyyli, kohina, ylos=False):
        o = self.o
        y = self._modconv(x, p + ".modulated_conv", tyyli, True, ylos) * (2 ** 0.5)
        y = y + self._p(p + ".weight") * kohina
        y = y + self._p(p + ".bias")
        return o.leaky(y, 0.2)

    def _torgb(self, x, p, tyyli, ohitus):
        y = self._modconv(x, p + ".modulated_conv", tyyli, False) + self._p(p + ".bias")
        if ohitus is not None:
            y = y + self._bilin(ohitus, 2)
        return y

    def __call__(self, x):
        """x (1,3,512,512) väreinä -1..1 -> palautettu (1,3,512,512) -1..1."""
        o = self.o
        feat = o.leaky(self._conv(x, "conv_body_first", 0), 0.2)
        ohitukset = []
        for i in range(self.log - 2):
            feat = self._resblock(feat, f"conv_body_down.{i}", 0.5)
            ohitukset.insert(0, feat)
        feat = o.leaky(self._conv(feat, "final_conv", 1), 0.2)
        n = feat.shape[0]
        koodi = o.linear(feat.reshape(n, -1), self._p("final_linear.weight"), self._p("final_linear.bias"))
        latentti = koodi.reshape(n, -1, 512)                         # (1, 16, 512)
        ehdot = []
        for i in range(self.log - 2):
            feat = self._resblock(feat + ohitukset[i], f"conv_body_up.{i}", 2)
            for laji in ("condition_scale", "condition_shift"):
                t = o.leaky(self._conv(feat, f"{laji}.{i}.0", 1), 0.2)
                ehdot.append(self._conv(t, f"{laji}.{i}.2", 1))
        d = "stylegan_decoder"
        kohina = [self._p(f"{d}.noises.noise{i}") for i in range(15)]
        out = self._p(f"{d}.constant_input.weight")
        out = self._styleconv(out, f"{d}.style_conv1", latentti[:, 0], kohina[0])
        ohitus = self._torgb(out, f"{d}.to_rgb1", latentti[:, 1], None)
        i = 1
        for j in range(7):
            out = self._styleconv(out, f"{d}.style_convs.{2 * j}", latentti[:, i], kohina[2 * j + 1], ylos=True)
            if i < len(ehdot):
                c = out.shape[1] // 2
                out = o.cat([out[:, :c], out[:, c:] * ehdot[i - 1] + ehdot[i]])
            out = self._styleconv(out, f"{d}.style_convs.{2 * j + 1}", latentti[:, i + 1], kohina[2 * j + 2])
            ohitus = self._torgb(out, f"{d}.to_rgbs.{j}", latentti[:, i + 2], ohitus)
            i += 2
        return ohitus


# ================================================================ suoristus ja takaisin sijoitus

def similaarisuus(lahde, kohde):
    """Paras samanmuotoisuusmuunnos (kierto, mittakaava, siirto) pisteistä lahde -> kohde. 2x3-matriisi."""
    ls, ks = lahde.astype(np.float64), kohde.astype(np.float64)
    lm, km = ls.mean(0), ks.mean(0)
    l0, k0 = ls - lm, ks - km
    a = (l0 * k0).sum() / (l0 ** 2).sum()
    b = (l0[:, 0] * k0[:, 1] - l0[:, 1] * k0[:, 0]).sum() / (l0 ** 2).sum()
    R = np.array([[a, -b], [b, a]])
    t = km - R @ lm
    return np.concatenate([R, t[:, None]], 1)


def _naytteista(kuva_f, xs, ys, reuna):
    """Bilineaarinen näytteistys (kuten cv2.warpAffine INTER_LINEAR, vakioreuna)."""
    h, w, c = kuva_f.shape
    x0, y0 = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    fx, fy = (xs - x0)[..., None], (ys - y0)[..., None]
    tulos = 0
    for dy, wy in ((0, 1 - fy), (1, fy)):
        for dx, wx in ((0, 1 - fx), (1, fx)):
            xi, yi = x0 + dx, y0 + dy
            sisalla = ((xi >= 0) & (xi < w) & (yi >= 0) & (yi < h))[..., None]
            arvo = kuva_f[np.clip(yi, 0, h - 1), np.clip(xi, 0, w - 1)]
            tulos = tulos + wy * wx * np.where(sisalla, arvo, reuna)
    return tulos


def suorista(rgb_u8, M, koko=512):
    """Kasvo pohjaan: ulostulon pikseli (u, v) <- lähteen M^-1 (u, v)."""
    A = np.vstack([M, [0, 0, 1]])
    Ai = np.linalg.inv(A)
    vv, uu = np.mgrid[0:koko, 0:koko].astype(np.float64)
    xs = Ai[0, 0] * uu + Ai[0, 1] * vv + Ai[0, 2]
    ys = Ai[1, 0] * uu + Ai[1, 1] * vv + Ai[1, 2]
    return _naytteista(rgb_u8.astype(np.float32), xs, ys, REUNAVARI)


def _maski(koko=512):
    """Pehmeäreunainen neliö kasvopohjassa (vastaa GFPGANin neliömaskia eroosioineen ja sumennuksineen)."""
    t = np.arange(koko, dtype=np.float32)
    reuna = np.minimum(t, koko - 1 - t)
    r = np.clip((reuna - koko * 0.03) / (koko * 0.08), 0, 1)
    r = r * r * (3 - 2 * r)
    return np.minimum(r[:, None], r[None, :])


def sijoita(ulos_u8, kasvo_f, M, skaala, voimakkuus):
    """Palautettu kasvo (512, 512, 3) takaisin suurennettuun kuvaan. M: lähde -> pohja, skaala: tulos/lähde."""
    oh, ow, _ = ulos_u8.shape
    A = np.vstack([M, [0, 0, 1]])
    Ai = np.linalg.inv(A)
    kulmat = np.array([[0, 0, 1], [511, 0, 1], [0, 511, 1], [511, 511, 1]], np.float64) @ Ai.T
    x0 = int(max(0, math.floor(kulmat[:, 0].min() * skaala) - 2))
    x1 = int(min(ow, math.ceil(kulmat[:, 0].max() * skaala) + 2))
    y0 = int(max(0, math.floor(kulmat[:, 1].min() * skaala) - 2))
    y1 = int(min(oh, math.ceil(kulmat[:, 1].max() * skaala) + 2))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    # tuloksen pikselin keskipiste lähteen koordinaateissa
    sx, sy = (xx + 0.5) / skaala - 0.5, (yy + 0.5) / skaala - 0.5
    u = M[0, 0] * sx + M[0, 1] * sy + M[0, 2]
    v = M[1, 0] * sx + M[1, 1] * sy + M[1, 2]
    kasvo = _naytteista(kasvo_f, u, v, np.zeros(3, np.float32))
    m = _naytteista(_maski()[..., None], u, v, np.zeros(1, np.float32)) * float(voimakkuus)
    alue = ulos_u8[y0:y1, x0:x1].astype(np.float32)
    ulos_u8[y0:y1, x0:x1] = np.clip(alue * (1 - m) + kasvo * m + 0.5, 0, 255).astype(np.uint8)


def palauta(ops, tunnistin, gfpgan, lahde_u8, ulos_u8, voimakkuus, max_kasvot=30, palaute=None):
    """Tunnistaa kasvot lähdekuvasta ja sijoittaa palautetut kasvot tulokseen (paikallaan).
    Palauttaa löydettyjen kasvojen määrän."""
    h, w, _ = lahde_u8.shape
    skaala = ulos_u8.shape[1] / w
    # tunnistus enintään 1600 px kuvasta (kuten facexlibin oletusraja suurille kuville)
    k = max(1.0, max(h, w) / 1600.0)
    if k > 1:
        wy = kuva.skaalausmatriisi(h, int(round(h / k)), "mitchell")
        wx = kuva.skaalausmatriisi(w, int(round(w / k)), "mitchell")
        pieni = np.clip(np.einsum("ij,jkc->ikc", wy, np.einsum("ij,kjc->kic", wx, lahde_u8.astype(np.float32))) + 0.5,
                        0, 255).astype(np.uint8)
        kx, ky = w / pieni.shape[1], h / pieni.shape[0]
    else:
        pieni, kx, ky = lahde_u8, 1.0, 1.0
    tul = tunnistin.tunnista(pieni)
    n = 0
    for r in tul[:max_kasvot]:
        pisteet = r[5:15].reshape(5, 2) * np.array([kx, ky], np.float32)
        if np.hypot(*(pisteet[0] - pisteet[1])) < 5:
            continue
        M = similaarisuus(pisteet, POHJA)
        kasvo = suorista(lahde_u8, M)
        x = ops.tensor((kasvo.transpose(2, 0, 1)[None] / 255.0 - 0.5) / 0.5)
        y = gfpgan(ops.half(x) if getattr(gfpgan, "puoli", False) else x)
        y = np.clip((ops.numpy(y)[0].transpose(1, 2, 0) + 1) / 2, 0, 1) * 255.0
        sijoita(ulos_u8, np.round(y).astype(np.float32), M, skaala, voimakkuus)
        n += 1
        if palaute:
            palaute(n / max(1, min(len(tul), max_kasvot)))
    return n
