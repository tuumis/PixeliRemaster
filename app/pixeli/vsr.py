"""PixeliRemaster – monikuvamalli (RealBasicVSR) videon "Terävin"-asetukseen.

RealBasicVSR (Chan ym., CVPR 2022, Apache 2.0) lukee useita peräkkäisiä kuvia: optinen virtaus
(SPyNet) kohdistaa naapurikuvien piirteet, ja tieto kulkee sekä eteen- että taaksepäin ajassa.
Rakenteet on toteutettu uudelleen mmediting-kirjaston mukaan ops-rajapinnan päälle.

MonikuvaKetju syöttää kuvat malliin ikkunoittain (ikkunan molemmin puolin kontekstia) ja katkaisee
ikkunat kohtausten vaihtuessa, jotta edellinen kohtaus ei näy seuraavassa.
"""
import math
from collections import deque

import numpy as np

from . import kuva

IKKUNA = 30            # kerralla valmiiksi laskettavat kuvat
KONTEKSTI = 5          # ikkunan molemmin puolin luettavat lisäkuvat


def bilin_matriisi(n_in, n_out, align_corners):
    """F.interpolate(mode='bilinear') yhteen suuntaan, ilman antialiasta."""
    W = np.zeros((n_out, n_in), np.float64)
    if align_corners:
        x = np.arange(n_out) * ((n_in - 1) / (n_out - 1) if n_out > 1 else 0.0)
    else:
        x = np.maximum((np.arange(n_out) + 0.5) * (n_in / n_out) - 0.5, 0.0)
    x0 = np.minimum(np.floor(x).astype(int), n_in - 1)
    x1 = np.minimum(x0 + 1, n_in - 1)
    l = x - x0
    np.add.at(W, (np.arange(n_out), x0), 1 - l)
    np.add.at(W, (np.arange(n_out), x1), l)
    return W.astype(np.float32)


class _Mat:
    def __init__(self, o):
        self.o, self.v = o, {}

    def __call__(self, a, b, align):
        k = (a, b, align)
        if k not in self.v:
            self.v[k] = self.o.param(bilin_matriisi(a, b, align))
        return self.v[k]


def _p(o, sd, k):
    return (o.param(sd[k + ".weight"]), o.param(sd[k + ".bias"]))


def _resblocks(o, sd, p):
    n = len({k[len(p) + 7:].split(".")[0] for k in sd if k.startswith(p + "main.2.")})
    return _p(o, sd, p + "main.0"), [_p(o, sd, f"{p}main.2.{j}.conv1") + _p(o, sd, f"{p}main.2.{j}.conv2")
                                      for j in range(n)]


def _aja_res(o, R, x):
    (w, b), lohkot = R
    x = o.leaky(o.conv2d(x, w, b, 1), 0.1)
    for w1, b1, w2, b2 in lohkot:
        x = x + o.conv2d(o.relu(o.conv2d(x, w1, b1, 1)), w2, b2, 1)
    return x


def _puolita(v):
    if isinstance(v, (list, tuple)):
        return type(v)(_puolita(t) for t in v)
    return v.half() if hasattr(v, "half") and not isinstance(v, np.ndarray) else v


