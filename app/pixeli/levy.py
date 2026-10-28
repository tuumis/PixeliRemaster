"""PixeliRemaster – DVD- ja Blu-ray-levyjen lisäys MakeMKV:n avulla.

MakeMKV ei ole avointa koodia eikä sitä saa jakaa muiden ohjelmien mukana, joten käytetään
käyttäjän koneelle asentamaa MakeMKV:tä sen komentorivityökalun (makemkvcon) robottitilassa:
  info disc:9999      -> asemat (DRV-rivit)
  info disc:N         -> levyn nimikkeet (TINFO-rivit)
  mkv disc:N T kansio -> nimike MKV-tiedostoksi, edistyminen PRGV-riveinä
Kopiosuojauksen käsittely ja sen lisenssi (Blu-ray-beta) ovat MakeMKV:n vastuulla.
"""
import csv
import os
import re
import subprocess
import time
from pathlib import Path

from . import moottori as m

# AP_ItemAttributeId (MakeMKV apdefs.h)
NIMI, LUVUT, KESTO, KOKO_TEKSTI, KOKO_TAVUT, LAHDE, OSIA, ULOS = 2, 8, 9, 10, 11, 16, 25, 27


def makemkvcon():
    """makemkvcon64.exe tavallisista asennuspaikoista (tai PATHista). None, jos ei asennettu."""
    ehdokkaat = []
    for juuri in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), r"C:\Program Files (x86)",
                  r"C:\Program Files"):
        if juuri:
            ehdokkaat += [Path(juuri) / "MakeMKV" / "makemkvcon64.exe", Path(juuri) / "MakeMKV" / "makemkvcon.exe"]
    for e in ehdokkaat:
        if e.exists():
            return str(e)
    import shutil
    return shutil.which("makemkvcon64") or shutil.which("makemkvcon")


def _kentat(loppu):
    """Robottirivin kentät: pilkuilla erotettuja, merkkijonot lainausmerkeissä."""
    return next(csv.reader([loppu], skipinitialspace=False))


def jasenna(teksti):
    """Robottitulosteen rivit -> {"asemat": [...], "levy": {...}, "nimikkeet": {id: {...}}, "viestit": [...]}"""
    tulos = {"asemat": [], "levy": {}, "nimikkeet": {}, "viestit": []}
    for rivi in teksti.splitlines():
        if ":" not in rivi:
            continue
        laji, loppu = rivi.split(":", 1)
        try:
            k = _kentat(loppu)
        except Exception:  # noqa: BLE001
            continue
        if laji == "DRV" and len(k) >= 6:
            levy = k[5].strip()
            if k[4].strip() or levy:
                tulos["asemat"].append({"indeksi": int(k[0]), "asema": k[4].strip(), "levy": levy,
                                        "polku": k[6].strip() if len(k) > 6 else "", "levy_sisalla": bool(levy)})
        elif laji == "CINFO" and len(k) >= 3:
            tulos["levy"][int(k[0])] = k[2]
        elif laji == "TINFO" and len(k) >= 4:
            tulos["nimikkeet"].setdefault(int(k[0]), {})[int(k[1])] = k[3]
        elif laji == "MSG" and len(k) >= 4:
            tulos["viestit"].append(k[3])
    return tulos


def kesto_s(s):
    """'1:45:32' -> 6332."""
    try:
        osat = [int(x) for x in str(s).split(":")]
    except ValueError:
        return 0
    t = 0
    for o in osat:
        t = t * 60 + o
    return t


