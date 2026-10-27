"""PixeliRemaster – soittimen (mpv) ja videotyökalujen (ffmpeg, ffprobe) päivitys asetuksista.

Työkalut ladataan ensimmäisellä käytöllä eivätkä päivity itsestään: lukittu versio ei voi
yllättäen muuttaa käsittelyä. Käyttäjä voi päivittää ne uusimpiin, kun mitään ei käsitellä.
Uudet tiedostot ladataan ensin erilliseen kansioon ja vaihdetaan vasta lopuksi, joten
epäonnistunut lataus jättää vanhat ennalleen.
"""
import shutil
import subprocess
import threading
import urllib.request
import zipfile

from . import moottori as m
from . import soitin

TILA = {"tila": "ei", "vaihe": None, "edistys": 0.0, "virhe": None}
_lukko = threading.Lock()


def ffmpeg_versio():
    try:
        p = subprocess.run([m.ffbin("ffmpeg"), "-hide_banner", "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20, creationflags=m.PIILOTA)
        rivi = p.stdout.splitlines()[0] if p.stdout else ""
        return rivi.replace("ffmpeg version ", "").split(" Copyright")[0].strip() or "?"
    except Exception:  # noqa: BLE001
        return None


def tiedot():
    return {"mpv": soitin.versio(), "ffmpeg": ffmpeg_versio(), "paivitys": dict(TILA)}


def _paivita_ffmpeg(edistys):
    from .asennus import FFMPEG_URL
    tmp = m.DATA / "ffmpeg_paivitys"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    z = tmp / "ffmpeg.zip"
    req = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": f"PixeliRemaster/{m.VERSIO}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(z, "wb") as f:
        koko = int(r.headers.get("Content-Length") or 0)
        n = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            n += len(b)
            if koko:
                edistys(n / koko)
    uudet = []
    with zipfile.ZipFile(z) as zf:
        for nimi in zf.namelist():
            if nimi.endswith(("bin/ffmpeg.exe", "bin/ffprobe.exe")):
                q = tmp / nimi.rsplit("/", 1)[1]
                q.write_bytes(zf.read(nimi))
                uudet.append(q)
    if len(uudet) != 2:
        raise RuntimeError("ffmpeg_paketti")
    # uusi versio toimii ennen vaihtoa
    p = subprocess.run([str(tmp / "ffmpeg.exe"), "-hide_banner", "-version"], capture_output=True,
                       creationflags=m.PIILOTA)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg_ei_toimi")
    m.TOOLS.mkdir(parents=True, exist_ok=True)
    for q in uudet:
        try:
            q.replace(m.TOOLS / q.name)
        except OSError:
            raise RuntimeError("tyokalu_kaytossa")
    m._KOODAIN.clear()                 # koodaintesti uudelleen uudella versiolla
    shutil.rmtree(tmp, ignore_errors=True)


def paivita(jono):
    if jono._kasiteltava or jono._kasiteltava_kuva or jono._ripattava:
        raise RuntimeError("kasittely_kaynnissa")
    with _lukko:
        if TILA["tila"] == "kaynnissa":
            return dict(TILA)
        TILA.update({"tila": "kaynnissa", "vaihe": "ffmpeg", "edistys": 0.0, "virhe": None})

    def saie():
        try:
            def ed_ff(f):
                TILA["edistys"] = round(0.5 * f, 3)
            _paivita_ffmpeg(ed_ff)
            TILA.update({"vaihe": "mpv", "edistys": 0.5})

            def ed_mpv(f):
                TILA["edistys"] = round(0.5 + 0.5 * f, 3)
            soitin.lataa(ed_mpv)
            TILA.update({"tila": "valmis", "vaihe": None, "edistys": 1.0})
            m.loki(f"Työkalut päivitetty: ffmpeg {ffmpeg_versio()}, mpv {soitin.versio()}")
        except Exception as e:  # noqa: BLE001
            m.loki(f"Työkalujen päivitys epäonnistui: {e}")
            avain = str(e) if str(e) in ("soitin_kaytossa", "tyokalu_kaytossa") else "lataus"
            TILA.update({"tila": "virhe", "virhe": avain})
    threading.Thread(target=saie, name="tyokalut", daemon=True).start()
    return dict(TILA)
