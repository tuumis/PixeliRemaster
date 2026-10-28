"""PixeliRemaster – käsittelymoottori.

Analyysi, AI-käsittely, koekuvat ja valmiin tiedoston kokoaminen. Kaikki laskenta tapahtuu
kuva.py:n Ketju-luokassa; tämä moduuli hoitaa videon purun, pakkauksen ja tiedostot.
"""
import hashlib
import json
import math
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import kuva

VERSIO = "1.8.1"
SOVELLUS = Path(__file__).resolve().parent.parent          # app-kansio
MALLIT = SOVELLUS / "mallit"


def _datakansio():
    if os.environ.get("PIXELI_DATA"):
        return Path(os.environ["PIXELI_DATA"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PixeliRemaster"
    return Path.home() / ".local" / "share" / "pixeliremaster"


DATA = _datakansio()
WORK = DATA / "tyo"
TOOLS = DATA / "ffmpeg"
LOKI = DATA / "loki.txt"
OSAN_PITUUS_S = 300                    # käsittely 5 min osissa (jatkaminen)
# pythonw.exe:stä käynnistetyt komentoriviohjelmat avaisivat muuten oman konsoli-ikkunan
PIILOTA = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW

MALLITIEDOSTOT = {
    "live": "2xLiveActionV1_SPAN_490000.pth",
    "vhs": "2xVHS2HD-RealPLKSR.pth",
    "ani": "2x_AniSD_AC_G6i2b_SPAN_190K.pth",
}

# Asetukset: tunniste -> parametrit. Nimet ja kuvaukset ovat käyttöliittymän käännöksissä.
ASETUKSET = {
    1: {"malli": None},                                                          # alkuperäinen
    2: {"malli": "live", "ai": 1.0, "rae": 0.30},                                # DVD-elokuva
    3: {"malli": "live", "ai": 0.85, "halo": 0.8, "halo_r": 2.5, "rae": 0.45},   # heikko lähde
    4: {"malli": "vhs", "ai": 1.0, "rae": 0.35},                                 # VHS / pehmeä
    5: {"malli": "ani", "ai": 1.0},                                              # piirretty
    6: {"malli": "monikuva", "ai": 0.8, "rae": 0.35},                            # terävin (kokeellinen)
}
KUVA_ASETUKSET = (1, 2, 3, 4, 5)       # monikuvamalli vain videoille
SAATO_AVAIMET = ("ai", "rae", "halo", "halo_r", "rae_koko", "kohina", "kasvot")
SAATO_RAJAT = {"ai": (0.0, 1.0), "rae": (0.0, 1.5), "halo": (0.0, 1.5), "halo_r": (1.0, 4.0), "rae_koko": (0.3, 2.0),
               "kohina": (0.0, 1.5), "kasvot": (0.0, 1.0)}

TUNNETUT_SUHTEET = [(4 / 3, "4:3"), (1.37, "1.37"), (1.5, "3:2"), (1.66, "1.66"), (16 / 9, "16:9"),
                    (1.85, "1.85"), (2.0, "2:1"), (2.2, "2.20"), (2.35, "2.35"), (2.39, "2.39")]
STD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
           Fraction(50), Fraction(60000, 1001), Fraction(60)]
VIDEOPAATTEET = {".mkv", ".vob", ".mpg", ".mpeg", ".mp4", ".m2v", ".ts", ".avi", ".m4v", ".mov", ".wmv"}


class Peruutettu(Exception):
    """Käyttäjä keskeytti; käsittely jatkuu myöhemmin valmiista osista."""


_loki_lukko = threading.Lock()


def loki(viesti):
    rivi = time.strftime("%Y-%m-%d %H:%M:%S ") + str(viesti)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with _loki_lukko, open(LOKI, "a", encoding="utf-8") as f:
            f.write(rivi + "\n")
    except OSError:
        pass
    if os.environ.get("PIXELI_LOKI_KONSOLIIN"):
        print(rivi, flush=True)


def ffbin(nimi):
    exe = TOOLS / (nimi + (".exe" if os.name == "nt" else ""))
    if exe.exists():
        return str(exe)
    p = shutil.which(nimi)
    if p:
        return p
    raise RuntimeError("ffmpeg_puuttuu")


def ffmpeg_ok():
    try:
        ffbin("ffmpeg")
        ffbin("ffprobe")
        return True
    except RuntimeError:
        return False


def suhde(s):
    if s is None:
        return None
    m = re.match(r"^\s*(\d+)\s*[:/]\s*(\d+)\s*$", str(s))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return Fraction(a, b) if a > 0 and b > 0 else None
    try:
        f = Fraction(str(s).replace(",", "."))
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def vakio_fps(f):
    """Pyöristää kuvanopeuden lähimpään standardiarvoon (esim. 29.9999 -> 30)."""
    if not f:
        return Fraction(25)
    lahin = min(STD_FPS, key=lambda s: abs(float(s) - float(f)))
    return lahin if abs(float(lahin) / float(f) - 1) < 0.003 else Fraction(f).limit_denominator(1001)


def tyokansio(src: Path) -> Path:
    tunnus = hashlib.sha1(str(Path(src).resolve()).encode("utf-8")).hexdigest()[:8]
    nimi = re.sub(r"[^\w\-]+", "_", Path(src).stem)[:40].strip("_") or "video"
    d = WORK / f"{nimi}_{tunnus}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tallenna_png(polku, kuva_hwc):
    h, w, _ = kuva_hwc.shape
    raaka = np.concatenate([np.zeros((h, 1), np.uint8), kuva_hwc.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raaka, 6)) + chunk(b"IEND", b""))
    tmp = Path(str(polku) + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(polku)


def rgb_png(polku, rgb_chw, leveys=None):
    """Float-RGB (3,h,w) 0..1 -> PNG. Valinnaisesti pienennetään (pinta-alakeskiarvo) annettuun leveyteen."""
    a = np.clip(rgb_chw.transpose(1, 2, 0), 0, 1)
    if leveys and a.shape[1] > leveys:
        k = int(math.ceil(a.shape[1] / leveys))
        h, w = a.shape[0] // k * k, a.shape[1] // k * k
        a = a[:h, :w].reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    tallenna_png(polku, (a * 255 + 0.5).astype(np.uint8))


def pysy_hereilla(paalla=True):
    """Estää Windowsia menemästä lepotilaan käsittelyn aikana."""
    if os.name == "nt":
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if paalla else 0))
        except Exception:  # noqa: BLE001
            pass


def nayton_resoluutio():
    """Päänäytön fyysinen resoluutio (Windows), Windowsin skaalauksesta (esim. 125 %) riippumatta.
    Muualla oletus 2560x1440."""
    if os.name == "nt":
        try:
            import ctypes
            user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
            hdc = user32.GetDC(0)
            try:
                w, h = gdi32.GetDeviceCaps(hdc, 118), gdi32.GetDeviceCaps(hdc, 117)   # DESKTOPHORZRES/VERTRES
            finally:
                user32.ReleaseDC(0, hdc)
            if w >= 640 and h >= 480:
                return int(w) // 2 * 2, int(h) // 2 * 2
        except Exception:  # noqa: BLE001
            pass
    return 2560, 1440


def aja(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, creationflags=PIILOTA,
                          encoding="utf-8", errors="replace")


