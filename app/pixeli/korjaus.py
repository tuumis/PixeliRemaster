"""PixeliRemaster – vanhojen valokuvien naarmujen ja pölyn poisto.

  Naarmuntunnistin – Bringing Old Photos Back to Life -projektin tunnistusverkko
                     (Microsoft, MIT): UNet, antialiasoitu alinäytteistys, BatchNorm.
  LaMa             – Big LaMa -täyttöverkko (Suvorov ym. 2022, Apache 2.0):
                     Fast Fourier Convolution -ResNet.
Rakenteet on toteutettu uudelleen alkuperäisten julkaisujen koodin mukaan (ops-rajapinnan
päälle, joten sama koodi toimii näytönohjaimella ja numpy-referenssinä).
"""
import numpy as np

from . import kuva

TUNNISTUS_KOKO = 256         # tunnistusverkko on opetettu kuvilla, joiden lyhyt sivu on 256
KYNNYS = 0.4                 # alkuperäisen toteutuksen raja-arvo
PALA = 512                   # täyttöverkon pala (täysi tarkkuus)
LIMITYS = 64


def _bn(sd, p, eps=1e-5):
    s = sd[p + "weight"] / np.sqrt(sd[p + "running_var"] + eps)
    return s.astype(np.float32), (sd[p + "bias"] - sd[p + "running_mean"] * s).astype(np.float32)


def _kerro(o, x, s, t):
    return x * s.reshape(1, -1, 1, 1) + t.reshape(1, -1, 1, 1)


# ------------------------------------------------------------------ naarmujen tunnistus

class Naarmuntunnistin:
    def __init__(self, o, sd):
        self.o = o
        P = o.param
        self.first = (P(sd["first.1.weight"]), P(sd["first.1.bias"]))
        self.syvyys = len({k.split(".")[1] for k in sd if k.startswith("down_path.")})

        def lohko(p):
            s1, t1 = _bn(sd, p + "block.2.")
            s2, t2 = _bn(sd, p + "block.6.")
            return (P(sd[p + "block.1.weight"]), P(sd[p + "block.1.bias"]), P(s1), P(t1),
                    P(sd[p + "block.5.weight"]), P(sd[p + "block.5.bias"]), P(s2), P(t2))
        self.alas = []
        for i in range(self.syvyys):
            s, t = _bn(sd, f"down_sample.{i}.2.")
            self.alas.append(((P(sd[f"down_sample.{i}.1.weight"]), P(sd[f"down_sample.{i}.1.bias"]), P(s), P(t)),
                              lohko(f"down_path.{i}.")))
        self.ylos = [((P(sd[f"up_path.{i}.up.2.weight"]), P(sd[f"up_path.{i}.up.2.bias"])),
                      lohko(f"up_path.{i}.conv_block.")) for i in range(self.syvyys)]
        self.last = (P(sd["last.1.weight"]), P(sd["last.1.bias"]))
        self._mat = {}

    def _lohko(self, x, L):
        o = self.o
        w1, b1, s1, t1, w2, b2, s2, t2 = L
        x = o.leaky(_kerro(o, o.conv2d(o.pad_ref(x, 1), w1, b1, 0), s1, t1), 0.2)
        return o.leaky(_kerro(o, o.conv2d(o.pad_ref(x, 1), w2, b2, 0), s2, t2), 0.2)

    def _kaksinkertaistus(self, n):
        if n not in self._mat:
            self._mat[n] = self.o.param(kuva.bilineaari_matriisi(n, 2))
        return self._mat[n]

    def __call__(self, y):
        """y: (1,1,h,w) harmaa 0..1, h ja w jaollisia 16:lla -> naarmun todennäköisyys (1,1,h,w)."""
        o = self.o
        x = o.leaky(o.conv2d(o.pad_ref((y - 0.5) / 0.5, 3), *self.first, 0), 0.2)
        sillat = []
        for (w, b, s, t), L in self.alas:
            sillat.append(x)
            x = o.leaky(_kerro(o, o.conv2d(o.pad_ref(x, 1), w, b, 0), s, t), 0.2)
            x = self._lohko(o.blur_s2(x), L)
        for i, ((w, b), L) in enumerate(self.ylos):
            u = o.matresize(x, self._kaksinkertaistus(x.shape[2]), self._kaksinkertaistus(x.shape[3]))
            u = o.conv2d(o.pad_ref(u, 1), w, b, 0)
            x = self._lohko(o.cat([u, sillat[-i - 1]]), L)
        return o.sigmoid(o.conv2d(o.pad_ref(x, 1), *self.last, 0))


