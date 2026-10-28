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
    d["suora"] = dict(SUORA)
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

def _yhteiset(otsikko, lokinimi="mpv"):
    """Kaikille toistotavoille. Elokuvan 24/25/50 kuvaa sekunnissa ei jakaudu tasan näytön virkistystaajuuteen
    (esim. 144 Hz), jolloin kuvat näkyvät epätasaisen pitkään ja liike nykii. display-resample tahdistaa
    toiston näyttöön (ääni sovitetaan huomaamattomasti) ja oversample tasoittaa loput ilman keinotekoista
    välikuvien laskentaa."""
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    return ["--force-window=yes", "--keep-open=yes", f"--title={otsikko}", "--slang=fi,fin,en,eng",
            "--sub-auto=fuzzy", "--osd-duration=2500", "--video-sync=display-resample", "--interpolation=yes",
            "--tscale=oversample", f"--log-file={loki / (lokinimi + '.log')}"]


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


RTX_TEKSTIT = {
    "fi": {"paalla": "RTX-parannus PÄÄLLÄ", "pois": "RTX-parannus POIS", "tavallinen": "tavallinen skaalaus",
           "ei_tukea": "RTX-parannus ei käytössä: näytönohjain tai ajuri ei tue sitä. Toistetaan ilman parannusta.",
           "ohje": "N vaihtaa", "ei_tarvetta": "Video on jo näytön kokoinen: RTX-parannukselle ei ole suurennettavaa."},
    "en": {"paalla": "RTX enhancement ON", "pois": "RTX enhancement OFF", "tavallinen": "regular scaling",
           "ei_tukea": "RTX enhancement unavailable: the graphics card or driver doesn't support it. Playing without it.",
           "ohje": "N toggles", "ei_tarvetta": "The video already matches the screen size: nothing for RTX enhancement to upscale."},
}

_RTX_LUA = r"""-- PixeliRemaster: RTX Video Super Resolution päälle/pois ja tilan näyttö.
-- Suodin lisätään vasta toiston alettua: jos näytönohjain tai ajuri ei tue sitä, lisäys vain
-- epäonnistuu ja video toistuu ilman parannusta (lomitetulle lähteelle silloin bwdif).
local mp = require "mp"
local SUODIN = %(suodin)s
local VARALLA = %(varalla)s
local SKAALA = %(skaala)s
local KESTO = %(kesto)s
local T = %(tekstit)s
local paalla = false
local tuettu = true
local aloitettu = false

local function koko(p)
    local v = mp.get_property_native(p)
    if v and v.w and v.h then return v.w, v.h end
    return nil
end

local function ilmoita(tila, teksti, kesto)
    mp.msg.info("RTX-tila: " .. tila)
    mp.osd_message(teksti, kesto)
end

-- parannus on käytössä, kun suodin on suurentanut kuvan (lähde -> ulostulo)
local function kerro(kesto, viimeinen)
    local lw, lh = koko("video-params")
    local ow, oh = koko("video-out-params")
    if not lw or not ow then return false end
    -- ulostulon koko päivittyy suodinketjun uudelleenrakennuksen jälkeen viiveellä: odotetaan
    if paalla and SKAALA >= 1.05 and oh <= lh * 1.05 and not viimeinen then return false end
    if SKAALA < 1.05 then
        ilmoita("ei_tarvetta", T.ei_tarvetta, kesto)
    elseif paalla and oh > lh * 1.05 then
        ilmoita("paalla", string.format("%%s  ·  %%d×%%d → %%d×%%d  ·  %%s", T.paalla, lw, lh, ow, oh, T.ohje), kesto)
    elseif paalla then
        ilmoita("ei_tukea", T.ei_tukea, kesto)
    else
        ilmoita("pois", string.format("%%s  ·  %%d×%%d, %%s  ·  %%s", T.pois, lw, lh, T.tavallinen, T.ohje), kesto)
    end
    return true
end

local function kerro_pian(kesto)
    -- ulostulon koko päivittyy vasta, kun suodinketju on rakennettu uudelleen
    local yritykset = 0
    local function yrita()
        yritykset = yritykset + 1
        if not kerro(kesto, yritykset >= 16) and yritykset < 16 then mp.add_timeout(0.25, yrita) end
    end
    mp.add_timeout(0.3, yrita)
end

local function kayttoon()
    if mp.commandv("vf", "add", SUODIN) then
        paalla = true
        if VARALLA ~= "" then mp.command("vf remove @varalla") end
        return true
    end
    tuettu = false
    paalla = false
    if VARALLA ~= "" then mp.commandv("vf", "add", VARALLA) end
    ilmoita("ei_tukea", T.ei_tukea, 6)
    return false
end

mp.add_key_binding("n", "pixeli-rtx", function()
    if not tuettu then
        ilmoita("ei_tukea", T.ei_tukea, 4)
        return
    end
    if paalla then
        mp.command("vf remove @rtx")
        paalla = false
        if VARALLA ~= "" then mp.commandv("vf", "add", VARALLA) end
        kerro_pian(3)
    elseif kayttoon() then
        kerro_pian(3)
    end
end)

-- levyltä toistettaessa: valitaan nimike, jonka kesto on lähinnä valittua (oletus: pisin = pääelokuva)
local function valitse_nimike()
    if KESTO <= 0 then return end
    -- levyn nimikkeet näkyvät soittimessa editioneina: "title: N (HH:MM:SS.mmm)"
    local lista = mp.get_property_native("edition-list") or {}
    if #lista < 2 then return end
    local paras, ero = nil, 1e9
    for _, e in ipairs(lista) do
        local h, mi, s = string.match(e.title or "", "(%%d+):(%%d+):(%%d+)")
        if h then
            local pit = tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
            if math.abs(pit - KESTO) < ero then paras, ero = e.id, math.abs(pit - KESTO) end
        end
    end
    if paras and paras ~= mp.get_property_number("current-edition") then
        pcall(mp.set_property_number, "edition", paras)
    end
end

mp.register_event("file-loaded", function()
    if aloitettu then return end
    aloitettu = true
    pcall(valitse_nimike)
    if kayttoon() then kerro_pian(5) end
end)
"""


