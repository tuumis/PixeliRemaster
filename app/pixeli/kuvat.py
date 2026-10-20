"""PixeliRemaster – valokuvat ja muut kuvatiedostot.

Sama käsittelyketju kuin videoissa (kuva.Ketju), mutta:
  * lähde puretaan kerran ffmpegillä RGB:ksi; JPEGin EXIF-suunta (puhelinkuvat) huomioidaan
  * koko valitaan kertoimena (2x, 4x) tai näytön kokoon; 4x ajaa mallin kahdesti
  * suuret kuvat lasketaan paloissa (kuva.Malli), joten näytönohjaimen muisti riittää
  * tulos tallennetaan kuvana: JPEG -> JPEG (EXIF- ja väriprofiilitiedot säilyvät), muut -> PNG
    (läpinäkyvyys säilyy)
"""
import hashlib
import json
import math
import os
import struct
import subprocess
import time
import zlib
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import kuva
from . import moottori as m

KUVAPAATTEET = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
HEIC = {".heic", ".heif"}
MAKS_LAHDE = 64_000_000         # pikseliä
MAKS_ULOS = 64_000_000
MAKS_SYOTE = 13_000_000         # mallin syöte yhdellä kierroksella (näytönohjaimen muisti)
ESIKATSELU_KOKO = 6_000_000     # tätä pienempi tulos esikatsellaan kokonaan
ESIKATSELU_ALUE = 3_000_000     # muuten kolme tämän kokoista aluetta (tuloksen pikseleinä)
SKAALAT = ("1", "2", "4", "naytto")
MUODOT = ("auto", "jpg", "png")
KIERROT = (0, 90, 180, 270)
PALOITTAIN_KUVA = 300_000       # käsittelyssä paloitellaan jo tätä suuremmat (edistyminen ja keskeytys)

# kuvissa ei oletuksena filmirakeita
KUVA_MUUTOKSET = {"rae": 0.0}


def on_kuva(polku):
    return Path(polku).suffix.lower() in KUVAPAATTEET


# ---------------------------------------------------------------- JPEG- ja PNG-rakenne

def jpeg_segmentit(data):
    """JPEGin otsakesegmentit ennen kuvadataa: [(merkki, alku, loppu)] (loppu = seuraavan alku)."""
    if data[:2] != b"\xff\xd8":
        return []
    i, seg = 2, []
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        mk = data[i + 1]
        if mk == 0xFF:
            i += 1
            continue
        if mk == 0x01 or 0xD0 <= mk <= 0xD8:
            i += 2
            continue
        pituus = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg.append((mk, i, i + 2 + pituus))
        if mk == 0xDA:
            break
        i += 2 + pituus
    return seg


def exif_suunta(app1):
    """EXIF-suunta (1–8) ja sen arvon sijainti APP1-segmentin datassa, tai (1, None)."""
    try:
        if not app1.startswith(b"Exif\x00\x00"):
            return 1, None
        t = app1[6:]
        e = "<" if t[:2] == b"II" else ">" if t[:2] == b"MM" else None
        if not e:
            return 1, None
        ifd = struct.unpack(e + "I", t[4:8])[0]
        n = struct.unpack(e + "H", t[ifd:ifd + 2])[0]
        for k in range(n):
            p = ifd + 2 + 12 * k
            tag, tyyppi, _ = struct.unpack(e + "HHI", t[p:p + 8])
            if tag == 0x0112 and tyyppi == 3:
                arvo = struct.unpack(e + "H", t[p + 8:p + 10])[0]
                return (arvo if 1 <= arvo <= 8 else 1), (6 + p + 8, e)
    except struct.error:
        pass
    return 1, None


_IJG_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
             14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
             49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


def jpeg_laatu(data, segmentit):
    """Arvio JPEGin laatuasetuksesta (1–100) kirkkauden kvantisointitaulukosta (IJG-asteikko)."""
    for mk, a, b in segmentit:
        if mk != 0xDB:
            continue
        p = a + 4
        while p < b:
            pq, tq = data[p] >> 4, data[p] & 15
            koko = 128 if pq else 64
            arvot = (struct.unpack(">64H", data[p + 1:p + 1 + koko]) if pq else data[p + 1:p + 65])
            if tq == 0:
                skaala = 100.0 * sum(arvot) / sum(_IJG_LUMA)
                q = (200 - skaala) / 2 if skaala <= 100 else 5000 / skaala
                return int(round(min(100, max(1, q))))
            p += 1 + koko
    return None


