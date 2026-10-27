"""Soittimen testi Windowsissa (GitHub Actions): mpv ladataan ja puretaan kuten käyttäjällä, ja
ohjelman muodostamat komennot (toisto, vertailutoisto, reaaliaikainen RTX-parannus) ajetaan
lyhyesti ilman näyttöä. Testaa myös, että mpv-käännös tuntee d3d11vpp:n NVIDIA-skaalauksen."""
import subprocess
import sys
from pathlib import Path

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
    if nimi == "reaaliaika":
        # pilvikoneessa ei ole NVIDIA-näytönohjainta: riittää, että asetukset hyväksytään
        assert "Error parsing option" not in loki and "not found" not in loki.lower(), loki
    else:
        assert p.returncode == 0, loki
print(f"::notice title=soitintesti::mpv ladattu ja purettu, toisto ja vertailutoisto toimivat")