def _aja(args, aikaraja=300):
    exe = makemkvcon()
    if not exe:
        raise RuntimeError("makemkv_puuttuu")
    p = subprocess.run([exe, "-r", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=aikaraja, creationflags=m.PIILOTA)
    return p.stdout


def asemat():
    """Optiset asemat ja niissä olevat levyt."""
    return jasenna(_aja(["--cache=1", "info", "disc:9999"], 120))["asemat"]


def nimikkeet(indeksi):
    """Levyn nimikkeet pisimmästä lyhimpään; pääelokuva (pisin) merkitään suositukseksi."""
    d = jasenna(_aja(["--cache=1", "--minlength=120", "info", f"disc:{int(indeksi)}"], 600))
    lista = []
    for tid, a in d["nimikkeet"].items():
        lista.append({"id": tid, "nimi": a.get(NIMI) or "", "kesto": kesto_s(a.get(KESTO)),
                      "luvut": int(a.get(LUVUT) or 0), "koko": int(a.get(KOKO_TAVUT) or 0),
                      "koko_teksti": a.get(KOKO_TEKSTI) or "", "lahde": a.get(LAHDE) or "",
                      "tiedosto": a.get(ULOS) or ""})
    lista.sort(key=lambda x: (-x["kesto"], x["id"]))
    if lista:
        lista[0]["paaelokuva"] = True
    levyn_nimi = d["levy"].get(NIMI) or d["levy"].get(32) or ""
    virhe = None
    if not lista:
        # tyypillisesti: ei levyä, Blu-ray-betan avain vanhentunut tai levyä ei voitu lukea
        virhe = next((v for v in reversed(d["viestit"]) if v), None)
    tyyppi = (d["levy"].get(1) or "").lower()       # esim. "DVD disc" / "Blu-ray disc"
    return {"levy": levyn_nimi, "nimikkeet": lista, "virhe": virhe,
            "bluray": "blu" in tyyppi or any(x["lahde"].lower().endswith(".mpls") for x in lista)}


def turvallinen_nimi(s, oletus="Levy"):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(s or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip()
    return (s[:80] or oletus)


def rippaa(indeksi, nimike, kansio, edistys=None, peruuta=None):
    """Nimike MKV-tiedostoksi kansioon. Palauttaa luodun tiedoston polun."""
    exe = makemkvcon()
    if not exe:
        raise RuntimeError("makemkv_puuttuu")
    kansio = Path(kansio)
    kansio.mkdir(parents=True, exist_ok=True)
    ennen = {p.name for p in kansio.glob("*.mkv")}
    alku = time.time()
    cmd = [exe, "-r", "--progress=-same", "--cache=512", "mkv", f"disc:{int(indeksi)}", str(int(nimike)), str(kansio)]
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=m.PIILOTA)
    viestit = []
    with open(loki / "makemkv.log", "w", encoding="utf-8") as lf:
        for rivi in p.stdout:
            lf.write(rivi)
            if peruuta is not None and peruuta.is_set():
                p.kill()
                break
            if rivi.startswith("PRGV:"):
                try:
                    nyt, kaikki, maks = (int(x) for x in rivi[5:].strip().split(","))
                    if edistys and maks:
                        edistys(kaikki / maks)
                except ValueError:
                    pass
            elif rivi.startswith("MSG:"):
                try:
                    viestit.append(_kentat(rivi[4:])[3])
                except Exception:  # noqa: BLE001
                    pass
    p.wait()
    if peruuta is not None and peruuta.is_set():
        for q in kansio.glob("*.mkv"):
            if q.name not in ennen and q.stat().st_mtime >= alku - 5:
                try:
                    q.unlink()
                except OSError:
                    pass
        raise m.Peruutettu()
    uudet = [q for q in kansio.glob("*.mkv") if q.name not in ennen and q.stat().st_size > 0]
    if p.returncode != 0 or not uudet:
        viesti = next((v for v in reversed(viestit) if v), "")
        raise RuntimeError(f"rippaus_epaonnistui: {viesti[-300:]}")
    return max(uudet, key=lambda q: q.stat().st_size)


# ------------------------------------------------------------------ DVD ilman MakeMKV:tä (soittimen omat kirjastot)
# Soitin (mpv) sisältää libdvdnavin, libdvdreadin ja libdvdcss:n (samat kuin VLC:ssä). Levyn nimikkeet
# luetaan soittimella, ja nimike kopioidaan soittimen --stream-dump-toiminnolla (purettu MPEG-PS
# sellaisenaan) ja pakataan ffmpegillä MKV:ksi kaikkine ääni- ja tekstitysraitoineen ja lukuineen
# (uudelleenkoodaamatta, laatu ei muutu). Blu-ray-levyjen AACS-suojausta soitin ei pura: niihin MakeMKV.

def optiset_asemat():
    """Windowsin optiset asemat ja niiden levyt: [{asema, levy, levy_sisalla, laji}] (laji dvd/bluray/muu)."""
    if os.name != "nt":
        return []
    import ctypes
    import string
    k32 = ctypes.windll.kernel32
    vanha = k32.SetErrorMode(0x0001 | 0x8000)          # ei "laita levy asemaan" -ilmoituksia
    try:
        maski = k32.GetLogicalDrives()
        tulos = []
        for i, kirjain in enumerate(string.ascii_uppercase):
            if not (maski >> i) & 1:
                continue
            juuri = f"{kirjain}:\\"
            if k32.GetDriveTypeW(juuri) != 5:              # DRIVE_CDROM
                continue
            nimi = ctypes.create_unicode_buffer(261)
            ok = bool(k32.GetVolumeInformationW(juuri, nimi, 261, None, None, None, None, 0))
            tulos.append({"asema": f"{kirjain}:", "levy": nimi.value if ok else "", "levy_sisalla": ok,
                          "laji": levyn_laji(juuri) if ok else None})
        return tulos
    finally:
        k32.SetErrorMode(vanha)


def levyn_laji(juuri):
    p = Path(juuri)
    try:
        if (p / "BDMV").is_dir():
            return "bluray"
        if (p / "VIDEO_TS").is_dir():
            return "dvd"
    except OSError:
        pass
    return "muu"


def ifo_nimikkeet(juuri):
    """VIDEO_TS.IFO:n nimiketaulukko (TT_SRPT, ei salattu): {nimike0: {"vts", "luvut", "kulmat"}}."""
    import struct
    try:
        data = (Path(juuri) / "VIDEO_TS" / "VIDEO_TS.IFO").read_bytes()
        if data[:12] != b"DVDVIDEO-VMG":
            return {}
        alku = struct.unpack(">I", data[0xC4:0xC8])[0] * 2048
        n = struct.unpack(">H", data[alku:alku + 2])[0]
        tulos = {}
        for i in range(min(n, 99)):
            r = data[alku + 8 + 12 * i: alku + 20 + 12 * i]
            if len(r) < 12:
                break
            tulos[i] = {"kulmat": r[1], "luvut": struct.unpack(">H", r[2:4])[0], "vts": r[6]}
        return tulos
    except (OSError, IndexError, struct.error):
        return {}


def vts_koko(juuri, vts):
    """Nimikejoukon videotiedostojen (VTS_nn_1..9.VOB) yhteiskoko tavuina."""
    koko = 0
    for k in range(1, 10):
        try:
            koko += (Path(juuri) / "VIDEO_TS" / f"VTS_{int(vts):02d}_{k}.VOB").stat().st_size
        except OSError:
            pass
    return koko


def _koko_teksti(tavut):
    return f"~{tavut / 1e9:.1f} GB" if tavut >= 1e8 else ""


_TIEDOT_LUA = r"""local mp = require "mp"
local utils = require "mp.utils"
local KOHDE = %(kohde)s
local function kirjoita()
    local d = {editions = mp.get_property_native("edition-list") or {},
               tracks = mp.get_property_native("track-list") or {},
               chapters = mp.get_property_native("chapter-list") or {},
               edition = mp.get_property_native("current-edition"),
               duration = mp.get_property_native("duration")}
    local f = io.open(KOHDE, "w")
    if f then f:write(utils.format_json(d)); f:close() end
    mp.command("quit")
end
mp.register_event("file-loaded", kirjoita)
mp.add_timeout(90, function() mp.command("quit 3") end)
"""

KIELI3 = {"fi": "fin", "sv": "swe", "en": "eng", "no": "nor", "nb": "nor", "da": "dan", "de": "ger", "fr": "fre",
          "es": "spa", "it": "ita", "nl": "dut", "pt": "por", "pl": "pol", "ru": "rus", "et": "est", "hu": "hun",
          "cs": "cze", "el": "gre", "tr": "tur", "is": "ice", "ja": "jpn", "zh": "chi", "ko": "kor"}


def _lua_jono(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def kesto_otsikosta(otsikko):
    """Soittimen nimikeotsikko 'title: 3 (01:45:32.120)' -> 6332."""
    r = re.search(r"\((\d+):(\d+):(\d+)", str(otsikko or ""))
    return int(r.group(1)) * 3600 + int(r.group(2)) * 60 + int(r.group(3)) if r else 0


def jasenna_dvd(d, ifo=None, koot=None):
    """Soittimen tiedot (edition-list, track-list) -> nimikkeet ja raidat käyttöliittymälle."""
    ifo, koot = ifo or {}, koot or {}
    nimikkeet = []
    for e in d.get("editions") or []:
        otsikko = str(e.get("title") or "")
        if not otsikko.lower().startswith("title"):
            continue                                         # esim. "Disc Menu"
        kesto = kesto_otsikosta(otsikko)
        if kesto < 120:
            continue
        nid = int(e.get("id", 0))
        tieto = ifo.get(nid, {})
        nimikkeet.append({"id": nid, "nimi": "", "kesto": kesto, "luvut": int(tieto.get("luvut") or 0),
                          "koko": 0, "koko_teksti": "", "lahde": f"title {nid + 1}", "vts": tieto.get("vts")})
    # koon arvio: nimikejoukon tiedostot jaettuna sen nimikkeiden kestojen suhteessa
    for n in nimikkeet:
        kaikki = koot.get(n["vts"]) or 0
        if kaikki:
            samat = sum(x["kesto"] for x in nimikkeet if x["vts"] == n["vts"]) or n["kesto"]
            n["koko"] = int(kaikki * min(1.0, n["kesto"] / samat))
            n["koko_teksti"] = _koko_teksti(n["koko"])
    nimikkeet.sort(key=lambda x: (-x["kesto"], x["id"]))
    if nimikkeet:
        nimikkeet[0]["paaelokuva"] = True
    raidat = [{"tyyppi": t.get("type"), "src": t.get("src-id"), "kieli": t.get("lang"), "koodekki": t.get("codec"),
               "nimi": t.get("title")}
              for t in d.get("tracks") or [] if t.get("type") in ("audio", "sub")]
    luvut = [float(c.get("time") or 0) for c in d.get("chapters") or []]
    return {"nimikkeet": nimikkeet, "raidat": raidat, "luvut": luvut, "kesto": d.get("duration")}


def soittimen_tiedot(url, laiteasetus, aikaraja=150):
    """Avaa levyn soittimessa näyttämättä mitään ja palauttaa sen editionit, raidat ja luvut (JSON)."""
    from . import soitin
    exe = soitin.mpv_exe()
    if not exe:
        soitin.lataa()
        exe = soitin.mpv_exe()
    if not exe:
        raise RuntimeError("soitin_puuttuu")
    kansio = m.DATA / "levy"
    kansio.mkdir(parents=True, exist_ok=True)
    kohde = kansio / "tiedot.json"
    if kohde.exists():
        kohde.unlink()
    skripti = kansio / "tiedot.lua"
    skripti.write_text(_TIEDOT_LUA % {"kohde": _lua_jono(kohde)}, encoding="utf-8")
    cmd = [exe, "--no-config", "--idle=no", "--vo=null", "--ao=null", "--pause", "--msg-level=all=v",
           laiteasetus, f"--script={skripti}", url]
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    with open(loki / "levy_tiedot.log", "wb") as lf:
        try:
            subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=aikaraja, creationflags=m.PIILOTA)
        except subprocess.TimeoutExpired:
            pass
    if not kohde.exists():
        raise RuntimeError("levya_ei_voitu_lukea")
    import json
    return json.loads(kohde.read_text(encoding="utf-8"))


def dvd_tiedot(asema, nimike=None, aikaraja=150):
    """Levyn nimikkeet (soittimen editionit) ja valitun nimikkeen raidat ja luvut."""
    laite = str(asema).rstrip("\\/")
    d = soittimen_tiedot(f"dvd://{int(nimike)}" if nimike is not None else "dvd://", f"--dvd-device={laite}",
                         aikaraja)
    juuri = laite + "\\"
    ifo = ifo_nimikkeet(juuri)
    koot = {v: vts_koko(juuri, v) for v in {x["vts"] for x in ifo.values()}}
    return jasenna_dvd(d, ifo, koot)


def nimike_kestolla(editionit, kesto):
    """Edition, jonka kesto on lähinnä annettua (MakeMKV:n nimikenumerot eivät ole levyn nimikenumeroita)."""
    paras, ero = None, None
    for e in editionit or []:
        pit = kesto_otsikosta(e.get("title"))
        if pit and (ero is None or abs(pit - kesto) < ero):
            paras, ero = int(e.get("id", 0)), abs(pit - kesto)
    return paras


def kielimetat(virrat, raidat):
    """ffprobe-virrat (MPEG-PS-tunnus, esim. 0x80) + soittimen raidat (src-id, kieli) -> ffmpegin metadata-argumentit."""
    kielet = {}
    for r in raidat or []:
        if r.get("src") is not None and r.get("kieli"):
            k = str(r["kieli"]).lower()
            kielet[int(r["src"])] = KIELI3.get(k, k)
    # kaikki ääni- ja tekstitysraidat kopioidaan syöttöjärjestyksessä, joten tyypin sisäinen järjestysnumero
    # on sama syötössä ja tuloksessa (s:a:N / s:s:N)
    meta, jarj = [], {"audio": 0, "subtitle": 0}
    for v in sorted(virrat or [], key=lambda x: int(x.get("index", 0))):
        tyyppi = v.get("codec_type")
        if tyyppi not in jarj:
            continue
        n = jarj[tyyppi]
        jarj[tyyppi] += 1
        try:
            tunnus = int(str(v.get("id")), 16)
        except (TypeError, ValueError):
            continue
        if tunnus in kielet:
            meta += [f"-metadata:s:{tyyppi[0]}:{n}", f"language={kielet[tunnus]}"]
    return meta


def lukumetat(luvut, kesto):
    """Luvut (alkuajat sekunteina) ffmpegin FFMETADATA-muotoon."""
    luvut = sorted(t for t in luvut or [] if t >= 0)
    if len(luvut) < 2:
        return ""
    rivit = [";FFMETADATA1"]
    for i, t in enumerate(luvut):
        loppu = luvut[i + 1] if i + 1 < len(luvut) else max(float(kesto or 0), t + 1)
        rivit += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t * 1000)}", f"END={int(loppu * 1000)}",
                  f"title={i + 1:02d}"]
    return "\n".join(rivit) + "\n"