def _lua_jono(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rtx_skripti(suodin, kieli="fi", kesto=0):
    t = RTX_TEKSTIT.get(kieli, RTX_TEKSTIT["en"])
    taulu = "{" + ", ".join(f"{k} = {_lua_jono(v)}" for k, v in t.items()) + "}"
    r = re.search(r"scale=([0-9.]+)", suodin)
    varalla = "@varalla:bwdif=deint=interlaced" if "deint=yes" in suodin else ""
    return _RTX_LUA % {"suodin": _lua_jono(suodin), "tekstit": taulu, "skaala": r.group(1) if r else "1",
                       "varalla": _lua_jono(varalla), "kesto": f"{float(kesto or 0):.1f}"}


# ------------------------------------------------------------------ soittimen oma suurennus (ei NVIDIAn asetusta)
# Avoimen lähdekoodin suurennusmallit mpv:n varjostimina: FSRCNNX (igv, LGPL-3.0) elokuville,
# Anime4K (bloc97, MIT, "Mode C HQ" pienen tarkkuuden piirretyille) piirretyille. Ne toimivat vain tässä
# soittimessa, eivätkä vaadi NVIDIAn RTX Video -asetusta.
VARJOSTIMET = {
    "elokuva": ["FSRCNNX_x2_16-0-4-1.glsl"],
    "piirretty": ["Anime4K_Clamp_Highlights.glsl", "Anime4K_Upscale_Denoise_CNN_x2_VL.glsl",
                  "Anime4K_AutoDownscalePre_x2.glsl", "Anime4K_AutoDownscalePre_x4.glsl",
                  "Anime4K_Upscale_CNN_x2_M.glsl"],
}
OMA_TEKSTIT = {
    "fi": {"paalla": "Parannus PÄÄLLÄ ({malli})", "pois": "Parannus POIS (tavallinen skaalaus)", "ohje": "N vaihtaa",
           "elokuva": "FSRCNNX", "piirretty": "Anime4K"},
    "en": {"paalla": "Enhancement ON ({malli})", "pois": "Enhancement OFF (regular scaling)", "ohje": "N toggles",
           "elokuva": "FSRCNNX", "piirretty": "Anime4K"},
}

_OMA_LUA = r"""-- PixeliRemaster: soittimen oma suurennus (varjostimet) päälle/pois
local mp = require "mp"
local T = %(tekstit)s
local MALLI = %(malli)s
local varjostimet = nil
local function kerro(paalla, kesto)
    local lw, lh = 0, 0
    local v = mp.get_property_native("video-params")
    if v and v.w then lw, lh = v.w, v.h end
    local t = paalla and T.paalla:gsub("{malli}", T[MALLI]) or T.pois
    mp.msg.info("Oma parannus: " .. (paalla and "paalla" or "pois"))
    mp.osd_message(string.format("%%s  ·  %%dx%%d  ·  %%s", t, lw, lh, T.ohje), kesto)
end
mp.register_event("file-loaded", function()
    if varjostimet == nil then
        varjostimet = mp.get_property_native("glsl-shaders") or {}
        mp.add_timeout(0.8, function() kerro(#varjostimet > 0, 5) end)
    end
end)
mp.add_key_binding("n", "pixeli-oma", function()
    local nyt = mp.get_property_native("glsl-shaders") or {}
    if #nyt > 0 then
        mp.set_property_native("glsl-shaders", {})
        kerro(false, 3)
    else
        mp.set_property_native("glsl-shaders", varjostimet or {})
        kerro(true, 3)
    end
end)
"""


def varjostinkansio():
    return m.SOVELLUS / "shaders"


def komento_oma(alkuperainen, an, nimi, kieli="fi", lisat=(), lokinimi="mpv", piirretty=False):
    laji = "piirretty" if piirretty else "elokuva"
    t = OMA_TEKSTIT.get(kieli, OMA_TEKSTIT["en"])
    skripti = kansio() / "pixeli_oma.lua"
    skripti.parent.mkdir(parents=True, exist_ok=True)
    taulu = "{" + ", ".join(f"{k} = {_lua_jono(v)}" for k, v in t.items()) + "}"
    skripti.write_text(_OMA_LUA % {"tekstit": taulu, "malli": _lua_jono(laji)}, encoding="utf-8")
    polut = [str(varjostinkansio() / f) for f in VARJOSTIMET[laji]]
    lomitettu = an.get("lomitus") in ("lomitettu", "ivtc", "desimointi")
    return [mpv_exe(), *_yhteiset(f"{nimi} – reaaliaikainen parannus", lokinimi), "--hwdec=auto-copy",
            "--vo=gpu-next", "--scale=ewa_lanczossharp", "--cscale=ewa_lanczossharp", "--dscale=mitchell",
            *(["--vf=@deint:bwdif=deint=interlaced"] if lomitettu else []),
            *[f"--glsl-shader={p}" for p in polut], f"--script={skripti}", *lisat, "--", str(alkuperainen)]


def komento_reaaliaika(alkuperainen, an, kohde_korkeus, nimi, kieli="fi", lisat=(), levyn_kesto=0, lokinimi="mpv"):
    suodin = rtx_suodin(an, kohde_korkeus)
    skripti = kansio() / "pixeli_rtx.lua"
    skripti.parent.mkdir(parents=True, exist_ok=True)
    skripti.write_text(rtx_skripti(suodin, kieli, levyn_kesto), encoding="utf-8")
    return [mpv_exe(), *_yhteiset(f"{nimi} – reaaliaikainen parannus", lokinimi), "--hwdec=d3d11va", "--gpu-api=d3d11",
            "--vo=gpu-next", f"--script={skripti}", *lisat, "--", str(alkuperainen)]


SUORA = {"tila": "ei", "virhe": None}


_NIMIKE_LUA = r"""local mp = require "mp"
local KESTO = %(kesto)s
mp.register_event("file-loaded", function()
    if KESTO <= 0 then return end
    -- levyn nimikkeet näkyvät soittimessa editioneina: "title: N (HH:MM:SS.mmm)"
    local lista = mp.get_property_native("edition-list") or {}
    if #lista < 2 then return end
    local paras, ero = nil, 1e9
    for _, e in ipairs(lista) do
        local h, mi, s = string.match(e.title or "", "(%%d+):(%%d+):(%%d+)")
        if h then
            local pit = tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
            if math.abs(pit - KESTO) < ero then paras, ero = e.id, math.abs(pit - KESTO) end
        end
    end
    if paras and paras ~= mp.get_property_number("current-edition") then
        pcall(mp.set_property_number, "edition", paras)
    end
end)
"""


def _nimike_skripti(kesto):
    p = kansio() / "pixeli_nimike.lua"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_NIMIKE_LUA % {"kesto": f"{float(kesto or 0):.1f}"}, encoding="utf-8")
    return [f"--script={p}"]