# ------------------------------------------------------------------ LaMa

class LaMa:
    def __init__(self, o, sd):
        self.o = o
        P = o.param

        def bn(p):
            s, t = _bn(sd, p)
            return P(s), P(t)
        self.alku = (P(sd["model.1.ffc.convl2l.weight"]),) + bn("model.1.bn_l.")
        self.alas = [(P(sd[f"model.{i}.ffc.convl2l.weight"]),) + bn(f"model.{i}.bn_l.") for i in (2, 3)]
        self.alas4 = (P(sd["model.4.ffc.convl2l.weight"]), P(sd["model.4.ffc.convl2g.weight"])) + \
            bn("model.4.bn_l.") + bn("model.4.bn_g.")
        self.lohkot = []
        i = 5
        while f"model.{i}.conv1.ffc.convl2l.weight" in sd:
            L = []
            for cv in ("conv1", "conv2"):
                q = f"model.{i}.{cv}."
                g = q + "ffc.convg2g."
                L.append({
                    "l2l": P(sd[q + "ffc.convl2l.weight"]), "l2g": P(sd[q + "ffc.convl2g.weight"]),
                    "g2l": P(sd[q + "ffc.convg2l.weight"]),
                    "g_c1": (P(sd[g + "conv1.0.weight"]),) + bn(g + "conv1.1."),
                    "g_fu": (P(sd[g + "fu.conv_layer.weight"]),) + bn(g + "fu.bn."),
                    "g_c2": P(sd[g + "conv2.weight"]),
                    "bn_l": bn(q + "bn_l."), "bn_g": bn(q + "bn_g.")})
            self.lohkot.append(L)
            i += 1
        j = i + 1                            # ConcatTupleLayer
        self.ylos = []
        for _ in range(3):
            self.ylos.append((P(sd[f"model.{j}.weight"]), P(sd[f"model.{j}.bias"])) + bn(f"model.{j + 1}."))
            j += 3
        self.loppu = (P(sd[f"model.{j + 1}.weight"]), P(sd[f"model.{j + 1}.bias"]))

    def _ffc(self, xl, xg, L):
        o = self.o
        ul = o.conv2d(o.pad_ref(xl, 1), L["l2l"], None, 0) + o.conv2d(o.pad_ref(xg, 1), L["g2l"], None, 0)
        w, s, t = L["g_c1"]
        z = o.relu(_kerro(o, o.conv2d(xg, w, None, 0), s, t))
        z = o.conv2d(z + o.spektri(z, *L["g_fu"]), L["g_c2"], None, 0)
        ug = o.conv2d(o.pad_ref(xl, 1), L["l2g"], None, 0) + z
        (sl, tl), (sg, tg) = L["bn_l"], L["bn_g"]
        return o.relu(_kerro(o, ul, sl, tl)), o.relu(_kerro(o, ug, sg, tg))

    def __call__(self, x, maski):
        """x (1,3,h,w) 0..1, maski (1,1,h,w) 1 = täytettävä; h ja w jaollisia 8:lla -> (1,3,h,w)."""
        o = self.o
        y = o.cat([x * (1 - maski), maski])
        w, s, t = self.alku
        y = o.relu(_kerro(o, o.conv2d(o.pad_ref(y, 3), w, None, 0), s, t))
        for w, s, t in self.alas:
            y = o.relu(_kerro(o, o.conv2d(o.pad_ref(y, 1), w, None, 0, stride=2), s, t))
        wl, wg, sl, tl, sg, tg = self.alas4
        yp = o.pad_ref(y, 1)
        xl = o.relu(_kerro(o, o.conv2d(yp, wl, None, 0, stride=2), sl, tl))
        xg = o.relu(_kerro(o, o.conv2d(yp, wg, None, 0, stride=2), sg, tg))
        del y, yp
        for L1, L2 in self.lohkot:
            yl, yg = self._ffc(xl, xg, L1)
            yl, yg = self._ffc(yl, yg, L2)
            xl, xg = xl + yl, xg + yg
        y = o.cat([xl, xg])
        for w, b, s, t in self.ylos:
            y = o.relu(_kerro(o, o.convT3s2(y, w, b), s, t))
        return o.sigmoid(o.conv2d(o.pad_ref(y, 3), *self.loppu, 0))


