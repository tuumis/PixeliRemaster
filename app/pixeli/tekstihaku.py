"""PixeliRemaster – tekstitysten haku OpenSubtitlesista (opensubtitles.com, REST API v1).

Haku nimellä (ja tiedoston tunnisteella, jos tiedosto on julkaisu sellaisenaan), valittu tekstitys
ladataan SRT:nä ja lisätään lopulliseen tiedostoon omaksi raidakseen. Jos tekstitys on tehty eri
nopeuksiseen julkaisuun (esim. 23,976 fps elokuva ja 25 fps PAL-DVD, joka toistuu 4 % nopeammin), ajat
sovitetaan automaattisesti.

Rajapinta vaatii sovellusavaimen (Api-Key), joka voi olla maksullinen. Ilman avainta tekstitys lisätään
tiedostosta: hakusivu avataan selaimeen valmiiksi elokuvan nimellä, ja käyttäjän lataama .srt (tai .vtt,
.ass, .ssa) tuodaan samalla tavalla kuin rajapinnasta haettu.
"""
import json
import os
import re
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import moottori as m

PERUS = "https://api.opensubtitles.com/api/v1"
KIELET = {"fi": ("fin", "Suomi", "Finnish"), "en": ("eng", "English", "English"), "sv": ("swe", "Svenska", "Swedish")}


class TekstiVirhe(RuntimeError):
    def __init__(self, avain, tieto=""):
        super().__init__(avain)
        self.avain, self.tieto = avain, tieto


def sovellusavain(asetukset=None):
    """Käyttäjän oma avain asetuksista tai asennuspakettiin rakennettu PixeliRemasterin avain."""
    oma = str((asetukset or {}).get("opensubtitles_avain") or "").strip()
    if oma:
        return oma
    if os.environ.get("PIXELI_OPENSUBTITLES"):
        return os.environ["PIXELI_OPENSUBTITLES"]
    d = m.lue_json(m.SOVELLUS / "pixeli" / "avaimet.json", {}) or {}
    return str(d.get("opensubtitles") or "").strip()


def _otsakkeet(avain):
    return {"Api-Key": avain, "User-Agent": f"PixeliRemaster v{m.VERSIO}", "Accept": "application/json"}


