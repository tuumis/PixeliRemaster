"""PixeliRemaster – videon lataus verkosta (YouTube ja muut yt-dlp:n tukemat sivut).

Lataus tehdään avoimen lähdekoodin yt-dlp:llä (Unlicense). YouTube vaatii nykyään JavaScript-ajoympäristön,
joten yt-dlp:n viereen ladataan Deno (MIT); yt-dlp löytää sen samasta kansiosta. Molemmat ladataan
ensimmäisellä käytöllä GitHubista ja tarkistetaan SHA-256-summalla. YouTube muuttuu usein, joten yt-dlp
päivitetään itsestään viikon välein, ja epäonnistunut lataus yritetään vielä kerran päivitetyllä versiolla.

Paras laatu: paras kuva + paras ääni (-f bv*+ba/b) yhdistettynä MKV:ksi ilman uudelleenpakkausta, sivun
omat tekstitykset (ei automaattisesti tunnistettuja) ja luvut mukaan.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import moottori as m

YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
DENO_API = "https://api.github.com/repos/denoland/deno/releases/latest"
PAIVITYSVALI_S = 7 * 24 * 3600
MUOTO = "bv*+ba/b"
TEKSTIKIELET = "fi.*,en.*,sv.*"

_lukko = threading.Lock()
TILA = {"tila": "ei", "edistys": 0.0, "virhe": None}


def kansio():
    return m.DATA / "yt-dlp"


def _nimi(perus):
    return perus + (".exe" if os.name == "nt" else "")


def ytdlp_exe():
    p = kansio() / _nimi("yt-dlp")
    if p.exists():
        return str(p)
    return shutil.which("yt-dlp") if os.environ.get("PIXELI_YTDLP") == "polku" else None


def deno_exe():
    p = kansio() / _nimi("deno")
    return str(p) if p.exists() else shutil.which("deno")


def _hae_json(url):
    from .soitin import _hae_json as hae
    return hae(url)


def _lataa_tiedosto(url, kohde, koko=None, tarkiste=None, edistys=None):
    h = hashlib.sha256()
    n = 0
    tmp = Path(str(kohde) + ".osa")
    req = urllib.request.Request(url, headers={"User-Agent": f"PixeliRemaster/{m.VERSIO}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        koko = koko or int(r.headers.get("Content-Length") or 0)
        while True:
            pala = r.read(1 << 20)
            if not pala:
                break
            f.write(pala)
            h.update(pala)
            n += len(pala)
            if edistys and koko:
                edistys(min(1.0, n / koko))
    if tarkiste and h.hexdigest() != tarkiste:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("lataaja_tarkistussumma")
    tmp.replace(kohde)
    return kohde


def _asset(julkaisu, nimi):
    return next((a for a in julkaisu.get("assets", []) if a.get("name") == nimi), None)


def _ytdlp_asset_nimi():
    return "yt-dlp.exe" if os.name == "nt" else "yt-dlp_linux"


def _deno_asset_nimi():
    return "deno-x86_64-pc-windows-msvc.zip" if os.name == "nt" else "deno-x86_64-unknown-linux-gnu.zip"


def versio():
    exe = ytdlp_exe()
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60, creationflags=m.PIILOTA)
        return p.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def asenna_ytdlp(edistys=None, julkaisu=None):
    julkaisu = julkaisu or _hae_json(YTDLP_API)
    a = _asset(julkaisu, _ytdlp_asset_nimi())
    if not a:
        raise RuntimeError("lataaja_pakettia_ei_loydy")
    k = kansio()
    k.mkdir(parents=True, exist_ok=True)
    uusi = k / (_nimi("yt-dlp") + ".uusi")
    _lataa_tiedosto(a["browser_download_url"], uusi, a.get("size"),
                    (a.get("digest") or "").removeprefix("sha256:") or None, edistys)
    if os.name != "nt":
        uusi.chmod(0o755)
    p = subprocess.run([str(uusi), "--version"], capture_output=True, text=True, timeout=120,
                       creationflags=m.PIILOTA)
    if p.returncode != 0:
        uusi.unlink(missing_ok=True)
        raise RuntimeError("lataaja_ei_toimi")
    try:
        uusi.replace(k / _nimi("yt-dlp"))
    except OSError:
        uusi.unlink(missing_ok=True)
        raise RuntimeError("lataaja_kaytossa")
    (k / "tarkistettu.txt").write_text(f"{julkaisu.get('tag_name', '')}\n{time.time()}", encoding="utf-8")
    m.loki(f"yt-dlp asennettu: {julkaisu.get('tag_name')}")


def asenna_deno(edistys=None):
    julkaisu = _hae_json(DENO_API)
    a = _asset(julkaisu, _deno_asset_nimi())
    if not a:
        raise RuntimeError("deno_pakettia_ei_loydy")
    k = kansio()
    k.mkdir(parents=True, exist_ok=True)
    z = k / "deno.zip"
    _lataa_tiedosto(a["browser_download_url"], z, a.get("size"),
                    (a.get("digest") or "").removeprefix("sha256:") or None, edistys)
    with zipfile.ZipFile(z) as zf:
        nimi = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == _nimi("deno"))
        uusi = k / (_nimi("deno") + ".uusi")
        uusi.write_bytes(zf.read(nimi))
    z.unlink(missing_ok=True)
    if os.name != "nt":
        uusi.chmod(0o755)
    try:
        uusi.replace(k / _nimi("deno"))
    except OSError:
        uusi.unlink(missing_ok=True)
        raise RuntimeError("lataaja_kaytossa")
    m.loki(f"Deno asennettu: {julkaisu.get('tag_name')}")


def _tarkistettu():
    try:
        rivit = (kansio() / "tarkistettu.txt").read_text(encoding="utf-8").splitlines()
        return rivit[0], float(rivit[1])
    except (OSError, IndexError, ValueError):
        return None, 0.0


def varmista(edistys=None, pakota_tarkistus=False):
    """yt-dlp ja Deno paikalleen; viikon välein (tai pyydettäessä) uusin yt-dlp."""
    with _lukko:
        if not ytdlp_exe():
            asenna_ytdlp(lambda f: edistys and edistys(0.3 * f))
        elif os.environ.get("PIXELI_YTDLP") == "polku":
            pass                                          # testeissä järjestelmän yt-dlp, ei päivityksiä
        elif pakota_tarkistus or time.time() - _tarkistettu()[1] > PAIVITYSVALI_S:
            try:
                j = _hae_json(YTDLP_API)
                if j.get("tag_name") and j["tag_name"] != versio():
                    asenna_ytdlp(julkaisu=j)
                else:
                    (kansio() / "tarkistettu.txt").write_text(f"{j.get('tag_name', '')}\n{time.time()}",
                                                             encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                m.loki(f"yt-dlp:n päivitystarkistus epäonnistui: {e}")
        if not deno_exe():
            asenna_deno(lambda f: edistys and edistys(0.3 + 0.7 * f))
    return ytdlp_exe()


def _ymparisto():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env["PATH"] = str(kansio()) + os.pathsep + env.get("PATH", "")        # Deno samasta kansiosta
    return env


def _perusargumentit():
    args = ["--no-playlist", "--no-config", "--encoding", "utf-8", "--no-color"]
    try:
        args += ["--ffmpeg-location", str(Path(m.ffbin("ffmpeg")).parent)]
    except RuntimeError:
        pass
    return args


VIRHEET = (
    (r"Sign in to confirm your age|age[- ]restricted", "ika"),
    (r"Sign in to confirm you.re not a bot|confirm you are not a bot", "botti"),
    (r"DRM protected|is DRM", "drm"),
    (r"Private video|members-only|Join this channel", "yksityinen"),
    (r"Video unavailable|This video is not available|has been removed|does not exist|HTTP Error 404", "ei_saatavilla"),
    (r"not available in your country|geo.?restrict", "alue"),
    (r"Unsupported URL|is not a valid URL|No video formats found", "ei_tuettu"),
    (r"live event will begin|is_upcoming|Premieres in", "tulossa"),
    (r"getaddrinfo|Unable to download webpage|timed out|Connection reset|urlopen error", "verkko"),
)


def virheavain(teksti):
    for kuvio, avain in VIRHEET:
        if re.search(kuvio, teksti or "", re.I):
            return avain
    return "lataus"


class LatausVirhe(RuntimeError):
    def __init__(self, avain, tieto=""):
        super().__init__(avain)
        self.avain, self.tieto = avain, tieto


def tiedot(url, aikaraja=120):
    """Videon tiedot ennen latausta: nimi, kesto, paras laatu ja saatavilla olevat tekstitykset."""
    url = str(url or "").strip()
    if not re.match(r"https?://", url, re.I):
        raise LatausVirhe("ei_linkki")
    exe = varmista()
    p = subprocess.run([exe, *_perusargumentit(), "-J", "-f", MUOTO, "--", url], capture_output=True,
                       timeout=aikaraja, env=_ymparisto(), creationflags=m.PIILOTA)
    ulos = p.stdout.decode("utf-8", "replace")
    virhe = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0 or not ulos.strip():
        raise LatausVirhe(virheavain(virhe), virhe[-600:])
    return jasenna_tiedot(json.loads(ulos))


def jasenna_tiedot(d):
    if d.get("_type") == "playlist":
        raise LatausVirhe("soittolista")
    if d.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise LatausVirhe("suora" if d["live_status"] != "is_upcoming" else "tulossa")
    valitut = d.get("requested_formats") or [d]
    video = next((f for f in valitut if (f.get("vcodec") or "none") != "none"), valitut[0])
    korkeus = int(video.get("height") or d.get("height") or 0)
    leveys = int(video.get("width") or d.get("width") or 0)
    osat = [{"id": str(f.get("format_id")), "koko": int(f.get("filesize") or f.get("filesize_approx") or 0)}
            for f in valitut]
    tekstit = sorted(k for k in (d.get("subtitles") or {}) if k != "live_chat")
    kielet = [k for k in tekstit if re.match(r"(fi|en|sv)", k)]
    return {"nimi": d.get("title") or d.get("id") or "video", "kanava": d.get("uploader") or d.get("channel") or "",
            "kesto": float(d.get("duration") or 0), "korkeus": korkeus, "leveys": leveys,
            "fps": float(video.get("fps") or d.get("fps") or 0), "koodekki": video.get("vcodec") or "",
            "sivu": d.get("extractor_key") or d.get("extractor") or "", "id": d.get("id") or "",
            "url": d.get("webpage_url") or d.get("original_url") or "", "osat": osat,
            "koko": sum(o["koko"] for o in osat), "tekstit": kielet, "kaikki_tekstit": tekstit}


class Edistys:
    """yt-dlp:n edistysrivit (oma muoto) -> kokonaisosuus. Kuva ja ääni ladataan erikseen."""

    def __init__(self, osat=None):
        self.koot = {o["id"]: o["koko"] for o in osat or [] if o.get("id")}
        self.ladattu = {}

    def rivi(self, rivi):
        if not rivi.startswith("PIXELI|"):
            return None
        osat = rivi.strip().split("|")
        if len(osat) < 5:
            return None
        ladattu, koko, arvio, tunnus = osat[1], osat[2], osat[3], osat[4]
        try:
            ladattu = float(ladattu)
        except ValueError:
            return None
        for k in (koko, arvio):
            try:
                if float(k) > 0:
                    self.koot[tunnus] = float(k)
                    break
            except ValueError:
                continue
        self.ladattu[tunnus] = ladattu
        kaikki = sum(self.koot.values())
        if kaikki <= 0:
            return None
        return min(1.0, sum(min(self.ladattu.get(t, 0.0), k) for t, k in self.koot.items()) / kaikki)


def lataa(url, kansio_, osat=None, tekstit=True, edistys=None, peruuta=None, uusi_yritys=True):
    """Lataa videon kansioon (väliaikainen alikansio) ja palauttaa valmiin MKV:n polun."""
    exe = varmista()
    kansio_ = Path(kansio_)
    kansio_.mkdir(parents=True, exist_ok=True)
    cmd = [exe, *_perusargumentit(), "-f", MUOTO, "--merge-output-format", "mkv", "--remux-video", "mkv",
           "--embed-metadata", "--embed-chapters", "--no-mtime", "--windows-filenames", "--trim-filenames", "150",
           "--newline", "--progress", "--progress-template",
           "download:PIXELI|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
           "%(progress.total_bytes_estimate)s|%(info.format_id)s",
           "--print", "after_move:PIXELI_VALMIS|%(filepath)s",
           "-P", str(kansio_), "-o", "%(title).150B [%(id)s].%(ext)s"]
    if tekstit:
        cmd += ["--write-subs", "--sub-langs", TEKSTIKIELET, "--convert-subs", "srt", "--embed-subs"]
    cmd += ["--", url]
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_ymparisto(),
                         creationflags=m.PIILOTA)
    ed = Edistys(osat)
    tulos = {"polku": None, "hanta": []}

    def lue():
        with open(loki / "lataus.log", "ab") as lf:
            lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {url}\n".encode("utf-8"))
            for raaka in iter(p.stdout.readline, b""):
                rivi = raaka.decode("utf-8", "replace").rstrip("\r\n")
                if rivi.startswith("PIXELI_VALMIS|"):
                    tulos["polku"] = rivi.split("|", 1)[1].strip()
                    lf.write(raaka)
                    continue
                f = ed.rivi(rivi)
                if f is not None:
                    if edistys:
                        edistys(0.97 * f)
                    continue
                lf.write(raaka)
                tulos["hanta"] = (tulos["hanta"] + [rivi])[-30:]
    lukija = threading.Thread(target=lue, daemon=True)
    lukija.start()
    while p.poll() is None:
        if peruuta is not None and peruuta.is_set():
            p.kill()
            p.wait()
            lukija.join(timeout=5)
            raise m.Peruutettu()
        time.sleep(0.3)
    lukija.join(timeout=10)
    hanta = "\n".join(tulos["hanta"])
    polku = Path(tulos["polku"]) if tulos["polku"] else None
    if p.returncode != 0 or not polku or not polku.exists():
        avain = virheavain(hanta)
        # YouTube muuttuu usein: yksi uusi yritys uusimmalla yt-dlp:llä
        if uusi_yritys and avain in ("lataus", "ei_tuettu", "botti"):
            try:
                varmista(pakota_tarkistus=True)
            except Exception as e:  # noqa: BLE001
                m.loki(f"yt-dlp:n päivitys epäonnistui: {e}")
            m.loki(f"Lataus epäonnistui ({avain}), yritetään uudelleen: {hanta[-300:]}")
            return lataa(url, kansio_, osat, tekstit, edistys, peruuta, uusi_yritys=False)
        raise LatausVirhe(avain, hanta[-600:])
    if edistys:
        edistys(1.0)
    return polku


def suositus(korkeus):
    """Kannattaako remasterointi: "hyva" (SD), "hd" (jo HD-tarkkuutta, ohjelma ei käsittele)."""
    return "hd" if korkeus >= 720 else "hyva"