def katso_levylta(asema, kesto, bluray, kohde_korkeus, nimi, kieli="fi", tapa="oma", piirretty=False, nimike=None):
    """Levy suoraan soittimeen ilman kopiointia. DVD toistetaan soittimen omalla DVD-tuella
    (libdvdnav/libdvdread/libdvdcss, kuten VLC:ssä): nimike suoraan numerolla (dvd://N), tai keston perusteella.
    Blu-ray toistuu vain, jos levy ei ole suojattu (soittimessa ei ole AACS-purkua)."""
    if SUORA["tila"] in ("avataan", "toistetaan"):
        return dict(SUORA)
    SUORA.update({"tila": "avataan", "virhe": None})

    def saie():
        try:
            if not mpv_exe():
                TILA.update({"tila": "ladataan", "edistys": 0.0, "virhe": None})
                lataa(lambda f: TILA.update({"edistys": round(f, 3)}))
                TILA.update({"tila": "valmis", "edistys": 1.0})
            laite = str(asema or "").rstrip("\\/")
            if bluray:
                an, url, lisat = {"korkeus": 1080, "lomitus": "progressiivinen"}, "bd://", [f"--bluray-device={laite}"]
            else:
                an, url, lisat = {"korkeus": 576, "lomitus": "lomitettu"}, "dvd://", [f"--dvd-device={laite}"]
            valinta_kesto = 0
            if not bluray and nimike is not None:
                url = f"dvd://{int(nimike)}"                          # nimike tiedetään suoraan
            elif kesto:
                # MakeMKV:n nimikenumero ei ole levyn numero: haetaan levyltä kestoltaan sama nimike
                from . import levy
                try:
                    valittu = levy.nimike_kestolla(levy.soittimen_tiedot(url, lisat[0], 120).get("editions"), kesto)
                except Exception as e:  # noqa: BLE001
                    m.loki(f"Levyn nimikkeitä ei saatu soittimelta: {e}")
                    valittu, valinta_kesto = None, kesto
                if valittu is not None:
                    url = f"{url}{valittu}"
            lisat += ["--cache=yes", "--cache-secs=90", "--demuxer-readahead-secs=90", "--demuxer-max-bytes=600MiB"]
            if tapa == "rtx":
                cmd = komento_reaaliaika(url, an, kohde_korkeus, nimi, kieli, lisat, levyn_kesto=valinta_kesto,
                                         lokinimi="mpv_levy")
            else:
                cmd = komento_oma(url, an, nimi, kieli, lisat + _nimike_skripti(valinta_kesto), lokinimi="mpv_levy",
                                  piirretty=piirretty)
            m.loki(f"Katsotaan levyltä: {url} {laite} (kesto {kesto} s)")
            SUORA["tila"] = "toistetaan"
            alku = time.time()
            koodi = kaynnista(cmd).wait()
            if koodi not in (0, None) and time.time() - alku < 20:
                raise RuntimeError("bluray_suojattu" if bluray else "levy_ei_auennut")
            SUORA["tila"] = "ei"
        except Exception as e:  # noqa: BLE001
            m.loki(f"Levyltä katselu epäonnistui: {e}")
            SUORA.update({"tila": "virhe", "virhe": str(e)[:300]})
    threading.Thread(target=saie, name="levylta", daemon=True).start()
    return dict(SUORA)


