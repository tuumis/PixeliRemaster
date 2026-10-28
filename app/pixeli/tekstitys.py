"""PixeliRemaster – DVD-tekstitysten muunto tekstiksi (SRT).

DVD:n tekstitykset ovat pieniä nelivärisiä kuvia (SPU, "VobSub"). Suurennetussa videossa ne
näyttävät sumeilta eikä niiden kokoa voi muuttaa. Tässä kuvat puretaan itse (SPU-muoto), teksti
erotetaan reunuksesta ja tunnistetaan Windowsin omalla tekstintunnistuksella (Windows.Media.Ocr,
käyttää Windowsiin asennettuja kieliä). Tuloksena on tavallinen SRT-tekstitysraita.
"""
import asyncio
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from . import moottori as m

YKSIKKO = 1024 / 90000.0            # SPU:n viiveiden aikayksikkö (s)


# ------------------------------------------------------------------ SPU-paketit

def _heksa(data):
    """ffprobe -show_data -muoto: '00000000: 0444 0425 ...  ascii' -> bytes."""
    tavut = bytearray()
    for rivi in data.splitlines():
        if len(rivi) > 10 and rivi[8:10] == ": ":
            tavut += bytes.fromhex(rivi[10:49].replace(" ", ""))
    return bytes(tavut)


def spu_paketit(src, raita):
    """Tekstitysraidan (s:raita) SPU-paketit: [(alku_s, kesto_s tai None, bytes)].
    MPEG-PS-lähteessä yksi SPU voi jakautua useaan pakettiin: ne yhdistetään koon perusteella."""
    p = subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-select_streams", f"s:{int(raita)}", "-show_packets",
                        "-show_data", "-of", "json", str(src)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=m.PIILOTA)
    paketit = json.loads(p.stdout or "{}").get("packets", [])
    tulos, kesken = [], None
    for pk in paketit:
        d = _heksa(pk.get("data") or "")
        if not d:
            continue
        pts = float(pk.get("pts_time") or pk.get("dts_time") or 0)
        kesto = float(pk["duration_time"]) if pk.get("duration_time") not in (None, "N/A") else None
        if kesken is not None:
            kesken[2] += d
        else:
            kesken = [pts, kesto, bytearray(d)]
        koko = (kesken[2][0] << 8) | kesken[2][1] if len(kesken[2]) >= 2 else 0
        if koko and len(kesken[2]) >= koko:
            tulos.append((kesken[0], kesken[1], bytes(kesken[2][:koko])))
            kesken = None
    return tulos


# ------------------------------------------------------------------ SPU-purku

def _rle_rivi(data, pos_nibble, leveys):
    """Yksi rivi nelibittistä RLE-koodia. Palauttaa (rivin indeksit, seuraava nibble-paikka)."""
    rivi = np.zeros(leveys, np.uint8)
    x = 0
    n = pos_nibble

    def nib():
        nonlocal n
        b = data[n >> 1] if (n >> 1) < len(data) else 0
        v = (b >> 4) if (n & 1) == 0 else (b & 0xF)
        n += 1
        return v
    while x < leveys:
        v = nib()
        if v < 0x4:
            v = (v << 4) | nib()
            if v < 0x10:
                v = (v << 4) | nib()
                if v < 0x40:
                    v = (v << 4) | nib()
        pituus, vari = v >> 2, v & 3
        if pituus == 0:
            pituus = leveys - x
        rivi[x:x + pituus] = vari
        x += pituus
    if n & 1:                       # rivi päättyy tavun rajalle
        n += 1
    return rivi, n