def png_icc(data):
    """PNG:n iCCP-lohko (väriprofiili) sellaisenaan, tai None."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    i = 8
    while i + 8 <= len(data):
        pituus = struct.unpack(">I", data[i:i + 4])[0]
        tyyppi = data[i + 4:i + 8]
        if tyyppi == b"iCCP":
            return data[i + 8:i + 8 + pituus]
        if tyyppi in (b"IDAT", b"IEND"):
            return None
        i += 12 + pituus
    return None


def _png_lohko(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)


def kirjoita_png(polku, rgb, alfa=None, icc=None):
    """8-bittinen PNG (RGB tai RGBA). Up-suodin pakkaa valokuvat selvästi suodattamatonta paremmin."""
    a = np.dstack([rgb, alfa]) if alfa is not None else rgb
    h, w, c = a.shape
    rivit = a.reshape(h, w * c)
    ero = rivit.copy()
    ero[1:] -= rivit[:-1]                        # uint8: modulo 256 kuten PNG vaatii
    raaka = np.concatenate([np.full((h, 1), 2, np.uint8), ero], axis=1)
    del ero
    z = zlib.compressobj(6)
    tmp = Path(str(polku) + ".kesken")
    with open(tmp, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + _png_lohko(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if c == 4 else 2, 0, 0, 0)))
        if icc:
            f.write(_png_lohko(b"iCCP", icc))
        kaista = max(1, (8 << 20) // raaka.shape[1])
        for r0 in range(0, h, kaista):
            osa = z.compress(raaka[r0:r0 + kaista].tobytes())
            if osa:
                f.write(_png_lohko(b"IDAT", osa))
        f.write(_png_lohko(b"IDAT", z.flush()))
        f.write(_png_lohko(b"IEND", b""))
    tmp.replace(polku)


def jpeg_metatiedot(exif=None, icc=None):
    """EXIF- ja ICC-tiedot JPEG-segmenteiksi (esim. HEIC-kuvasta)."""
    seg = b""
    if exif:
        exif = exif if exif.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + exif
        if len(exif) + 2 <= 0xFFFF:
            seg += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    if icc:
        osat = [icc[i:i + 65519] for i in range(0, len(icc), 65519)][:255]
        for i, osa in enumerate(osat, 1):
            data = b"ICC_PROFILE\x00" + bytes([i, len(osat)]) + osa
            seg += b"\xff\xe2" + struct.pack(">H", len(data) + 2) + data
    return b"\xff\xd8" + seg + b"\xff\xda\x00\x02"     # kelvollinen otsake segmenttien lukemiseen


def kirjoita_jpeg(polku, rgb, lahde_data=None):
    """JPEG ffmpegillä (laatu ~95, värit täydellä tarkkuudella 4:4:4). Alkuperäisen EXIF-tiedot
    (suunta nollataan, koska kuva on jo käännetty) ja väriprofiili kopioidaan mukaan."""
    h, w, _ = rgb.shape
    tmp = Path(str(polku) + ".kesken.jpg")
    cmd = [m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-video_size", f"{w}x{h}", "-i", "pipe:0", "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "2",
           "-pix_fmt", "yuvj444p", "-f", "image2", "-update", "1", str(tmp)]
    p = subprocess.run(cmd, input=rgb.tobytes(), capture_output=True, creationflags=m.PIILOTA)
    if p.returncode != 0 or not tmp.exists():
        raise RuntimeError("tallennus: " + p.stderr.decode("utf-8", "replace")[-400:])
    uusi = tmp.read_bytes()
    lisat = b""
    if lahde_data:
        for mk, a, b in jpeg_segmentit(lahde_data):
            if mk == 0xE1 and lahde_data[a + 4:a + 10] == b"Exif\x00\x00":
                seg = bytearray(lahde_data[a:b])
                _, kohta = exif_suunta(bytes(seg[4:]))
                if kohta:
                    paikka, e = kohta
                    seg[4 + paikka:4 + paikka + 2] = struct.pack(e + "H", 1)
                lisat += bytes(seg)
            elif mk == 0xE2 and lahde_data[a + 4:a + 16] == b"ICC_PROFILE\x00":
                lisat += lahde_data[a:b]
    seg = jpeg_segmentit(uusi)
    # SOI + ffmpegin JFIF-otsake + alkuperäisen tiedot + taulukot ja kuvadata (ffmpegin kommentti pois)
    jfif = b"".join(uusi[a:b] for mk, a, b in seg if mk == 0xE0)
    muut = b"".join(uusi[a:b] for mk, a, b in seg if mk not in (0xE0, 0xFE, 0xDA))
    sos = next(a for mk, a, b in seg if mk == 0xDA)
    valmis = b"\xff\xd8" + jfif + lisat + muut + uusi[sos:]
    vali = Path(str(polku) + ".kesken")
    vali.write_bytes(valmis)
    vali.replace(polku)
    tmp.unlink()


# ---------------------------------------------------------------- purku ja analyysi

def _suunnista(a, suunta):
    """EXIF-suunnan mukainen kääntö (kuten Pillowin exif_transpose)."""
    if suunta == 2:
        return a[:, ::-1]
    if suunta == 3:
        return a[::-1, ::-1]
    if suunta == 4:
        return a[::-1]
    if suunta == 5:
        return a.swapaxes(0, 1)
    if suunta == 6:
        return np.rot90(a, -1)
    if suunta == 7:
        return a[::-1, ::-1].swapaxes(0, 1)
    if suunta == 8:
        return np.rot90(a, 1)
    return a


def _alfa_mahdollinen(pix):
    pix = (pix or "").lower()
    return pix == "pal8" or pix.startswith(("rgba", "bgra", "argb", "abgr", "ya", "yuva", "gbrap"))


def avaa_heic(src):
    """HEIC/HEIF (iPhone) pillow-heifillä. libheif soveltaa kuvan omat kierrot, joten suunta on jo oikein.
    Palauttaa (rgb, alfa tai None, exif, icc)."""
    try:
        import pillow_heif
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("heic_puuttuu") from e
    pillow_heif.register_heif_opener()
    with Image.open(src) as im:
        exif, icc = im.info.get("exif"), im.info.get("icc_profile")
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        a = np.asarray(im)
    if a.shape[2] == 4:
        al = np.ascontiguousarray(a[:, :, 3])
        return np.ascontiguousarray(a[:, :, :3]), (al if int(al.min()) < 255 else None), exif, icc
    return np.ascontiguousarray(a), None, exif, icc


def pura(src, an):
    """Lähdekuva RGB-taulukoksi (h, w, 3) uint8 ja läpinäkyvyys (h, w) tai None, suunta korjattuna."""
    if an.get("muoto") == "heic" or Path(src).suffix.lower() in HEIC:
        rgb, alfa, _, _ = avaa_heic(src)
        return rgb, alfa
    alfa = an.get("alfa_mahd")
    muoto = "rgba" if alfa else "rgb24"
    w0, h0 = an["leveys_raaka"], an["korkeus_raaka"]
    c = 4 if alfa else 3
    r = subprocess.run([m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-noautorotate",
                        "-i", str(src), "-map", "0:v:0", "-frames:v", "1",
                        "-vf", f"scale=flags=accurate_rnd+full_chroma_int+bicubic,format={muoto}",
                        "-f", "rawvideo", "-pix_fmt", muoto, "-"], capture_output=True, creationflags=m.PIILOTA)
    if len(r.stdout) < w0 * h0 * c:
        raise RuntimeError("Kuvaa ei voitu purkaa: " + r.stderr.decode("utf-8", "replace")[-300:])
    a = np.frombuffer(r.stdout[:w0 * h0 * c], np.uint8).reshape(h0, w0, c)
    a = np.ascontiguousarray(_suunnista(a, an.get("suunta", 1)))
    if c == 4:
        rgb, al = np.ascontiguousarray(a[:, :, :3]), np.ascontiguousarray(a[:, :, 3])
        return rgb, (al if int(al.min()) < 255 else None)
    return a, None


def skaalaa_alfa(alfa, oh, ow):
    """Läpinäkyvyys uuteen kokoon (prosessorilla; esikatselua ja pikkukuvia varten)."""
    h, w = alfa.shape
    wy = kuva.skaalausmatriisi(h, oh, "catmullrom" if oh >= h else "mitchell")
    wx = kuva.skaalausmatriisi(w, ow, "catmullrom" if ow >= w else "mitchell")
    return np.clip(wy @ (alfa.astype(np.float32) / 255.0) @ wx.T, 0, 1)


def ruudukolle(rgb, alfa01):
    """Läpinäkyvä kuva harmaan ruudukon päälle, jotta läpinäkyvät kohdat näkyvät esikatselussa."""
    h, w, _ = rgb.shape
    yy, xx = np.mgrid[0:h, 0:w]
    tausta = np.where(((yy // 12 + xx // 12) % 2) == 0, 92.0, 120.0).astype(np.float32)[..., None]
    a = alfa01[..., None]
    return np.clip(rgb.astype(np.float32) * a + tausta * (1 - a) + 0.5, 0, 255).astype(np.uint8)


def _pikkukuva(polku, rgb, alfa=None, koko=(192, 120)):
    if alfa is not None:
        rgb = ruudukolle(rgb, alfa.astype(np.float32) / 255.0)
    h, w, _ = rgb.shape
    k = max(1, int(math.ceil(max(w / koko[0], h / koko[1]))))
    hh, ww = h // k * k, w // k * k
    pieni = rgb[:hh, :ww].reshape(hh // k, k, ww // k, k, 3).mean(axis=(1, 3))
    m.tallenna_png(polku, (pieni + 0.5).astype(np.uint8))


def analysoi_kuva(src: Path, wd: Path):
    st = src.stat()
    avain = [st.st_size, int(st.st_mtime), m.VERSIO, "kuva", 3]
    vanha = m.lue_json(wd / "analyysi.json")
    if vanha and vanha.get("avain") == avain and (wd / "pikku.png").exists() and (wd / "yleis.png").exists():
        return vanha
    m.loki(f"Analysoidaan kuva {src.name}")
    if src.suffix.lower() in HEIC:
        rgb0, _, _, _ = avaa_heic(src)
        v = {"width": rgb0.shape[1], "height": rgb0.shape[0], "codec_name": "heic", "pix_fmt": "rgb24"}
        del rgb0
    else:
        p = m.aja([m.ffbin("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", src])
        if p.returncode != 0:
            raise RuntimeError(f"Tiedostoa ei voitu lukea: {p.stderr.strip()[-400:]}")
        v = next((s for s in json.loads(p.stdout).get("streams", []) if s.get("codec_type") == "video"), None)
        if not v or not v.get("width"):
            raise RuntimeError("Tiedostossa ei ole kuvaa.")
    w0, h0 = int(v["width"]), int(v["height"])
    if w0 * h0 > MAKS_LAHDE:
        raise RuntimeError("liian_suuri")
    koodekki = v.get("codec_name", "")
    with open(src, "rb") as f:
        alku = f.read(1 << 20)
    suunta, laatu, huomiot = 1, None, []
    if koodekki == "mjpeg":
        seg = jpeg_segmentit(alku)
        for mk, a, b in seg:
            if mk == 0xE1:
                s_, _ = exif_suunta(alku[a + 4:b])
                if s_ != 1:
                    suunta = s_
                    break
        laatu = jpeg_laatu(alku, seg)
    an = {"leveys_raaka": w0, "korkeus_raaka": h0, "suunta": suunta,
          "alfa_mahd": _alfa_mahdollinen(v.get("pix_fmt"))}
    rgb, alfa = pura(src, an)
    h, w, _ = rgb.shape
    _pikkukuva(wd / "pikku.png", rgb, alfa)
    _pikkukuva(wd / "yleis.png", rgb, alfa, (720, 540))          # yleiskuva esikatselualueiden valintaan

    # suositus: piirrokset (vähän värejä häviöttömässä tiedostossa), pakatut/pienet, muut valokuvia
    askel = max(1, int(math.sqrt(w * h / 1_000_000)))
    otos = rgb[::askel, ::askel].reshape(-1, 3).astype(np.uint32)
    varit = int(np.unique((otos[:, 0] << 16) | (otos[:, 1] << 8) | otos[:, 2]).size)
    haviollinen = koodekki in ("mjpeg", "webp", "heic")
    if not haviollinen and varit < min(20000, 0.15 * len(otos)):
        tyyppi, suositus = "piirros", 5
    elif (laatu is not None and laatu < 86) or max(w, h) <= 900:
        tyyppi, suositus = "pakattu", 3
    else:
        tyyppi, suositus = "valokuva", 2
    if max(w, h) >= 3000:
        huomiot.append("kuva_suuri")
    muoto = {"mjpeg": "jpeg"}.get(koodekki, koodekki)
    d = {"laji": "kuva", "avain": avain, "lahde": str(src), "leveys": w, "korkeus": h,
         "leveys_raaka": w0, "korkeus_raaka": h0, "suunta": suunta, "alfa_mahd": an["alfa_mahd"],
         "alfa": alfa is not None, "koodekki": koodekki, "muoto": muoto, "laatu": laatu, "varit": varit,
         "tyyppi": tyyppi, "suositus": suositus, "huomiot": huomiot, "rajaus": [0, 0, w, h],
         "sar": "1/1", "dar_levy": f"{Fraction(w, h).numerator}/{Fraction(w, h).denominator}",
         "kesto": 0.0, "fps": "1/1", "R": "1/1", "lomitus": "kuva", "matriisi": "bt709", "tekstityksia": 0}
    m.kirjoita_json(wd / "analyysi.json", d)
    return d


# ---------------------------------------------------------------- koko

def kaanna(a, kierto):
    """Käyttäjän valitsema kierto myötäpäivään (0, 90, 180, 270)."""
    return a if not kierto else np.ascontiguousarray(np.rot90(a, k=-(int(kierto) // 90)))


def geometria_kuva(an, valinta=None, kohde=(2560, 1440)):
    """Lopputuloksen koko. Kuvasuhde säilyy aina (sama kerroin molempiin suuntiin).
    1x parantaa kuvan suurentamatta (malli laskee 2x ja tulos pienennetään takaisin)."""
    valinta = valinta or {}
    kierto = int(valinta.get("kierto") or 0) % 360
    w, h = (an["korkeus"], an["leveys"]) if kierto in (90, 270) else (an["leveys"], an["korkeus"])
    vaihtoehdot = {}
    for v in SKAALAT:
        if v == "naytto":
            s = max(1.0, min(4.0, min(kohde[0] / w, kohde[1] / h)))
        else:
            s = float(v)
        ow, oh = max(1, int(round(w * s))), max(1, int(round(h * s)))
        kierrokset = 1 if s <= 2.0 + 1e-9 else 2
        syote = w * h * (4 if kierrokset == 2 else 1)
        vaihtoehdot[v] = {"ow": ow, "oh": oh, "s": s, "kierrokset": kierrokset,
                          "ok": ow * oh <= MAKS_ULOS and syote <= MAKS_SYOTE}
    sk = valinta.get("skaala") or "2"
    if sk not in vaihtoehdot or not vaihtoehdot[sk]["ok"]:
        sk = next((v for v in ("2", "naytto") if vaihtoehdot[v]["ok"]), "naytto")
    va = vaihtoehdot[sk]
    return {"rajaus": [0, 0, w, h], "ow": va["ow"], "oh": va["oh"], "suhde": w / h, "suhde_nimi": None,
            "dar": f"{w}:{h}", "skaala": sk, "s": va["s"], "kierrokset": va["kierrokset"],
            "vaihtoehdot": vaihtoehdot, "kierto": kierto}


def asetus_kuva(L, n, saato, geo):
    a = L.asetus(n, saato, kuva=True)
    a["kierrokset"] = geo["kierrokset"]
    return a


# ---------------------------------------------------------------- esikatselu

def koe_tunniste(geo):
    return hashlib.sha1(json.dumps([geo["rajaus"], geo["ow"], geo["oh"], geo["kierrokset"], geo.get("kierto", 0),
                                    m.VERSIO, "kuva", 3]).encode()).hexdigest()[:8]


def esikatselualueet(rgb, geo):
    """Koko kuva, jos tulos on kohtuullisen kokoinen; muuten keskikohta ja kaksi
    yksityiskohtaisinta kohtaa (samankokoiset alueet)."""
    h, w, _ = rgb.shape
    s = geo["s"]
    if w * h * s * s <= ESIKATSELU_KOKO:
        return [[0, 0, w, h]]
    pinta = ESIKATSELU_ALUE / (s * s)
    aw = int(min(w, round(math.sqrt(pinta * 4 / 3)))) // 2 * 2
    ah = int(min(h, round(pinta / max(aw, 1)))) // 2 * 2
    aw, ah = max(64, aw), max(64, ah)
    alueet = [[(w - aw) // 2, (h - ah) // 2, aw, ah]]
    # yksityiskohtaisuus: kirkkauden muutokset pienennetystä kuvasta
    k = max(1, int(math.sqrt(w * h / 250_000)))
    y = rgb[::k, ::k].astype(np.float32).mean(axis=2)
    g = np.abs(np.diff(y, axis=0))[:, :-1] + np.abs(np.diff(y, axis=1))[:-1, :]
    ii = np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    kh, kw = max(1, ah // k), max(1, aw // k)
    ehdokkaat = []
    for y0 in range(0, max(1, g.shape[0] - kh + 1), max(1, kh // 3)):
        for x0 in range(0, max(1, g.shape[1] - kw + 1), max(1, kw // 3)):
            y1, x1 = min(g.shape[0], y0 + kh), min(g.shape[1], x0 + kw)
            arvo = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
            ehdokkaat.append((float(arvo), min(w - aw, x0 * k), min(h - ah, y0 * k)))
    ehdokkaat.sort(reverse=True)
    for _, x0, y0 in ehdokkaat:
        if len(alueet) == 3:
            break
        if all(abs(x0 - a[0]) >= aw * 0.9 or abs(y0 - a[1]) >= ah * 0.9 for a in alueet):
            alueet.append([int(x0) // 2 * 2, int(y0) // 2 * 2, aw, ah])
    return alueet


def _alue_koko(alue, geo):
    """Esikatselualueen koko tuloksessa (sama kerroin kuin koko kuvalla)."""
    s = geo["ow"] / geo["rajaus"][2]
    return max(1, int(round(alue[3] * s))), max(1, int(round(alue[2] * s)))


def _koko_kuva(src, an, kd, geo):
    """Purettu (ja käyttäjän valitsemalla tavalla käännetty) lähdekuva välimuistista. Samalla
    kirjoitetaan yleiskuva esikatselualueiden valintaan."""
    p, pa = kd / "koko.npy", kd / "koko_alfa.npy"
    if p.exists() and (kd / "yleis.png").exists():
        return np.load(p, mmap_mode="r"), (np.load(pa, mmap_mode="r") if pa.exists() else None)
    rgb, alfa = pura(src, an)
    rgb, alfa = kaanna(rgb, geo.get("kierto", 0)), (kaanna(alfa, geo.get("kierto", 0)) if alfa is not None else None)
    np.save(p, rgb)
    if alfa is not None:
        np.save(pa, alfa)
    _pikkukuva(kd / "yleis.png", rgb, alfa, (720, 540))
    return rgb, alfa


def _tallenna_lahdealue(kd, i, rgb, alfa, alue):
    x, y, aw, ah = alue
    osa = np.ascontiguousarray(rgb[y:y + ah, x:x + aw])[None]
    np.save(kd / f"lahde_{i}.npy", osa)
    if alfa is not None:
        np.save(kd / f"alfa_{i}.npy", np.ascontiguousarray(alfa[y:y + ah, x:x + aw]))
    return osa


def lahdealueet(src, an, geo, kd):
    tiedot = m.lue_json(kd / "alueet.json")
    if tiedot and all((kd / f"lahde_{i}.npy").exists() for i in range(1, len(tiedot) + 1)):
        return tiedot, [np.load(kd / f"lahde_{i}.npy") for i in range(1, len(tiedot) + 1)]
    rgb, alfa = _koko_kuva(src, an, kd, geo)
    alueet = esikatselualueet(np.asarray(rgb), geo)
    kuvat = [_tallenna_lahdealue(kd, i, rgb, alfa, a) for i, a in enumerate(alueet, 1)]
    m.kirjoita_json(kd / "alueet.json", alueet)
    return alueet, kuvat


def _tallenna_esikatselu(polku, kd, i, rgb_u8, _valimuisti={}):
    """Esikatselukuva; läpinäkyvä kuva näytetään ruudukon päällä."""
    ap = kd / f"alfa_{i}.npy"
    if ap.exists():
        h, w, _ = rgb_u8.shape
        avain = (str(ap), ap.stat().st_mtime_ns, h, w)
        if avain not in _valimuisti:
            _valimuisti.clear()
            _valimuisti[avain] = skaalaa_alfa(np.load(ap), h, w)
        rgb_u8 = ruudukolle(rgb_u8, _valimuisti[avain])
    m.tallenna_png(polku, rgb_u8)


def mittaa_kuva(src, an, geo, wd):
    """Kuvan laadun mittaus ja siihen sovitetut säädöt (välimuistissa)."""
    from . import mittaus
    kd = wd / f"koe_{koe_tunniste(geo)}"
    kd.mkdir(parents=True, exist_ok=True)
    vanha = m.lue_json(kd / "mittaus.json")
    if vanha and vanha.get("v") == 1:
        return vanha
    rgb, _ = _koko_kuva(src, an, kd, geo)
    mm = mittaus.mittaa([np.asarray(rgb)])
    d = {"v": 1, "mittaus": mm, "kuvaus": mittaus.kuvaus(mm), "oletukset": mittaus.oletukset(mm, kuva=True)}
    m.kirjoita_json(kd / "mittaus.json", d)
    return d


def koekuvat_kuva(src, an, geo, wd, L, edistys=None, peruuta=None):
    """Vertailukuvat kaikilla asetuksilla (kuvaan sovitetuilla säädöillä) ja käsittelyajan arvio."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    kd.mkdir(parents=True, exist_ok=True)
    alueet, kuvat = lahdealueet(src, an, geo, kd)
    mt = mittaa_kuva(src, an, geo, wd)
    ol = mt["oletukset"]
    oh, ow = _alue_koko(alueet[0], geo)
    vaiheet = len(m.ASETUKSET) * len(kuvat)
    tehty = 0
    aika = {}
    for n in m.ASETUKSET:
        with L.lukko:
            ketju = kuva.Ketju(L.ops, alueet[0][3], alueet[0][2], oh, ow, asetus_kuva(L, n, ol.get(str(n)), geo),
                               yuv=False)
            ajat = []
            with L.inferenssi():
                for i, k in enumerate(kuvat, 1):
                    if peruuta is not None and peruuta.is_set():
                        raise m.Peruutettu()
                    t = time.time()
                    rgb = ketju.rgb_lopullinen(L.ops.from_uint8(k), 1000 * i)
                    _tallenna_esikatselu(kd / f"v{n}_k{i}.png", kd, i, L.ops.uint8_hwc(rgb))
                    ajat.append(time.time() - t)
                    del rgb
                    tehty += 1
                    if edistys:
                        edistys(tehty, vaiheet)
            del ketju
            L.tyhjenna()
        # arvio koko kuvalle: alueen laskenta-aika suhteutettuna pikselimäärään (+ tallennus)
        mitattu = min(ajat[1:]) if len(ajat) > 1 else ajat[0]
        suhde = (geo["rajaus"][2] * geo["rajaus"][3]) / max(1, alueet[0][2] * alueet[0][3])
        aika[str(n)] = round(mitattu * suhde * (1.1 if suhde > 1.5 else 1.0) + geo["ow"] * geo["oh"] / 12e6, 1)
    tiedot = {"tunniste": tun, "aika": aika, "alueet": alueet, "aika_luotu": time.time(), "mittaus": mt["mittaus"],
              "kuvaus": mt["kuvaus"], "oletukset": ol}
    m.kirjoita_json(kd / "koe.json", tiedot)
    return tiedot