def lue_json(p, oletus=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return oletus




def kirjoita_json(p, d):
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _tuplakuvat(kehykset):
    """Etsii toistuvat kuvat (esim. 24 kuvan elokuva venytetty 30 kuvaan monistamalla).
    Palauttaa (toistojakso N tai None, tuplien määrä)."""
    valit, tuplat, aktiivisia = [], 0, 0
    for seq in kehykset:
        if len(seq) < 20:
            continue
        d = np.abs(np.diff(seq, axis=0)).mean(axis=(1, 2))
        tarkka = d < 0.03
        # yksittäinen täsmälleen sama kuva (esim. XviD:n tyhjät kuvat) – naapurit eivät ole samoja
        # ja ympärillä on selvää muutosta (paikallaan pysyvissä kohtauksissa toistoja on muutenkin)
        selva = d > 0.25
        eristetty = tarkka & np.r_[False, selva[:-1]] & np.r_[selva[1:], False]
        # uudelleenpakatut toistot: selvästi pienempi ero kuin liikkeessä yleensä
        liikkuu = np.convolve(d, np.ones(5) / 5, "same") > 1.0
        mediaani = float(np.median(d[liikkuu])) if liikkuu.any() else 0.0
        suhteellinen = liikkuu & (d < max(0.15, min(0.6, 0.12 * mediaani)))
        on = eristetty | suhteellinen
        idx = np.nonzero(on)[0]
        tuplat += len(idx)
        aktiivisia += int((selva | on).sum())
        valit += list(np.diff(idx))
    if tuplat < 8 or not valit:
        return None, tuplat
    valit = np.array(valit)
    tiheys = tuplat / max(aktiivisia, 1)
    for N in (5, 6):
        if float((valit == N).mean()) >= 0.6 and 0.6 / N <= tiheys <= 1.5 / N:
            return N, tuplat
    return None, tuplat


def analysoi(src: Path, wd: Path):
    st = src.stat()
    avain = [st.st_size, int(st.st_mtime), VERSIO]
    vanha = lue_json(wd / "analyysi.json")
    if vanha and vanha.get("avain") == avain:
        return vanha

    loki(f"Analysoidaan {src.name}")
    p = aja([ffbin("ffprobe"), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", src])
    if p.returncode != 0:
        raise RuntimeError(f"Tiedostoa ei voitu lukea: {p.stderr.strip()[-400:]}")
    info = json.loads(p.stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    if not v:
        raise RuntimeError("Tiedostossa ei ole videokuvaa.")
    w, h = int(v["width"]), int(v["height"])
    koodekki = v.get("codec_name", "")
    n_tekstit = sum(1 for s_ in info.get("streams", []) if s_.get("codec_type") == "subtitle")

    def raidat(laji):
        tulos = []
        for s_ in info.get("streams", []):
            if s_.get("codec_type") != laji:
                continue
            tg = s_.get("tags") or {}
            tulos.append({"i": len(tulos), "kieli": (tg.get("language") or "").lower()[:3] or None,
                          "nimi": (tg.get("title") or "")[:60] or None, "koodekki": s_.get("codec_name"),
                          "kanavat": s_.get("channels"),
                          "oletus": bool((s_.get("disposition") or {}).get("default"))})
        return tulos
    aaniraidat, tekstiraidat = raidat("audio"), raidat("subtitle")

    huomiot = []
    sar = suhde(v.get("sample_aspect_ratio"))
    dar_levy = suhde(v.get("display_aspect_ratio"))
    if not sar:
        if dar_levy:
            sar = dar_levy * h / w
        elif (w, h) in ((720, 576), (720, 480), (704, 576), (704, 480), (352, 576), (352, 480)):
            sar = Fraction(4, 3) * h / w
            huomiot.append("oletettu_43")
        else:
            sar = Fraction(1)   # tiedostoissa ilman tietoa pikselit ovat käytännössä aina neliöitä
    dar_levy = sar * w / h

    kesto = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    vstart = float(v.get("start_time") or 0)
    avg, rfr = suhde(v.get("avg_frame_rate")), suhde(v.get("r_frame_rate"))
    fps = avg if avg and (not rfr or abs(float(avg) / float(rfr) - 1) < 0.02) else (rfr or avg)
    fps = vakio_fps(fps)

    # näytteet eri kohdista elokuvaa: lomitus + mustat palkit (täysi kuva) ja toistetut kuvat (pieni kuva)
    kohdat = [kesto * (0.08 + 0.84 * i / 7) for i in range(8)] if kesto > 60 else [0.0]
    tff = bff = prog = rep = rep_kaikki = 0
    rajaukset, kehykset = [], []
    for t in kohdat:
        cmd = [ffbin("ffmpeg"), "-hide_banner", "-nostats", "-nostdin"]
        if t > 0:
            cmd += ["-ss", f"{t:.2f}"]
        cmd += ["-i", src, "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", "idet,cropdetect=limit=24:round=2:reset=0,scale=64:48:flags=area,format=gray",
                "-frames:v", "250", "-f", "rawvideo", "-"]
        r = subprocess.run([str(c) for c in cmd], capture_output=True, creationflags=PIILOTA)
        e = r.stderr.decode("utf-8", "replace")
        kehykset.append(np.frombuffer(r.stdout, np.uint8)[: len(r.stdout) // 3072 * 3072]
                        .reshape(-1, 48, 64).astype(np.float32))
        m = re.findall(r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)", e)
        if m:
            a, b, c = map(int, m[-1])
            tff, bff, prog = tff + a, bff + b, prog + c
        m = re.findall(r"Repeated Fields:\s*Neither:\s*(\d+)\s*Top:\s*(\d+)\s*Bottom:\s*(\d+)", e)
        if m:
            n_, t_, b_ = map(int, m[-1])
            rep += t_ + b_
            rep_kaikki += n_ + t_ + b_
        c = re.findall(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)", e)
        if c:
            cw, ch, cx, cy = map(int, c[-1])
            if cw > 0 and ch > 0:
                rajaukset.append((cx, cy, cw, ch))

    # Aidosti lomitetussa kuvassa toinen kenttäjärjestys hallitsee selvästi.
    kaikki = tff + bff + prog
    lom_osuus = (max(tff, bff) - min(tff, bff)) / kaikki if kaikki else 0.0
    rep_osuus = rep / rep_kaikki if rep_kaikki else 0.0
    ntsc = abs(float(fps) - 29.97) < 0.1
    desim, tuplat = (None, 0)
    if ntsc and rep_osuus > 0.08 and lom_osuus > 0.1:
        tila, R = "ivtc", Fraction(24000, 1001)
    elif lom_osuus > 0.15 and float(fps) < 31:
        tila, R = "lomitettu", fps * 2
    else:
        tila, R = "progressiivinen", fps
        if float(fps) >= 29:
            desim, tuplat = _tuplakuvat(kehykset)
            if desim:
                tila, R = "desimointi", fps * (desim - 1) / desim

    # mustat palkit: kaikkien näytteiden yhdiste, ettei kuvaa leikata liikaa
    if rajaukset:
        x0 = min(r[0] for r in rajaukset) // 2 * 2
        y0 = min(r[1] for r in rajaukset) // 2 * 2
        x1 = min(w, (max(r[0] + r[2] for r in rajaukset) + 1) // 2 * 2)
        y1 = min(h, (max(r[1] + r[3] for r in rajaukset) + 1) // 2 * 2)
        rajaus = [x0, y0, x1 - x0, y1 - y0]
        if rajaus[2] * rajaus[3] < 0.4 * w * h:
            huomiot.append("rajaus_epavarma")
            rajaus = [0, 0, w, h]
    else:
        rajaus = [0, 0, w, h]

    if w >= 1280 or h >= 720:
        tyyppi, suositus = "hd", 1
    elif koodekki == "mpeg2video" and w in (704, 720) and h in (480, 576):
        tyyppi, suositus = "dvd", 2
    else:
        tyyppi, suositus = "heikko", 3
    cs = (v.get("color_space") or "").lower()
    matriisi = "bt709" if cs == "bt709" or tyyppi == "hd" else "bt601"

    d = {
        "avain": avain, "lahde": str(src), "leveys": w, "korkeus": h, "koodekki": koodekki,
        "sar": f"{sar.numerator}/{sar.denominator}",
        "dar_levy": f"{dar_levy.numerator}/{dar_levy.denominator}",
        "kesto": kesto, "vstart": vstart, "fps": f"{fps.numerator}/{fps.denominator}",
        "lomitus": tila, "lomitus_osuus": round(lom_osuus, 3), "toisto_osuus": round(rep_osuus, 3),
        "desimointi": desim, "tuplakuvia": tuplat,
        "R": f"{R.numerator}/{R.denominator}", "rajaus": rajaus, "huomiot": huomiot,
        "tyyppi": tyyppi, "suositus": suositus, "matriisi": matriisi, "tekstityksia": n_tekstit,
        "aaniraidat": aaniraidat, "tekstiraidat": tekstiraidat,
    }
    kirjoita_json(wd / "analyysi.json", d)
    return d


def geometria(an, valinta=None, kohde=(2560, 1440)):
    """Lasketaan rajaus ja lopputuloksen koko niin, ettei kuvasuhde muutu."""
    valinta = valinta or {}
    w, h = an["leveys"], an["korkeus"]
    sar = Fraction(an["sar"])
    if valinta.get("dar"):
        sar = suhde(valinta["dar"]) * h / w
    x, y, cw, ch = valinta.get("rajaus") or an["rajaus"]
    nayttosuhde = float(cw * sar / ch)          # rajatun kuvan todellinen muoto
    KW, KH = int(kohde[0]), int(kohde[1])
    if nayttosuhde >= KW / KH:
        ow = KW
        oh = int(round(KW / nayttosuhde / 2)) * 2
    else:
        oh = KH
        ow = int(round(KH * nayttosuhde / 2)) * 2
    lahin = min(TUNNETUT_SUHTEET, key=lambda t: abs(math.log(nayttosuhde / t[0])))
    tunnettu = abs(math.log(nayttosuhde / lahin[0])) < 0.03
    dar = sar * w / h
    return {"rajaus": [x, y, cw, ch], "ow": ow, "oh": oh, "suhde": nayttosuhde,
            "suhde_nimi": lahin[1] if tunnettu else None, "dar": f"{dar.numerator}:{dar.denominator}"}


def suodin_kuva(an):
    tila = an["lomitus"]
    if tila == "lomitettu":
        return "bwdif=mode=send_field:parity=auto:deint=all"
    if tila == "ivtc":
        return "fieldmatch=order=auto:combmatch=full,yadif=deint=interlaced,decimate"
    if tila == "desimointi":
        return f"decimate=cycle={an['desimointi']}"
    return None




_KOODAIN = {}


# ------------------------------------------------------------ tehonkäyttö
# Näytönohjaimen käyttöaste rajoitetaan pitämällä laskentaerien välissä tauko: jos erä vei t sekuntia,
# taukoa pidetään t·(1/osuus − 1). Keskimääräinen teho (ja lämpö ja tuulettimien ääni) laskee
# samassa suhteessa, käsittely kestää 1/osuus kertaa pidempään. Tason voi vaihtaa kesken käsittelyn.
TEHOTASOT = {"taysi": 1.0, "tasapaino": 0.65, "hiljainen": 0.4}
_TEHO = {"taso": "taysi"}


def aseta_teho(taso):
    _TEHO["taso"] = taso if taso in TEHOTASOT else "taysi"
    # rajoitetulla tasolla ohjelma (ja sen käynnistämät ffmpegit) väistää muita ohjelmia
    if os.name == "nt":
        try:
            import ctypes
            luokka = 0x20 if _TEHO["taso"] == "taysi" else 0x4000     # NORMAL / BELOW_NORMAL_PRIORITY_CLASS
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), luokka)
        except Exception:  # noqa: BLE001
            pass


def teho_osuus():
    return TEHOTASOT[_TEHO["taso"]]


def tehotauko(kesto, peruuta=None):
    """Tauko laskentaerän jälkeen valitun tehotason mukaan (keskeytys katkaisee tauon)."""
    s = teho_osuus()
    if s >= 1.0 or kesto <= 0:
        return
    tauko = min(10.0, kesto * (1.0 / s - 1.0))
    if peruuta is not None:
        peruuta.wait(tauko)
    else:
        time.sleep(tauko)


def koodain(muoto, pieni=False):
    """Valitsee parhaan toimivan koodaimen (NVENC näytönohjaimella, varalla prosessori).
    pieni=True: noin puolet pienempi tiedosto, hieman pehmeämpi rae."""
    avain = (muoto, bool(pieni))
    if avain in _KOODAIN:
        return _KOODAIN[avain]
    if muoto == "hevc":
        cq, crf = ("24", "22") if pieni else ("19", "18")
        ehdokkaat = [
            ["-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
             "-spatial_aq", "1", "-temporal_aq", "1", "-rc-lookahead", "32", "-bf", "3",
             "-b_ref_mode", "middle", "-profile:v", "main10", "-pix_fmt", "p010le"],
            ["-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
             "-profile:v", "main10", "-pix_fmt", "p010le"],
            # varalla prosessori; psy-asetukset säilyttävät filmirakeen
            ["-c:v", "libx265", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p10le",
             "-x265-params", "log-level=error:aq-mode=3:psy-rd=2.0:psy-rdoq=1.5:deblock=-1,-1"],
        ]
    else:   # koepätkät selaimeen
        ehdokkaat = [
            ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "17", "-b:v", "0", "-pix_fmt", "yuv420p"],
            ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p"],
        ]
    for e in ehdokkaat:
        p = aja([ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "testsrc2=s=640x360:d=0.5", "-pix_fmt", "yuv420p10le", *e, "-f", "null", "-"])
        if p.returncode == 0:
            _KOODAIN[avain] = e
            return e
    _KOODAIN[avain] = ehdokkaat[-1]
    return ehdokkaat[-1]


def aja_osa(src, an, geo, ketju, ops, ulos, alku, maara, muoto, lokit, png=None, edistys=None, era=2,
            peruuta=None, pikkukuva=None, pieni=False, rajoita=True):
    """Purkaa `maara` kuvaa kohdasta `alku`, käsittelee ja koodaa tiedostoon. Palauttaa kuvamäärän."""
    R = Fraction(an["R"])
    x, y, cw, ch = geo["rajaus"]
    ow, oh = geo["ow"], geo["oh"]
    suodin = [s for s in [suodin_kuva(an)] if s]
    if (cw, ch) != (an["leveys"], an["korkeus"]):
        suodin.append(f"crop={cw}:{ch}:{x}:{y}")
    mat = "bt709" if an.get("matriisi") == "bt709" else "bt601"
    suodin.append(f"scale=in_color_matrix={mat}:in_range=tv:flags=accurate_rnd+full_chroma_int+bicubic,"
                  "format=rgb24")
    rate = f"{R.numerator}/{R.denominator}"
    # monikuvamalli tarvitsee naapurikuvat myös osan reunoilla: puretaan muutama lisäkuva molemmin puolin
    ajallinen = getattr(ketju, "ajallinen", False)
    esi = jalki = 0
    if ajallinen:
        from .vsr import KONTEKSTI
        esi, jalki = min(KONTEKSTI, alku), KONTEKSTI
        ketju.nollaa()
        ketju.alku = alku - esi
    dek = [ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if alku - esi > 0:
        dek += ["-ss", f"{float((alku - esi) / R):.6f}"]
    dek += ["-i", src, "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", ",".join(suodin),
            "-fps_mode", "cfr", "-r", rate, "-frames:v", str(maara + esi + jalki),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    enk = [ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-video_size", f"{ow}x{oh}", "-framerate", rate,
           "-i", "pipe:0", *koodain(muoto, pieni),
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-color_range", "tv", "-chroma_sample_location", "left"]
    if str(ulos).endswith(".mp4"):
        enk += ["-movflags", "+faststart"]
    enk.append(ulos)

    lokit.mkdir(parents=True, exist_ok=True)
    dek_log = open(lokit / "purku.log", "w", encoding="utf-8", errors="replace")
    enk_log = open(lokit / "pakkaus.log", "w", encoding="utf-8", errors="replace")
    dp = subprocess.Popen([str(c) for c in dek], stdout=subprocess.PIPE, stderr=dek_log, creationflags=PIILOTA)
    ep = subprocess.Popen([str(c) for c in enk], stdin=subprocess.PIPE, stderr=enk_log, creationflags=PIILOTA)

    koko = cw * ch * 3
    q_in, q_out = queue.Queue(8), queue.Queue(6)
    virheet = []
    lopeta = threading.Event()

    def lukija():
        try:
            while not lopeta.is_set():
                b = dp.stdout.read(koko)
                if not b or len(b) < koko:
                    break
                q_in.put(np.frombuffer(b, np.uint8).reshape(ch, cw, 3))
        except Exception as e:  # noqa: BLE001
            virheet.append(e)
        finally:
            q_in.put(None)

    def kirjoittaja():
        rikki = False
        while True:
            k = q_out.get()
            if k is None:
                break
            if rikki:
                continue
            try:
                for rivi in k:
                    ep.stdin.write(rivi.tobytes())
            except Exception as e:  # noqa: BLE001
                virheet.append(e)
                rikki = True
        try:
            ep.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    tl = threading.Thread(target=lukija, daemon=True)
    tk = threading.Thread(target=kirjoittaja, daemon=True)
    tl.start()
    tk.start()
    n = 0
    normaali = False
    torch = getattr(ops, "torch", None)
    tila = {"tuotettu": 0, "viime_pk": 0.0}

    def valmiit(rgb):
        """Valmiit kuvat koodaimelle (monikuvamallilla kontekstikuvat jätetään pois)."""
        nonlocal n
        if rgb is None:
            return
        m_ = rgb.shape[0]
        a0, a1 = max(0, esi - tila["tuotettu"]), min(m_, esi + maara - tila["tuotettu"])
        tila["tuotettu"] += m_
        if a1 <= a0:
            return
        if (a0, a1) != (0, m_):
            rgb = rgb[a0:a1]
        if png:
            for i in range(rgb.shape[0]):
                if n + i in png:
                    a = np.clip(ops.numpy(rgb[i:i + 1])[0].transpose(1, 2, 0) * 255 + 0.5, 0, 255)
                    tallenna_png(png[n + i], a.astype(np.uint8))
        if pikkukuva and time.time() - tila["viime_pk"] > 4:
            tila["viime_pk"] = time.time()
            try:
                pikkukuva(ops.numpy(rgb[:1])[0])
            except Exception:  # noqa: BLE001
                pass
        q_out.put(ops.pakkaa(*ketju.yuv420p10(rgb)))
        n += rgb.shape[0]
        if edistys:
            edistys(n)

    try:
        konteksti = torch.inference_mode() if torch else _Tyhja()
        with konteksti:
            valmis = False
            while not valmis and not virheet:
                if peruuta is not None and peruuta.is_set():
                    raise Peruutettu()
                kuvat = []
                while len(kuvat) < era:
                    k = q_in.get()
                    if k is None:
                        valmis = True
                        break
                    kuvat.append(k)
                if not kuvat:
                    break
                t_era = time.perf_counter()
                xb = ops.from_uint8(np.stack(kuvat))
                valmiit(ketju.syota(xb) if ajallinen else ketju.rgb_lopullinen(xb, alku + n))
                if rajoita:
                    tehotauko(time.perf_counter() - t_era, peruuta)
            if ajallinen and not virheet:
                valmiit(ketju.loppu())
        normaali = True
    finally:
        q_out.put(None)
        if not normaali:
            lopeta.set()
            dp.kill()
            ep.kill()
        tk.join()
        if not normaali:
            while tl.is_alive():
                try:
                    q_in.get(timeout=0.2)
                except queue.Empty:
                    pass
        tl.join()
        dp.wait()
        ep.wait()
        dek_log.close()
        enk_log.close()

    if virheet or ep.returncode != 0 or dp.returncode not in (0, None):
        loki = (lokit / "pakkaus.log").read_text(encoding="utf-8", errors="replace")[-600:]
        loki += (lokit / "purku.log").read_text(encoding="utf-8", errors="replace")[-600:]
        raise RuntimeError(f"Videon käsittely epäonnistui. {virheet[:1]}\n{loki}")
    return n


class _Tyhja:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False




def testikuva(h=40, w=56):
    """Toistettava testikuva: liukuvärejä, teräviä reunoja, hienoa kuviota ja kohinaa."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 0.5 + 0.4 * np.sin(xx / 5.0) * np.cos(yy / 7.0)
    g = (xx / w) * 0.8 + 0.1
    b = ((xx // 8 + yy // 8) % 2) * 0.6 + 0.2
    kuva_ = np.stack([r, g, b])
    kuva_[:, h // 3:h // 3 + 3, :] = 0.95
    kuva_ += np.random.default_rng(5).normal(0, 0.03, kuva_.shape).astype(np.float32)
    return np.clip(kuva_, 0, 1)[None].astype(np.float32)


def itsetesti(edistys=None):
    """Ajaa samat laskut prosessorilla (numpy-referenssi) ja näytönohjaimella ja vertaa tuloksia.
    Mittaa lisäksi nopeuden. Tulos tallennetaan tiedostoon itsetesti.json."""
    import torch
    tulos = {"versio": VERSIO, "ok": True, "virheet": [], "nopeus": {}, "naytonohjain": None,
             "fp32_mallit": []}
    if not torch.cuda.is_available():
        tulos["ok"] = False
        tulos["virheet"].append("NVIDIA-näytönohjain ei ole PyTorchin käytettävissä (CUDA).")
        kirjoita_json(DATA / "itsetesti.json", tulos)
        return tulos
    tulos["naytonohjain"] = torch.cuda.get_device_name(0)
    vanhat = (torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32)
    torch.backends.cudnn.allow_tf32 = torch.backends.cuda.matmul.allow_tf32 = False
    on = kuva.NumpyOps()
    ot32 = kuva.TorchOps("cuda", puolitarkkuus=False)
    ot16 = kuva.TorchOps("cuda", puolitarkkuus=True)
    x = testikuva()
    h, w = x.shape[2], x.shape[3]
    oh, ow = 120, 168
    sd_cache = {}
    try:
        with torch.inference_mode():
            for n, cfg in ASETUKSET.items():
                if cfg.get("malli") == "monikuva":
                    continue                       # tarkistetaan erikseen alla
                a = {k: v for k, v in cfg.items() if k not in ("nimi", "kuvaus", "rae")}
                if a.get("malli"):
                    nimi = a["malli"]
                    if nimi not in sd_cache:
                        sd_cache[nimi] = kuva.lataa_pth(MALLIT / MALLITIEDOSTOT[nimi])
                    sd = sd_cache[nimi]
                    an, a32, a16 = dict(a), dict(a), dict(a)
                    an["malli"] = kuva.Malli(on, sd)
                    a32["malli"] = kuva.Malli(ot32, sd)
                    m16 = kuva.Malli(ot16, sd)
                    m16.puolita()
                    a16["malli"] = m16
                else:
                    an = a32 = a16 = a
                rn = kuva.Ketju(on, h, w, oh, ow, an).rgb_lopullinen(x)
                k32 = kuva.Ketju(ot32, h, w, oh, ow, a32)
                r32 = k32.rgb_lopullinen(ot32.tensor(x))
                r16 = kuva.Ketju(ot16, h, w, oh, ow, a16).rgb_lopullinen(ot16.tensor(x))
                e32 = float(np.abs(ot32.numpy(r32) - rn).max())
                e16 = float(np.abs(ot16.numpy(r16) - rn).mean())
                yn = on.pakkaa(*kuva.Ketju(on, h, w, oh, ow, an).yuv420p10(rn))
                yt = ot32.pakkaa(*k32.yuv420p10(r32))
                eyuv = int(np.abs(yn.astype(np.int32) - yt.astype(np.int32)).max())
                ok = e32 < 2e-3 and eyuv <= 1
                ok16 = e16 < 6e-3 and bool(np.isfinite(ot16.numpy(r16)).all())
                tulos[f"asetus{n}"] = {"ero_fp32": e32, "ero_fp16_keskim": e16, "ero_yuv": eyuv,
                                       "ok": bool(ok), "fp16_ok": ok16}
                if a.get("malli") and not ok16 and a["malli"] not in tulos["fp32_mallit"]:
                    tulos["fp32_mallit"].append(a["malli"])
                if edistys:
                    edistys("tarkkuus", n, len(ASETUKSET))
                if not ok:
                    tulos["ok"] = False
                    tulos["virheet"].append(f"Asetus {n}: tulokset eroavat (fp32 {e32}, yuv {eyuv})")
            # kohinanpoisto (DRUNet), jos malli on ladattu
            from . import lisamallit
            kp = lisamallit.polku("kohina")
            if kp:
                sd = kuva.lataa_pth(kp)
                xk = testikuva(48, 64)
                dn = kuva.Kohinanpoisto(on, sd)(xk, 10.0)
                d32 = ot32.numpy(kuva.Kohinanpoisto(ot32, sd)(ot32.tensor(xk), 10.0))
                k16 = kuva.Kohinanpoisto(ot16, sd)
                k16.puolita()
                d16 = ot16.numpy(k16(ot16.tensor(xk), 10.0))
                e32, e16 = float(np.abs(d32 - dn).max()), float(np.abs(d16 - dn).mean())
                tulos["kohina"] = {"ero_fp32": e32, "ero_fp16_keskim": e16}
                if e32 > 2e-3:
                    tulos["ok"] = False
                    tulos["virheet"].append(f"Kohinanpoisto: tulokset eroavat ({e32})")
                if not (e16 < 6e-3 and bool(np.isfinite(d16).all())):
                    tulos["fp32_mallit"].append("kohina")
            # monikuvamalli (RealBasicVSR), jos ladattu: lyhyt kuvasarja
            vp = lisamallit.polku("monikuva")
            if vp:
                from . import vsr
                sd = kuva.lataa_pth(vp)
                xs = [np.roll(testikuva(64, 96), (k, 2 * k), (2, 3)) for k in range(3)]
                vn = vsr.RealBasicVSR(on, sd)(xs)
                v32 = vsr.RealBasicVSR(ot32, sd)([ot32.tensor(v) for v in xs])
                m16 = vsr.RealBasicVSR(ot16, sd)
                m16.puolita()
                v16 = m16([ot16.tensor(v) for v in xs])
                e32 = max(float(np.abs(ot32.numpy(a_) - b_).max()) for a_, b_ in zip(v32, vn))
                e16 = float(np.mean([np.abs(ot16.numpy(a_) - b_).mean() for a_, b_ in zip(v16, vn)]))
                tulos["monikuva"] = {"ero_fp32": e32, "ero_fp16_keskim": e16}
                if e32 > 5e-3:
                    tulos["ok"] = False
                    tulos["virheet"].append(f"Monikuvamalli: tulokset eroavat ({e32})")
                if not (e16 < 6e-3 and all(bool(np.isfinite(ot16.numpy(v)).all()) for v in v16)):
                    tulos["fp32_mallit"].append("monikuva")
            # rae: tilastollinen tarkistus
            a = {"rae": 1.0}
            kr = kuva.Ketju(ot32, h, w, 288, 384, a)
            base = kuva.Ketju(ot32, h, w, 288, 384, {}).rgb_lopullinen(ot32.tensor(x))
            dz = ot32.numpy(kr.rgb_lopullinen(ot32.tensor(x), 0) - base)
            std = float(dz.std() * 255)
            if not (0.8 < std < 3.5):
                tulos["ok"] = False
                tulos["virheet"].append(f"Filmirakeen voimakkuus poikkeava ({std:.2f})")
    finally:
        torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32 = vanhat

    kirjoita_json(DATA / "itsetesti.json", tulos)   # fp32_mallit käyttöön nopeusmittaukselle
    # nopeus täysikokoisella DVD-kuvalla. Ensin valitaan tälle näytönohjaimelle nopein laskentatapa
    # (muistijärjestys ja eräkoko): vaihtoehto otetaan käyttöön vain, jos se on selvästi nopeampi
    # ja tulos pysyy samana.
    tulos["optimointi"], tulos["optimointi_mittaukset"] = _valitse_optimointi()
    kirjoita_json(DATA / "itsetesti.json", tulos)
    L = Laite(optimointi=tulos["optimointi"])
    for n in ASETUKSET:
        if ASETUKSET[n].get("malli") == "monikuva":
            continue
        tulos["nopeus"][str(n)] = round(_mittaa_nopeus(L, n, L.era), 1)
        if edistys:
            edistys("nopeus", n, len(ASETUKSET))
    from . import lisamallit
    if lisamallit.polku("kohina"):
        D = L.kohinamalli()
        xb = L.ops.tensor(np.repeat(testikuva(576, 720), 2, axis=0))
        with torch.inference_mode():
            for _ in range(2):
                D(xb, 8.0)
            torch.cuda.synchronize()
            t = time.time()
            for _ in range(4):
                D(xb, 8.0)
            torch.cuda.synchronize()
        tulos["nopeus"]["kohina"] = round(8 / (time.time() - t), 1)
    if lisamallit.polku("monikuva"):
        n6 = next(n for n, a in ASETUKSET.items() if a.get("malli") == "monikuva")
        from .vsr import IKKUNA, KONTEKSTI
        k = L.ketju({"rajaus": [0, 0, 720, 576], "ow": 1920, "oh": 1440}, n6, None, {})
        x1 = L.ops.tensor(testikuva(576, 720))
        with torch.inference_mode():
            k.syota(L.ops.cat_n([x1] * 4))              # lämmittely
            k.loppu()
            k.nollaa()
            torch.cuda.synchronize()
            t = time.time()
            maara = IKKUNA + 2 * KONTEKSTI
            for j in range(maara):
                y = k.syota(x1)
                if y is not None:
                    L.ops.pakkaa(*k.yuv420p10(y))
            y = k.loppu()
            if y is not None:
                L.ops.pakkaa(*k.yuv420p10(y))
            torch.cuda.synchronize()
        # mittauksessa laskettiin maara + KONTEKSTI kuvaa; pitkässä videossa IKKUNA + 2*KONTEKSTI per IKKUNA
        suhde = (maara + KONTEKSTI) / maara / ((IKKUNA + 2 * KONTEKSTI) / IKKUNA)
        tulos["nopeus"][str(n6)] = round(maara / (time.time() - t) * suhde, 2)
        del k
    L.vapauta()
    tulos["valmis"] = True
    kirjoita_json(DATA / "itsetesti.json", tulos)
    return tulos




def _mittaa_nopeus(L, n, era, kierrokset=4, tulos_out=None):
    """Kuvaa sekunnissa: DVD-kuva (720x576) -> 1920x1440 asetuksella n, era kuvaa kerrallaan."""
    import torch
    xb = L.ops.from_uint8(np.repeat((testikuva(576, 720)[0].transpose(1, 2, 0) * 255).astype(np.uint8)[None],
                                    era, axis=0))
    k = kuva.Ketju(L.ops, 576, 720, 1440, 1920, L.asetus(n))
    synk = torch.cuda.synchronize if torch.cuda.is_available() else (lambda: None)
    with torch.inference_mode():
        ensimmainen = L.ops.pakkaa(*k(xb, 0))       # vertailuun: ensimmäinen kuva samalla siemenellä
        L.ops.pakkaa(*k(xb))
        synk()
        t = time.perf_counter()
        for i in range(kierrokset):
            L.ops.pakkaa(*k(xb, i * era))
        synk()
        fps = era * kierrokset / (time.perf_counter() - t)
        if tulos_out is not None:
            tulos_out.append(np.asarray(ensimmainen)[:1])
    del k
    return fps


def _valitse_optimointi():
    """Mittaa suosituimmalla asetuksella (2) vaihtoehdot ja palauttaa nopeimman sekä mittaukset."""
    import torch
    vaihtoehdot = [{"kanavat_viimeiseksi": False, "era": 2}, {"kanavat_viimeiseksi": True, "era": 2},
                   {"kanavat_viimeiseksi": False, "era": 4}, {"kanavat_viimeiseksi": True, "era": 4}]
    mitatut, viite = [], None
    for v in vaihtoehdot:
        try:
            L = Laite(optimointi=v)
            ulos = []
            fps = max(_mittaa_nopeus(L, 2, v["era"], 3, ulos), _mittaa_nopeus(L, 2, v["era"], 3))
            y = ulos[0].astype(np.int32)
            if viite is None:
                viite = y
            n_ = min(len(viite), len(y))
            ero = float(np.abs(viite[:n_] - y[:n_]).mean())
            mitatut.append(dict(v, fps=round(fps, 2), ero=round(ero, 3)))
            L.vapauta()
            del L
            torch.cuda.empty_cache()
        except Exception as e:  # noqa: BLE001
            mitatut.append(dict(v, virhe=str(e)[:200]))
    perus = mitatut[0].get("fps") or 0
    paras = vaihtoehdot[0]
    paras_fps = perus
    for v, mt in zip(vaihtoehdot, mitatut):
        # vähintään 5 % nopeampi ja sama tulos (10-bittisinä arvoina keskimäärin < 2 yksikön ero)
        if mt.get("fps") and mt["fps"] > paras_fps * 1.05 and mt.get("ero", 99) < 2.0:
            paras, paras_fps = v, mt["fps"]
    loki(f"Laskentatavan valinta: {mitatut} -> {paras}")
    return dict(paras), mitatut


def ulkoiset_tekstitykset(src):
    """Elokuvan vieressä olevat .srt-tiedostot (samanniminen, tai ainoa kansiossa)."""
    kansio = src.parent
    srt = sorted(kansio.glob("*.srt"))
    omat = [s for s in srt if s.stem.lower().startswith(src.stem.lower())]
    if omat:
        return omat
    videot = [f for f in kansio.iterdir() if f.suffix.lower() in VIDEOPAATTEET and "[AI-skaalattu]" not in f.name]
    return srt if len(srt) == 1 and len(videot) == 1 else []


PUHE_DYNAMIIKKA = ("acompressor=threshold=0.08:ratio=4:attack=10:release=220:makeup=3,"
                   "dynaudnorm=f=250:g=11:p=0.9:m=6,alimiter=limit=0.89,aresample=48000")


def puhe_suodin(kanavat):
    """Selkeä puhe läppärin kaiuttimille: 5.1-äänestä keskikanava (puhe) korostettuna stereoksi,
    sitten voimakkuuserojen tasaus (hiljaiset repliikit esiin, räjähdykset hillitymmiksi)."""
    if int(kanavat or 2) >= 6:
        alku = "aformat=channel_layouts=5.1,pan=stereo|FL=0.8*FC+0.35*FL+0.25*BL|FR=0.8*FC+0.35*FR+0.25*BR"
    else:
        alku = "aformat=channel_layouts=stereo"
    return f"{alku},{PUHE_DYNAMIIKKA}"


def yhdista(src, an, osat, lista, ulos, valinta=None, lisatekstit=None):
    """Kokoaa valmiin tiedoston: AI-video + alkuperäiset (tai valitut) ääniraidat, tekstitykset ja kappaleet.
    Lisäksi (valinnaisesti) selkeän puheen stereoraita ja DVD-tekstityksistä tunnistetut tekstiraidat."""
    valinta = valinta or {}
    lisatekstit = lisatekstit or []
    lista.write_text("".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n" for p in osat),
                     encoding="utf-8")
    tmp = ulos.with_name(ulos.stem + ".kesken.mkv")
    srt = ulkoiset_tekstitykset(src) if valinta.get("ulkoiset", True) else []
    aanet, tekstit = valinta.get("aanet"), valinta.get("tekstit")
    a_map = ["-map", "1:a?"] if aanet is None else [x for i in aanet for x in ("-map", f"1:a:{int(i)}")]
    s_map = ["-map", "1:s?"] if tekstit is None else [x for i in tekstit for x in ("-map", f"1:s:{int(i)}")]
    pohja = [ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
             "-itsoffset", f"{an['vstart']:.6f}", "-f", "concat", "-safe", "0", "-i", lista, "-i", src]
    for s_ in srt:
        pohja += (["-i", s_] if _utf8(s_) else ["-sub_charenc", "CP1252", "-i", s_])
    for t_ in lisatekstit:
        pohja += ["-i", str(t_["srt"])]
    ar = an.get("aaniraidat") or []
    puhe = []
    if valinta.get("puhe") is not False and ar:
        li = int(aanet[0]) if aanet else 0
        r = next((x for x in ar if int(x.get("i", -1)) == li), ar[0])
        na = len(ar) if aanet is None else len(aanet)
        puhe = ["-filter_complex", f"[1:a:{li}]{puhe_suodin(r.get('kanavat'))}[puhe]", "-map", "[puhe]",
                f"-c:a:{na}", "aac", f"-b:a:{na}", "192k", f"-metadata:s:a:{na}", "title=Selkeä puhe (stereo)",
                f"-metadata:s:a:{na}", f"language={r.get('kieli') or 'und'}", f"-disposition:a:{na}", "default"]
        for i in range(na):
            puhe += [f"-disposition:a:{i}", "0"]
    loppu = ["-map_metadata", "1", "-map_chapters", "1", "-max_interleave_delta", "0",
             "-metadata:s:v:0", "title=PixeliRemaster", "-disposition:v:0", "default", tmp]
    ns = int(an.get("tekstityksia", 0)) if tekstit is None else len(tekstit)

    def ulkoiset(alku):
        m = []
        for j in range(len(srt)):
            m += ["-map", f"{2 + j}:s:0", f"-c:s:{alku + j}", "srt"]
        if any(t_.get("oletus") for t_ in lisatekstit):
            for i in range(alku):
                m += [f"-disposition:s:{i}", "0"]
        for j, t_ in enumerate(lisatekstit):     # tunnistetut tekstit heti levyn raitojen jälkeen
            k = alku + len(srt) + j
            m += ["-map", f"{2 + len(srt) + j}:s:0", f"-c:s:{k}", "srt",
                  f"-metadata:s:s:{k}", f"title={t_['otsikko']}", f"-metadata:s:s:{k}", f"language={t_['kieli']}"]
            if t_.get("oletus"):
                m += [f"-disposition:s:{k}", "default"]
        return m
    yritykset = [
        (["-map", "0:v:0", *a_map, *s_map, "-c", "copy", *ulkoiset(ns)], None),
        (["-map", "0:v:0", *a_map, *s_map, "-c", "copy", "-c:s", "srt", *ulkoiset(ns)],
         "levyn tekstitykset muunnettiin SRT-muotoon"),
        (["-map", "0:v:0", *a_map, "-c", "copy", *ulkoiset(0)],
         "levyn sisäisiä tekstityksiä ei voitu kopioida"),
        (["-map", "0:v:0", *a_map, "-c", "copy"], "tekstityksiä ei voitu kopioida"),
    ]
    for liput, huom in yritykset:
        p = aja(pohja + liput + puhe + loppu)
        if p.returncode == 0:
            break
    else:
        raise RuntimeError(f"Yhdistäminen epäonnistui: {p.stderr[-800:]}")
    tmp.replace(ulos)
    koodit = {None: None, "levyn tekstitykset muunnettiin SRT-muotoon": "tekstit_srt",
              "levyn sisäisiä tekstityksiä ei voitu kopioida": "tekstit_sisaiset_pois",
              "tekstityksiä ei voitu kopioida": "tekstit_pois"}
    lisatyt = [s_.name for s_ in srt] if huom != "tekstityksiä ei voitu kopioida" else []
    return {"ulkoiset_tekstitykset": lisatyt, "huomio": koodit.get(huom), "puhe": bool(puhe),
            "tekstiksi": len(lisatekstit) if huom != "tekstityksiä ei voitu kopioida" else 0}


def _utf8(p):
    try:
        Path(p).read_bytes().decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False




# ---------------------------------------------------------------- laite ja mallit

class Laite:
    """Näytönohjain (tai testitilassa numpy) ja ladatut mallit. Yksi Laite per säie."""

    def __init__(self, laite=None, optimointi=None):
        laite = laite or os.environ.get("PIXELI_LAITE", "auto")
        self._mallit = {}
        self.lukko = threading.Lock()
        self.era = 2
        if laite == "numpy":                 # testitila ilman PyTorchia (hyvin hidas)
            self.torch, self.cuda, self.ops = None, False, kuva.NumpyOps()
            return
        import torch
        self.torch = torch
        self.cuda = laite != "cpu" and torch.cuda.is_available()
        if optimointi is None:               # itsetestin mittaama nopein tapa tällä koneella
            optimointi = (lue_json(DATA / "itsetesti.json", {}) or {}).get("optimointi") or {}
        self.optimointi = dict(optimointi)
        self.era = int(optimointi.get("era") or 2) if self.cuda else 2
        self.ops = kuva.TorchOps("cuda" if self.cuda else "cpu", puolitarkkuus=self.cuda,
                                 kanavat_viimeiseksi=bool(optimointi.get("kanavat_viimeiseksi")))
        if self.cuda:
            torch.backends.cudnn.benchmark = True

    def malli(self, nimi):
        if nimi not in self._mallit:
            p = MALLIT / MALLITIEDOSTOT[nimi]
            if not p.exists():
                raise RuntimeError("malli_puuttuu")
            m = kuva.Malli(self.ops, kuva.lataa_pth(p))
            t = lue_json(DATA / "itsetesti.json", {}) or {}
            if nimi not in t.get("fp32_mallit", []):
                m.puolita()
            self._mallit[nimi] = m
        return self._mallit[nimi]

    def asetus(self, n, saato=None, kuva=False):
        a = dict(ASETUKSET[int(n)])
        if kuva:
            from .kuvat import KUVA_MUUTOKSET
            a.update(KUVA_MUUTOKSET)
        for k, v in (saato or {}).items():
            if k in SAATO_AVAIMET and v is not None:
                lo, hi = SAATO_RAJAT[k]
                a[k] = min(hi, max(lo, float(v)))
        if a.get("malli") == "monikuva":
            a["monikuva"], a["malli"] = self.monikuvamalli(), None
        a["malli"] = self.malli(a["malli"]) if a.get("malli") else None
        if float(a.get("kohina", 0) or 0) > 0:
            a["kohinamalli"] = self.kohinamalli()
        return a

    def kasvomallit(self):
        """(tunnistin, GFPGAN); ladataan ensimmäisellä käytöllä (noin 460 Mt). GFPGAN lasketaan fp32:na."""
        if "kasvot" not in self._mallit:
            from . import kasvot, lisamallit
            T = kasvot.Tunnistin(self.ops, kuva.lataa_pth(lisamallit.varmista("tunnistin")))
            G = kasvot.GFPGAN(self.ops, kuva.lataa_pth(lisamallit.varmista("kasvot")))
            self._mallit["kasvot"] = (T, G)
        return self._mallit["kasvot"]

    def palauta_kasvot(self, lahde_u8, tulos_u8, voimakkuus):
        """Kasvojen palautus valmiiseen kuvaan (paikallaan), jos voimakkuus > 0."""
        if not voimakkuus or float(voimakkuus) <= 0:
            return 0
        from . import kasvot
        T, G = self.kasvomallit()
        with self.inferenssi():
            return kasvot.palauta(self.ops, T, G, np.ascontiguousarray(lahde_u8), tulos_u8, float(voimakkuus))

    def monikuvamalli(self):
        """RealBasicVSR "Terävin"-asetukseen; ladataan ensimmäisellä käytöllä (noin 20 Mt)."""
        if "monikuva" not in self._mallit:
            from . import vsr, lisamallit
            net = vsr.RealBasicVSR(self.ops, kuva.lataa_pth(lisamallit.varmista("monikuva")))
            t = lue_json(DATA / "itsetesti.json", {}) or {}
            if "monikuva" not in t.get("fp32_mallit", []):
                net.puolita()
            self._mallit["monikuva"] = net
        return self._mallit["monikuva"]

    def korjausmallit(self):
        """(naarmuntunnistin, LaMa) vanhojen kuvien korjaukseen; ladataan ensimmäisellä käytöllä. fp32."""
        if "korjaus" not in self._mallit:
            from . import korjaus, lisamallit
            T = korjaus.Naarmuntunnistin(self.ops, kuva.lataa_pth(lisamallit.varmista("naarmut")))
            LM = korjaus.LaMa(self.ops, kuva.lataa_pth(lisamallit.varmista("taytto")))
            self._mallit["korjaus"] = (T, LM)
        return self._mallit["korjaus"]

    def varimalli(self):
        """DDColor; painot ovat puolitarkkuudella, laskenta fp32:na."""
        if "vari" not in self._mallit:
            from . import vari, lisamallit
            self._mallit["vari"] = vari.DDColor(self.ops, kuva.lataa_pth(lisamallit.varmista("vari")))
        return self._mallit["vari"]

    def korjaa_kuva(self, rgb_u8, naarmut=False, varita=False, palaute=None):
        """Vanhan kuvan korjaukset ennen parannusta: naarmujen ja pölyn poisto, väritys."""
        from . import korjaus, vari
        osat = [n for n, k in (("naarmut", naarmut), ("vari", varita)) if k]
        tulos = rgb_u8
        with self.inferenssi():
            for i, osa in enumerate(osat):
                def pal(f, i=i):
                    if palaute:
                        palaute((i + f) / len(osat))
                if osa == "naarmut":
                    T, LM = self.korjausmallit()
                    maski = korjaus.tunnista(self.ops, T, tulos)
                    tulos = korjaus.tayta(self.ops, LM, tulos, maski, pal)
                else:
                    tulos = vari.varita(self.ops, self.varimalli(), tulos, pal)
                self.tyhjenna()
        return tulos

    def kohinamalli(self):
        if "kohina" not in self._mallit:
            from . import lisamallit
            m_ = kuva.Kohinanpoisto(self.ops, kuva.lataa_pth(lisamallit.varmista("kohina")))
            t = lue_json(DATA / "itsetesti.json", {}) or {}
            if "kohina" not in t.get("fp32_mallit", []):
                m_.puolita()
            self._mallit["kohina"] = m_
        return self._mallit["kohina"]

    def ketju(self, geo, n, saato, an):
        a = self.asetus(n, saato)
        if a.get("monikuva") is not None:
            from . import vsr
            return vsr.MonikuvaKetju(self.ops, geo["rajaus"][3], geo["rajaus"][2], geo["oh"], geo["ow"],
                                     a, an.get("matriisi", "bt601"))
        return kuva.Ketju(self.ops, geo["rajaus"][3], geo["rajaus"][2], geo["oh"], geo["ow"],
                          a, an.get("matriisi", "bt601"))

    def inferenssi(self):
        return self.torch.inference_mode() if self.torch else _Tyhja()

    def vapauta(self):
        self._mallit.clear()
        if self.cuda:
            self.torch.cuda.empty_cache()

    def tyhjenna(self):
        """Vapauttaa välitulosten muistin (mallit säilyvät)."""
        if self.cuda:
            self.torch.cuda.empty_cache()


# ---------------------------------------------------------------- korkean tason toiminnot

def kuvasuodin(an, geo):
    x, y, cw, ch = geo["rajaus"]
    suodin = [s for s in [suodin_kuva(an)] if s]
    if (cw, ch) != (an["leveys"], an["korkeus"]):
        suodin.append(f"crop={cw}:{ch}:{x}:{y}")
    mat = "bt709" if an.get("matriisi") == "bt709" else "bt601"
    suodin.append(f"scale=in_color_matrix={mat}:in_range=tv:flags=accurate_rnd+full_chroma_int+bicubic,"
                  "format=rgb24")
    return ",".join(suodin)


KOE_KOHDAT = (0.25, 0.6)          # esikatselun tasavälein valitut kohdat (osuus kestosta); 3. = vaikein kohta
MITTAUS_NAYTTEET = 16             # laadun mittaus: kuvia tasaisesti koko videon matkalta


def _pura_kuvat(src, an, geo, t, maara):
    """Purkaa `maara` kuvaa kohdasta t (sekunteina) lomituksen purun ja rajauksen jälkeen."""
    x, y, cw, ch = geo["rajaus"]
    R = Fraction(an["R"])
    r = subprocess.run([ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-map", "0:v:0",
                        "-an", "-sn", "-dn", "-vf", kuvasuodin(an, geo), "-fps_mode", "cfr",
                        "-r", f"{R.numerator}/{R.denominator}", "-frames:v", str(maara),
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True,
                       creationflags=PIILOTA)
    koko = cw * ch * 3
    n_ = len(r.stdout) // koko
    return np.frombuffer(r.stdout[:n_ * koko], np.uint8).reshape(-1, ch, cw, 3)


def mittausnaytteet(src, an, geo, maara=MITTAUS_NAYTTEET):
    """[(aika_s, kuva)] tasaisesti koko videon matkalta (alku- ja lopputekstit jäävät väliin)."""
    from concurrent.futures import ThreadPoolExecutor
    kesto = float(an.get("kesto") or 0)
    ajat = [kesto * (0.04 + 0.92 * i / (maara - 1)) for i in range(maara)] if kesto > 20 else [0.0]

    def yksi(t):
        k = _pura_kuvat(src, an, geo, t, 2)          # 2 kuvaa: lomituksen purku tarvitsee naapurin
        return (t, k[-1]) if len(k) else None
    with ThreadPoolExecutor(max_workers=4) as ex:
        tulos = [r for r in ex.map(yksi, ajat) if r is not None]
    if not tulos:
        raise RuntimeError("purku_epaonnistui")
    return tulos


def esikatselukohdat(src, an, geo, kd):
    """Esikatselukohtien ajat sekunteina: käyttäjän siirtämät, muuten mittauksen valitsemat."""
    oma = lue_json(kd / "kohdat.json")
    if oma and len(oma.get("ajat", [])) == 3:
        return [float(t) for t in oma["ajat"]]
    mt = lue_json(kd / "mittaus.json") or {}
    if mt.get("v") == 2 and mt.get("kohdat"):
        return [float(t) for t in mt["kohdat"]]
    kesto = float(an.get("kesto") or 0)
    return [kesto * f for f in KOE_KOHDAT] + [kesto * 0.85]


def lahdekuvat(src, an, geo, kd):
    """Purkaa kolme lähdekuvaa (lomituksen purun ja rajauksen jälkeen) esikatselua varten. Jokaisen
    ympäriltä tallennetaan myös lyhyt kuvasarja monikuvamallia varten (jakso_i.npy)."""
    from .vsr import KONTEKSTI
    R = Fraction(an["R"])
    kuvat = []
    for i, t0 in enumerate(esikatselukohdat(src, an, geo, kd), 1):
        p = kd / f"lahde_{i}.npy"
        if not p.exists():
            jakso, k = np.zeros((0,)), 0
            # säiliön kesto voi olla videota pidempi (esim. tekstitykset), joten yritetään aiemmin
            for t in (t0, t0 * 0.5, 0.0):
                t = max(0.0, t)
                alku = max(0.0, t - KONTEKSTI / float(R))
                jakso = _pura_kuvat(src, an, geo, alku, 2 * KONTEKSTI + 1)
                if len(jakso):
                    k = min(len(jakso) - 1, int(round((t - alku) * float(R))))
                    break
            if not len(jakso):
                raise RuntimeError("purku_epaonnistui")
            np.save(kd / f"jakso_{i}.npy", jakso)
            kirjoita_json(kd / f"jakso_{i}.json", {"k": k})
            np.save(p, jakso[k:k + 1])
        kuvat.append(np.load(p))
    return kuvat


def lahdejakso(kd, i):
    """Esikatselukohdan i kuvasarja ja lähdekuvan paikka siinä."""
    j = np.load(kd / f"jakso_{i}.npy")
    return j, int((lue_json(kd / f"jakso_{i}.json", {}) or {}).get("k", 0))


def esikatselukuva(L, ketju, kd, i, kuva_u8, siemen):
    """Yksi esikatselukuva; monikuvamalli saa lisäksi naapurikuvat."""
    if getattr(ketju, "ajallinen", False):
        jakso, k = lahdejakso(kd, i)
        return ketju.kehys([L.ops.from_uint8(jakso[j:j + 1]) for j in range(len(jakso))], k, siemen), len(jakso)
    return ketju.rgb_lopullinen(L.ops.from_uint8(kuva_u8), siemen), 1


def koe_tunniste(geo):
    return hashlib.sha1(json.dumps([geo["rajaus"], geo["ow"], geo["oh"], VERSIO]).encode()).hexdigest()[:8]


def mittaa_tiedosto(src, an, geo, wd):
    """Lähteen laadun mittaus ja siihen sovitetut säädöt (välimuistissa esikatselun kansiossa).
    Mitataan 16 kuvaa koko videon matkalta (mediaani), tunnistetaan piirretty ja laadun vaihtelu
    ja valitaan esikatseluun kaksi tasavälistä kohtaa ja vaikein kohta."""
    from . import mittaus
    kd = wd / f"koe_{koe_tunniste(geo)}"
    kd.mkdir(parents=True, exist_ok=True)
    vanha = lue_json(kd / "mittaus.json")
    if vanha and vanha.get("v") == 2:
        return vanha
    naytteet = mittausnaytteet(src, an, geo)
    mitat = [(t, mittaus.mittaa_yksi(k)) for t, k in naytteet]
    mitat = [(t, x) for t, x in mitat if x] or [(naytteet[len(naytteet) // 2][0],
                                                  mittaus.mittaa([naytteet[len(naytteet) // 2][1]]))]
    mm = {k: round(float(np.median([x[k] for _, x in mitat])), 3) for k in ("kohina", "lohkot", "teravyys")}
    kesto = float(an.get("kesto") or 0)
    # vaikein kohta, joka ei osu tasavälisten kohtien päälle (muuten esikatselu näyttäisi saman kohtauksen)
    tasaiset = [kesto * f for f in KOE_KOHDAT]
    jarjestys = sorted(mitat, key=lambda tx: -mittaus.vaikeus(tx[1]))
    vaikein_t = next((t for t, _ in jarjestys if all(abs(t - u) >= 0.08 * kesto for u in tasaiset)),
                     jarjestys[0][0])
    d = {"v": 2, "mittaus": mm, "kuvaus": mittaus.kuvaus(mm), "oletukset": mittaus.oletukset(mm),
         "naytteet": [{"t": round(t, 2), **x} for t, x in mitat],
         "vaihtelee": mittaus.vaihtelee([x for _, x in mitat]),
         "piirretty": mittaus.piirretty([k for _, k in naytteet]),
         "vaikein": round(vaikein_t, 2),
         "kohdat": [round(kesto * f, 2) for f in KOE_KOHDAT] + [round(vaikein_t, 2)]}
    kirjoita_json(kd / "mittaus.json", d)
    return d


def koekuvat(src, an, geo, wd, L, edistys=None, peruuta=None):
    """Laskee jokaisella asetuksella kolme vertailukuvaa (lähteeseen sovitetuilla säädöillä) ja mittaa
    nopeuden. Palauttaa {"tunniste", "nopeus": {asetus: kuvaa/s}, "mittaus", "kuvaus", "oletukset"}."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    kd.mkdir(parents=True, exist_ok=True)
    mt = mittaa_tiedosto(src, an, geo, wd)
    kuvat = lahdekuvat(src, an, geo, kd)
    ol = mt["oletukset"]
    nopeus = {}
    vaiheet = len(ASETUKSET) * len(kuvat)
    tehty = 0
    for n in ASETUKSET:
        with L.lukko:
            ketju = L.ketju(geo, n, ol.get(str(n)), an)
            ajat = []
            with L.inferenssi():
                for i, k in enumerate(kuvat, 1):
                    if peruuta is not None and peruuta.is_set():
                        raise Peruutettu()
                    t = time.time()
                    rgb, maara = esikatselukuva(L, ketju, kd, i, k, 1000 * i)
                    out = L.ops.pakkaa(*ketju.yuv420p10(rgb))   # mukaan nopeusmittaukseen
                    del out
                    # monikuvamalli: sarja lasketaan kerralla; käsittelyssä ikkunan reunoilla on kontekstia
                    ajat.append((time.time() - t) / maara * (1.0 if maara == 1 else 1.35))
                    rgb_png(kd / f"v{n}_k{i}.png", L.ops.numpy(rgb)[0])
                    tehty += 1
                    if edistys:
                        edistys(tehty, vaiheet)
            del ketju
            if n != 1:
                L.vapauta()
        mitattu = min(ajat[1:]) if len(ajat) > 1 else ajat[0]
        nopeus[n] = round(1.0 / max(mitattu, 1e-4), 2)
    tiedot = {"tunniste": tun, "nopeus": nopeus, "aika": time.time(), "mittaus": mt["mittaus"],
              "kuvaus": mt["kuvaus"], "oletukset": ol, "vaihtelee": mt.get("vaihtelee", False),
              "piirretty": mt.get("piirretty", False), "kohdat": kohtatiedot(src, an, geo, kd)}
    kirjoita_json(kd / "koe.json", tiedot)
    return tiedot


def kohtatiedot(src, an, geo, kd):
    """Esikatselukohdat käyttöliittymälle: [{"t": s, "laji": "tasainen" | "vaikein" | "oma"}]."""
    ajat = esikatselukohdat(src, an, geo, kd)
    oma = (lue_json(kd / "kohdat.json") or {}).get("omat", [])
    return [{"t": round(t, 2), "laji": "oma" if i in oma else ("vaikein" if i == 3 else "tasainen")}
            for i, t in enumerate(ajat, 1)]


def siirra_kohta(src, an, geo, wd, L, k, aika, edistys=None):
    """Siirtää videon esikatselukohdan k aikaan `aika` (s) ja laskee sen vertailukuvat kaikilla
    asetuksilla. Palauttaa päivitetyt kohtatiedot."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    mt = mittaa_tiedosto(src, an, geo, wd)
    ajat = esikatselukohdat(src, an, geo, kd)
    k = max(1, min(3, int(k)))
    kesto = float(an.get("kesto") or 0)
    ajat[k - 1] = round(max(0.0, min(float(aika), max(0.0, kesto - 0.5))), 2)
    oma = sorted(set((lue_json(kd / "kohdat.json") or {}).get("omat", [])) | {k})
    for nimi in (f"lahde_{k}.npy", f"jakso_{k}.npy", f"jakso_{k}.json"):
        (kd / nimi).unlink(missing_ok=True)
    kirjoita_json(kd / "kohdat.json", {"ajat": ajat, "omat": oma})
    kuvat = lahdekuvat(src, an, geo, kd)
    ol = mt["oletukset"]
    for j, n in enumerate(ASETUKSET, 1):
        with L.lukko:
            ketju = L.ketju(geo, n, ol.get(str(n)), an)
            with L.inferenssi():
                rgb, _ = esikatselukuva(L, ketju, kd, k, kuvat[k - 1], 1000 * k)
                rgb_png(kd / f"v{n}_k{k}.png", L.ops.numpy(rgb)[0])
            del ketju, rgb
            if n != 1:
                L.vapauta()
        if edistys:
            edistys(j, len(ASETUKSET))
    tiedot = lue_json(kd / "koe.json", {}) or {}
    tiedot["kohdat"] = kohtatiedot(src, an, geo, kd)
    kirjoita_json(kd / "koe.json", tiedot)
    return tiedot["kohdat"]


def saatokuva(src, an, geo, wd, L, n, saato, k):
    """Yksi vertailukuva käyttäjän säädöillä (liukusäätimet). Palauttaa tiedostonimen."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    mittaa_tiedosto(src, an, geo, wd)                 # kohdat valitaan mittauksessa (välimuistissa)
    kuvat = lahdekuvat(src, an, geo, kd)
    aika = esikatselukohdat(src, an, geo, kd)[max(1, min(3, int(k))) - 1]
    avain = hashlib.sha1(json.dumps([n, saato, k, aika], sort_keys=True).encode()).hexdigest()[:10]
    nimi = f"s_{avain}.png"
    if not (kd / nimi).exists():
        with L.lukko, L.inferenssi():
            ketju = L.ketju(geo, n, saato, an)
            rgb, _ = esikatselukuva(L, ketju, kd, k, kuvat[k - 1], 1000 * k)
            rgb_png(kd / nimi, L.ops.numpy(rgb)[0])
    return f"koe_{tun}/{nimi}"


def liikepatka(src, an, geo, wd, L, n, saato, edistys=None, peruuta=None, kesto=5.0):
    """Lyhyt videopätkä valituilla asetuksilla (selaimessa toistettava H.264)."""
    tun = koe_tunniste(geo)
    kd = wd / f"koe_{tun}"
    kd.mkdir(parents=True, exist_ok=True)
    avain = hashlib.sha1(json.dumps([n, saato], sort_keys=True).encode()).hexdigest()[:10]
    nimi = f"liike_{avain}.mp4"
    if (kd / nimi).exists():
        return f"koe_{tun}/{nimi}"
    R = Fraction(an["R"])
    if saato is None:
        saato = mittaa_tiedosto(src, an, geo, wd)["oletukset"].get(str(n))
    kohta = max(0.0, min(an["kesto"] * 0.3, max(0.0, an["kesto"] - kesto - 1)))
    maara = max(4, int(kesto * R))
    with L.lukko:
        ketju = L.ketju(geo, n, saato, an)
        tmp = kd / (nimi + ".tmp.mp4")
        aja_osa(src, an, geo, ketju, L.ops, tmp, int(kohta * R), maara, "h264", wd / "lokit",
                edistys=(lambda m: edistys(m, maara)) if edistys else None, peruuta=peruuta,
                    rajoita=False)
        tmp.replace(kd / nimi)
    return f"koe_{tun}/{nimi}"


def arvioi_nopeus(an, geo, n, kohina_kaytossa=False):
    """Arvio kuvaa/s itsetestin mittauksesta (720x576 -> 1920x1440) suhteutettuna kuvakokoon."""
    t = lue_json(DATA / "itsetesti.json", {}) or {}
    perus = (t.get("nopeus") or {}).get(str(n))
    if not perus:
        return None
    x, y, cw, ch = geo["rajaus"]
    kerroin_l = (720 * 576) / max(1, cw * ch)
    kerroin_u = (1920 * 1440) / max(1, geo["ow"] * geo["oh"])
    nop = perus * kerroin_u if n == 1 else perus * (0.85 * kerroin_l + 0.15 * kerroin_u)
    kohina = (t.get("nopeus") or {}).get("kohina")
    if kohina_kaytossa and kohina:
        nop = 1.0 / (1.0 / nop + 1.0 / (kohina * kerroin_l))
    return nop


KASITTELY_VERSIO = 2        # vaihdetaan vain, jos osien laskenta muuttuu (muuten keskeneräinen jatkuu päivityksen yli)


def dvd_tekstiraidat(an, valinta):
    """Tekstiksi muunnettavat raidat: valitut (tai kaikki) DVD:n kuvatekstitykset."""
    if valinta.get("ocr") is False or os.environ.get("PIXELI_OCR") == "pois":
        return []
    valitut = valinta.get("tekstit")
    return [r for r in an.get("tekstiraidat") or []
            if r.get("koodekki") == "dvd_subtitle" and (valitut is None or r["i"] in valitut)]


KIELTEN_NIMET = {"fin": "Suomi", "swe": "Svenska", "eng": "English", "nor": "Norsk", "dan": "Dansk",
                 "ger": "Deutsch", "deu": "Deutsch", "fre": "Français", "fra": "Français", "spa": "Español",
                 "ita": "Italiano", "est": "Eesti", "rus": "Русский"}


class _OcrTyo:
    """Tunnistaa DVD-tekstitykset taustasäikeessä videon käsittelyn aikana. Valmiit SRT:t säilyvät
    keskeytyksen yli. Epäonnistuminen ei estä videon valmistumista (alkuperäiset raidat säilyvät)."""

    def __init__(self, src, an, valinta, wd, peruuta):
        self.raidat = dvd_tekstiraidat(an, valinta)
        self.kansio = Path(wd) / "tekstit"
        self.edistys, self.virhe, self._tulos = 0.0, None, []
        self._saie = None
        if not self.raidat or os.name != "nt" and os.environ.get("PIXELI_OCR") != "testi":
            self.raidat = []
            return
        self._saie = threading.Thread(target=self._aja, args=(src, peruuta), name="ocr", daemon=True)
        self._saie.start()

    def _aja(self, src, peruuta):
        from . import tekstitys
        try:
            tekstitys.varmista_ocr()
            self.kansio.mkdir(parents=True, exist_ok=True)
            for j, r in enumerate(self.raidat):
                kohde = self.kansio / f"s{r['i']}.srt"
                if not kohde.exists():
                    tmp = kohde.with_suffix(".kesken")

                    def ed(f, j=j):
                        self.edistys = (j + f) / len(self.raidat)
                    _, n = tekstitys.tee_srt(src, r["i"], r.get("kieli"), tmp, ed, peruuta)
                    tmp.replace(kohde)
                    loki(f"Tekstitys {r['i']} ({r.get('kieli')}): {n} tekstiä tunnistettu")
                kieli = r.get("kieli") or "und"
                nimi = r.get("nimi") or KIELTEN_NIMET.get(kieli, kieli.upper())
                self._tulos.append({"srt": kohde, "kieli": kieli, "oletus": bool(r.get("oletus")),
                                    "otsikko": f"{nimi} (teksti)"})
        except Peruutettu:
            pass
        except Exception as e:  # noqa: BLE001
            loki(f"Tekstitysten tunnistus epäonnistui: {traceback_teksti()}")
            self.virhe = str(e)[:200]

    def kaynnissa(self):
        return self._saie is not None and self._saie.is_alive()

    def tulokset(self):
        return [t for t in self._tulos if Path(t["srt"]).stat().st_size > 0]


def traceback_teksti():
    import traceback
    return traceback.format_exc()[-1500:]


def kasittele(src, an, geo, valinta, wd, L, ulos, edistys=None, peruuta=None, pikkukuva=None):
    """Koko elokuva osissa. Jatkuu keskeytyksestä. Palauttaa yhdistämisen tiedot."""
    n = int(valinta.get("asetus") or an["suositus"])
    saato = valinta.get("saato") or mittaa_tiedosto(src, an, geo, wd)["oletukset"].get(str(n))
    R = Fraction(an["R"])
    N = int(round(OSAN_PITUUS_S * R))
    arvio = max(1, int(an["kesto"] * float(R)))
    od = wd / "osat"
    pieni = valinta.get("laatu") == "pieni"
    allekirjoitus = {"asetus": n, "saato": saato, "geo": [geo["rajaus"], geo["ow"], geo["oh"]],
                     "lomitus": an["lomitus"], "R": an["R"], "N": N, "koodain": koodain("hevc", pieni),
                     "v": KASITTELY_VERSIO}
    meta = lue_json(od / "osat.json")
    if not meta or meta.get("allekirjoitus") != allekirjoitus:
        shutil.rmtree(od, ignore_errors=True)
        meta = {"allekirjoitus": allekirjoitus, "maarat": {}}
    od.mkdir(parents=True, exist_ok=True)
    for t in od.glob("*.tmp.mkv"):
        try:
            t.unlink()
        except OSError:
            pass
    maarat = meta["maarat"]
    tila = {"t0": time.time(), "viim": 0.0, "historia": [(time.time(), sum(maarat.values()))]}
    ocr = _OcrTyo(src, an, valinta, wd, peruuta)       # tekstitysten tunnistus prosessorilla rinnalla

    with L.lukko:
        ketju = L.ketju(geo, n, saato, an)
        i = 0
        while True:
            k = str(i)
            if k in maarat:
                c = maarat[k]
            else:
                pohja = sum(maarat.values())

                def ed(m, pohja=pohja):
                    nyt = time.time()
                    if nyt - tila["viim"] < 0.5 or not edistys:
                        return
                    tila["viim"] = nyt
                    tehty = pohja + m
                    # nopeus viimeisen minuutin ajalta: tehotason vaihto näkyy arviossa nopeasti
                    h = tila["historia"]
                    h.append((nyt, tehty))
                    while len(h) > 2 and nyt - h[1][0] >= 60:
                        h.pop(0)
                    nop = (tehty - h[0][1]) / max(1e-6, nyt - h[0][0])
                    edistys({"tehty": tehty, "kaikki": arvio, "nopeus": round(nop, 2),
                             "jaljella_s": (arvio - tehty) / nop if nop > 0 else None})

                tmp = od / f"osa_{i:04d}.tmp.mkv"
                c = aja_osa(src, an, geo, ketju, L.ops, tmp, i * N, N, "hevc", wd / "lokit", era=L.era,
                            edistys=ed, peruuta=peruuta, pikkukuva=pikkukuva, pieni=pieni)
                if c > 0:
                    tmp.replace(od / f"osa_{i:04d}.mkv")
                elif tmp.exists():
                    tmp.unlink()
                maarat[k] = c
                kirjoita_json(od / "osat.json", meta)
                try:
                    katselutiedot(src, an, wd)
                except Exception:  # noqa: BLE001
                    pass
            if c < N:
                break
            i += 1
        del ketju
    if edistys:
        edistys({"tehty": arvio, "kaikki": arvio, "nopeus": None, "jaljella_s": 0, "yhdistetaan": True})
    while ocr.kaynnissa():
        if peruuta is not None and peruuta.is_set():
            raise Peruutettu()
        if edistys:
            edistys({"tehty": arvio, "kaikki": arvio, "nopeus": None, "jaljella_s": 0, "tekstit": ocr.edistys})
        time.sleep(0.5)
    lisatekstit = ocr.tulokset()
    if edistys:
        edistys({"tehty": arvio, "kaikki": arvio, "nopeus": None, "jaljella_s": 0, "yhdistetaan": True})
    osat = [od / f"osa_{int(j):04d}.mkv" for j in sorted(maarat, key=int) if maarat[j] > 0]
    ulos = Path(ulos)
    ulos.parent.mkdir(parents=True, exist_ok=True)
    tulos = yhdista(src, an, osat, od / "lista.txt", ulos, valinta, lisatekstit=lisatekstit)
    # käsittelyn aikana katsova soitin siirtyy valmiiseen tiedostoon (osat poistetaan alla)
    kirjoita_json(wd / "katselu.json", {"edl": str(ulos), "kesto": float(an.get("kesto") or 0),
                                        "kesto_kaikki": float(an.get("kesto") or 0), "valmis": True,
                                        "vstart": 0.0, "aika": time.time()})
    try:
        kesto = float(aja([ffbin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                           "default=nw=1:nk=1", ulos]).stdout.strip())
        tulos["kesto_ero"] = round(abs(kesto - an["kesto"]), 1)
    except ValueError:
        tulos["kesto_ero"] = None
    shutil.rmtree(od, ignore_errors=True)
    if ocr.virhe:
        tulos["ocr_virhe"] = ocr.virhe
    tulos["ulos"] = str(ulos)
    tulos["aika_s"] = round(time.time() - tila["t0"])
    return tulos


def _edl_polku(p):
    s = str(Path(p).resolve())
    return f"%{len(s.encode('utf-8'))}%{s}"


def katselutiedot(src, an, wd, valmis=False):
    """Käsittelyn aikana katsottava kokonaisuus mpv:lle: valmiit osat peräkkäin (kuva) ja alkuperäinen
    tiedosto (ääni ja tekstitykset). Palauttaa tilatiedoston polun tai None, jos osia ei vielä ole."""
    od = Path(wd) / "osat"
    meta = lue_json(od / "osat.json") or {}
    maarat = meta.get("maarat") or {}
    osat = []
    for j in sorted(maarat, key=int):            # vain yhtenäinen alku: ensimmäiseen puuttuvaan asti
        if int(j) != len(osat) or maarat[j] <= 0 or not (od / f"osa_{int(j):04d}.mkv").exists():
            break
        osat.append((od / f"osa_{int(j):04d}.mkv", maarat[j]))
    if not osat:
        return None
    R = Fraction(an["R"])
    kesto = sum(c for _, c in osat) / float(R)
    valmis = valmis or any(0 <= c < round(OSAN_PITUUS_S * R) for c in maarat.values())   # viimeinen osa on lyhyempi
    rivit = ["# mpv EDL v0", "!new_stream"] + [_edl_polku(p) for p, _ in osat] + ["!new_stream", _edl_polku(src)]
    edl = Path(wd) / "katselu.edl"
    tmp = edl.with_suffix(".edl.tmp")
    tmp.write_text("\n".join(rivit) + "\n", encoding="utf-8", newline="\n")   # mpv vaatii LF-rivinvaihdot
    tmp.replace(edl)
    tila = Path(wd) / "katselu.json"
    tmpj = tila.with_suffix(".json.tmp")
    kirjoita_json(tmpj, {"edl": str(edl), "kesto": round(kesto, 3), "kesto_kaikki": float(an.get("kesto") or 0),
                         "osia": len(osat), "valmis": bool(valmis), "vstart": float(an.get("vstart") or 0),
                         "aika": time.time()})
    tmpj.replace(tila)
    return tila


def ulostulo_polku(src, kansio=None, paate=".mkv"):
    """Valmiin tiedoston polku: alkuperäisen viereen tai valittuun kansioon; jos ei kirjoitusoikeutta,
    datakansion Valmiit-kansioon."""
    src = Path(src)
    nimi = f"{src.stem} [Remaster]{paate}"
    ehdokkaat = [Path(kansio) / nimi] if kansio else []
    ehdokkaat += [src.with_name(nimi), DATA / "Valmiit" / nimi]
    for p in ehdokkaat:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            testi = p.with_name(p.name + ".kirjoitustesti")
            testi.write_bytes(b"")
            testi.unlink()
            return p
        except OSError:
            continue
    return ehdokkaat[-1]