# ------------------------------------------------------------------ korkean tason toiminto

def _laajenna(m, r):
    """Binäärimaskin laajennus (neliö 2r+1)."""
    if r <= 0:
        return m
    h, w = m.shape
    p = np.pad(m, r)
    v = np.zeros((h + 2 * r, w), bool)
    for d in range(2 * r + 1):
        v |= p[:, d:d + w]
    u = np.zeros((h, w), bool)
    for d in range(2 * r + 1):
        u |= v[d:d + h]
    return u


def tunnista(o, T, rgb_u8):
    """Naarmumaski (h, w) bool lähteen tarkkuudella."""
    h, w, _ = rgb_u8.shape
    if w < h:
        tw, th = TUNNISTUS_KOKO, h / w * TUNNISTUS_KOKO
    else:
        th, tw = TUNNISTUS_KOKO, w / h * TUNNISTUS_KOKO
    tw, th = max(16, int(round(tw / 16)) * 16), max(16, int(round(th / 16)) * 16)
    harmaa = (rgb_u8.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)) / 255.0
    pieni = kuva.skaalausmatriisi(h, th, "catmullrom") @ harmaa @ kuva.skaalausmatriisi(w, tw, "catmullrom").T
    p = o.numpy(T(o.tensor(np.clip(pieni, 0, 1)[None, None])))[0, 0]
    # takaisin täyteen kokoon (lähin naapuri kuten alkuperäisessä), sitten pieni laajennus
    iy = np.minimum((np.arange(h) * th / h).astype(int), th - 1)
    ix = np.minimum((np.arange(w) * tw / w).astype(int), tw - 1)
    maski = (p >= KYNNYS)[iy][:, ix]
    return _laajenna(maski, max(1, int(round(min(h, w) / TUNNISTUS_KOKO * 0.75))))


def tayta(o, LM, rgb_u8, maski, palaute=None):
    """Täyttää maskin alueet LaMalla paloittain täydellä tarkkuudella. Palauttaa uuden uint8-kuvan."""
    h, w, _ = rgb_u8.shape
    x = rgb_u8.astype(np.float32) / 255.0
    if not maski.any():
        return rgb_u8.copy()
    if max(h, w) <= PALA * 2:                  # pieni kuva kerralla
        palat = [(0, 0, h, w)]
    else:
        askel = PALA - LIMITYS
        palat = []
        for y0 in range(0, max(1, h - LIMITYS), askel):
            for x0 in range(0, max(1, w - LIMITYS), askel):
                y1, x1 = min(h, y0 + PALA), min(w, x0 + PALA)
                y0_, x0_ = max(0, y1 - PALA), max(0, x1 - PALA)
                if maski[y0_:y1, x0_:x1].any():
                    palat.append((y0_, x0_, y1, x1))
    summa = np.zeros((h, w, 3), np.float32)
    paino = np.zeros((h, w, 1), np.float32)
    for n, (y0, x0, y1, x1) in enumerate(palat):
        ph, pw = y1 - y0, x1 - x0
        H, W = (ph + 7) // 8 * 8, (pw + 7) // 8 * 8
        osa = np.pad(x[y0:y1, x0:x1], ((0, H - ph), (0, W - pw), (0, 0)), mode="symmetric")
        mo = np.pad(maski[y0:y1, x0:x1].astype(np.float32), ((0, H - ph), (0, W - pw)))
        y = o.numpy(LM(o.tensor(osa.transpose(2, 0, 1)[None]), o.tensor(mo[None, None])))[0].transpose(1, 2, 0)
        # reunoja kohti pienenevä paino, jottei palojen saumat näy
        wy = np.minimum(np.arange(ph) + 1, np.arange(ph)[::-1] + 1).astype(np.float32)
        wx = np.minimum(np.arange(pw) + 1, np.arange(pw)[::-1] + 1).astype(np.float32)
        wk = np.minimum(np.minimum.outer(wy, wx) / LIMITYS, 1.0)[..., None]
        summa[y0:y1, x0:x1] += y[:ph, :pw] * wk
        paino[y0:y1, x0:x1] += wk
        if palaute:
            palaute((n + 1) / len(palat))
    taytetty = summa / np.maximum(paino, 1e-6)
    m3 = maski[..., None]
    tulos = np.where(m3 & (paino > 0), taytetty, x)
    return (np.clip(tulos, 0, 1) * 255.0 + 0.5).astype(np.uint8)
