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


def mittaa_yksi(a):
    """Yhden kuvan mittaukset tai None (musta, häivytys tai liian pieni kuva ei kerro laadusta)."""
    a = np.asarray(a)
    a = a[0] if a.ndim == 4 else a
    y = _luma(a[::2, ::2])
    if y.shape[0] < 16 or y.shape[1] < 16 or y.mean() < 16 or y.std() < 5:
        return None
    return mittaa([a])


def siivoustarve(mitta):
    """Kuinka paljon kohinan- ja pakkausjälkien poistoa lähde tarvitsisi (sigma 0..)."""
    return max(0.0, mitta["kohina"] - 1.2) + max(0.0, mitta["lohkot"] - 1.15) * 10.0


def vaikeus(mitta):
    """Lajitteluarvo esikatselun "vaikeimman kohdan" valintaan: pakkausjäljet, kohina ja pehmeys."""
    return siivoustarve(mitta) + max(0.0, 0.45 - mitta["teravyys"]) * 10.0


def vaihtelee(mitat):
    """Vaihteleeko lähteen laatu niin paljon, että eri kohdat tarvitsisivat selvästi eri säädöt."""
    if len(mitat) < 4:
        return False
    tarve = np.array([min(4.0, siivoustarve(x)) for x in mitat])
    ter = np.array([x["teravyys"] for x in mitat])
    return bool(tarve.max() - np.median(tarve) > 2.0 or np.median(ter) - ter.min() > 0.2)


def _mediaani3(a):
    """3x3-mediaani jokaiselle kanavalle (numpy)."""
    h, w = a.shape[:2]
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    return np.median(np.stack([p[i:i + h, j:j + w] for i in range(3) for j in range(3)]), axis=0)


def piirrospinnat(a, B=8, raja=1.2):
    """Piirretyn tunnusmerkki: tasaväriset, värikkäät pinnat. Palauttaa (osuus keskisävyisistä
    lohkoista, tasaisten värikkäiden lohkojen värisävyt asteina)."""
    a = np.asarray(a)
    a = a[0] if a.ndim == 4 else a
    h, w = a.shape[:2]
    if h * w > 1_000_000:                      # iso kuva: pienennetään (sama mittakaava kuin SD)
        k = int(math.ceil(math.sqrt(h * w / 500_000)))
        a = a[:h // k * k, :w // k * k].reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    f = _mediaani3(a.astype(np.float32))
    y = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    cb, cr = f[..., 2] - y, f[..., 0] - y
    h, w = y.shape
    h8, w8 = h // B * B, w // B * B
    if h8 < 4 * B or w8 < 4 * B:
        return 0.0, np.zeros(0)

    def lohkot(z):
        return z[:h8, :w8].reshape(h8 // B, B, w8 // B, B).transpose(0, 2, 1, 3).reshape(h8 // B, w8 // B, B * B)
    by, bb, br = lohkot(y), lohkot(cb), lohkot(cr)
    ka = by.mean(-1)
    keski = (ka > 35) & (ka < 220)
    if keski.sum() < 50:
        return 0.0, np.zeros(0)
    tasainen = (by.std(-1) < raja) & (bb.std(-1) < raja * 1.5) & (br.std(-1) < raja * 1.5)
    mb, mr = bb.mean(-1), br.mean(-1)
    v = tasainen & keski & (np.sqrt(mb ** 2 + mr ** 2) > 25)
    return float(v.sum() / keski.sum()), np.degrees(np.arctan2(mr[v], mb[v])) % 360


def piirretty(kuvat):
    """Onko video piirrettyä? Tasaväristen värikkäiden pintojen osuus ja niiden värisävyjen kirjo:
    piirretyissä pintoja on paljon ja monessa sävyssä, luontokuvassa (esim. nurmikko) yhdessä."""
    osuudet, savyt = [], []
    for a in kuvat:
        o, s = piirrospinnat(a)
        osuudet.append(o)
        savyt.append(s)
    if len(osuudet) < 4:
        return False
    s = np.concatenate(savyt) if savyt else np.zeros(0)
    if len(s) < 50:
        return False
    jakauma = np.histogram(s, bins=12, range=(0, 360))[0] / len(s)
    o = np.array(osuudet)
    return bool(np.median(o) >= 0.10 and (o > 0.08).mean() >= 0.6 and (jakauma >= 0.04).sum() >= 5)


def oletukset(mitta, kuva=False):
    """Mittauksiin sovitetut säädöt jokaiselle asetukselle: {"2": {"ai":…, "rae":…, "kohina":…}, …}"""
    kohina, lohkot, ter = mitta["kohina"], mitta["lohkot"], mitta["teravyys"]
    # siivouksen tarve: kohina + selvä lohkoisuus, maltillisesti (sigma enintään 4 ~ kohina 0.2).
    # Vertailuissa (XviD, YouTube-H.264) voimakkaampi siivous vei kasvoista ja kankaista yksityiskohdat.
    sigma = min(4.0, siivoustarve(mitta))
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