KESKEN_TEKSTIT = {
    "fi": {"alku": "Katsotaan käsittelyn aikana – valmiina {valmis} / {kaikki}",
           "odottaa": "Odotetaan käsittelyä… seuraava osa valmistuu pian",
           "jatkuu": "Uusi osa valmis – jatketaan",
           "ei_viela": "Tätä kohtaa ei ole vielä käsitelty (valmiina {valmis})",
           "valmis": "Koko elokuva on käsitelty"},
    "en": {"alku": "Watching while processing – ready {valmis} / {kaikki}",
           "odottaa": "Waiting for processing… the next part is almost ready",
           "jatkuu": "New part ready – continuing",
           "ei_viela": "This part hasn't been processed yet (ready up to {valmis})",
           "valmis": "The whole movie has been processed"},
}

_KESKEN_LUA = r"""-- PixeliRemaster: katselu käsittelyn aikana. Lukee tilatiedostoa, lataa uudet valmiit osat,
-- pysähtyy odottamaan, jos katselu saavuttaa käsittelyn, eikä päästä käsittelemättömään kohtaan.
local mp = require "mp"
local utils = require "mp.utils"
local TILA = %(tila)s
local T = %(tekstit)s
local ladattu = 0          -- ladatun kokonaisuuden kuvan kesto (s)
local odottaa = false

local function lue()
    local f = io.open(TILA, "r")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return utils.parse_json(s)
end

local function aika(s)
    s = math.max(0, math.floor(s))
    return string.format("%%d:%%02d:%%02d", math.floor(s / 3600), math.floor(s %% 3600 / 60), s %% 60)
end

local function teksti(m, d)
    return (m:gsub("{valmis}", aika(d.kesto or 0)):gsub("{kaikki}", aika(d.kesto_kaikki or 0)))
end

local function lataa_uudelleen(d, kohta, jatka)
    local tauko = (not jatka) and mp.get_property_bool("pause")
    local opts = string.format("start=%%.3f,pause=%%s", kohta, tauko and "yes" or "no")
    local aid, sid = mp.get_property("aid"), mp.get_property("sid")
    if aid and aid ~= "auto" then opts = opts .. ",aid=" .. aid end
    if sid and sid ~= "auto" then opts = opts .. ",sid=" .. sid end
    ladattu = d.kesto
    mp.set_property_number("audio-delay", -(d.vstart or 0))   -- valmis tiedosto on jo tahdistettu
    mp.commandv("loadfile", d.edl, "replace", "-1", opts)
end

local function tarkista()
    local d = lue()
    if not d or not d.kesto then return end
    local kohta = mp.get_property_number("time-pos")
    if not kohta then return end
    -- käsittelemättömään kohtaan siirtyminen (esim. aikajanalta)
    if kohta > ladattu + 0.5 then
        mp.osd_message(teksti(T.ei_viela, d), 4)
        if d.kesto > ladattu + 1 then lataa_uudelleen(d, math.max(0, ladattu - 3))
        else mp.commandv("seek", tostring(math.max(0, ladattu - 3)), "absolute") end
        return
    end
    local uutta = d.kesto > ladattu + 1
    if odottaa and uutta then
        odottaa = false
        mp.osd_message(T.jatkuu, 2)
        lataa_uudelleen(d, kohta, true)
    elseif uutta and kohta > ladattu - 20 then
        lataa_uudelleen(d, kohta)        -- uudet osat mukaan hyvissä ajoin ennen loppua
    elseif not d.valmis and kohta > ladattu - 1.5 and not odottaa then
        odottaa = true
        mp.set_property_bool("pause", true)
        mp.osd_message(T.odottaa, 3600)
    elseif odottaa then
        mp.osd_message(T.odottaa, 3600)
    end
end

local alussa = true
mp.register_event("file-loaded", function()
    if alussa then
        alussa = false
        local d = lue()
        if d then
            ladattu = d.kesto or 0
            mp.osd_message(teksti(d.valmis and T.valmis or T.alku, d), 5)
        end
    end
end)
mp.add_periodic_timer(1.5, tarkista)
"""


def kesken_skripti(tila_polku, kieli="fi"):
    t = KESKEN_TEKSTIT.get(kieli, KESKEN_TEKSTIT["en"])
    taulu = "{" + ", ".join(f"{k} = {_lua_jono(v)}" for k, v in t.items()) + "}"
    return _KESKEN_LUA % {"tila": _lua_jono(str(tila_polku)), "tekstit": taulu}


def komento_kesken(tila_polku, nimi, kieli="fi"):
    d = json.loads(Path(tila_polku).read_text(encoding="utf-8"))
    skripti = kansio() / "pixeli_kesken.lua"
    skripti.parent.mkdir(parents=True, exist_ok=True)
    skripti.write_text(kesken_skripti(tila_polku, kieli), encoding="utf-8")
    return [mpv_exe(), *_yhteiset(f"{nimi} – käsittelyn aikana"), "--hwdec=auto-safe", "--vid=1",
            f"--audio-delay={-float(d.get('vstart') or 0):.3f}", f"--script={skripti}", "--", d["edl"]]


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