class NimikkeenLoppu:
    """Soittimen (-v) lokiriveistä: milloin levynlukija on siirtynyt valitun nimikkeen ulkopuolelle."""
    SOLU = re.compile(r"cell change: cell=(\d+) pg=\d+ pgc_len=[\d.]+ cur_time=([\d.]+) domain=(\w+)")
    UUSI = re.compile(r"new title (\d+)")

    def __init__(self, nimike):
        self.nimike = int(nimike) + 1          # libdvdnavin numerointi alkaa ykkösestä
        self.alkanut = False
        self.solu = 0
        self.aika = -1.0
        self.syy = None

    def rivi(self, rivi):
        if self.syy:
            return False
        r = self.SOLU.search(rivi)
        if r:
            solu, aika, alue = int(r.group(1)), float(r.group(2)), r.group(3)
            if self.alkanut:
                if alue != "VTS":
                    self.syy = f"alue {alue}"
                elif solu < self.solu or aika + 1.0 < self.aika:
                    self.syy = f"solu {self.solu}->{solu}, aika {self.aika:.0f}->{aika:.0f}"
            if not self.syy and alue == "VTS":
                self.alkanut, self.solu, self.aika = True, solu, aika
            return bool(self.syy)
        r = self.UUSI.search(rivi)
        if r and self.alkanut and int(r.group(1)) != self.nimike:
            self.syy = f"nimike {r.group(1)}"
        elif "Requested title not found" in rivi and self.alkanut:
            self.syy = "nimike vaihtui"
        elif "switched to VTS" in rivi and self.alkanut:
            self.syy = "nimikejoukko vaihtui"
        return bool(self.syy)