def siirra_alue(src, an, geo, wd, L, k, cx, cy, edistys=None):
    """Siirtää esikatselualueen k kohtaan (cx, cy) (lähteen pikseleinä, alueen keskipiste) ja laskee
    sen vertailukuvat kaikilla asetuksilla. Palauttaa päivitetyt alueet."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    alueet, _ = lahdealueet(src, an, geo, kd)
    k = max(1, min(len(alueet), int(k)))
    _, _, aw, ah = alueet[k - 1]
    w, h = geo["rajaus"][2], geo["rajaus"][3]
    x = int(max(0, min(w - aw, round(float(cx) - aw / 2)))) // 2 * 2
    y = int(max(0, min(h - ah, round(float(cy) - ah / 2)))) // 2 * 2
    alueet[k - 1] = [x, y, aw, ah]
    rgb, alfa = _koko_kuva(src, an, kd, geo)
    osa = _tallenna_lahdealue(kd, k, rgb, alfa, alueet[k - 1])
    oh, ow = _alue_koko(alueet[k - 1], geo)
    ol = mittaa_kuva(src, an, geo, wd)["oletukset"]
    for j, n in enumerate(m.ASETUKSET, 1):
        with L.lukko, L.inferenssi():
            ketju = kuva.Ketju(L.ops, ah, aw, oh, ow, asetus_kuva(L, n, ol.get(str(n)), geo), yuv=False)
            rgb_o = ketju.rgb_lopullinen(L.ops.from_uint8(osa), 1000 * k)
            _tallenna_esikatselu(kd / f"v{n}_k{k}.png", kd, k, L.ops.uint8_hwc(rgb_o))
            del rgb_o, ketju
            L.tyhjenna()
        if edistys:
            edistys(j, len(m.ASETUKSET))
    m.kirjoita_json(kd / "alueet.json", alueet)
    tiedot = m.lue_json(kd / "koe.json", {}) or {}
    tiedot["alueet"] = alueet
    m.kirjoita_json(kd / "koe.json", tiedot)
    return alueet


def saatokuva_kuva(src, an, geo, wd, L, n, saato, k):
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    kd.mkdir(parents=True, exist_ok=True)
    alueet, kuvat = lahdealueet(src, an, geo, kd)
    k = max(1, min(len(kuvat), int(k)))
    avain = hashlib.sha1(json.dumps([n, saato, k, alueet[k - 1]], sort_keys=True).encode()).hexdigest()[:10]
    nimi = f"s_{avain}.png"
    if not (kd / nimi).exists():
        oh, ow = _alue_koko(alueet[k - 1], geo)
        with L.lukko, L.inferenssi():
            ketju = kuva.Ketju(L.ops, alueet[k - 1][3], alueet[k - 1][2], oh, ow, asetus_kuva(L, n, saato, geo),
                               yuv=False)
            rgb = ketju.rgb_lopullinen(L.ops.from_uint8(kuvat[k - 1]), 1000 * k)
            u = L.ops.uint8_hwc(rgb)
            L.palauta_kasvot(kuvat[k - 1][0], u, (saato or {}).get("kasvot"))
            _tallenna_esikatselu(kd / nimi, kd, k, u)
            del rgb, ketju
            L.tyhjenna()
    return f"koe_{tun}/{nimi}"


# ---------------------------------------------------------------- käsittely

def ulostulo_paate(an, valinta=None):
    """Tallennusmuoto: käyttäjän valinta, muuten JPEG-kuvista (ja iPhonen HEIC-kuvista) JPEG, muista PNG."""
    muoto = (valinta or {}).get("muoto") or "auto"
    if muoto == "jpg":
        return ".jpg"
    if muoto == "png":
        return ".png"
    return ".jpg" if an.get("muoto") in ("jpeg", "heic") else ".png"


def kasittele_kuva(src, an, geo, valinta, wd, L, ulos, edistys=None, peruuta=None):
    n = int(valinta.get("asetus") or an["suositus"])
    saato = valinta.get("saato") or mittaa_kuva(src, an, geo, wd)["oletukset"].get(str(n))
    t0 = time.time()
    rgb, alfa = pura(src, an)
    rgb = kaanna(rgb, geo.get("kierto", 0))
    alfa = kaanna(alfa, geo.get("kierto", 0)) if alfa is not None else None
    h, w, _ = rgb.shape
    oh, ow = geo["oh"], geo["ow"]
    tila = {"viim": 0.0}

    def palaute(f):
        if peruuta is not None and peruuta.is_set():
            raise m.Peruutettu()
        nyt = time.time()
        if edistys and nyt - tila["viim"] > 0.3:
            tila["viim"] = nyt
            kulunut = nyt - t0
            edistys({"tehty": int(f * 1000), "kaikki": 1000, "nopeus": None,
                     "jaljella_s": kulunut * (1 - f) / f if f > 0.03 else None})

    if edistys:
        edistys({"tehty": 0, "kaikki": 1000, "nopeus": None, "jaljella_s": None})
    with L.lukko, L.inferenssi():
        try:
            asetus = asetus_kuva(L, n, saato, geo)
            # läpinäkyvyys lasketaan samalla mallilla (terävät reunat), ilman raetta ja halonpoistoa
            osuus_rgb = 0.6 if alfa is not None and asetus.get("malli") else 1.0
            ketju = kuva.Ketju(L.ops, h, w, oh, ow, asetus, yuv=False)
            ketju.palaute = lambda f: palaute(f * osuus_rgb)
            ketju.pala_raja = PALOITTAIN_KUVA
            y = ketju.rgb_lopullinen(L.ops.from_uint8(rgb[None]), 0)
            del ketju
            tulos = L.ops.uint8_hwc(y)
            del y
            if float((saato or {}).get("kasvot") or 0) > 0:
                palaute(osuus_rgb * 0.98)
                L.palauta_kasvot(rgb, tulos, saato["kasvot"])
            alfa_ulos = None
            if alfa is not None and asetus.get("malli"):
                a2 = dict(asetus, rae=0.0, halo=0.0)
                ketju = kuva.Ketju(L.ops, h, w, oh, ow, a2, yuv=False)
                ketju.palaute = lambda f: palaute(osuus_rgb + (1 - osuus_rgb) * f)
                ketju.pala_raja = PALOITTAIN_KUVA
                ya = ketju.rgb_lopullinen(L.ops.from_uint8(np.repeat(alfa[None, :, :, None], 3, axis=3)), 0)
                del ketju
                alfa_ulos = L.ops.uint8_hwc(ya)[:, :, 1].copy()
                del ya
            elif alfa is not None:
                alfa_ulos = (skaalaa_alfa(alfa, oh, ow) * 255.0 + 0.5).astype(np.uint8)
        finally:
            L.tyhjenna()
    if edistys:
        edistys({"tehty": 1000, "kaikki": 1000, "nopeus": None, "jaljella_s": 0, "tallennetaan": True})
    ulos = Path(ulos)
    ulos.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as f:
        alku = f.read(4 << 20)
    if Path(src).suffix.lower() in HEIC:            # HEIC: EXIF ja väriprofiili JPEG-segmenteiksi
        _, _, exif, icc = avaa_heic(src)
        alku = jpeg_metatiedot(exif, icc)
    if ulos.suffix.lower() == ".jpg":
        if alfa_ulos is not None:          # JPEG ei tue läpinäkyvyyttä: valkoinen tausta
            a = alfa_ulos.astype(np.float32)[..., None] / 255.0
            tulos = (tulos.astype(np.float32) * a + 255.0 * (1 - a) + 0.5).astype(np.uint8)
        kirjoita_jpeg(ulos, tulos, alku)
    else:
        kirjoita_png(ulos, tulos, alfa_ulos, png_icc(alku))
    return {"ulos": str(ulos), "aika_s": round(time.time() - t0, 1), "koko": [ow, oh]}