def pura_spu(spu):
    """SPU -> {"alku", "loppu" (s tai None), "x", "y", "indeksit" (h,w), "alfa" [4], "vari" [4]} tai None."""
    if len(spu) < 4:
        return None
    koko = (spu[0] << 8) | spu[1]
    ohjaus = (spu[2] << 8) | spu[3]
    alku = loppu = None
    vari, alfa = [0, 1, 2, 3], [0, 15, 15, 15]
    x1 = x2 = y1 = y2 = None
    ylakentta = alakentta = None
    pos = ohjaus
    kaydyt = set()
    while pos + 4 <= len(spu) and pos not in kaydyt:
        kaydyt.add(pos)
        viive = ((spu[pos] << 8) | spu[pos + 1]) * YKSIKKO
        seuraava = (spu[pos + 2] << 8) | spu[pos + 3]
        i = pos + 4
        while i < len(spu):
            k = spu[i]
            i += 1
            if k in (0x00, 0x01):
                alku = viive if alku is None else alku
            elif k == 0x02:
                loppu = viive
            elif k == 0x03 and i + 2 <= len(spu):
                vari = [spu[i + 1] & 0xF, spu[i + 1] >> 4, spu[i] & 0xF, spu[i] >> 4]
                i += 2
            elif k == 0x04 and i + 2 <= len(spu):
                alfa = [spu[i + 1] & 0xF, spu[i + 1] >> 4, spu[i] & 0xF, spu[i] >> 4]
                i += 2
            elif k == 0x05 and i + 6 <= len(spu):
                b = spu[i:i + 6]
                x1, x2 = (b[0] << 4) | (b[1] >> 4), ((b[1] & 0xF) << 8) | b[2]
                y1, y2 = (b[3] << 4) | (b[4] >> 4), ((b[4] & 0xF) << 8) | b[5]
                i += 6
            elif k == 0x06 and i + 4 <= len(spu):
                ylakentta, alakentta = (spu[i] << 8) | spu[i + 1], (spu[i + 2] << 8) | spu[i + 3]
                i += 4
            elif k == 0x07 and i + 2 <= len(spu):
                i += (spu[i] << 8) | spu[i + 1]          # värimuutokset: ohitetaan
            else:                                         # 0xFF = loppu (tai tuntematon)
                break
        if seuraava == pos or seuraava >= koko:
            break
        pos = seuraava
    if None in (x1, x2, y1, y2, ylakentta, alakentta) or x2 < x1 or y2 < y1:
        return None
    w, h = x2 - x1 + 1, y2 - y1 + 1
    if w > 2000 or h > 2000:
        return None
    kuva = np.zeros((h, w), np.uint8)
    n_yla, n_ala = ylakentta * 2, alakentta * 2
    for y in range(h):
        if y % 2 == 0:
            kuva[y], n_yla = _rle_rivi(spu, n_yla, w)
        else:
            kuva[y], n_ala = _rle_rivi(spu, n_ala, w)
    return {"alku": alku or 0.0, "loppu": loppu, "x": x1, "y": y1, "indeksit": kuva, "alfa": alfa, "vari": vari}


def tekstikuva(s, kerroin=2):
    """Tunnistusta varten: teksti mustana valkoisella. DVD:n tekstityksessä on yleensä tekstin täyte ja
    sen ympärillä reunus; täyte on se näkyvä väri, joka koskettaa läpinäkyvää taustaa vähiten."""
    ind = s["indeksit"]
    nakyva = np.array([s["alfa"][i] > 0 for i in range(4)])
    vis = nakyva[ind]
    if vis.sum() < 8:
        return None
    tausta = ~vis
    reuna = np.zeros_like(tausta)
    reuna[1:] |= tausta[:-1]
    reuna[:-1] |= tausta[1:]
    reuna[:, 1:] |= tausta[:, :-1]
    reuna[:, :-1] |= tausta[:, 1:]
    paras, paras_osuus = None, 2.0
    for i in range(4):
        maara = int(((ind == i) & vis).sum())
        if not nakyva[i] or maara < 8:
            continue
        osuus = float((reuna & (ind == i)).sum()) / maara
        if osuus < paras_osuus:
            paras, paras_osuus = i, osuus
    teksti = (ind == paras) & vis
    if teksti.sum() < 8:
        return None
    ys, xs = np.nonzero(teksti)
    teksti = teksti[max(0, ys.min() - 2):ys.max() + 3, max(0, xs.min() - 2):xs.max() + 3]
    kuva = np.where(teksti, 0, 255).astype(np.uint8)
    if kerroin > 1:
        kuva = np.repeat(np.repeat(kuva, kerroin, 0), kerroin, 1)
    return np.pad(kuva, 16, constant_values=255)


# ------------------------------------------------------------------ tekstintunnistus (Windows)