def _pyynto(avain, polku, parametrit=None, data=None, aikaraja=30):
    if not avain:
        raise TekstiVirhe("avain_puuttuu")
    url = PERUS + polku
    if parametrit:
        # rajapinnan ohje: parametrit aakkosjärjestyksessä, pienillä kirjaimilla, välilyönti '+'
        url += "?" + urllib.parse.urlencode(sorted((k.lower(), str(v).lower() if k != "query" else str(v))
                                                    for k, v in parametrit.items() if v not in (None, "")))
    otsakkeet = _otsakkeet(avain)
    if data is not None:
        otsakkeet["Content-Type"] = "application/json"
        data = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=otsakkeet, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=aikaraja) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            runko = json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            runko = {}
        if e.code in (401, 403):
            raise TekstiVirhe("avain_vaara", str(runko)[:300])
        if e.code == 406 or "quota" in str(runko).lower() or "allowed" in str(runko).lower():
            raise TekstiVirhe("kiintio", str(runko.get("reset_time") or runko.get("message") or "")[:120])
        if e.code == 429:
            raise TekstiVirhe("liian_nopeasti")
        raise TekstiVirhe("palvelu", f"HTTP {e.code}: {str(runko)[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TekstiVirhe("verkko", str(e)[:200])


def tiedostotunniste(polku):
    """OpenSubtitlesin tiedostotunniste (koko + ensimmäisen ja viimeisen 64 kt:n 64-bittinen summa)."""
    polku = Path(polku)
    koko = polku.stat().st_size
    if koko < 131072:
        return None
    h = koko
    with open(polku, "rb") as f:
        for alku in (0, koko - 65536):
            f.seek(alku)
            pala = f.read(65536)
            for (x,) in struct.iter_unpack("<Q", pala):
                h = (h + x) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


_ROSKA = re.compile(r"\b(480p|576p|720p|1080p|2160p|dvd(rip)?|bluray|brrip|web(-?dl|rip)?|x26[45]|h\.?26[45]|hevc|"
                    r"aac|ac3|dts|remux|pal|ntsc|disc ?\d|dvd ?\d|title ?\d+|pixeliremaster|remaster(ed)?)\b", re.I)


def ehdotettu_haku(nimi):
    """Tiedoston tai levyn nimestä hakusana ja vuosi: 'Taru_sormusten_herrasta.2001.DVD [abc].mkv' ->
    ('Taru sormusten herrasta', 2001)."""
    s = Path(str(nimi)).stem
    s = re.sub(r"\[[^\]]*\]|\([^)]*(remaster|pixeli)[^)]*\)", " ", s, flags=re.I)
    vuosi = None
    r = re.search(r"(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)", s)
    if r:
        vuosi = int(r.group(1))
        s = s[:r.start()]
    s = re.sub(r"[._]+", " ", s)
    s = _ROSKA.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return (s.title() if s.isupper() else s) or Path(str(nimi)).stem, vuosi


def jasenna_tulokset(d, hash_osumat=()):
    tulos = []
    for x in d.get("data") or []:
        a = x.get("attributes") or {}
        tiedostot = a.get("files") or []
        if not tiedostot or len(tiedostot) > 1:          # monilevyiset (CD1/CD2) eivät sovi yhteen tiedostoon
            continue
        fd = a.get("feature_details") or {}
        tulos.append({
            "file_id": int(tiedostot[0].get("file_id")), "nimi": a.get("release") or tiedostot[0].get("file_name") or "",
            "elokuva": fd.get("title") or fd.get("movie_name") or "", "vuosi": fd.get("year"),
            "kieli": a.get("language") or "", "lataukset": int(a.get("download_count") or 0),
            "fps": float(a.get("fps") or 0), "kuulovammaisille": bool(a.get("hearing_impaired")),
            "kone": bool(a.get("machine_translated") or a.get("ai_translated")),
            "luotettu": bool(a.get("from_trusted")), "hash_osuma": bool(a.get("moviehash_match"))
            or str(x.get("id")) in hash_osumat,
            "kausi": fd.get("season_number"), "jakso": fd.get("episode_number"),
        })
    # tiedostoon täsmäävät ensin, sitten luotetut ja ihmisen kääntämät, sitten suosituimmat
    tulos.sort(key=lambda r: (not r["hash_osuma"], r["kone"], not r["luotettu"], -r["lataukset"]))
    return tulos


def hae(asetukset, kysely, kieli="fi", vuosi=None, tiedosto=None):
    avain = sovellusavain(asetukset)
    kieli = kieli if kieli in KIELET else "fi"
    tulos, nahdyt = [], set()
    if tiedosto and Path(tiedosto).exists():
        try:
            h = tiedostotunniste(tiedosto)
            if h:
                for r in jasenna_tulokset(_pyynto(avain, "/subtitles", {"languages": kieli, "moviehash": h})):
                    r["hash_osuma"] = True
                    tulos.append(r)
                    nahdyt.add(r["file_id"])
        except TekstiVirhe as e:
            if e.avain in ("avain_puuttuu", "avain_vaara"):
                raise
    if kysely:
        for r in jasenna_tulokset(_pyynto(avain, "/subtitles", {"languages": kieli, "query": kysely,
                                                               "year": vuosi})):
            if r["file_id"] not in nahdyt:
                tulos.append(r)
                nahdyt.add(r["file_id"])
    return tulos[:40]


def _dekoodaa(tavut):
    for koodaus in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return tavut.decode(koodaus)
        except UnicodeDecodeError:
            continue
    return tavut.decode("utf-8", "replace")


_AIKA = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _sek(r):
    return int(r.group(1)) * 3600 + int(r.group(2)) * 60 + int(r.group(3)) + int(r.group(4).ljust(3, "0")) / 1000


def _aika(s):
    s = max(0.0, s)
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def sovita_srt(teksti, kerroin=1.0, siirto=0.0):
    """Kertoo SRT:n ajat kertoimella ja lisää siirron (sekunteina)."""
    if abs(kerroin - 1.0) < 1e-6 and not siirto:
        return teksti
    return _AIKA.sub(lambda r: _aika(_sek(r) * kerroin + siirto), teksti)


def nopeuskerroin(tekstin_fps, videon_fps):
    """PAL-nopeutus: 23,976/24 fps julkaisuun tehty tekstitys 25 fps videolle (ja päinvastoin)."""
    try:
        a, v = float(tekstin_fps or 0), float(videon_fps or 0)
    except (TypeError, ValueError):
        return 1.0
    if not a or not v:
        return 1.0
    pal = lambda f: abs(f - 25) < 0.1 or abs(f - 50) < 0.1  # noqa: E731
    filmi = lambda f: abs(f - 23.976) < 0.05 or abs(f - 24) < 0.05  # noqa: E731
    if pal(v) and filmi(a):
        return a / 25.0
    if filmi(v) and pal(a):
        return 25.0 / 23.976
    return 1.0


def lataa(asetukset, file_id, kohde, videon_fps=None, tekstin_fps=None):
    """Lataa tekstityksen UTF-8-SRT:ksi. Palauttaa {"polku", "jaljella", "kerroin"}."""
    avain = sovellusavain(asetukset)
    d = _pyynto(avain, "/download", data={"file_id": int(file_id), "sub_format": "srt"})
    linkki = d.get("link")
    if not linkki:
        raise TekstiVirhe("kiintio" if d.get("remaining") == 0 else "palvelu", str(d)[:300])
    req = urllib.request.Request(linkki, headers={"User-Agent": f"PixeliRemaster v{m.VERSIO}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            tavut = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise TekstiVirhe("verkko", str(e)[:200])
    teksti = _dekoodaa(tavut).replace("\r\n", "\n")
    if not _AIKA.search(teksti):
        raise TekstiVirhe("ei_srt")
    kerroin = nopeuskerroin(tekstin_fps, videon_fps)
    teksti = sovita_srt(teksti, kerroin)
    kohde = Path(kohde)
    kohde.parent.mkdir(parents=True, exist_ok=True)
    kohde.write_text(teksti, encoding="utf-8")
    return {"polku": str(kohde), "jaljella": d.get("remaining"), "nollaus": d.get("reset_time"), "kerroin": kerroin}


def lisaa_valmiiseen(ulos, srt, kieli3, otsikko, oletus=True):
    """Lisää tekstitysraidan jo valmiiseen MKV:hen (kopio uudelleenpakkaamatta, sitten vaihto)."""
    ulos = Path(ulos)
    tmp = ulos.with_name(ulos.stem + ".tekstit.mkv")
    p = subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-select_streams", "s", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(ulos)], capture_output=True, text=True, creationflags=m.PIILOTA)
    ns = len([r for r in p.stdout.splitlines() if r.strip()])
    cmd = [m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(ulos), "-i", str(srt),
           "-map", "0", "-map", "1:s:0", "-c", "copy", f"-c:s:{ns}", "srt",
           f"-metadata:s:s:{ns}", f"language={kieli3}", f"-metadata:s:s:{ns}", f"title={otsikko}"]
    if oletus:
        cmd += [x for i in range(ns) for x in (f"-disposition:s:{i}", "0")] + [f"-disposition:s:{ns}", "default"]
    cmd += [str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=m.PIILOTA)
    if r.returncode != 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise TekstiVirhe("lisays", r.stderr[-400:])
    for _ in range(10):
        try:
            tmp.replace(ulos)
            return True
        except PermissionError:                     # tiedosto auki soittimessa
            time.sleep(0.5)
    tmp.unlink(missing_ok=True)
    raise TekstiVirhe("tiedosto_kaytossa")


TEKSTIPAATTEET = (".srt", ".vtt", ".ass", ".ssa")
HAKUSIVU = "https://www.opensubtitles.org/{ui}/search2/sublanguageid-{k3}/moviename-{q}"


def hakusivu(kysely, kieli="fi", ui="fi"):
    """OpenSubtitlesin hakusivu selaimeen (ei vaadi avainta)."""
    k3 = KIELET.get(kieli, KIELET["fi"])[0]
    q = urllib.parse.quote_plus(str(kysely or "").strip())
    return HAKUSIVU.format(ui="fi" if ui == "fi" else "en", k3=k3, q=q)


def arvaa_kieli(nimi):
    """Tiedostonimestä kieli: 'Elokuva.fi.srt', 'Elokuva_FIN.srt', 'Elokuva (Finnish).srt' -> 'fi'."""
    sanat = set(re.split(r"[^a-zåäö]+", Path(str(nimi)).stem.lower()))
    for kieli, tunnukset in (("fi", {"fi", "fin", "finnish", "suomi", "suomeksi"}),
                             ("sv", {"sv", "swe", "swedish", "svenska", "ruotsi"}),
                             ("en", {"en", "eng", "english", "englanti"})):
        if sanat & tunnukset:
            return kieli
    return None


def viimeinen_aika(teksti):
    ajat = [_sek(r) for r in _AIKA.finditer(teksti)]
    return max(ajat) if ajat else 0.0


def tuo_tiedosto(lahde, kohde, videon_kesto=None, videon_fps=None):
    """Käyttäjän tekstitystiedosto UTF-8-SRT:ksi. Jos tekstitys on selvästi videota pidempi samassa
    suhteessa kuin elokuvan ja PAL-DVD:n nopeusero (25 / 23,976 = 1,043), ajat sovitetaan."""
    lahde, kohde = Path(lahde), Path(kohde)
    if lahde.suffix.lower() not in TEKSTIPAATTEET or not lahde.is_file():
        raise TekstiVirhe("ei_srt")
    if lahde.suffix.lower() == ".srt":
        teksti = _dekoodaa(lahde.read_bytes())
    else:
        tmp = kohde.with_suffix(".muunnos.srt")
        kohde.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(lahde),
                            "-c:s", "srt", str(tmp)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=m.PIILOTA)
        if r.returncode != 0 or not tmp.exists():
            raise TekstiVirhe("ei_srt", r.stderr[-300:])
        teksti = _dekoodaa(tmp.read_bytes())
        tmp.unlink(missing_ok=True)
    teksti = teksti.replace("\r\n", "\n").lstrip("\ufeff")
    if not _AIKA.search(teksti):
        raise TekstiVirhe("ei_srt")
    kerroin = 1.0
    try:
        kesto, loppu = float(videon_kesto or 0), viimeinen_aika(teksti)
        pal = videon_fps is None or abs(float(videon_fps) - 25) < 0.1 or abs(float(videon_fps) - 50) < 0.1
        if kesto > 300 and pal and 1.025 < loppu / kesto < 1.06:
            kerroin = 23.976 / 25.0
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    teksti = sovita_srt(teksti, kerroin)
    kohde.parent.mkdir(parents=True, exist_ok=True)
    kohde.write_text(teksti, encoding="utf-8")
    return {"polku": str(kohde), "kerroin": kerroin}
