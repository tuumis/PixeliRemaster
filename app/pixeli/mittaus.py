"""PixeliRemaster – lähteen laadun mittaus ja siihen sovitetut säädöt.

Mitataan muutamasta kuvasta:
  kohina      – kohinan keskihajonta (0..255), Immerkærin menetelmä tasaisilta alueilta
  lohkot      – pakkauksen 8x8-lohkorajojen voimakkuus suhteessa muuhun kuvaan (1.0 = ei lohkoja)
  teravyys    – hienojen yksityiskohtien osuus keskikarkeisiin nähden (pehmeä lähde = pieni)

Säädöt valitaan niin, ettei tulos muutu muoviseksi: kohinanpoisto vain sen verran kuin
lähteessä on kohinaa ja pakkausjälkiä (ja maltillisesti), tekoälyn osuutta pienennetään hyvin
pehmeissä lähteissä (niissä malli keksii eniten) ja filmiraetta lisätään sitä enemmän, mitä
enemmän siivotaan.
"""
import math

import numpy as np

from . import moottori as m

_LAPL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)


def _luma(rgb_u8):
    a = rgb_u8.astype(np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _konv3(y, k):
    h, w = y.shape
    t = np.zeros((h - 2, w - 2), np.float32)
    for i in range(3):
        for j in range(3):
            if k[i, j]:
                t += k[i, j] * y[i:i + h - 2, j:j + w - 2]
    return t


def _sumennus(y, sigma):
    r = max(1, int(math.ceil(sigma * 3)))
    g = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    g /= g.sum()
    p = np.pad(y, r, mode="reflect")
    t = sum(g[i] * p[i:i + y.shape[0], :] for i in range(2 * r + 1))
    return sum(g[i] * t[:, i:i + y.shape[1]] for i in range(2 * r + 1))


def _kohina(y):
    """Kohinan keskihajonta: Laplace-suodin tasaisilla alueilla, vankka mediaaniarvio."""
    lap = np.abs(_konv3(y, _LAPL))
    gy, gx = np.abs(np.diff(y, axis=0))[:, :-1], np.abs(np.diff(y, axis=1))[:-1, :]
    grad = (gx + gy)[:lap.shape[0], :lap.shape[1]]
    tasainen = grad < np.percentile(grad, 40)
    if tasainen.sum() < 500:
        return 0.0
    return float(1.4826 * np.median(lap[tasainen]) / 6.0)


def _lohkot(y):
    """8x8-lohkorajojen erottuvuus: vaaka- ja pystyerot lohkorajoilla / muualla (paras siirtymä)."""
    tulos = []
    for d in (np.abs(np.diff(y, axis=1)).mean(axis=0), np.abs(np.diff(y, axis=0)).mean(axis=1)):
        n = len(d) // 8 * 8
        if n < 16:
            continue
        r = d[:n].reshape(-1, 8).mean(axis=0)
        tulos.append(float(r.max() / max(1e-3, np.delete(r, r.argmax()).mean())))
    return float(np.mean(tulos)) if tulos else 1.0


def _teravyys(y):
    s1, s3 = _sumennus(y, 1.0), _sumennus(y, 3.0)
    hieno = float(np.abs(y - s1).mean())
    karkea = float(np.abs(s1 - s3).mean())
    return hieno / max(karkea, 1e-3)


def mittaa(kuvat):
    """kuvat: lista (1,h,w,3) tai (h,w,3) uint8 -taulukoita. Palauttaa mittaukset mediaaneina."""
    k, l, t = [], [], []
    for a in kuvat:
        a = np.asarray(a)
        a = a[0] if a.ndim == 4 else a
        h, w, _ = a.shape
        if h * w > 1_500_000:                       # iso kuva: keskeltä 1200x1200 (sama mittakaava)
            y0, x0 = max(0, h // 2 - 600), max(0, w // 2 - 600)
            a = a[y0:y0 + 1200, x0:x0 + 1200]
        y = _luma(a)
        if y.shape[0] < 32 or y.shape[1] < 32:
            continue
        k.append(_kohina(y))
        l.append(_lohkot(y))
        t.append(_teravyys(y))
    if not k:
        return {"kohina": 0.0, "lohkot": 1.0, "teravyys": 0.5}
    return {"kohina": round(float(np.median(k)), 2), "lohkot": round(float(np.median(l)), 3),
            "teravyys": round(float(np.median(t)), 3)}


def oletukset(mitta, kuva=False):
    """Mittauksiin sovitetut säädöt jokaiselle asetukselle: {"2": {"ai":…, "rae":…, "kohina":…}, …}"""
    kohina, lohkot, ter = mitta["kohina"], mitta["lohkot"], mitta["teravyys"]
    # siivouksen tarve: kohina + selvä lohkoisuus, maltillisesti (sigma enintään 4 ~ kohina 0.2).
    # Vertailuissa (XviD, YouTube-H.264) voimakkaampi siivous vei kasvoista ja kankaista yksityiskohdat.
    sigma = max(0.0, kohina - 1.2) * 1.0 + max(0.0, lohkot - 1.15) * 10.0
    sigma = min(4.0, sigma)
    siivous = round(sigma / 20.0 / 0.05) * 0.05
    # hyvin pehmeässä lähteessä malli keksii eniten: tekoälyn osuutta pienennetään hieman
    pehmeys = min(1.0, max(0.0, (0.45 - ter) / 0.2))           # 0 = terävä, 1 = hyvin pehmeä
    tulos = {}
    for n, a in m.ASETUKSET.items():
        if not a.get("malli"):
            continue
        s = {}
        if a["malli"] == "ani":
            s["kohina"] = round(min(0.3, siivous * 0.6) / 0.05) * 0.05
            s["ai"] = 1.0
            s["rae"] = 0.0
        else:
            perus_ai = float(a.get("ai", 1.0))
            s["kohina"] = siivous
            s["ai"] = round(max(0.6, perus_ai - (0.1 * pehmeys if a["malli"] == "live" else 0.0)) / 0.05) * 0.05
            perus_rae = 0.0 if kuva else float(a.get("rae", 0.0))
            # raetta takaisin suhteessa siivoukseen, ettei pinta muutu vahamaiseksi
            s["rae"] = round(min(0.7, perus_rae + siivous * (0.5 if not kuva else 0.3)) / 0.05) * 0.05
        tulos[str(n)] = {k: round(v, 2) for k, v in s.items()}
    return tulos


def kuvaus(mitta):
    """Sanalliset luokat käyttöliittymään."""
    k, l, t = mitta["kohina"], mitta["lohkot"], mitta["teravyys"]
    return {"kohina": "vahva" if k > 4 else "kohtalainen" if k > 2 else "vahainen",
            "lohkot": "vahvat" if l > 1.25 else "selvat" if l > 1.08 else "vahaiset",
            "teravyys": "pehmea" if t < 0.35 else "kohtalainen" if t < 0.55 else "terava"}