def _ocr_moduulit():
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap  # type: ignore
    from winrt.windows.globalization import Language  # type: ignore
    from winrt.windows.media.ocr import OcrEngine  # type: ignore
    from winrt.windows.storage.streams import DataWriter  # type: ignore
    return BitmapPixelFormat, SoftwareBitmap, Language, OcrEngine, DataWriter


WINRT_PAKETIT = ["winrt-runtime", "winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging",
                 "winrt-Windows.Globalization", "winrt-Windows.Storage.Streams", "winrt-Windows.Foundation",
                 "winrt-Windows.Foundation.Collections"]


def varmista_ocr():
    """Windowsin tekstintunnistuksen Python-liitos (pywinrt, MIT) asennetaan PyPI:stä ensimmäisellä käytöllä."""
    try:
        _ocr_moduulit()
        return True
    except Exception:  # noqa: BLE001
        pass
    from .asennus import _python_exe
    p = subprocess.run([_python_exe(), "-m", "pip", "install", *WINRT_PAKETIT, "--disable-pip-version-check",
                        "--no-warn-script-location"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=m.PIILOTA)
    m.loki("pip winrt: " + (p.stdout + p.stderr)[-800:])
    import importlib
    importlib.invalidate_caches()
    _ocr_moduulit()
    return True


# ISO 639-2 (DVD) -> BCP-47 (Windows)
KIELET = {"fin": "fi", "swe": "sv", "eng": "en", "nor": "nb", "nob": "nb", "dan": "da", "ger": "de", "deu": "de",
          "fre": "fr", "fra": "fr", "spa": "es", "ita": "it", "dut": "nl", "nld": "nl", "por": "pt", "pol": "pl",
          "est": "et", "rus": "ru", "hun": "hu", "cze": "cs", "ces": "cs", "gre": "el", "ell": "el", "tur": "tr",
          "ice": "is", "isl": "is"}


class Tunnistin:
    def __init__(self, kieli=None):
        BitmapPixelFormat, SoftwareBitmap, Language, OcrEngine, DataWriter = _ocr_moduulit()
        self._m = (BitmapPixelFormat, SoftwareBitmap, DataWriter)
        self.moottori, self.kieli = None, None
        ehdokkaat = [KIELET.get((kieli or "").lower(), kieli)] if kieli else []
        for k in ehdokkaat:
            try:
                kl = Language(k)
                if OcrEngine.is_language_supported(kl):
                    self.moottori, self.kieli = OcrEngine.try_create_from_language(kl), k
                    break
            except Exception:  # noqa: BLE001
                pass
        if self.moottori is None:          # Windowsin käyttäjän kielet
            self.moottori = OcrEngine.try_create_from_user_profile_languages()
            self.kieli = self.moottori.recognizer_language.language_tag if self.moottori else None
        if self.moottori is None:
            raise RuntimeError("ocr_ei_kielta")
        self.silmukka = asyncio.new_event_loop()

    def tunnista(self, harmaa):
        BitmapPixelFormat, SoftwareBitmap, DataWriter = self._m
        h, w = harmaa.shape
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., :3] = harmaa[..., None]
        bgra[..., 3] = 255
        kirjoittaja = DataWriter()
        kirjoittaja.write_bytes(bgra.tobytes())
        bm = SoftwareBitmap.create_copy_from_buffer(kirjoittaja.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        tulos = self.silmukka.run_until_complete(self.moottori.recognize_async(bm))
        return "\n".join(r.text for r in tulos.lines).strip()


# ------------------------------------------------------------------ korjaukset ja SRT

_KORJAUKSET = [
    (re.compile(r"(?<=\w)\s+([,.!?:;])"), r"\1"),       # välilyönti ennen välimerkkiä
    (re.compile(r"^[-–—]\s*"), "- "),                     # repliikkiviiva
    (re.compile(r"\s{2,}"), " "),
]


def siisti(teksti):
    rivit = []
    for r in teksti.splitlines():
        r = r.strip().replace("|", "I")
        for kuvio, korvaus in _KORJAUKSET:
            r = kuvio.sub(korvaus, r)
        if r:
            rivit.append(r)
    return "\n".join(rivit)


def _aika(s):
    ms = int(round(max(0.0, s) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def tee_srt(src, raita, kieli, kohde, edistys=None, peruuta=None, tunnistin=None):
    """Tunnistaa raidan tekstit ja kirjoittaa SRT-tiedoston. Palauttaa (polku, tekstien määrä)."""
    paketit = spu_paketit(src, raita)
    tunnistin = tunnistin or Tunnistin(kieli)
    tapahtumat = []
    for j, (pts, kesto, data) in enumerate(paketit):
        if peruuta is not None and peruuta.is_set():
            raise m.Peruutettu()
        s = pura_spu(data)
        if s is None:
            continue
        alku = pts + s["alku"]
        loppu = pts + s["loppu"] if s["loppu"] else (pts + kesto if kesto else None)
        kuva = tekstikuva(s)
        if kuva is None:
            continue
        teksti = siisti(tunnistin.tunnista(kuva))
        if teksti:
            tapahtumat.append([alku, loppu, teksti])
        if edistys and paketit:
            edistys((j + 1) / len(paketit))
    for i, t in enumerate(tapahtumat):          # puuttuva loppuaika: seuraavan alkuun (enintään 6 s)
        if t[1] is None or t[1] <= t[0]:
            seur = tapahtumat[i + 1][0] if i + 1 < len(tapahtumat) else t[0] + 4
            t[1] = min(seur, t[0] + 6)
    Path(kohde).write_text("".join(f"{i}\n{_aika(a)} --> {_aika(b)}\n{t}\n\n"
                                   for i, (a, b, t) in enumerate(tapahtumat, 1)), encoding="utf-8")
    return Path(kohde), len(tapahtumat)


# ------------------------------------------------------------------ testejä varten: SPU-koodaus

def _rle_koodaa(rivi):
    nib = []
    x, w = 0, len(rivi)
    while x < w:
        c = int(rivi[x])
        n = 1
        while x + n < w and rivi[x + n] == c and n < 255:
            n += 1
        if x + n >= w and n > 3:
            v, pit = c, 4                     # loppurivi: pituus 0
        elif n <= 3:
            v, pit = (n << 2) | c, 1
        elif n <= 15:
            v, pit = (n << 2) | c, 2
        elif n <= 63:
            v, pit = (n << 2) | c, 3
        else:
            v, pit = (n << 2) | c, 4
        nib += [(v >> (4 * k)) & 0xF for k in range(pit - 1, -1, -1)]
        x += n
    if len(nib) % 2:
        nib.append(0)
    return bytes((nib[i] << 4) | nib[i + 1] for i in range(0, len(nib), 2))


def koodaa_spu(indeksit, x=100, y=400, kesto=2.0, alfa=(0, 15, 15, 15), vari=(0, 15, 1, 2)):
    """(h,w) indeksit 0..3 -> SPU-paketti (alku heti, loppu kesto sekunnin kuluttua)."""
    h, w = indeksit.shape
    yla = b"".join(_rle_koodaa(indeksit[r]) for r in range(0, h, 2))
    ala = b"".join(_rle_koodaa(indeksit[r]) for r in range(1, h, 2))
    o_yla, o_ala = 4, 4 + len(yla)
    ohjaus = 4 + len(yla) + len(ala)
    x2, y2 = x + w - 1, y + h - 1
    k1 = bytes([0x01, 0x03, (vari[3] << 4) | vari[2], (vari[1] << 4) | vari[0],
                0x04, (alfa[3] << 4) | alfa[2], (alfa[1] << 4) | alfa[0],
                0x05, x >> 4, ((x & 0xF) << 4) | (x2 >> 8), x2 & 0xFF, y >> 4, ((y & 0xF) << 4) | (y2 >> 8), y2 & 0xFF,
                0x06, o_yla >> 8, o_yla & 0xFF, o_ala >> 8, o_ala & 0xFF, 0xFF])
    toinen = ohjaus + 4 + len(k1)
    if toinen % 2:
        k1 += b"\xff"
        toinen += 1
    viive = int(round(kesto / YKSIKKO))
    d1 = bytes([0, 0, toinen >> 8, toinen & 0xFF]) + k1
    d2 = bytes([viive >> 8, viive & 0xFF, toinen >> 8, toinen & 0xFF, 0x02, 0xFF])
    runko = yla + ala + d1 + d2
    koko = 4 + len(runko)
    return bytes([koko >> 8, koko & 0xFF, ohjaus >> 8, ohjaus & 0xFF]) + runko
