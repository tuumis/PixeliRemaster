"""PixeliRemaster – päivitys ohjelman sisältä.

Ohjelma tarkistaa GitHubista (julkinen rajapinta, pelkkä luku) uusimman julkaistun version
käynnistyessä ja 12 tunnin välein. Kun käyttäjä valitsee "Päivitä nyt":
  1. asennusohjelma ladataan ja sen SHA-256 tarkistetaan julkaisun tiedoista,
  2. käsittelyssä olleet tiedostot merkitään jatkettaviksi ja käsittely pysäytetään hallitusti
     (valmiit osat säilyvät),
  3. erillinen apuprosessi odottaa, että ohjelma on sulkeutunut, ja ajaa asennuksen hiljaisena,
  4. asennusohjelma käynnistää uuden version, joka jatkaa käsittelyä siitä mihin jäätiin.
Käyttäjän asetukset, ladatut mallit, PyTorch ja työtiedostot säilyvät (kuten käsin päivitettäessä).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import moottori as m

REPO = "tuumis/PixeliRemaster"
RAJAPINTA = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
VALI_S = 12 * 3600

TILA = {"tila": "ei_kaytossa", "versio": None, "muutokset": None, "edistys": 0.0, "virhe": None,
        "tarkistettu": None}
_lukko = threading.Lock()
_julkaisu = {}


def versio(s):
    """'v1.6.0' / '1.6.0' -> (1, 6, 0); muu kuin versiotunniste -> None."""
    t = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(s or "").strip())
    return tuple(int(x) for x in t.groups()) if t else None


def asennettu():
    """Ajetaanko asennettua ohjelmaa (Inno Setup: python- ja app-kansio sekä poisto-ohjelma vierekkäin)."""
    if os.environ.get("PIXELI_PAIVITYS") == "pois":
        return False
    if os.environ.get("PIXELI_PAIVITYS") == "testi":
        return True
    if os.name != "nt":
        return False
    juuri = Path(sys.executable).resolve().parent.parent
    return (juuri / "app" / "kaynnista.py").exists() and any(juuri.glob("unins*.exe"))


def muutokset(teksti, nykyinen):
    """Julkaisutiedoista nykyistä uudempien versioiden rivit: {"fi": [...], "en": [...]}."""
    tulos = {"fi": [], "en": []}
    for rivi in (teksti or "").splitlines():
        r = re.match(r"\*\*(Uutta|Korjattu|New in|Fixed in) (v?\d+\.\d+\.\d+):\*\*\s*(.+)", rivi.strip())
        if not r or (versio(r.group(2)) or (0,)) <= nykyinen:
            continue
        kieli = "fi" if r.group(1) in ("Uutta", "Korjattu") else "en"
        tulos[kieli].append({"versio": r.group(2).lstrip("v"), "teksti": r.group(3).strip()})
    return tulos


def _hae_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"PixeliRemaster/{m.VERSIO}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def tarkista():
    """Hakee uusimman julkaisun. Palauttaa TILA-kopion."""
    nykyinen = versio(m.VERSIO)
    with _lukko:
        if TILA["tila"] in ("ladataan", "asennetaan"):
            return dict(TILA)
        TILA["tila"] = "tarkistetaan"
    try:
        julkaisut = _hae_json(RAJAPINTA)
        paras = None
        for j in julkaisut:
            v = versio(j.get("tag_name"))
            if not v or j.get("draft") or j.get("prerelease"):
                continue
            asennin = next((a for a in j.get("assets", []) if a.get("name", "").endswith("-Setup.exe")), None)
            if asennin and (paras is None or v > paras[0]):
                paras = (v, j, asennin)
        with _lukko:
            TILA["tarkistettu"] = time.time()
            TILA["virhe"] = None
            if paras and paras[0] > nykyinen:
                v, j, a = paras
                _julkaisu.update({"url": a["browser_download_url"], "nimi": a["name"], "koko": a.get("size"),
                                  "sha256": (a.get("digest") or "").removeprefix("sha256:") or None})
                TILA.update({"tila": "saatavilla", "versio": ".".join(map(str, v)),
                             "muutokset": muutokset(j.get("body"), nykyinen)})
            else:
                TILA.update({"tila": "ajan_tasalla", "versio": None, "muutokset": None})
    except Exception as e:  # noqa: BLE001
        m.loki(f"Päivitysten tarkistus epäonnistui: {e}")
        with _lukko:
            TILA.update({"tila": "virhe_tarkistus", "virhe": str(e)[:200], "tarkistettu": time.time()})
    return dict(TILA)


def kaynnista_tarkistus():
    """Taustasäie: ensimmäinen tarkistus pian käynnistyksen jälkeen, sitten 12 tunnin välein."""
    if not asennettu():
        return
    TILA["tila"] = "odottaa"
    _siivoa()

    def saie():
        time.sleep(8)
        while True:
            tarkista()
            time.sleep(VALI_S)
    threading.Thread(target=saie, name="paivitys", daemon=True).start()


def _kansio():
    return m.DATA / "paivitys"


def _siivoa():
    """Vanhat ladatut asennusohjelmat pois (uusi versio on jo asennettu)."""
    try:
        for p in _kansio().glob("*-Setup.exe*"):
            p.unlink()
    except OSError:
        pass


def lataa(edistys=None):
    """Lataa asennusohjelman ja tarkistaa koon ja SHA-256-summan. Palauttaa polun."""
    if not _julkaisu:
        raise RuntimeError("ei_paivitysta")
    k = _kansio()
    k.mkdir(parents=True, exist_ok=True)
    kohde = k / _julkaisu["nimi"]
    tmp = kohde.with_suffix(".exe.osa")
    h = hashlib.sha256()
    n = 0
    req = urllib.request.Request(_julkaisu["url"], headers={"User-Agent": f"PixeliRemaster/{m.VERSIO}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        koko = _julkaisu.get("koko") or int(r.headers.get("Content-Length") or 0)
        while True:
            pala = r.read(1 << 20)
            if not pala:
                break
            f.write(pala)
            h.update(pala)
            n += len(pala)
            if edistys and koko:
                edistys(n / koko)
    if _julkaisu.get("koko") and n != _julkaisu["koko"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"lataus_kesken ({n}/{_julkaisu['koko']})")
    if _julkaisu.get("sha256") and h.hexdigest() != _julkaisu["sha256"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("tarkistussumma")
    tmp.replace(kohde)
    return kohde


def _ps(s):
    return "'" + str(s).replace("'", "''") + "'"


def _apuprosessi(asennin):
    """Odottaa tämän prosessin sulkeutumista ja ajaa asennuksen hiljaisena (ei näkyviä ikkunoita)."""
    loki = _kansio() / "asennus.log"
    argit = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CLOSEAPPLICATIONS", "/PAIVITYS=1",
             f'/LOG="{loki}"']
    komento = (f"try {{ Wait-Process -Id {os.getpid()} -Timeout 120 -ErrorAction Stop }} catch {{}}; "
               f"Start-Sleep -Seconds 1; "
               f"Start-Process -FilePath {_ps(asennin)} -ArgumentList {','.join(_ps(a) for a in argit)}")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-Command", komento]
    liput = m.PIILOTA | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, creationflags=liput | 0x01000000, close_fds=True)   # + CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, creationflags=liput, close_fds=True)


JATKA = "jatka.json"


def merkitse_jatkettavat(jono):
    """Käsittelyssä ja jonossa olevat tiedostot jatkuvat automaattisesti uuden version käynnistyttyä."""
    with jono.lukko:
        idt = [t["id"] for t in jono.tyot if t["tila"] in ("jonossa", "kasitellaan")]
    m.kirjoita_json(m.DATA / JATKA, {"idt": idt, "aika": time.time()})
    return idt


def jatka(jono):
    """Käynnistyksessä: päivitystä edeltänyt käsittely jatkuu (vain tuoreen päivityksen jälkeen)."""
    p = m.DATA / JATKA
    d = m.lue_json(p)
    if not d:
        return
    try:
        p.unlink()
    except OSError:
        pass
    if time.time() - float(d.get("aika") or 0) > 3600:
        return
    for tid in d.get("idt") or []:
        try:
            jono.aloita(tid)
        except Exception:  # noqa: BLE001
            pass
    if d.get("idt"):
        m.loki(f"Päivityksen jälkeen jatketaan {len(d['idt'])} tiedoston käsittelyä")


def paivita(sov):
    """Käyttöliittymän "Päivitä nyt": lataus, käsittelyn pysäytys, asennus ja ohjelman sulkeminen."""
    with _lukko:
        if TILA["tila"] not in ("saatavilla", "virhe_lataus"):
            return dict(TILA)
        TILA.update({"tila": "ladataan", "edistys": 0.0, "virhe": None})

    def saie():
        try:
            def ed(f):
                TILA["edistys"] = round(f, 3)
            asennin = lataa(ed)
            TILA["tila"] = "asennetaan"
            m.loki(f"Päivitetään versioon {TILA['versio']}: {asennin.name}")
            j = sov.jono
            merkitse_jatkettavat(j)
            j.lopeta()
            loppu = time.time() + 15
            while (j._kasiteltava or j._kasiteltava_kuva) and time.time() < loppu:
                time.sleep(0.2)
            if os.environ.get("PIXELI_PAIVITYS") == "testi":
                TILA["testi_valmis"] = True
                return
            _apuprosessi(asennin)
            time.sleep(1.0)          # käyttöliittymä ehtii näyttää "asennetaan"
            try:
                (m.DATA / "instanssi.json").unlink()
            except OSError:
                pass
            m.loki("Suljetaan päivitystä varten")
            os._exit(0)
        except Exception as e:  # noqa: BLE001
            m.loki(f"Päivitys epäonnistui: {e}")
            with _lukko:
                TILA.update({"tila": "virhe_lataus", "virhe": str(e)[:200]})
    threading.Thread(target=saie, name="paivita", daemon=True).start()
    return dict(TILA)


def tila():
    return dict(TILA)