def tiedoston_kesto(polku):
    try:
        r = subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                            "default=nw=1:nk=1", str(polku)], capture_output=True, text=True, timeout=60,
                           creationflags=m.PIILOTA)
        return float(r.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def rippaa_dvd(asema, nimike, kesto, kansio, edistys=None, peruuta=None, arvio=0):
    """DVD:n nimike MKV:ksi ilman MakeMKV:tä. Palauttaa luodun tiedoston polun."""
    import json
    import threading
    from . import soitin
    exe = soitin.mpv_exe()
    if not exe:
        soitin.lataa()
        exe = soitin.mpv_exe()
    if not exe:
        raise RuntimeError("soitin_puuttuu")
    kansio = Path(kansio)
    kansio.mkdir(parents=True, exist_ok=True)
    laite = str(asema).rstrip("\\/")
    try:
        tiedot = dvd_tiedot(laite, nimike)
    except Exception as e:  # noqa: BLE001
        m.loki(f"Levyn raitatietoja ei saatu: {e}")
        tiedot = {"raidat": [], "luvut": [], "kesto": None}
    vob = kansio / "nimike.vob"
    if peruuta is not None and peruuta.is_set():
        raise m.Peruutettu()
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--no-config", "--quiet", "--msg-level=all=v", f"--dvd-device={laite}", f"--stream-dump={vob}",
           f"dvd://{int(nimike)}"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=m.PIILOTA)
    # Soittimen levynlukija ei lopeta nimikkeen loppuun, vaan jatkaa levyn ohjelman mukaan (seuraava nimike,
    # valikko tai sama nimike alusta). Nimikkeen loppu tunnistetaan lokista, ja ylimääräinen osa leikataan
    # pakattaessa nimikkeen kestoon.
    loppu = NimikkeenLoppu(nimike)

    def lue():
        with open(loki / "levy_kopiointi.log", "wb") as lf:
            for rivi in iter(p.stdout.readline, b""):
                lf.write(rivi)
                if loppu.rivi(rivi.decode("utf-8", "replace")):
                    lf.write(f"[PixeliRemaster] nimike loppui: {loppu.syy}\n".encode("utf-8"))
                    lf.flush()
                    try:
                        p.kill()
                    except OSError:
                        pass
    lukija = threading.Thread(target=lue, daemon=True)
    lukija.start()
    arvio = float(arvio or 0) or max(1.0, float(kesto or 0)) * 0.8e6        # DVD keskimäärin ~6 Mbit/s
    yla = max(3 * arvio, arvio + 2e9)
    while p.poll() is None:
        if peruuta is not None and peruuta.is_set():
            p.kill()
            p.wait()
            try:
                vob.unlink()
            except OSError:
                pass
            raise m.Peruutettu()
        koko = vob.stat().st_size if vob.exists() else 0
        if koko > yla and not loppu.syy:                  # varmistus: ei koskaan loputonta kopiointia
            loppu.syy = "koko"
            p.kill()
        if edistys:
            edistys(0.95 * min(0.99, koko / arvio))
        time.sleep(0.5)
    p.wait()
    lukija.join(timeout=5)
    if peruuta is not None and peruuta.is_set():
        try:
            vob.unlink()
        except OSError:
            pass
        raise m.Peruutettu()
    if loppu.syy:
        m.loki(f"DVD-nimike {nimike} loppui ({loppu.syy}), kopio {vob.stat().st_size if vob.exists() else 0} t")
    if (p.returncode != 0 and not loppu.syy) or not vob.exists() or vob.stat().st_size < 1_000_000:
        try:
            hanta = (loki / "levy_kopiointi.log").read_bytes()[-1500:].decode("utf-8", "replace")
        except OSError:
            hanta = ""
        raise RuntimeError(f"dvd_kopiointi_epaonnistui ({p.returncode}, "
                           f"{vob.stat().st_size if vob.exists() else -1} t): {hanta}")
    # MPEG-PS -> MKV (kaikki raidat sellaisenaan, kielet ja luvut levyltä)
    if edistys:
        edistys(0.96)
    mkv = kansio / "nimike.mkv"
    pr = subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-probesize", "400M", "-analyzeduration", "400M",
                         "-show_entries", "stream=index,id,codec_type", "-of", "json", str(vob)],
                        capture_output=True, text=True, creationflags=m.PIILOTA)
    try:
        virrat = json.loads(pr.stdout or "{}").get("streams", [])
    except ValueError:
        virrat = []
    meta = kielimetat(virrat, tiedot.get("raidat"))
    luvut = lukumetat(tiedot.get("luvut"), tiedot.get("kesto") or kesto)
    lukuarg = []
    if luvut:
        lukutiedosto = kansio / "luvut.txt"
        lukutiedosto.write_text(luvut, encoding="utf-8")
        lukuarg = ["-f", "ffmetadata", "-i", str(lukutiedosto)]
    cmd = [m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-probesize", "400M",
           "-analyzeduration", "400M", "-fflags", "+genpts", "-i", str(vob), *lukuarg,
           "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-c", "copy",
           *(["-t", f"{float(kesto) + 1.0:.3f}"] if kesto else []),
           *(["-map_chapters", "1"] if lukuarg else []), *meta, str(mkv)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=m.PIILOTA)
    if r.returncode != 0 or not mkv.exists():
        raise RuntimeError(f"dvd_pakkaus_epaonnistui: {r.stderr[-400:]}")
    # lukuvirhe (naarmu) voi katkaista kopion ennen loppua: verrataan kestoa levyn ilmoittamaan
    saatu = tiedoston_kesto(mkv)
    if saatu and kesto and saatu < 0.9 * float(kesto) - 30:
        raise RuntimeError(f"dvd_kopio_vajaa ({saatu:.0f}/{float(kesto):.0f} s)")
    for q in (vob, kansio / "luvut.txt"):
        try:
            q.unlink()
        except OSError:
            pass
    if edistys:
        edistys(1.0)
    return mkv


