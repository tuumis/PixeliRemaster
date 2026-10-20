"""PixeliRemaster – mustavalkoisten (ja seepiansävyisten) valokuvien väritys.

DDColor (Kang ym., ICCV 2023, Apache 2.0) toteutettu uudelleen alkuperäisen koodin
(github.com/piddnad/DDColor) mukaan ops-rajapinnan päälle: ConvNeXt-kooderi, fastai-tyyppinen
UNet-dekooderi (PixelShuffle + sumennus) ja monimittakaavainen värikyselydekooderi.
Malli ennustaa Lab-värin a- ja b-kanavat 512x512-kokoisesta kuvasta; kirkkaus (L) otetaan
alkuperäisestä kuvasta täydellä tarkkuudella, joten yksityiskohdat eivät muutu.
"""
import math

import numpy as np

from . import kuva

KOKO = 512


def _spektri(sd, p):
    """spectral_norm (eval): W = W_orig / (u · W v)."""
    W = sd[p + "weight_orig"]
    u, v = sd[p + "weight_u"].astype(np.float64), sd[p + "weight_v"].astype(np.float64)
    sigma = float(u @ (W.reshape(W.shape[0], -1).astype(np.float64) @ v))
    return (W / sigma).astype(np.float32)


def _paino(sd, p):
    if p + "weight_orig" in sd:
        return _spektri(sd, p)
    return sd[p + "weight"]


def _bn(sd, p, eps=1e-5):
    s = sd[p + "weight"] / np.sqrt(sd[p + "running_var"] + eps)
    return s.astype(np.float32), (sd[p + "bias"] - sd[p + "running_mean"] * s).astype(np.float32)