class SPyNet:
    def __init__(self, o, sd, p):
        self.o = o
        self.mat = _Mat(o)
        self.mod = [[_p(o, sd, f"{p}basic_module.{lv}.basic_module.{i}.conv") for i in range(5)] for lv in range(6)]
        self.mean = o.param(np.array([0.485, 0.456, 0.406], np.float32).reshape(1, 3, 1, 1))
        self.std = o.param(np.array([0.229, 0.224, 0.225], np.float32).reshape(1, 3, 1, 1))

    def _moduuli(self, lv, x):
        o = self.o
        for i, (w, b) in enumerate(self.mod[lv]):
            x = o.conv2d(x, w, b, 3)
            if i < 4:
                x = o.relu(x)
        return x

    def __call__(self, ref, supp):
        """Virtaus, jolla supp siirretään ref:n kohdalle (pikseleinä). Lasketaan fp32:na."""
        o = self.o
        ref, supp = o.float(ref), o.float(supp)
        h, w = ref.shape[2:]
        hu, wu = math.ceil(h / 32) * 32, math.ceil(w / 32) * 32
        ref = (o.matresize(ref, self.mat(h, hu, False), self.mat(w, wu, False)) - self.mean) / self.std
        supp = (o.matresize(supp, self.mat(h, hu, False), self.mat(w, wu, False)) - self.mean) / self.std
        r, s = [ref], [supp]
        for _ in range(5):
            r.append(o.avgpool2(r[-1]))
            s.append(o.avgpool2(s[-1]))
        r, s = r[::-1], s[::-1]
        flow = o.zeros((1, 2, hu // 32, wu // 32), ref)
        for lv in range(6):
            if lv == 0:
                up = flow
            else:
                fh, fw = flow.shape[2], flow.shape[3]
                up = o.matresize(flow, self.mat(fh, 2 * fh, True), self.mat(fw, 2 * fw, True)) * 2.0
            flow = up + self._moduuli(lv, o.cat([r[lv], o.warp(s[lv], up, "border"), up]))
        f = o.matresize(flow, self.mat(hu, h, False), self.mat(wu, w, False))
        return o.cat([f[:, 0:1] * (w / wu), f[:, 1:2] * (h / hu)])


class RealBasicVSR:
    skaala = 4

    def __init__(self, o, sd, p="generator_ema."):
        if not any(k.startswith(p) for k in sd):
            p = ""
        self.o = o
        self.puhd = _resblocks(o, sd, p + "image_cleaning.0.")
        self.puhd_ulos = _p(o, sd, p + "image_cleaning.1")
        q = p + "basicvsr."
        self.spynet = SPyNet(o, sd, q + "spynet.")
        self.taakse = _resblocks(o, sd, q + "backward_resblocks.")
        self.eteen = _resblocks(o, sd, q + "forward_resblocks.")
        self.fusion, self.up1, self.up2 = _p(o, sd, q + "fusion"), _p(o, sd, q + "upsample1.upsample_conv"), \
            _p(o, sd, q + "upsample2.upsample_conv")
        self.hr, self.last = _p(o, sd, q + "conv_hr"), _p(o, sd, q + "conv_last")
        self.mid = int(sd[q + "fusion.weight"].shape[0])
        self.mat = _Mat(o)
        self.puoli = False

    def puolita(self):
        if getattr(self.o, "ht", False):
            for k in ("puhd", "puhd_ulos", "taakse", "eteen", "fusion", "up1", "up2", "hr", "last"):
                setattr(self, k, _puolita(getattr(self, k)))
            self.puoli = True

    def _h(self, x):
        return self.o.half(x) if self.puoli else self.o.float(x)

    def __call__(self, lrs, kasittele=None):
        """lrs: lista (1,3,h,w) 0..1. Palauttaa listan (1,3,4h,4w) fp32 tai, jos kasittele(i, y)
        on annettu, antaa tulokset sille yksi kerrallaan (muisti ei kasva)."""
        o = self.o
        t = len(lrs)
        # kuvien puhdistus (yksi kierros: dynamic_refine_thres = 255/255)
        puhtaat = []
        for x in lrs:
            r = o.float(o.conv2d(_aja_res(o, self.puhd, self._h(x)), *self.puhd_ulos, 1))
            puhtaat.append(o.float(x) + r)
        h, w = puhtaat[0].shape[2:]
        taakse_v = [self.spynet(puhtaat[i], puhtaat[i + 1]) for i in range(t - 1)]
        eteen_v = [self.spynet(puhtaat[i + 1], puhtaat[i]) for i in range(t - 1)]
        x_in = [self._h(v) for v in puhtaat]
        tal = [None] * t
        f = o.zeros((1, self.mid, h, w), x_in[0])
        for i in range(t - 1, -1, -1):
            if i < t - 1:
                f = o.warp(f, taakse_v[i])
            f = _aja_res(o, self.taakse, o.cat([x_in[i], f]))
            tal[i] = f
        f = o.zeros(f.shape, f)
        Wy, Wx = self.mat(h, 4 * h, False), self.mat(w, 4 * w, False)
        tulos = []
        for i in range(t):
            if i > 0:
                f = o.warp(f, eteen_v[i - 1])
            f = _aja_res(o, self.eteen, o.cat([x_in[i], f]))
            y = o.leaky(o.conv2d(o.cat([tal[i], f]), *self.fusion, 0), 0.1)
            tal[i] = None
            y = o.leaky(o.pixel_shuffle(o.conv2d(y, *self.up1, 1), 2), 0.1)
            y = o.leaky(o.pixel_shuffle(o.conv2d(y, *self.up2, 1), 2), 0.1)
            y = o.leaky(o.conv2d(y, *self.hr, 1), 0.1)
            y = o.float(o.conv2d(y, *self.last, 1)) + o.matresize(puhtaat[i], Wy, Wx)
            if kasittele:
                kasittele(i, y)
            else:
                tulos.append(y)
            del y
        return tulos


# ------------------------------------------------------------------ käsittelyketju

class MonikuvaKetju:
    """Ketju-luokan kaltainen, mutta tulokset valmistuvat viiveellä: syota() ottaa kuvia ja palauttaa
    valmiit (0..n kpl), loppu() loput. asetus: monikuva (RealBasicVSR), ai, rae, halo, kohina..."""
    ajallinen = True

    def __init__(self, ops, h, w, oh, ow, asetus, matriisi="bt601", yuv=True):
        o = self.o = ops
        a = dict(asetus)
        self.net = a.pop("monikuva")
        self.ai = float(a.get("ai", 1.0))
        self.h, self.w, self.oh, self.ow = h, w, oh, ow
        esi = {k: v for k, v in a.items() if k in ("kohina", "kohinamalli", "halo", "halo_r", "halo_tumma")}
        esi["malli"] = None
        self.esi = kuva.Ketju(o, h, w, h, w, esi, matriisi, yuv=False) \
            if float(a.get("kohina", 0) or 0) > 0 or float(a.get("halo", 0) or 0) > 0 else None
        self.jalki = kuva.Ketju(o, 4 * h, 4 * w, oh, ow, {"malli": None, "rae": a.get("rae", 0),
                                                         "rae_koko": a.get("rae_koko", 0.7)}, matriisi, yuv=yuv)
        if self.ai < 1.0:
            self.Wy4 = o.param(kuva.skaalausmatriisi(h, 4 * h, "catmullrom"))
            self.Wx4 = o.param(kuva.skaalausmatriisi(w, 4 * w, "catmullrom"))
        k = 8 if min(h, w) >= 256 else 1
        self.Wy_l = o.param(kuva.skaalausmatriisi(h, max(8, h // k), "mitchell"))
        self.Wx_l = o.param(kuva.skaalausmatriisi(w, max(8, w // k), "mitchell"))
        self.palaute = None
        self.pala_raja = None
        self.alku = 0                    # ensimmäisen syötetyn kuvan numero (rakeen siemen)
        self.nollaa()

    def nollaa(self):
        self.jakso = []                  # nykyisen kohtauksen puskuroidut kuvat
        self.j0 = 0                      # jakso[0]:n numero
        self.seg0 = 0                    # kohtauksen ensimmäinen kuva
        self.annettu = 0                 # seuraava valmistuva kuva
        self.saatu = 0                   # vastaanotettuja kuvia
        self.edellinen = None
        self.erot = deque(maxlen=12)
        self.leikkauksia = 0

    # ---------------------------------------------------------- kohtausten tunnistus
    def _leikkaus(self, x):
        o = self.o
        y = o.numpy(o.matresize(x[:, 0:1] * 0.299 + x[:, 1:2] * 0.587 + x[:, 2:3] * 0.114, self.Wy_l, self.Wx_l))[0, 0]
        uusi = False
        if self.edellinen is not None:
            d = float(np.abs(y - self.edellinen).mean())
            tavallinen = float(np.median(self.erot)) if self.erot else 0.02
            uusi = d > 0.07 and d > 4.0 * max(tavallinen, 0.008)
            if not uusi:
                self.erot.append(d)
        self.edellinen = y
        return uusi

    # ---------------------------------------------------------- laskenta
    def _valmis(self, x4, i):
        """4x-tulos -> näytön kokoinen RGB (AI-osuus, rae)."""
        o = self.o
        if self.ai < 1.0:
            perus = o.matresize(self.jakso[i - self.j0][1], self.Wy4, self.Wx4)
            x4 = perus + (x4 - perus) * self.ai
        return self.jalki.rgb_lopullinen(o.clamp(x4, 0.0, 1.0), self.alku + i)

    def _laske(self, a, b):
        """Laskee kuvat a..b-1 nykyisestä kohtauksesta (konteksti molemmin puolin)."""
        loppu = self.j0 + len(self.jakso)
        alku = max(self.seg0, self.j0, a - KONTEKSTI)
        yla = min(loppu, b + KONTEKSTI)
        syote = [self.jakso[i - self.j0][0] for i in range(alku, yla)]
        tulos = []

        def ota(k, y):
            i = alku + k
            if a <= i < b:
                tulos.append(self._valmis(y, i))
        self.net(syote, ota)
        self.annettu = b
        pois = max(0, (b - KONTEKSTI) - self.j0)            # vanhat kuvat pois puskurista
        if pois:
            self.jakso = self.jakso[pois:]
            self.j0 += pois
        return tulos

    def _tyhjenna(self):
        tulos = []
        loppu = self.j0 + len(self.jakso)
        while self.annettu < loppu:
            tulos += self._laske(self.annettu, min(loppu, self.annettu + IKKUNA))
        return tulos

    def syota(self, xb):
        """xb (n,3,h,w) 0..1 -> valmiit kuvat (m,3,oh,ow) tai None."""
        o = self.o
        valmiit = []
        for k in range(xb.shape[0]):
            x = o.float(xb[k:k + 1])
            if self.esi is not None:
                x = self.esi.rgb_lopullinen(o.clamp(x, 0.0, 1.0))
            if self._leikkaus(x) and self.saatu > self.seg0:
                valmiit += self._tyhjenna()
                self.jakso, self.j0, self.seg0 = [], self.saatu, self.saatu
                self.leikkauksia += 1
            self.jakso.append((self.net._h(x), x if self.ai < 1.0 else None))
            self.saatu += 1
            while self.j0 + len(self.jakso) - self.annettu >= IKKUNA + KONTEKSTI:
                valmiit += self._laske(self.annettu, self.annettu + IKKUNA)
        return o.cat_n(valmiit) if valmiit else None

    def loppu(self):
        valmiit = self._tyhjenna()
        return self.o.cat_n(valmiit) if valmiit else None

    def kehys(self, kuvat, k, siemen=0):
        """Esikatselu: kuvasarjasta (lista (1,3,h,w)) vain kuva k valmiiksi."""
        self.nollaa()
        self.alku = siemen - k
        tulos = None
        for i, x in enumerate(kuvat):
            y = self.syota(x)
            if y is not None:
                for j in range(y.shape[0]):
                    if self.annettu - y.shape[0] + j == k:
                        tulos = y[j:j + 1]
        y = self.loppu()
        if y is not None:
            alku = self.annettu - y.shape[0]
            if alku <= k < self.annettu:
                tulos = y[k - alku:k - alku + 1]
        self.nollaa()
        return tulos

    def yuv420p10(self, rgb):
        return self.jalki.yuv420p10(rgb)
