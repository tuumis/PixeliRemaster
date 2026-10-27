"""PixeliRemaster – videon katselu mpv-soittimella.

Ohjelman ikkuna (WebView2) ei toista MKV-tiedostoja eikä 10-bittistä HEVC:tä, joten katseluun
käytetään mpv:tä (avointa koodia, GPL/LGPL). Sitä ei jaeta asennusohjelmassa: se ladataan
ensimmäisellä käytöllä alkuperäisestä lähteestä (shinchiro/mpv-winbuild-cmake, mpv.io:n
suosittelema Windows-käännös) samaan tapaan kuin ffmpeg, ja paketin SHA-256 tarkistetaan.

Kolme tapaa katsoa:
  toista      valmis remasteroitu video (kaikki ääni- ja tekstitysraidat)
  vertaa      alkuperäinen ja remasteroitu jaettuna kuvana samaan tahtiin (vasen | oikea)
  reaaliaika  alkuperäinen sellaisenaan, NVIDIAn RTX Video Super Resolution parantaa kuvaa
              toiston aikana (näytönohjaimen oma tekoäly; N vaihtaa päälle/pois)
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
from pathlib import Path

from . import moottori as m

RAJAPINTA = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases?per_page=5"
PAKETTI = re.compile(r"^mpv-x86_64-\d{8}-git-[0-9a-f]+\.7z$")

TILA = {"tila": "ei", "edistys": 0.0, "virhe": None}
_lukko = threading.Lock()


def kansio():
    return m.DATA / "mpv"


def mpv_exe():
    p = kansio() / ("mpv.exe" if os.name == "nt" else "mpv")
    if p.exists():
        return str(p)
    return shutil.which("mpv")


def versio():
    """Ladatun paketin päiväys ja git-tunniste, esim. '2026-08-21 (49418246f3)'."""
    try:
        n = (kansio() / "pixeli_versio.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "?" if mpv_exe() else None
    r = re.search(r"-(\d{4})(\d{2})(\d{2})-git-([0-9a-f]+)", n)
    return f"{r.group(1)}-{r.group(2)}-{r.group(3)} ({r.group(4)})" if r else n


def tila():
    d = dict(TILA)
    d["asennettu"] = bool(mpv_exe())
    return d


def _hae_json(url):
    otsakkeet = {"Accept": "application/vnd.github+json", "User-Agent": f"PixeliRemaster/{m.VERSIO}"}
    if os.environ.get("PIXELI_GH_TOKEN"):          # vain testiajoissa (GitHub Actions)
        otsakkeet["Authorization"] = "Bearer " + os.environ["PIXELI_GH_TOKEN"]
    req = urllib.request.Request(url, headers=otsakkeet)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _pura_7z(paketti, kohde):
    """Windows 10/11:n oma tar (libarchive) purkaa 7z-paketit; varalla py7zr PyPI:stä."""
    kohde.mkdir(parents=True, exist_ok=True)
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if tar.exists():
        p = subprocess.run([str(tar), "-xf", str(paketti), "-C", str(kohde)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=m.PIILOTA)
        if p.returncode == 0 and any(kohde.rglob("mpv.exe")):
            return
        m.loki(f"tar ei purkanut mpv-pakettia: {p.stderr[-300:]}")
    from .asennus import _python_exe
    subprocess.run([_python_exe(), "-m", "pip", "install", "py7zr", "--disable-pip-version-check",
                    "--no-warn-script-location"], capture_output=True, creationflags=m.PIILOTA)
    import importlib
    importlib.invalidate_caches()
    import py7zr
    with py7zr.SevenZipFile(paketti, "r") as z:
        z.extractall(kohde)


def lataa(edistys=None):
    """Lataa ja purkaa mpv:n kansioon DATA/mpv. Palauttaa mpv.exe-polun."""
    julkaisut = _hae_json(RAJAPINTA)
    paketti = None
    for j in julkaisut:
        if j.get("draft"):
            continue
        paketti = next((a for a in j.get("assets", []) if PAKETTI.match(a.get("name", ""))), None)
        if paketti:
            break
    if not paketti:
        raise RuntimeError("mpv_pakettia_ei_loydy")
    k = kansio()
    tmp = k.parent / "mpv_lataus"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    tiedosto = tmp / paketti["name"]
    h = hashlib.sha256()
    n = 0
    req = urllib.request.Request(paketti["browser_download_url"], headers={"User-Agent": f"PixeliRemaster/{m.VERSIO}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tiedosto, "wb") as f:
        koko = paketti.get("size") or int(r.headers.get("Content-Length") or 0)
        while True:
            pala = r.read(1 << 20)
            if not pala:
                break
            f.write(pala)
            h.update(pala)
            n += len(pala)
            if edistys and koko:
                edistys(0.9 * n / koko)
    tarkiste = (paketti.get("digest") or "").removeprefix("sha256:")
    if tarkiste and h.hexdigest() != tarkiste:
        raise RuntimeError("mpv_tarkistussumma")
    purettu = tmp / "purettu"
    _pura_7z(tiedosto, purettu)
    exe = next(purettu.rglob("mpv.exe"), None)
    if exe is None:
        raise RuntimeError("mpv_purku")
    (exe.parent / "pixeli_versio.txt").write_text(paketti["name"], encoding="utf-8")
    for q in k.parent.glob("mpv_vanha*"):        # aiemmin auki olleen soittimen jäänteet
        shutil.rmtree(q, ignore_errors=True)
    vanha = k.parent / f"mpv_vanha_{os.getpid()}_{int(time.time())}"
    if k.exists():
        try:
            k.rename(vanha)            # Windows ei salli siirtoa, jos soitin on auki: vanha jää ehjäksi
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("soitin_kaytossa")
    shutil.move(str(exe.parent), str(k))
    shutil.rmtree(vanha, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    m.loki(f"mpv asennettu: {paketti['name']}")
    if edistys:
        edistys(1.0)
    return str(k / "mpv.exe")


# ------------------------------------------------------------------ komennot

def _yhteiset(otsikko):
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    return ["--force-window=yes", "--keep-open=yes", f"--title={otsikko}", "--slang=fi,fin,en,eng",
            "--sub-auto=fuzzy", "--osd-duration=2500", f"--log-file={loki / 'mpv.log'}"]


def _lainaa(arvo):
    """mpv:n asetusarvo, jossa voi olla pilkkuja ja kaksoispisteitä: %pituus%arvo."""
    return f"%{len(arvo.encode('utf-8'))}%{arvo}"


def komento_toista(tulos, nimi):
    return [mpv_exe(), *_yhteiset(f"{nimi} – PixeliRemaster"), "--hwdec=auto-safe", "--", str(tulos)]


def vertailusuodin(an, geo):
    """lavfi-complex: vasemmalla alkuperäinen (sama lomituksen poisto, rajaus ja koko kuin käsittelyssä),
    oikealla remasteroitu; keskellä ohut jakoviiva."""
    x, y, cw, ch = geo["rajaus"]
    ow, oh = int(geo["ow"]), int(geo["oh"])
    alku = [s for s in [m.suodin_kuva(an)] if s]
    if (cw, ch) != (an["leveys"], an["korkeus"]):
        alku.append(f"crop={cw}:{ch}:{x}:{y}")
    puoli = ow // 2 // 2 * 2
    alkup = ",".join(alku + [f"scale={ow}:{oh}:flags=bicubic", "setsar=1", "format=yuv420p",
                             f"crop={puoli}:{oh}:0:0"])
    uusi = ",".join([f"scale={ow}:{oh}", "setsar=1", "format=yuv420p", f"crop={ow - puoli}:{oh}:{puoli}:0",
                     "drawbox=x=0:y=0:w=2:h=ih:color=white@0.7:t=fill"])
    return f"[vid2]{alkup}[va];[vid1]{uusi}[vb];[va][vb]hstack[vo];[aid1]anull[ao]"


def komento_vertaa(tulos, alkuperainen, an, geo, nimi):
    return [mpv_exe(), *_yhteiset(f"{nimi} – alkuperäinen | remasteroitu"), "--hwdec=auto-copy",
            f"--external-file={alkuperainen}", f"--lavfi-complex={vertailusuodin(an, geo)}",
            "--osd-playing-msg=Vasemmalla alkuperäinen, oikealla remasteroitu", "--", str(tulos)]


def rtx_suodin(an, kohde_korkeus):
    """d3d11vpp: (tarvittaessa lomituksen poisto) ja NVIDIA RTX Video Super Resolution -skaalaus."""
    h = max(1, int(an.get("korkeus") or 576))
    skaala = max(1.0, min(4.0, round(kohde_korkeus / h, 2)))
    osat = []
    if an.get("lomitus") in ("lomitettu", "ivtc", "desimointi"):
        osat += ["deint=yes", "interlaced-only=yes"]
    osat += [f"scale={skaala}", "scaling-mode=nvidia"]
    return "@rtx:d3d11vpp=" + ":".join(osat)


def komento_reaaliaika(alkuperainen, an, kohde_korkeus, nimi):
    syote = kansio() / "pixeli_input.conf"
    syote.parent.mkdir(parents=True, exist_ok=True)
    syote.write_text('n vf toggle @rtx ; show-text "RTX-parannus päälle / pois"\n', encoding="utf-8")
    return [mpv_exe(), *_yhteiset(f"{nimi} – reaaliaikainen parannus"), "--hwdec=d3d11va", "--gpu-api=d3d11",
            "--vo=gpu-next", f"--vf={rtx_suodin(an, kohde_korkeus)}", f"--input-conf={syote}",
            "--osd-playing-msg=RTX-parannus päällä – N vaihtaa päälle/pois", "--", str(alkuperainen)]


def kaynnista(cmd):
    m.loki("mpv: " + " ".join(str(c) for c in cmd[1:4]) + " …")
    return subprocess.Popen([str(c) for c in cmd], creationflags=0x00000200 if os.name == "nt" else 0)


def varmista_ja_kaynnista(tee_komento):
    """Käynnistää soittimen; jos mpv puuttuu, lataa sen ensin taustalla (edistyminen TILA:ssa)."""
    if mpv_exe():
        kaynnista(tee_komento())
        return {"tila": "toistetaan"}
    with _lukko:
        if TILA["tila"] == "ladataan":
            return dict(TILA)
        TILA.update({"tila": "ladataan", "edistys": 0.0, "virhe": None})

    def saie():
        try:
            def ed(f):
                TILA["edistys"] = round(f, 3)
            lataa(ed)
            TILA.update({"tila": "valmis", "edistys": 1.0})
            kaynnista(tee_komento())
        except Exception as e:  # noqa: BLE001
            m.loki(f"mpv:n lataus epäonnistui: {e}")
            TILA.update({"tila": "virhe", "virhe": str(e)[:200]})
    threading.Thread(target=saie, name="mpv", daemon=True).start()
    return dict(TILA)