def _sijainti(h, w, n_feats=128, temp=10000.0):
    """PositionEmbeddingSine(normalize=True) muodossa (h*w, 2*n_feats)."""
    eps, scale = 1e-6, 2 * math.pi
    y = np.arange(1, h + 1, dtype=np.float32)[:, None].repeat(w, 1) / (h + eps) * scale
    x = np.arange(1, w + 1, dtype=np.float32)[None, :].repeat(h, 0) / (w + eps) * scale
    d = np.arange(n_feats, dtype=np.float32)
    d = temp ** (2 * (d // 2) / n_feats)
    px, py = x[:, :, None] / d, y[:, :, None] / d
    px = np.stack((np.sin(px[:, :, 0::2]), np.cos(px[:, :, 1::2])), axis=3).reshape(h, w, -1)
    py = np.stack((np.sin(py[:, :, 0::2]), np.cos(py[:, :, 1::2])), axis=3).reshape(h, w, -1)
    return np.concatenate([py, px], axis=2).reshape(h * w, -1).astype(np.float32)


class DDColor:
    def __init__(self, o, sd):
        self.o = o
        P = o.param
        e = "encoder.arch."
        self.syvyys = [len({k.split(".")[4] for k in sd if k.startswith(f"{e}stages.{i}.")}) for i in range(4)]
        self.alas = []
        for i in range(4):
            q = f"{e}downsample_layers.{i}."
            self.alas.append((P(sd[q + "0.weight"]), P(sd[q + "0.bias"]), P(sd[q + "1.weight"]), P(sd[q + "1.bias"])))
        self.vaiheet = [[tuple(P(sd[f"{e}stages.{i}.{j}.{k}"]) for k in (
            "dwconv.weight", "dwconv.bias", "norm.weight", "norm.bias", "pwconv1.weight", "pwconv1.bias",
            "pwconv2.weight", "pwconv2.bias", "gamma")) for j in range(self.syvyys[i])] for i in range(4)]
        self.normit = [(P(sd[f"{e}norm{i}.weight"]), P(sd[f"{e}norm{i}.bias"])) for i in range(4)]
        d = "decoder."
        self.ylos = []
        for i in range(3):
            q = f"{d}layers.{i}."
            ss, st = _bn(sd, q + "shuf.conv.1.")
            xs, xt = _bn(sd, q + "bn.")
            cs, ct = _bn(sd, q + "conv.2.")
            self.ylos.append((P(_paino(sd, q + "shuf.conv.0.")), P(ss), P(st), P(xs), P(xt),
                              P(_paino(sd, q + "conv.0.")), P(cs), P(ct)))
        self.viim = (P(_paino(sd, d + "last_shuf.conv.0.")), P(sd[d + "last_shuf.conv.0.bias"]))
        c = d + "color_decoder."
        n_kerr = len({k.split(".")[3] for k in sd if k.startswith(c + "transformer_self_attention_layers.")})
        self.proj = [(P(sd[f"{c}input_proj.{i}.weight"]), P(sd[f"{c}input_proj.{i}.bias"])) for i in range(3)]
        self.taso = [P(sd[c + "level_embed.weight"][i][None]) for i in range(3)]
        self.q_feat = P(sd[c + "query_feat.weight"])
        self.q_pos = P(sd[c + "query_embed.weight"])
        self.dim = int(sd[c + "query_feat.weight"].shape[1])
        self.paat = 8

        def mha(q):
            return (P(sd[q + "in_proj_weight"]), P(sd[q + "in_proj_bias"]),
                    P(sd[q + "out_proj.weight"]), P(sd[q + "out_proj.bias"]))

        def n(q):
            return P(sd[q + "norm.weight"]), P(sd[q + "norm.bias"])
        self.kerrokset = []
        for i in range(n_kerr):
            ca, sa, ff = (f"{c}transformer_cross_attention_layers.{i}.", f"{c}transformer_self_attention_layers.{i}.",
                          f"{c}transformer_ffn_layers.{i}.")
            self.kerrokset.append({
                "ca": mha(ca + "multihead_attn."), "ca_n": n(ca), "sa": mha(sa + "self_attn."), "sa_n": n(sa),
                "ff": (P(sd[ff + "linear1.weight"]), P(sd[ff + "linear1.bias"]),
                       P(sd[ff + "linear2.weight"]), P(sd[ff + "linear2.bias"])), "ff_n": n(ff)})
        self.dec_norm = (P(sd[c + "decoder_norm.weight"]), P(sd[c + "decoder_norm.bias"]))
        self.mlp = [(P(sd[f"{c}color_embed.layers.{i}.weight"]), P(sd[f"{c}color_embed.layers.{i}.bias"]))
                    for i in range(3)]
        self.refine = (P(_paino(sd, "refine_net.0.0.")), P(sd["refine_net.0.0.bias"]))
        self.mean = P(np.array([0.485, 0.456, 0.406], np.float32).reshape(1, 3, 1, 1))
        self.std = P(np.array([0.229, 0.224, 0.225], np.float32).reshape(1, 3, 1, 1))
        self._pos = {}

    def _kooderi(self, x):
        o = self.o
        koukut = []
        for i in range(4):
            a, b, c, d = self.alas[i]
            if i == 0:
                x = o.ln_c(o.patch_conv(x, a, b, 4), c, d)
            else:
                x = o.patch_conv(o.ln_c(x, a, b), c, d, 2)
            for dw_w, dw_b, n_w, n_b, p1w, p1b, p2w, p2b, g in self.vaiheet[i]:
                y = o.ln_last(o.kanavat_viimeiseksi(o.dwconv7(x, dw_w, dw_b)), n_w, n_b, 1e-6)
                y = o.linear(o.gelu(o.linear(y, p1w, p1b)), p2w, p2b) * g
                x = x + o.kanavat_ensin(y)
            koukut.append(o.ln_c(x, *self.normit[i]))
        return koukut

    def _unet(self, koukut):
        o = self.o
        x = koukut[3]
        ulos = []
        for i, kk in enumerate((2, 1, 0)):
            sw, ss, st, xs, xt, cw, cs, ct = self.ylos[i]
            u = o.conv2d(x, sw, None, 0) * ss.reshape(1, -1, 1, 1) + st.reshape(1, -1, 1, 1)
            u = o.sumennus2(o.pixel_shuffle(o.relu(u), 2))
            s = koukut[kk] * xs.reshape(1, -1, 1, 1) + xt.reshape(1, -1, 1, 1)
            y = o.relu(o.conv2d(o.relu(o.cat([u, s])), cw, None, 1))
            x = y * cs.reshape(1, -1, 1, 1) + ct.reshape(1, -1, 1, 1)
            ulos.append(x)
        w, b = self.viim
        return ulos, o.sumennus2(o.pixel_shuffle(o.relu(o.conv2d(x, w, b, 0)), 4))

    def _mha(self, p, q, k, v):
        """nn.MultiheadAttention eräkoolla 1: q (L, E), k ja v (S, E) -> (L, E)."""
        o = self.o
        W, B, Wo, Bo = p
        E, h = self.dim, self.paat
        Q = o.paat(o.linear(q, W[:E], B[:E]), h)
        K = o.paat(o.linear(k, W[E:2 * E], B[E:2 * E]), h)
        V = o.paat(o.linear(v, W[2 * E:], B[2 * E:]), h)
        A = o.softmax(o.matmul(Q * (1.0 / math.sqrt(E // h)), o.transpose_last(K)))
        return o.linear(o.paat_pois(o.matmul(A, V)), Wo, Bo)

    def _varidekooderi(self, x, kuvapiirteet):
        o = self.o
        src, pos = [], []
        for i in range(3):
            _, _, h, w = x[i].shape
            if (h, w) not in self._pos:
                self._pos[(h, w)] = o.param(_sijainti(h, w, self.dim // 2))
            pos.append(self._pos[(h, w)])
            s = o.conv2d(x[i], *self.proj[i], 0)[0].reshape(self.dim, h * w)
            src.append(o.transpose_last(s) + self.taso[i])
        out, qp = self.q_feat, self.q_pos
        for i, L in enumerate(self.kerrokset):
            t = i % 3
            out = o.ln_last(out + self._mha(L["ca"], out + qp, src[t] + pos[t], src[t]), *L["ca_n"], 1e-5)
            q = out + qp
            out = o.ln_last(out + self._mha(L["sa"], q, q, out), *L["sa_n"], 1e-5)
            w1, b1, w2, b2 = L["ff"]
            out = o.ln_last(out + o.linear(o.relu(o.linear(out, w1, b1)), w2, b2), *L["ff_n"], 1e-5)
        out = o.ln_last(out, *self.dec_norm, 1e-5)
        for i, (w, b) in enumerate(self.mlp):
            out = o.linear(out, w, b)
            if i < 2:
                out = o.relu(out)
        _, c, h, w = kuvapiirteet.shape
        return o.matmul(out, kuvapiirteet[0].reshape(c, h * w)).reshape(1, -1, h, w)

    def __call__(self, x):
        """x (1,3,S,S): harmaa RGB 0..1 -> ab (1,2,S,S) Lab-yksiköissä."""
        o = self.o
        x = (x - self.mean) / self.std
        ulos, kuvapiirteet = self._unet(self._kooderi(x))
        return o.conv2d(o.cat([self._varidekooderi(ulos, kuvapiirteet), x]), *self.refine, 0)


# ------------------------------------------------------------------ Lab (sRGB, D65; kuten OpenCV)

_M = np.array([[0.412453, 0.357580, 0.180423], [0.212671, 0.715160, 0.072169], [0.019334, 0.119193, 0.950227]])
_VALK = np.array([0.950456, 1.0, 1.088754])


def rgb_lab(rgb):
    """(..., 3) sRGB 0..1 -> Lab (L 0..100)."""
    rgb = rgb.astype(np.float64)
    c = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    xyz = c @ _M.T / _VALK
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    L = np.where(xyz[..., 1] > 0.008856, 116.0 * f[..., 1] - 16.0, 903.3 * xyz[..., 1])
    return np.stack([L, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])], -1)


def lab_rgb(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0

    def finv(t):
        return np.where(t > 0.206893, t ** 3, (t - 16.0 / 116.0) / 7.787)
    y = np.where(L > 7.9996, fy ** 3, L / 903.3)
    c = np.clip(np.stack([finv(fx), y, finv(fz)], -1) * _VALK @ np.linalg.inv(_M).T, 0.0, None)
    return np.where(c > 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, 12.92 * c)


def harmaasavy(rgb_u8):
    """Kuinka paljon värit vaihtelevat muuten kuin kirkkauden mukana (Lab a/b, jäännöshajonta).
    Mustavalkoisessa ja yksisävyisessä (seepia) kuvassa väri riippuu vain kirkkaudesta: arvo on pieni."""
    h, w, _ = rgb_u8.shape
    k = max(1, int(math.sqrt(h * w / 200_000)))
    lab = rgb_lab(rgb_u8[::k, ::k].reshape(-1, 3) / 255.0)
    lab = lab[lab[:, 0] > 8] if (lab[:, 0] > 8).sum() >= 100 else lab      # hyvin tummat kohdat pois
    lokero = np.clip((lab[:, 0] / 5).astype(int), 0, 20)
    jaannos = np.zeros((len(lab), 2))
    for b in np.unique(lokero):
        s = lokero == b
        jaannos[s] = lab[s, 1:] - lab[s, 1:].mean(0)
    return float(np.sqrt((jaannos ** 2).sum(1).mean()))


HARMAA_RAJA = 2.5            # tätä pienempi: kuva on todennäköisesti mustavalkoinen tai seepia


def varita(o, net, rgb_u8, palaute=None):
    """Väritys: värit mallilta pienestä kuvasta, kirkkaus alkuperäisestä täydellä tarkkuudella."""
    h, w, _ = rgb_u8.shape
    rgb = rgb_u8.astype(np.float32) / 255.0
    pieni = kuva.skaalausmatriisi(h, KOKO, "catmullrom") @ rgb.transpose(2, 0, 1) @ kuva.skaalausmatriisi(w, KOKO, "catmullrom").T
    pieni = np.clip(pieni.transpose(1, 2, 0), 0, 1)
    Lp = rgb_lab(pieni)[..., :1]
    harmaa = lab_rgb(np.concatenate([Lp, np.zeros_like(Lp), np.zeros_like(Lp)], -1)).astype(np.float32)
    ab = o.numpy(net(o.tensor(harmaa.transpose(2, 0, 1)[None])))[0]
    if palaute:
        palaute(0.7)
    Wy, Wx = kuva.skaalausmatriisi(KOKO, h, "catmullrom"), kuva.skaalausmatriisi(KOKO, w, "catmullrom")
    ab = np.stack([Wy @ ab[i] @ Wx.T for i in range(2)], -1)
    tulos = np.empty((h, w, 3), np.uint8)
    for y0 in range(0, h, 512):                 # Lab-muunnos kaistoittain (muisti)
        y1 = min(h, y0 + 512)
        L = rgb_lab(rgb[y0:y1])[..., :1]
        tulos[y0:y1] = (np.clip(lab_rgb(np.concatenate([L, ab[y0:y1]], -1)), 0, 1) * 255.0 + 0.5).astype(np.uint8)
    return tulos
