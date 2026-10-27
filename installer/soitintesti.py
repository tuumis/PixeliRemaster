"""Soittimen testi Windowsissa (GitHub Actions): mpv ladataan ja puretaan kuten käyttäjällä, ja
ohjelman muodostamat komennot (toisto, vertailutoisto, reaaliaikainen RTX-parannus) ajetaan
lyhyesti ilman näyttöä. Testaa myös, että mpv-käännös tuntee d3d11vpp:n NVIDIA-skaalauksen."""
import subprocess
import sys
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=soitintesti::{rivit[-3000:]}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"))
from pixeli import moottori as m, soitin  # noqa: E402

data = Path(sys.argv[1])
exe = soitin.lataa(lambda f: None)
print("mpv:", exe)
print(subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.splitlines()[0])
# d3d11vpp-suodattimen asetukset (scaling-mode=nvidia vaatii riittävän uuden mpv:n)
apu = subprocess.run([exe, "--vf=d3d11vpp=help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
print(apu.stdout[-1500:])
assert "scaling-mode" in apu.stdout and "nvidia" in apu.stdout, "d3d11vpp ei tunne scaling-mode=nvidia"
alkup, remaster = data / "alkup.mkv", data / "remaster.mkv"
an = {"leveys": 720, "korkeus": 576, "lomitus": "lomitettu"}
geo = {"rajaus": [0, 72, 720, 432], "ow": 1280, "oh": 720}
testi = ["--vo=null", "--ao=null", "--frames=20", "--keep-open=no", "--force-window=no", "--msg-level=all=warn"]
for nimi, cmd in (("toista", soitin.komento_toista(remaster, "testi")),
                  ("vertaa", soitin.komento_vertaa(remaster, alkup, an, geo, "testi")),
                  ("reaaliaika", soitin.komento_reaaliaika(alkup, an, 1440, "testi"))):
    cmd = [c for c in cmd if not str(c).startswith(("--force-window", "--keep-open"))]
    cmd = cmd[:-2] + testi + cmd[-2:]
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    loki = (p.stdout + p.stderr)[-1200:]
    print(f"--- {nimi}: paluukoodi {p.returncode}\n{loki}")
    if p.returncode != 0 and nimi != "reaaliaika":
        print(f"::error title=soitintesti {nimi}::{loki[-800:].replace(chr(10), '%0A')}")
    if nimi == "reaaliaika":
        # pilvikoneessa ei ole NVIDIA-näytönohjainta: riittää, että asetukset hyväksytään
        assert "Error parsing option" not in loki and "not found" not in loki.lower(), loki
    else:
        assert p.returncode == 0, loki
# päivitys asetuksista: avoin soitin estää vaihdon eikä riko vanhaa versiota
import time  # noqa: E402
from pixeli import tyokalut  # noqa: E402
auki = subprocess.Popen([exe, "--idle=yes", "--vo=null", "--ao=null", "--force-window=no"])
time.sleep(2)
try:
    # Windows joko estää vaihdon (vanha jää ehjäksi) tai sallii sen (avoin soitin jatkaa vanhasta kansiosta)
    soitin.lataa()
    print("avoimen soittimen aikana vaihdettu")
except RuntimeError as e:
    assert str(e) == "soitin_kaytossa", e
    print("avoin soitin esti vaihdon")
finally:
    auki.kill()
    auki.wait()
time.sleep(1)
assert subprocess.run([exe, "--version"], capture_output=True).returncode == 0
print("versio:", soitin.versio())


class Jono:
    _kasiteltava = _kasiteltava_kuva = _ripattava = None


tyokalut.paivita(Jono())
for _ in range(600):
    time.sleep(1)
    if tyokalut.TILA["tila"] != "kaynnissa":
        break
print("työkalut:", tyokalut.TILA, tyokalut.tiedot())
assert tyokalut.TILA["tila"] == "valmis", tyokalut.TILA
assert tyokalut.ffmpeg_versio() and soitin.versio() and soitin.versio() != "?"
print(f"::notice title=soitintesti::mpv {soitin.versio()} ladattu, toisto ja vertailutoisto toimivat, "
      f"työkalujen päivitys toimii (ffmpeg {tyokalut.ffmpeg_versio()})")