# ------------------------------------------------------------------ asemat ja nimikkeet (oma tai MakeMKV)
def kaikki_asemat():
    """Asemat käyttöliittymälle. DVD:t luetaan omalla soittimella; MakeMKV:tä käytetään vain Blu-raylle."""
    mk = bool(makemkvcon())
    asemat = optiset_asemat()
    if not asemat and mk and os.name != "nt":
        asemat = [{"asema": a.get("polku") or a.get("asema"), "levy": a["levy"], "levy_sisalla": a["levy_sisalla"],
                   "laji": None} for a in asemat_makemkv()]
    return {"makemkv": mk, "asemat": asemat}


def asemat_makemkv():
    return asemat()


def makemkv_indeksi(asema):
    """MakeMKV:n asemanumero asemakirjaimesta."""
    kirjain = str(asema or "").rstrip("\\/").upper()
    lista = asemat()
    for a in lista:
        if str(a.get("polku") or "").rstrip("\\/").upper() == kirjain:
            return a["indeksi"]
    levylliset = [a for a in lista if a.get("levy_sisalla")]
    if len(levylliset) == 1:
        return levylliset[0]["indeksi"]
    raise RuntimeError("makemkv_ei_loytanyt_asemaa")


def nimikkeet_asemalta(asema, laji=None):
    """Valitun aseman nimikkeet. DVD: oma lukija (MakeMKV varalla, jos asennettu). Blu-ray: MakeMKV."""
    mk = makemkvcon()
    laji = laji or (levyn_laji(str(asema).rstrip("\\/") + "\\") if os.name == "nt" else None)
    virhe = None
    if laji in ("dvd", None):
        try:
            d = dvd_tiedot(asema)
            if d["nimikkeet"]:
                return {"levy": "", "nimikkeet": [{k: v for k, v in n.items() if k != "vts"} for n in d["nimikkeet"]],
                        "bluray": False, "tapa": "oma", "virhe": None}
            virhe = "ei_nimikkeita"
        except Exception as e:  # noqa: BLE001
            m.loki(f"DVD:n luku soittimella epäonnistui: {e}")
            virhe = str(e)[:200]
        if not mk:
            return {"levy": "", "nimikkeet": [], "bluray": False, "tapa": "oma", "virhe": virhe}
    elif laji == "bluray" and not mk:
        return {"levy": "", "nimikkeet": [], "bluray": True, "tapa": None, "virhe": "bluray_makemkv"}
    elif laji == "muu":
        return {"levy": "", "nimikkeet": [], "bluray": False, "tapa": None, "virhe": "ei_videolevy"}
    if not mk:
        return {"levy": "", "nimikkeet": [], "bluray": False, "tapa": None, "virhe": virhe or "levya_ei_voitu_lukea"}
    indeksi = makemkv_indeksi(asema)
    r = nimikkeet(indeksi)
    r.update({"tapa": "makemkv", "indeksi": indeksi})
    if laji == "bluray":
        r["bluray"] = True
    return r
