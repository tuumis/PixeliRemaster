"""PixeliRemaster – kuvankäsittelyketju.

Kaikki algoritmit (AI-mallit, halonpoisto, skaalaus, filmirae, YUV-muunnos) on
kirjoitettu kerran `ops`-rajapinnan päälle. Rajapinnasta on kaksi toteutusta:

  TorchOps  – näytönohjaimella (CUDA, fp16/fp32), varsinainen käsittely
  NumpyOps  – prosessorilla, referenssi ja asennuksen itsetesti

Itsetesti ajaa saman kuvan molemmilla ja vertaa tuloksia, joten näytönohjain-
toteutuksen oikeellisuus tarkistetaan jokaisella koneella erikseen.
"""
import math
import pickle
import zipfile
import collections
import numpy as np

# ============================================================== mallitiedostot


def lataa_pth(polku):
    """Lukee PyTorchin .pth-tiedoston numpy-taulukoiksi ilman PyTorchia.
    Tiedosto on zip, jonka sisällä pickle; vain tensorit ja sanakirjat sallitaan."""
    DT = {"FloatStorage": np.float32, "HalfStorage": np.float16, "BFloat16Storage": None,
          "DoubleStorage": np.float64, "LongStorage": np.int64, "IntStorage": np.int32}
    if not zipfile.is_zipfile(polku):
        return _lataa_pth_vanha(polku, DT)
    zf = zipfile.ZipFile(polku)
    nimet = zf.namelist()
    pkl = next(n for n in nimet if n.endswith("data.pkl"))
    juuri = pkl[: -len("data.pkl")]

    class Lukija(pickle.Unpickler):
        def find_class(self, mod, name):
            if name == "_rebuild_tensor_v2":
                def rb(storage, offset, size, stride, *a):
                    if not size:
                        return storage[offset:offset + 1].reshape(()).copy()
                    it = storage.itemsize
                    return np.lib.stride_tricks.as_strided(
                        storage[offset:], shape=size, strides=[s * it for s in stride]).copy()
                return rb
            if name == "_rebuild_parameter":
                return lambda data, *a: data
            if name == "OrderedDict":
                return collections.OrderedDict
            if mod.startswith("torch") and name.endswith("Storage"):
                return name
            raise pickle.UnpicklingError(f"Ei sallittu: {mod}.{name}")

        def persistent_load(self, pid):
            _, styp, key, _loc, n = pid
            if not isinstance(styp, str):
                styp = getattr(styp, "__name__", str(styp))
            dt = DT[styp]
            raw = zf.read(f"{juuri}data/{key}")
            return np.frombuffer(raw, dtype=dt)[:n]

    sd = Lukija(zf.open(pkl)).load()
    for k in ("params_ema", "params", "state_dict", "model_state"):
        if isinstance(sd, dict) and k in sd and isinstance(sd[k], dict):
            sd = sd[k]
    return {k: np.asarray(v, dtype=np.float32) for k, v in sd.items()}


def _lataa_pth_vanha(polku, DT):
    """PyTorchin vanha (ennen 1.6) tallennusmuoto: taikaluku, versio ja järjestelmätiedot picklenä,
    sitten rakenne (tensorit viitteinä tallennuksiin), tallennusten avaimet ja lopuksi raakadata."""
    import io
    with open(polku, "rb") as f:
        data = f.read()
    bio = io.BytesIO(data)
    tallennukset = {}          # avain -> [dtype, taulukko]
    tensorit = []              # (avain, offset, koko, askel)

    class Lukija(pickle.Unpickler):
        def find_class(self, mod, name):
            if name == "_rebuild_tensor_v2":
                def rb(avain, offset, size, stride, *a):
                    tensorit.append((avain, offset, tuple(size), tuple(stride)))
                    return ("__tensori__", len(tensorit) - 1)
                return rb
            if name == "_rebuild_parameter":
                return lambda d, *a: d
            if name == "OrderedDict":
                return collections.OrderedDict
            if mod.startswith("torch") and name.endswith("Storage"):
                return name
            raise pickle.UnpicklingError(f"Ei sallittu: {mod}.{name}")

        def persistent_load(self, pid):
            _, styp, avain = pid[:3]
            if not isinstance(styp, str):
                styp = getattr(styp, "__name__", str(styp))
            tallennukset.setdefault(avain, [DT[styp], None])
            return avain

    for _ in range(3):
        pickle.load(bio)
    rakenne = Lukija(bio).load()
    avaimet = pickle.load(bio)
    pos = bio.tell()
    for avain in avaimet:
        dt = tallennukset[avain][0]
        maara = int.from_bytes(data[pos:pos + 8], "little")
        pos += 8
        koko = maara * np.dtype(dt).itemsize
        tallennukset[avain][1] = np.frombuffer(data[pos:pos + koko], dtype=dt)
        pos += koko

    def ratkaise(o):
        if isinstance(o, tuple) and len(o) == 2 and o[0] == "__tensori__":
            avain, off, size, stride = tensorit[o[1]]
            st = tallennukset[avain][1]
            if not size:
                return st[off:off + 1].reshape(()).astype(np.float32)
            it = st.itemsize
            return np.lib.stride_tricks.as_strided(st[off:], shape=size, strides=[x * it for x in stride]).astype(np.float32)
        if isinstance(o, dict):
            return {k: ratkaise(v) for k, v in o.items()}
        return o
    sd = ratkaise(rakenne)
    for k in ("params_ema", "params", "state_dict", "model_state"):
        if isinstance(sd, dict) and k in sd and isinstance(sd[k], dict):
            sd = sd[k]
    return {k: np.asarray(v, dtype=np.float32) for k, v in sd.items()}


def erf(x):
    """erf numpylle: Numerical Recipes erfc-Tšebyšev-approksimaatio (suhteellinen virhe < 1.2e-7)."""
    z = np.abs(x).astype(np.float64)
    t = 1.0 / (1.0 + 0.5 * z)
    r = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (
        -0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return np.where(x >= 0, 1.0 - r, r - 1.0).astype(np.float32)


# ============================================================== ops: numpy


class NumpyOps:
    nimi = "numpy"

    def param(self, a):
        return np.ascontiguousarray(np.asarray(a, dtype=np.float32))

    def tensor(self, a):
        return np.ascontiguousarray(np.asarray(a, dtype=np.float32))

    def numpy(self, x):
        return np.asarray(x, dtype=np.float32)

    # --- konvoluutio (ristikorrelaatio kuten torch.nn.functional.conv2d)
    def conv2d(self, x, w, b=None, pad=0, stride=1):
        if stride > 1:        # referenssi: lasketaan askeleella 1 ja harvennetaan (sama tulos)
            return self.conv2d(x, w, b, pad)[:, :, ::stride, ::stride]
        n, c, h, ww = x.shape
        o, ci, kh, kw = w.shape
        assert ci == c, (ci, c)
        if pad:
            x = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
        oh, ow = x.shape[2] - kh + 1, x.shape[3] - kw + 1
        if kh * kw >= 49:
            out = self._conv_fft(x, w, oh, ow)
        else:
            out = np.zeros((n, o, oh, ow), np.float32)
            for i in range(kh):
                for j in range(kw):
                    out += np.einsum("oc,nchw->nohw", w[:, :, i, j], x[:, :, i:i + oh, j:j + ow],
                                     optimize=True)
        if b is not None:
            out += b.reshape(1, -1, 1, 1)
        return out

    @staticmethod
    def _conv_fft(x, w, oh, ow):
        n, c, hp, wp = x.shape
        o, _, kh, kw = w.shape
        fh, fw = hp + kh - 1, wp + kw - 1
        X = np.fft.rfft2(x.astype(np.float64), s=(fh, fw))
        K = np.fft.rfft2(w[:, :, ::-1, ::-1].astype(np.float64), s=(fh, fw))
        Y = np.einsum("nchw,ochw->nohw", X, K, optimize=True)
        y = np.fft.irfft2(Y, s=(fh, fw))
        return y[:, :, kh - 1:kh - 1 + oh, kw - 1:kw - 1 + ow].astype(np.float32)

    def silu(self, x):
        return x * self.sigmoid(x)

    def sigmoid(self, x):
        return 0.5 * (1.0 + np.tanh(0.5 * x))

    def mish(self, x):
        return x * np.tanh(np.logaddexp(0.0, x))

    def group_norm(self, x, g, w, b, eps=1e-5):
        n, c, h, ww = x.shape
        t = x.reshape(n, g, -1).astype(np.float64)
        m = t.mean(-1, keepdims=True)
        v = t.var(-1, keepdims=True)
        t = ((t - m) / np.sqrt(v + eps)).reshape(n, c, h, ww).astype(np.float32)
        return t * w.reshape(1, -1, 1, 1) + b.reshape(1, -1, 1, 1)

    def pixel_shuffle(self, x, r):
        n, c, h, w = x.shape
        c2 = c // (r * r)
        return x.reshape(n, c2, r, r, h, w).transpose(0, 1, 4, 2, 5, 3).reshape(n, c2, h * r, w * r)

    def repeat_ch(self, x, r):
        return np.repeat(x, r, axis=1)

    def cat(self, xs):
        return np.concatenate(xs, axis=1)

    def max3(self, x):
        p = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)), mode="edge")
        h, w = x.shape[2], x.shape[3]
        return np.max(np.stack([p[:, :, i:i + h, j:j + w] for i in range(3) for j in range(3)]), axis=0)

    def min3(self, x):
        return -self.max3(-x)

    def matresize(self, x, wy, wx):
        """x (n,c,h,w), wy (oh,h), wx (ow,w) -> (n,c,oh,ow)"""
        t = np.matmul(x, wx.T)
        return np.matmul(wy, t)

    def clamp(self, x, lo, hi):
        return np.clip(x, lo, hi)

    def where(self, c, a, b):
        return np.where(c, a, b)

    def randn(self, shape, seed):
        return np.random.default_rng(seed).standard_normal(shape, dtype=np.float32)

    def to_int16(self, x):
        return np.rint(x).astype(np.int16)

    def cat_n(self, xs):
        return np.concatenate(xs, axis=0)

    def pad_reflect(self, x, p):
        return np.pad(x, ((0, 0), (0, 0), (p, p), (p, p)), mode="reflect")

    def pakkaa(self, y, u, v):
        """(n,1,h,w)+(n,1,h/2,w/2)x2 -> (n, L) int16, tasot peräkkäin (yuv420p10le)."""
        n = y.shape[0]
        return np.concatenate([y.reshape(n, -1), u.reshape(n, -1), v.reshape(n, -1)], axis=1)

    def from_uint8(self, a):
        """(n,h,w,3) uint8 -> (n,3,h,w) float 0..1"""
        return np.ascontiguousarray(a.transpose(0, 3, 1, 2)).astype(np.float32) / 255.0

    def half(self, x):
        return x

    def float(self, x):
        return x

    def zeros(self, shape, mallina):
        return np.zeros(shape, dtype=mallina.dtype)

    def relu(self, x):
        return np.maximum(x, 0)

    def leaky(self, x, a):
        return np.where(x >= 0, x, x * np.float32(a))

    def maxpool3s2(self, x):
        """3x3-maksimi, askel 2, täyte 1 (ResNetin alku)."""
        p = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)), constant_values=-np.inf)
        h, w = x.shape[2], x.shape[3]
        oh, ow = (h - 1) // 2 + 1, (w - 1) // 2 + 1
        return np.max(np.stack([p[:, :, i:i + 2 * oh:2, j:j + 2 * ow:2] for i in range(3) for j in range(3)]), axis=0)

    def lahin(self, x, h, w):
        """Lähimmän naapurin skaalaus kokoon (h, w) kuten F.interpolate(mode="nearest")."""
        iy = (np.arange(h) * x.shape[2] / h).astype(np.int64)
        ix = (np.arange(w) * x.shape[3] / w).astype(np.int64)
        return x[:, :, iy][:, :, :, ix]

    def linear(self, x, w, b=None):
        y = x @ w.T
        return y + b if b is not None else y

    def summa(self, x, akselit):
        return x.sum(axis=akselit)

    def conv_s2(self, x, w):
        """2x2-konvoluutio askeleella 2 (puolittaa koon), ilman biasia."""
        n, c, h, ww = x.shape
        t = x.reshape(n, c, h // 2, 2, ww // 2, 2)
        return np.einsum("ocij,nchiwj->nohw", w, t, optimize=True).astype(np.float32)

    def convT_s2(self, x, w):
        """2x2-transpoosikonvoluutio askeleella 2 (kaksinkertaistaa koon); w muotoa (in, out, 2, 2)."""
        n, c, h, ww = x.shape
        o = w.shape[1]
        return np.einsum("nchw,coij->nohiwj", x, w, optimize=True).reshape(n, o, h * 2, ww * 2).astype(np.float32)

    def pad_replicate(self, x, oikea, ala):
        return np.pad(x, ((0, 0), (0, 0), (0, ala), (0, oikea)), mode="edge")

    def taysi(self, mallina, shape, arvo):
        return np.full(shape, arvo, dtype=mallina.dtype)

    def lisaa_(self, a, b):
        a += b
        return a

    def group_norm_suuri(self, x, g, w, b, eps=1e-5):
        return self.group_norm(x, g, w, b, eps)

    def uint8_hwc(self, x):
        """(1,3,h,w) float 0..1 -> (h,w,3) uint8"""
        return (np.clip(x[0], 0, 1).transpose(1, 2, 0) * 255.0 + 0.5).astype(np.uint8)

    # --- kuvien korjaus ja väritys (korjaus.py, vari.py)
    def pad_ref(self, x, p):
        return np.pad(x, ((0, 0), (0, 0), (p, p), (p, p)), mode="reflect")

    def blur_s2(self, x):
        """Antialiasoitu alinäytteistys: [1,2,1]^2/16, heijastava reuna, joka toinen pikseli."""
        p = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)), mode="reflect")
        h, w = x.shape[2], x.shape[3]
        y = (p[:, :, 0:h] + 2 * p[:, :, 1:h + 1] + p[:, :, 2:h + 2]) / 4
        y = (y[:, :, :, 0:w] + 2 * y[:, :, :, 1:w + 1] + y[:, :, :, 2:w + 2]) / 4
        return np.ascontiguousarray(y[:, :, ::2, ::2])

    def convT3s2(self, x, w, b):
        """ConvTranspose2d(k=3, s=2, p=1, output_padding=1); w muotoa (in, out, 3, 3)."""
        n, c, h, ww = x.shape
        z = np.zeros((n, c, 2 * h + 2, 2 * ww + 2), x.dtype)
        z[:, :, 1:2 * h:2, 1:2 * ww:2] = x
        return self.conv2d(z, np.ascontiguousarray(w.transpose(1, 0, 2, 3)[:, :, ::-1, ::-1]), b, 0)

    def spektri(self, x, w, s, t):
        """LaMan FourierUnit: rfft2 -> 1x1-konvoluutio (re/im) -> BN -> ReLU -> irfft2."""
        n, c, h, ww = x.shape
        f = np.fft.rfft2(x.astype(np.float64), norm="ortho")
        z = np.stack([f.real, f.imag], axis=2).reshape(n, 2 * c, h, ww // 2 + 1).astype(np.float32)
        z = self.conv2d(z, w, None, 0) * s.reshape(1, -1, 1, 1) + t.reshape(1, -1, 1, 1)
        z = np.maximum(z, 0).reshape(n, -1, 2, h, ww // 2 + 1)
        return np.fft.irfft2(z[:, :, 0] + 1j * z[:, :, 1], s=(h, ww), norm="ortho").astype(np.float32)

    def dwconv7(self, x, w, b):
        n, c, h, ww = x.shape
        p = np.pad(x, ((0, 0), (0, 0), (3, 3), (3, 3)))
        out = np.zeros_like(x)
        for i in range(7):
            for j in range(7):
                out += p[:, :, i:i + h, j:j + ww] * w[:, 0, i, j].reshape(1, -1, 1, 1)
        return out + b.reshape(1, -1, 1, 1)

    def patch_conv(self, x, w, b, k):
        n, c, h, ww = x.shape
        t = x.reshape(n, c, h // k, k, ww // k, k)
        return (np.einsum("ocij,nchiwj->nohw", w, t, optimize=True) + b.reshape(1, -1, 1, 1)).astype(np.float32)

    def ln_c(self, x, w, b, eps=1e-6):
        u = x.mean(1, keepdims=True)
        s = ((x - u) ** 2).mean(1, keepdims=True)
        return (x - u) / np.sqrt(s + eps) * w.reshape(1, -1, 1, 1) + b.reshape(1, -1, 1, 1)

    def ln_last(self, x, w, b, eps):
        u = x.mean(-1, keepdims=True)
        s = ((x - u) ** 2).mean(-1, keepdims=True)
        return (x - u) / np.sqrt(s + eps) * w + b

    def gelu(self, x):
        return 0.5 * x * (1.0 + erf(x / math.sqrt(2.0)))

    def softmax(self, x):
        e = np.exp(x - x.max(-1, keepdims=True))
        return e / e.sum(-1, keepdims=True)

    def kanavat_viimeiseksi(self, x):
        return np.ascontiguousarray(x.transpose(0, 2, 3, 1))

    def kanavat_ensin(self, x):
        return np.ascontiguousarray(x.transpose(0, 3, 1, 2))

    def matmul(self, a, b):
        return np.matmul(a, b)

    def transpose_last(self, x):
        return np.swapaxes(x, -1, -2)

    def paat(self, x, h):
        """(L, E) -> (h, L, E/h)"""
        return np.ascontiguousarray(x.reshape(x.shape[0], h, -1).transpose(1, 0, 2))

    def paat_pois(self, x):
        """(h, L, d) -> (L, h*d)"""
        return np.ascontiguousarray(x.transpose(1, 0, 2).reshape(x.shape[1], -1))

    def sumennus2(self, x):
        """ReplicationPad2d((1,0,1,0)) + AvgPool2d(2, stride=1)."""
        p = np.pad(x, ((0, 0), (0, 0), (1, 0), (1, 0)), mode="edge")
        return (p[:, :, :-1, :-1] + p[:, :, 1:, :-1] + p[:, :, :-1, 1:] + p[:, :, 1:, 1:]) * 0.25

    # --- monikuvamalli (vsr.py)
    def warp(self, x, flow, reuna="zeros"):
        """F.grid_sample(bilinear, align_corners=True); virtaus (n,2,h,w) pikseleinä (x, y)."""
        n, c, h, w = x.shape
        gy, gx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
        px = gx[None] + flow[:, 0]
        py = gy[None] + flow[:, 1]
        if reuna == "border":
            px, py = np.clip(px, 0, w - 1), np.clip(py, 0, h - 1)
        x0, y0 = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
        lx, ly = (px - x0).astype(np.float32), (py - y0).astype(np.float32)
        out = np.zeros_like(x)
        for dy, wy in ((0, 1 - ly), (1, ly)):
            for dx, wx in ((0, 1 - lx), (1, lx)):
                xi, yi = x0 + dx, y0 + dy
                ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
                xc, yc = np.clip(xi, 0, w - 1), np.clip(yi, 0, h - 1)
                paino = (wy * wx * ok).astype(np.float32)
                for b in range(n):
                    out[b] += x[b][:, yc[b], xc[b]] * paino[b][None]
        return out

    def avgpool2(self, x):
        n, c, h, w = x.shape
        return x[:, :, :h // 2 * 2, :w // 2 * 2].reshape(n, c, h // 2, 2, w // 2, 2).mean((3, 5))

    def keskiabs(self, x):
        return float(np.abs(x).mean())


# ============================================================== ops: torch


class TorchOps:
    nimi = "torch"
    kaista_alkiot = 1 << 22      # GroupNormin kaistan koko suurilla kuvilla

    def __init__(self, laite="cuda", puolitarkkuus=True):
        import torch
        import torch.nn.functional as F
        self.torch, self.F = torch, F
        self.dev = torch.device(laite)
        self.ht = puolitarkkuus and self.dev.type == "cuda"
        self._gen = None

    def param(self, a):
        return self.torch.from_numpy(np.ascontiguousarray(np.asarray(a, np.float32))).to(self.dev)

    def tensor(self, a):
        return self.param(a)

    def numpy(self, x):
        return x.detach().float().cpu().numpy()

    def conv2d(self, x, w, b=None, pad=0, stride=1):
        if x.dtype != w.dtype:
            w = w.to(x.dtype)
            b = b.to(x.dtype) if b is not None else None
        return self.F.conv2d(x, w, b, padding=pad, stride=stride)

    def silu(self, x):
        return self.F.silu(x)

    def sigmoid(self, x):
        return self.torch.sigmoid(x)

    def mish(self, x):
        return self.F.mish(x)

    def group_norm(self, x, g, w, b, eps=1e-5):
        return self.F.group_norm(x.float(), g, w.float(), b.float(), eps).to(x.dtype)

    def pixel_shuffle(self, x, r):
        return self.F.pixel_shuffle(x, r)

    def repeat_ch(self, x, r):
        return self.torch.repeat_interleave(x, r, dim=1)

    def cat(self, xs):
        return self.torch.cat(xs, dim=1)

    def max3(self, x):
        p = self.F.pad(x, (1, 1, 1, 1), mode="replicate")
        return self.F.max_pool2d(p, 3, stride=1)

    def min3(self, x):
        return -self.max3(-x)

    def matresize(self, x, wy, wx):
        wy, wx = wy.to(x.dtype), wx.to(x.dtype)
        return self.torch.matmul(wy, self.torch.matmul(x, wx.transpose(0, 1)))

    def clamp(self, x, lo, hi):
        return self.torch.clamp(x, lo, hi)

    def where(self, c, a, b):
        return self.torch.where(c, a, b)

    def randn(self, shape, seed):
        if self._gen is None:
            self._gen = self.torch.Generator(device=self.dev)
        self._gen.manual_seed(int(seed))
        return self.torch.randn(shape, generator=self._gen, device=self.dev, dtype=self.torch.float32)

    def to_int16(self, x):
        return self.torch.round(x).to(self.torch.int16)

    def cat_n(self, xs):
        return self.torch.cat(xs, dim=0)

    def pad_reflect(self, x, p):
        return self.F.pad(x, (p, p, p, p), mode="reflect")

    def pakkaa(self, y, u, v):
        t = self.torch.cat([y.flatten(1), u.flatten(1), v.flatten(1)], dim=1)
        return t.cpu().numpy()

    def from_uint8(self, a):
        t = self.torch.from_numpy(a).to(self.dev, non_blocking=True)
        return t.permute(0, 3, 1, 2).float().div_(255.0).contiguous()

    def half(self, x):
        return x.half() if self.ht else x

    def float(self, x):
        return x.float()

    def zeros(self, shape, mallina):
        return self.torch.zeros(shape, dtype=mallina.dtype, device=mallina.device)

    def relu(self, x):
        return self.F.relu(x)

    def leaky(self, x, a):
        return self.F.leaky_relu(x, a)

    def maxpool3s2(self, x):
        return self.F.max_pool2d(x, 3, 2, 1)

    def lahin(self, x, h, w):
        return self.F.interpolate(x, size=[h, w], mode="nearest")

    def linear(self, x, w, b=None):
        return self.F.linear(x, w.to(x.dtype), b.to(x.dtype) if b is not None else None)

    def summa(self, x, akselit):
        return x.sum(dim=akselit)

    def conv_s2(self, x, w):
        return self.F.conv2d(x, w.to(x.dtype), None, stride=2)

    def convT_s2(self, x, w):
        return self.F.conv_transpose2d(x, w.to(x.dtype), None, stride=2)

    def pad_replicate(self, x, oikea, ala):
        return self.F.pad(x, (0, oikea, 0, ala), mode="replicate")

    def taysi(self, mallina, shape, arvo):
        return self.torch.full(shape, arvo, dtype=mallina.dtype, device=mallina.device)

    def lisaa_(self, a, b):
        return a.add_(b)

    def group_norm_suuri(self, x, g, w, b, eps=1e-5):
        """GroupNorm suurelle kuvalle kaistoittain (koko kuvan tilastot, muisti ei kasva).
        Tulos kirjoitetaan x:n päälle."""
        torch = self.torch
        n, c, h, ww = x.shape
        cg = c // g
        kaista = max(1, self.kaista_alkiot // max(1, cg * ww))
        for i in range(n):
            for gi in range(g):
                ka = slice(gi * cg, (gi + 1) * cg)
                maara = cg * h * ww
                summa = 0.0
                for r0 in range(0, h, kaista):
                    summa += float(x[i, ka, r0:r0 + kaista].sum(dtype=torch.float64))
                ka_arvo = summa / maara
                vari = 0.0
                for r0 in range(0, h, kaista):
                    vari += float(((x[i, ka, r0:r0 + kaista].float() - ka_arvo) ** 2).sum(dtype=torch.float64))
                kerroin = 1.0 / math.sqrt(vari / maara + eps)
                wk = w[ka].float().view(-1, 1, 1) * kerroin
                bk = b[ka].float().view(-1, 1, 1)
                for r0 in range(0, h, kaista):
                    osa = x[i, ka, r0:r0 + kaista]
                    osa.copy_(((osa.float() - ka_arvo) * wk + bk).to(x.dtype))
        return x

    def uint8_hwc(self, x):
        t = (self.torch.clamp(x[0].float(), 0, 1) * 255.0 + 0.5).to(self.torch.uint8)
        return t.permute(1, 2, 0).contiguous().cpu().numpy()

    # --- kuvien korjaus ja väritys (korjaus.py, vari.py)
    def pad_ref(self, x, p):
        return self.F.pad(x, (p, p, p, p), mode="reflect")

    def blur_s2(self, x):
        c = x.shape[1]
        k = self.torch.tensor([1.0, 2.0, 1.0], device=x.device)
        k = ((k[:, None] * k[None, :]) / 16.0).to(x.dtype).expand(c, 1, 3, 3)
        return self.F.conv2d(self.F.pad(x, (1, 1, 1, 1), mode="reflect"), k, stride=2, groups=c)

    def convT3s2(self, x, w, b):
        return self.F.conv_transpose2d(x, w.to(x.dtype), b.to(x.dtype) if b is not None else None,
                                       stride=2, padding=1, output_padding=1)

    def spektri(self, x, w, s, t):
        torch = self.torch
        n, c, h, ww = x.shape
        f = torch.fft.rfft2(x.float(), norm="ortho")
        z = torch.stack([f.real, f.imag], dim=2).reshape(n, 2 * c, h, ww // 2 + 1)
        z = self.F.conv2d(z, w.float()) * s.float().reshape(1, -1, 1, 1) + t.float().reshape(1, -1, 1, 1)
        z = torch.relu(z).reshape(n, -1, 2, h, ww // 2 + 1)
        return torch.fft.irfft2(torch.complex(z[:, :, 0], z[:, :, 1]), s=(h, ww), norm="ortho").to(x.dtype)

    def dwconv7(self, x, w, b):
        return self.F.conv2d(x, w.to(x.dtype), b.to(x.dtype), padding=3, groups=x.shape[1])

    def patch_conv(self, x, w, b, k):
        return self.F.conv2d(x, w.to(x.dtype), b.to(x.dtype), stride=k)

    def ln_c(self, x, w, b, eps=1e-6):
        xf = x.float()
        u = xf.mean(1, keepdim=True)
        s = (xf - u).pow(2).mean(1, keepdim=True)
        return ((xf - u) / self.torch.sqrt(s + eps) * w.float().reshape(1, -1, 1, 1)
                + b.float().reshape(1, -1, 1, 1)).to(x.dtype)

    def ln_last(self, x, w, b, eps):
        return self.F.layer_norm(x.float(), (x.shape[-1],), w.float(), b.float(), eps).to(x.dtype)

    def gelu(self, x):
        return self.F.gelu(x)

    def softmax(self, x):
        return self.torch.softmax(x.float(), dim=-1).to(x.dtype)

    def kanavat_viimeiseksi(self, x):
        return x.permute(0, 2, 3, 1).contiguous()

    def kanavat_ensin(self, x):
        return x.permute(0, 3, 1, 2).contiguous()

    def matmul(self, a, b):
        return self.torch.matmul(a, b.to(a.dtype))

    def transpose_last(self, x):
        return x.transpose(-1, -2)

    def paat(self, x, h):
        return x.reshape(x.shape[0], h, -1).transpose(0, 1).contiguous()

    def paat_pois(self, x):
        return x.transpose(0, 1).reshape(x.shape[1], -1)

    def sumennus2(self, x):
        return self.F.avg_pool2d(self.F.pad(x, (1, 0, 1, 0), mode="replicate"), 2, stride=1)

    # --- monikuvamalli (vsr.py)
    def warp(self, x, flow, reuna="zeros"):
        torch = self.torch
        n, c, h, w = x.shape
        flow = flow.float()
        avain = (h, w, flow.device)
        if getattr(self, "_ruudukko", (None,))[0] != avain:
            gy, gx = torch.meshgrid(torch.arange(h, device=flow.device, dtype=torch.float32),
                                    torch.arange(w, device=flow.device, dtype=torch.float32), indexing="ij")
            self._ruudukko = (avain, gx, gy)
        _, gx, gy = self._ruudukko
        px = (gx + flow[:, 0]) * (2.0 / max(w - 1, 1)) - 1.0
        py = (gy + flow[:, 1]) * (2.0 / max(h - 1, 1)) - 1.0
        g = torch.stack([px, py], dim=3)
        return self.F.grid_sample(x.float(), g, mode="bilinear", padding_mode=reuna, align_corners=True).to(x.dtype)

    def avgpool2(self, x):
        return self.F.avg_pool2d(x, 2, 2)

    def keskiabs(self, x):
        return float(x.float().abs().mean())


# ============================================================== skaalausmatriisit


def _bicubic(b, c):
    def k(x):
        x = np.abs(x)
        x2, x3 = x * x, x * x * x
        r = np.zeros_like(x)
        m1 = x < 1
        m2 = (x >= 1) & (x < 2)
        r[m1] = ((12 - 9 * b - 6 * c) * x3[m1] + (-18 + 12 * b + 6 * c) * x2[m1] + (6 - 2 * b)) / 6
        r[m2] = ((-b - 6 * c) * x3[m2] + (6 * b + 30 * c) * x2[m2] + (-12 * b - 48 * c) * x[m2]
                 + (8 * b + 24 * c)) / 6
        return r
    return k, 2.0


def _lanczos(a):
    def k(x):
        x = np.abs(x)
        r = np.sinc(x) * np.sinc(x / a)
        r[x >= a] = 0
        return r
    return k, float(a)


SUOTIMET = {
    "catmullrom": _bicubic(0.0, 0.5),
    "mitchell": _bicubic(1 / 3, 1 / 3),
    "bspline": _bicubic(1.0, 0.0),
    "hermite": _bicubic(0.0, 0.0),
    "lanczos3": _lanczos(3),
}


def skaalausmatriisi(n_in, n_out, suodin="catmullrom", siirto=0.0):
    """Tiheä (n_out, n_in) -painomatriisi. Pienennettäessä ydin levitetään (antialias).
    Reunoilla pikselit toistetaan. `siirto` siirtää näytteenottoa lähteen pikseleinä."""
    k, tuki = SUOTIMET[suodin]
    s = n_out / n_in
    leveys = 1.0 / s if s < 1 else 1.0
    x = (np.arange(n_out) + 0.5) / s - 0.5 + siirto
    vasen = np.floor(x - tuki * leveys).astype(int) + 1
    n_tap = int(math.ceil(2 * tuki * leveys)) + 1
    idx = vasen[:, None] + np.arange(n_tap)[None, :]
    w = k((idx - x[:, None]) / leveys)
    w = w / w.sum(1, keepdims=True)
    W = np.zeros((n_out, n_in), np.float64)
    np.add.at(W, (np.repeat(np.arange(n_out), n_tap), np.clip(idx, 0, n_in - 1).ravel()), w.ravel())
    return W.astype(np.float32)


def bilineaari_matriisi(n, kerroin):
    """F.interpolate(mode="bilinear", align_corners=False) yhdessä suunnassa kertoimella 2 tai 0.5."""
    if kerroin == 2:
        W = np.zeros((2 * n, n), np.float32)
        for i in range(n):
            W[2 * i, i] += 0.75
            W[2 * i, max(i - 1, 0)] += 0.25
            W[2 * i + 1, i] += 0.75
            W[2 * i + 1, min(i + 1, n - 1)] += 0.25
        return W
    W = np.zeros((n // 2, n), np.float32)
    for i in range(n // 2):
        W[i, 2 * i] = W[i, 2 * i + 1] = 0.5
    return W


def alinaytteistys_matriisit(h, w):
    """4:2:0-värien alinäytteistys: vaakasuunnassa vasemmalle sijoitettu [1,2,1]/4,
    pystysuunnassa kahden rivin keskiarvo (MPEG-2/H.264/HEVC-oletussijainti)."""
    wy = np.zeros((h // 2, h), np.float32)
    for i in range(h // 2):
        wy[i, 2 * i] = wy[i, 2 * i + 1] = 0.5
    wx = np.zeros((w // 2, w), np.float32)
    for i in range(w // 2):
        for d, p in ((-1, 0.25), (0, 0.5), (1, 0.25)):
            wx[i, min(max(2 * i + d, 0), w - 1)] += p
    return wy, wx


# ============================================================== AI-mallit


def _fuse_conv3xc(sd, p):
    """SPANin Conv3XC: 1x1 -> 3x3 -> 1x1 + 1x1-ohitus yhdistetään yhdeksi 3x3-konvoluutioksi
    (sama laskutoimitus kuin alkuperäisen update_params)."""
    w1, b1 = sd[p + ".conv.0.weight"], sd[p + ".conv.0.bias"]
    w2, b2 = sd[p + ".conv.1.weight"], sd[p + ".conv.1.bias"]
    w3, b3 = sd[p + ".conv.2.weight"], sd[p + ".conv.2.bias"]
    a = w1[:, :, 0, 0].astype(np.float64)              # (g, cin)
    k = np.einsum("mgij,gc->mcij", w2.astype(np.float64), a)
    c3 = w3[:, :, 0, 0].astype(np.float64)              # (cout, m)
    W = np.einsum("om,mcij->ocij", c3, k)
    bb = (w2.astype(np.float64) * b1.reshape(1, -1, 1, 1)).sum((1, 2, 3)) + b2
    B = c3 @ bb + b3
    W[:, :, 1, 1] += sd[p + ".sk.weight"][:, :, 0, 0]
    B = B + sd[p + ".sk.bias"]
    return W.astype(np.float32), B.astype(np.float32)


# Suuret kuvat lasketaan paloissa. Palojen reunoille otetaan mukaan mallin näkökentän verran
# naapuripikseleitä, ja tulos rajataan, joten tulos on sama kuin koko kuvan kerralla laskettuna.
PALA = 512                  # palan koko lähteen pikseleinä
PALOITTAIN_YLI = 1_000_000  # paloitellaan, kun kuvassa on enemmän pikseleitä


def paloittain(ops, fn, syotteet, reuna, skaala, pala=PALA, palaute=None, osuus=(0.0, 1.0)):
    """Laskee fn(*syotteet) paloissa (kaikilla syötteillä sama korkeus ja leveys). Palan ympärille
    otetaan `reuna` pikseliä naapureita, ja tulos rajataan, joten saumoja ei synny."""
    h, w = syotteet[0].shape[2], syotteet[0].shape[3]
    palat = [(y0, x0) for y0 in range(0, h, pala) for x0 in range(0, w, pala)]
    ulos = None
    for i, (y0, x0) in enumerate(palat):
        y1, x1 = min(h, y0 + pala), min(w, x0 + pala)
        a0, b0 = max(0, y0 - reuna), max(0, x0 - reuna)
        a1, b1 = min(h, y1 + reuna), min(w, x1 + reuna)
        r = fn(*[s_[:, :, a0:a1, b0:b1] for s_ in syotteet])
        if ulos is None:
            ulos = ops.zeros((r.shape[0], r.shape[1], h * skaala, w * skaala), r)
        s = skaala
        ulos[:, :, y0 * s:y1 * s, x0 * s:x1 * s] = r[:, :, (y0 - a0) * s:(y1 - a0) * s, (x0 - b0) * s:(x1 - b0) * s]
        del r
        if palaute:
            palaute(osuus[0] + (osuus[1] - osuus[0]) * (i + 1) / len(palat))
    return ulos


class Malli:
    """SPAN- tai RealPLKSR-malli tilasanakirjasta. Arkkitehtuuri tunnistetaan avaimista.

    palaute: valinnainen funktio(osuus 0..1), jota kutsutaan paloittain laskettaessa.
    """

    palaute = None
    pala = PALA
    raja = PALOITTAIN_YLI

    def __init__(self, ops, sd):
        self.ops = ops
        if "conv_1.sk.weight" in sd:
            self._span(sd)
        elif "feats.1.channel_mixer.0.weight" in sd:
            self._realplksr(sd)
        else:
            raise ValueError("Tuntematon malliarkkitehtuuri")

    # ---------------------------------------------------------- SPAN
    def _span(self, sd):
        o = self.ops
        self.tyyppi = "SPAN"
        if "no_norm" not in sd:
            raise ValueError("SPAN-malli normalisoinnilla ei ole tuettu")
        P = {}
        nimet = ["conv_1", "conv_2"] + [f"block_{i}.c{j}_r" for i in range(1, 7) for j in (1, 2, 3)]
        for n in nimet:
            W, B = _fuse_conv3xc(sd, n)     # tiedoston eval_conv-painot voivat olla vanhentuneita
            P[n] = (o.param(W), o.param(B))
        P["conv_cat"] = (o.param(sd["conv_cat.weight"]), o.param(sd["conv_cat.bias"]))
        P["up"] = (o.param(sd["upsampler.0.weight"]), o.param(sd["upsampler.0.bias"]))
        self.skaala = int(round(math.sqrt(sd["upsampler.0.weight"].shape[0] / 3)))
        self.P = P

    def _span_fwd(self, x, raja):
        if x.shape[0] == 1 and x.shape[2] * x.shape[3] > raja:
            # näkökenttä 21 px: conv_1 + 6 lohkoa x 3 + conv_2 + upsampler (3x3)
            return self._paloittain(self._span_koko, [x], reuna=24, skaala=self.skaala,
                                    osuus=(0.0, 1.0))
        return self._span_koko(x)

    def _span_koko(self, x):
        o, P = self.ops, self.P

        def c(n, t, pad=1):
            return o.conv2d(t, P[n][0], P[n][1], pad)

        feat = c("conv_1", x)
        b = feat
        talteen = {}
        for i in range(1, 7):
            p = f"block_{i}"
            # Alkuperäisessä SiLU(inplace=True) muuttaa c1-tuloksen paikallaan, joten
            # talteen otettava välitulos on aktivoitu (tarkistettu mallin ONNX-viennistä).
            o1 = o.silu(c(p + ".c1_r", b))
            o3 = c(p + ".c3_r", o.silu(c(p + ".c2_r", o1)))
            b = (o3 + b) * (o.sigmoid(o3) - 0.5)
            if i == 1:
                talteen["b1"] = b
            if i == 6:
                talteen["b5_2"] = o1
        b6 = c("conv_2", b)
        out = c("conv_cat", o.cat([feat, b6, talteen["b1"], talteen["b5_2"]]), pad=0)
        return o.pixel_shuffle(c("up", out), self.skaala)

    # ---------------------------------------------------------- RealPLKSR
    def _realplksr(self, sd):
        o = self.ops
        self.tyyppi = "RealPLKSR"
        if "to_img.init_pos" in sd or "feats.1.layer_norm.bias" in sd:
            raise ValueError("RealPLKSR DySample/LayerNorm -versio ei ole tuettu")
        idx = sorted({int(k.split(".")[1]) for k in sd if k.startswith("feats.")})
        self.n_lohkot = len([i for i in idx if f"feats.{i}.channel_mixer.0.weight" in sd])
        viim = idx[-1]
        self.skaala = int(round(math.sqrt(sd[f"feats.{viim}.weight"].shape[0] / 3)))
        P = {"alku": (o.param(sd["feats.0.weight"]), o.param(sd["feats.0.bias"])),
             "loppu": (o.param(sd[f"feats.{viim}.weight"]), o.param(sd[f"feats.{viim}.bias"]))}
        self.lohkot = []
        for i in range(1, self.n_lohkot + 1):
            p = f"feats.{i}."
            L = {k: (o.param(sd[p + k + ".weight"]), o.param(sd[p + k + ".bias"]))
                 for k in ("channel_mixer.0", "channel_mixer.2", "lk.conv", "attn.f.0", "refine", "norm")}
            self.lohkot.append(L)
        self.pdim = sd["feats.1.lk.conv.weight"].shape[0]
        self.lk_pad = sd["feats.1.lk.conv.weight"].shape[2] // 2
        self.ryhmat = 4
        self.P = P

    def _plksr_lohko(self, L, f):
        """Lohko ennen GroupNormia (GroupNorm tarvitsee koko kuvan tilastot)."""
        o = self.ops
        t = o.conv2d(f, *L["channel_mixer.0"], 1)
        t = o.mish(t)
        t = o.conv2d(t, *L["channel_mixer.2"], 1)
        t = o.cat([o.conv2d(t[:, :self.pdim], *L["lk.conv"], self.lk_pad), t[:, self.pdim:]])
        t = t * o.sigmoid(o.conv2d(t, *L["attn.f.0"], 1))
        return o.conv2d(t, *L["refine"], 0)

    def _plksr_loppu(self, f, x):
        o = self.ops
        f = o.conv2d(f, *self.P["loppu"], 1) + o.repeat_ch(x, self.skaala * self.skaala)
        return o.pixel_shuffle(f, self.skaala)

    def _plksr_fwd(self, x, raja):
        o, P = self.ops, self.P
        if x.shape[0] == 1 and x.shape[2] * x.shape[3] > raja:
            return self._plksr_paloittain(x)
        f = o.conv2d(x, *P["alku"], 1)
        for L in self.lohkot:
            t = self._plksr_lohko(L, f)
            t = o.group_norm(t, self.ryhmat, *L["norm"])
            f = t + f
        return self._plksr_loppu(f, x)

    def _plksr_paloittain(self, x):
        """Lohko kerrallaan: jokainen lohko lasketaan paloissa (näkökenttä 11 px), GroupNorm koko
        kuvan tilastoilla. Tulos vastaa koko kuvan laskentaa."""
        o, P = self.ops, self.P
        n = len(self.lohkot) + 2
        f = self._paloittain(lambda z: o.conv2d(z, *P["alku"], 1), [x], 2, 1, (0, 1 / n))
        for i, L in enumerate(self.lohkot, 1):
            t = self._paloittain(lambda z, L=L: self._plksr_lohko(L, z), [f], 12, 1, (i / n, (i + 1) / n))
            t = o.group_norm_suuri(t, self.ryhmat, *L["norm"])
            f = o.lisaa_(f, t)
            del t
        return self._paloittain(self._plksr_loppu, [f, x], 2, self.skaala, ((n - 1) / n, 1.0))

    def _paloittain(self, fn, syotteet, reuna, skaala, osuus):
        return paloittain(self.ops, fn, syotteet, reuna, skaala, self.pala, self.palaute, osuus)

    def __call__(self, x, raja=None):
        """raja: paloitellaan, kun pikseleitä on enemmän (oletus self.raja)."""
        raja = self.raja if raja is None else raja
        return self._span_fwd(x, raja) if self.tyyppi == "SPAN" else self._plksr_fwd(x, raja)

    puoli = False

    def puolita(self):
        """Muunna painot fp16:ksi (vain torch). GroupNorm lasketaan silti fp32:na."""
        o = self.ops
        if not getattr(o, "ht", False):
            return
        self.puoli = True

        def h(t):
            return (t[0].half(), t[1].half())
        if self.tyyppi == "SPAN":
            self.P = {k: h(v) for k, v in self.P.items()}
        else:
            self.P = {k: h(v) for k, v in self.P.items()}
            self.lohkot = [{k: (v if k == "norm" else h(v)) for k, v in L.items()} for L in self.lohkot]


class Kohinanpoisto:
    """DRUNet (Kai Zhang ym., KAIR, MIT): kohinan- ja pakkausjälkien poisto ennen skaalausta.
    Voimakkuus annetaan kohinatasona (sigma, 0..255-asteikolla), joten siivousta voi säätää."""

    def __init__(self, ops, sd):
        self.ops = o = ops
        self.puoli = False
        self.P = {k: o.param(v) for k, v in sd.items()}
        if "m_head.weight" not in sd or sd["m_head.weight"].shape[1] != 4:
            raise ValueError("Tuntematon kohinanpoistomalli")

    def puolita(self):
        if getattr(self.ops, "ht", False):
            self.P = {k: v.half() for k, v in self.P.items()}
            self.puoli = True

    def _res(self, x, p):
        o, P = self.ops, self.P
        return x + o.conv2d(o.relu(o.conv2d(x, P[p + ".res.0.weight"], None, 1)), P[p + ".res.2.weight"], None, 1)

    palaute = None

    def __call__(self, x, sigma, raja=None):
        """x (n,3,h,w) 0..1 -> siivottu kuva. Suuret kuvat paloissa (reuna 48 px; verkon näkökenttä on
        laaja, mutta kauempana olevien pikselien vaikutus on häviävän pieni)."""
        raja = PALOITTAIN_YLI if raja is None else raja
        if x.shape[0] == 1 and x.shape[2] * x.shape[3] > raja:
            return paloittain(self.ops, lambda z: self._laske(z, sigma), [x], 48, 1, PALA, self.palaute)
        return self._laske(x, sigma)

    def _laske(self, x, sigma):
        """Kuva täydennetään 8:lla jaolliseksi kuten alkuperäisessä toteutuksessa."""
        o, P = self.ops, self.P
        n, _, h, w = x.shape
        ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
        xp = o.pad_replicate(x, pw, ph) if (ph or pw) else x
        if self.puoli:
            xp = o.half(xp)
        kartta = o.taysi(xp, (n, 1, h + ph, w + pw), float(sigma) / 255.0)
        x0 = o.cat([xp, kartta])
        x1 = o.conv2d(x0, P["m_head.weight"], None, 1)
        x2 = self._taso(x1, 1)
        x3 = self._taso(x2, 2)
        x4 = self._taso(x3, 3)
        y = x4
        for j in range(4):
            y = self._res(y, f"m_body.{j}")
        y = self._ylos(y + x4, 3)
        y = self._ylos(y + x3, 2)
        y = self._ylos(y + x2, 1)
        y = o.conv2d(y + x1, P["m_tail.weight"], None, 1)
        y = o.float(y)
        return y[:, :, :h, :w]

    def _taso(self, y, i):
        for j in range(4):
            y = self._res(y, f"m_down{i}.{j}")
        return self.ops.conv_s2(y, self.P[f"m_down{i}.4.weight"])

    def _ylos(self, y, i):
        y = self.ops.convT_s2(y, self.P[f"m_up{i}.0.weight"])
        for j in range(1, 5):
            y = self._res(y, f"m_up{i}.{j}")
        return y


# ============================================================== käsittelyketju

# BT.601 (SD) ja BT.709 luma-kertoimet
KR_KB = {"bt601": (0.299, 0.114), "bt709": (0.2126, 0.0722)}


class Ketju:
    """Yhden asetuksen koko käsittelyketju lähteen RGB-kuvasta valmiiksi YUV 4:2:0 10-bit -kuvaksi.

    asetus: dict
      malli        – Malli tai None
      ai           – AI-tuloksen osuus 0..1 (loput tavallista skaalausta)
      halo         – halonpoiston voimakkuus 0..1 (0 = pois)
      halo_r       – halon leveys lähteen pikseleinä (DeHalo_alpha rx/ry)
      rae          – filmirakeen voimakkuus 0..1
      rae_koko     – rakeen koko (1080p-kuvan pikseleinä)
      kohina       – kohinan- ja pakkausjälkien poisto ennen mallia 0..1.5 (DRUNet, sigma = 20 x kohina)
      kohinamalli  – Kohinanpoisto tai None
    Lähteen pikseleitä ei venytetä ennen mallia: malli saa alkuperäiset pikselit ja
    oikea kuvasuhde tehdään loppuskaalauksessa (testattu: terävämpi kuin esivenytys).
    """

    def __init__(self, ops, h, w, oh, ow, asetus, matriisi="bt601", yuv=True):
        self.o = o = ops
        self.h, self.w, self.oh, self.ow = h, w, oh, ow
        self.a = dict(asetus)
        self.malli = self.a.get("malli")
        self.kohinamalli = self.a.get("kohinamalli") if float(self.a.get("kohina", 0) or 0) > 0 else None
        self.kr, self.kb = KR_KB[matriisi]
        self.palaute = None                 # kuville: funktio(osuus 0..1)
        self.pala_raja = None               # kuville: paloittelu jo pienemmästä koosta (edistyminen, keskeytys)
        s = self.malli.skaala if self.malli else 1
        # kierrokset: 4x kuvalle malli ajetaan kahdesti (2x + 2x)
        self.kierrokset = max(1, int(self.a.get("kierrokset", 1))) if self.malli else 1
        self.koot = [(h * s ** i, w * s ** i) for i in range(self.kierrokset + 1)]
        self.mh, self.mw = self.koot[-1]
        # perinteinen skaalaus kierroksen lähteestä mallin resoluutioon (AI-osuuden sekoitusta varten)
        self.W_m = []
        if self.malli and float(self.a.get("ai", 1.0)) < 1.0:
            for (ah, aw), (bh, bw) in zip(self.koot, self.koot[1:]):
                self.W_m.append((o.param(skaalausmatriisi(ah, bh, "catmullrom")),
                                 o.param(skaalausmatriisi(aw, bw, "catmullrom"))))
        # loppuskaalaus näytön kokoon (ohitetaan, jos koko on jo oikea)
        self.Wy_o = self.Wx_o = None
        if (oh, ow) != (self.mh, self.mw):
            self.Wy_o = o.param(skaalausmatriisi(self.mh, oh, "catmullrom" if oh >= self.mh else "mitchell"))
            self.Wx_o = o.param(skaalausmatriisi(self.mw, ow, "catmullrom" if ow >= self.mw else "mitchell"))
        # halonpoisto
        if self.a.get("halo", 0) > 0:
            r = float(self.a.get("halo_r", 2.0))
            hh, hw = max(8, int(round(h / r / 4)) * 4), max(8, int(round(w / r / 4)) * 4)
            self.Wy_hd = o.param(skaalausmatriisi(h, hh, "mitchell"))
            self.Wx_hd = o.param(skaalausmatriisi(w, hw, "mitchell"))
            self.Wy_hu = o.param(skaalausmatriisi(hh, h, "bspline"))
            self.Wx_hu = o.param(skaalausmatriisi(hw, w, "bspline"))
        # filmirae: gaussinen sumennus antaa rakeelle koon
        if self.a.get("rae", 0) > 0:
            sigma = float(self.a.get("rae_koko", 0.7)) * oh / 1080.0
            r = max(1, int(math.ceil(sigma * 2.5)))
            g = np.exp(-0.5 * (np.arange(-r, r + 1) / max(sigma, 1e-3)) ** 2)
            g /= g.sum()
            self.rae_norm = float(1.0 / (g ** 2).sum())   # sumennetun kohinan keskihajonta takaisin 1:ksi
            self.Gy = o.param(g.reshape(1, 1, -1, 1).astype(np.float32))
            self.Gx = o.param(g.reshape(1, 1, 1, -1).astype(np.float32))
            self.rae_pad = r
        # YUV 4:2:0 (vain video)
        if yuv:
            self.Cy, self.Cx = [o.param(m) for m in alinaytteistys_matriisit(oh, ow)]

    # ---------------------------------------------------------- vaiheet
    def luma(self, x, kr=None, kb=None):
        kr = self.kr if kr is None else kr
        kb = self.kb if kb is None else kb
        return x[:, 0:1] * kr + x[:, 1:2] * (1 - kr - kb) + x[:, 2:3] * kb

    def halonpoisto(self, x):
        """DeHalo_alpha (Didée) lumalle. Muutos lisätään kaikkiin RGB-kanaviin, joten sävy säilyy."""
        o, a = self.o, self.a
        y = self.luma(x)
        halos = o.matresize(o.matresize(y, self.Wy_hd, self.Wx_hd), self.Wy_hu, self.Wx_hu)
        are = o.max3(y) - o.min3(y)
        ugly = o.max3(halos) - o.min3(halos)
        lows, highs = float(a.get("halo_low", 50)), float(a.get("halo_high", 50))
        so = ((are - ugly) / (are + 1e-6) * 255.0 - lows) * ((are * 255.0 + 256.0) / 512.0 + highs / 100.0)
        so = o.clamp(so, 0.0, 255.0) / 255.0
        lets = halos + (y - halos) * so
        remove = o.clamp(y, o.min3(lets), o.max3(lets))
        d = y - remove
        vahvuus = float(a.get("halo", 1.0))
        tumma = float(a.get("halo_tumma", 0.5)) * vahvuus
        uusi = y - o.where(y < remove, d * tumma, d * vahvuus)
        return x + (uusi - y)

    def rae(self, rgb, ensimmainen):
        """Filmirae lumaan. Jokaisella kuvalla oma siemen (kuvan numero), joten tulos on sama
        vaikka käsittely keskeytettäisiin ja jatkettaisiin."""
        o, a = self.o, self.a
        n = rgb.shape[0]
        z = o.cat_n([o.randn((1, 1, self.oh, self.ow), 7919 + ensimmainen + i) for i in range(n)])
        p = self.rae_pad
        z = o.conv2d(o.conv2d(o.pad_reflect(z, p), self.Gy), self.Gx) * self.rae_norm
        y = o.clamp(self.luma(rgb, 0.2126, 0.0722), 0.0, 1.0)
        # eniten rakeita keskisävyissä, vähemmän syvissä varjoissa ja kirkkaissa kohdissa
        paino = 0.35 + 0.65 * o.clamp(4.0 * y * (1.0 - y), 0.0, 1.0)
        amp = float(a.get("rae", 0)) * 3.0 / 255.0
        return rgb + z * (paino * amp)

    def yuv420p10(self, rgb):
        """RGB 0..1 -> rajattu BT.709 YUV 4:2:0, 10 bittiä. Palauttaa (Y, U, V) int16-taulukot."""
        o = self.o
        rgb = o.clamp(rgb, 0.0, 1.0)
        kr, kb = 0.2126, 0.0722
        y = self.luma(rgb, kr, kb)
        cb = o.matresize((rgb[:, 2:3] - y) / (2 * (1 - kb)), self.Cy, self.Cx)
        cr = o.matresize((rgb[:, 0:1] - y) / (2 * (1 - kr)), self.Cy, self.Cx)
        return (o.to_int16(64.0 + 876.0 * y), o.to_int16(512.0 + 896.0 * cb),
                o.to_int16(512.0 + 896.0 * cr))

    def rgb_lopullinen(self, x, ensimmainen=0):
        """Lähteen RGB (n,3,h,w) 0..1 -> näytön kokoinen RGB (ennen YUV-muunnosta)."""
        o, a = self.o, self.a
        if self.kohinamalli is not None:
            x = self.kohinamalli(o.clamp(x, 0.0, 1.0), 20.0 * float(a["kohina"]), self.pala_raja)
        if a.get("halo", 0) > 0:
            x = self.halonpoisto(x)
        if self.malli is not None:
            osuus = float(a.get("ai", 1.0))
            # edistyminen painotetaan pikselimäärällä (toinen kierros on 4 kertaa suurempi)
            painot = [kh * kw for kh, kw in self.koot[:-1]]
            kertyma = 0.0
            for k in range(self.kierrokset):
                if self.palaute:
                    alku, osa = kertyma / sum(painot), painot[k] / sum(painot)
                    self.palaute(alku)
                    self.malli.palaute = lambda f, alku=alku, osa=osa: self.palaute(alku + osa * f)
                kertyma += painot[k]
                xm = o.clamp(x, 0.0, 1.0)
                try:
                    ai = o.float(self.malli(o.half(xm) if self.malli.puoli else xm, self.pala_raja))
                finally:
                    self.malli.palaute = None
                if osuus < 1.0:
                    perus = o.matresize(x, *self.W_m[k])
                    ai = perus + (ai - perus) * osuus
                    del perus
                x = ai
                del ai
        if self.Wy_o is not None:
            x = o.matresize(x, self.Wy_o, self.Wx_o)
        if a.get("rae", 0) > 0:
            x = self.rae(x, ensimmainen)
        return x

    def __call__(self, x, ensimmainen=0):
        return self.yuv420p10(self.rgb_lopullinen(x, ensimmainen))
