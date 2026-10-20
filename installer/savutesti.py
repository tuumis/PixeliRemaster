"""Asennuspaketin savutesti (GitHub Actions, ilman näytönohjainta ja ffmpegiä).

Tarkistaa, että upotettu Python löytää sovelluksen ja kirjastot, että AI-mallit latautuvat ja
laskevat järkevän tuloksen numpy-referenssillä, ja että palvelin vastaa.
"""
import json
import os
import sys
import traceback
import tempfile
import urllib.request
from pathlib import Path

def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=savutesti::{rivit}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe
juuri = Path(__file__).resolve().parent.parent
app = juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"
sys.path.insert(0, str(app))
os.environ["PIXELI_DATA"] = tempfile.mkdtemp(prefix="pixeli_")
os.environ["PIXELI_LAITE"] = "numpy"

import numpy as np  # noqa: E402

from pixeli import asennus, kuva, moottori as m, palvelin, tyojono  # noqa: E402,F401

print("Python", sys.version.split()[0], "numpy", np.__version__, "app", app)
try:
    import webview  # noqa: F401
    print("pywebview OK")
except Exception as e:  # noqa: BLE001
    if os.name == "nt":
        raise
    print("pywebview ei saatavilla:", e)

o = kuva.NumpyOps()
x = m.testikuva(32, 40)
for n, cfg in m.ASETUKSET.items():
    a = dict(cfg)
    if a.get("malli"):
        a["malli"] = kuva.Malli(o, kuva.lataa_pth(m.MALLIT / m.MALLITIEDOSTOT[a["malli"]]))
    k = kuva.Ketju(o, 32, 40, 96, 120, a)
    y = k.rgb_lopullinen(x, 0)
    assert y.shape == (1, 3, 96, 120) and np.isfinite(y).all(), n
    ero = float(np.abs(y.mean() - x.mean()))
    assert ero < 0.08, (n, ero)          # kirkkaus säilyy
    print(f"asetus {n}: OK (keskikirkkauden ero {ero:.4f})")

# kuvat: PNG-kirjoitus (luetaan takaisin ilman lisäkirjastoja), EXIF-suunta ja koon valinta
import struct as _st  # noqa: E402
import zlib as _zl  # noqa: E402
from pixeli import kuvat as kv  # noqa: E402
rng = np.random.default_rng(1)
rgb_t = rng.integers(0, 256, (13, 17, 3), dtype=np.uint8)
alfa_t = rng.integers(0, 256, (13, 17), dtype=np.uint8)
png_p = Path(os.environ["PIXELI_DATA"]) / "testi.png"
kv.kirjoita_png(png_p, rgb_t, alfa_t, icc=b"sRGB\x00\x00" + _zl.compress(b"x"))
data = png_p.read_bytes()
idat, i = b"", 8
while i < len(data):
    n_, tyyppi = _st.unpack(">I", data[i:i + 4])[0], data[i + 4:i + 8]
    if tyyppi == b"IDAT":
        idat += data[i + 8:i + 8 + n_]
    i += 12 + n_
raaka = np.frombuffer(_zl.decompress(idat), np.uint8).reshape(13, 1 + 17 * 4)
assert (raaka[:, 0] == 2).all()
rivit = np.cumsum(raaka[:, 1:].astype(np.int64), axis=0) % 256
assert (rivit.reshape(13, 17, 4)[..., :3] == rgb_t).all() and (rivit.reshape(13, 17, 4)[..., 3] == alfa_t).all()
assert kv.png_icc(data).startswith(b"sRGB")
tiff = b"II*\x00" + _st.pack("<I", 8) + _st.pack("<H", 1) + _st.pack("<HHI", 0x0112, 3, 1) + _st.pack("<HH", 6, 0) + b"\0\0\0\0"
app1 = b"Exif\x00\x00" + tiff
jpg = b"\xff\xd8\xff\xe1" + _st.pack(">H", len(app1) + 2) + app1 + b"\xff\xda\x00\x02"
seg = kv.jpeg_segmentit(jpg)
assert [mk for mk, _, _ in seg] == [0xE1, 0xDA], seg
assert kv.exif_suunta(jpg[seg[0][1] + 4:seg[0][2]])[0] == 6
g = kv.geometria_kuva({"leveys": 4032, "korkeus": 3024}, {"skaala": "4"}, (2560, 1440))
assert g["skaala"] == "2" and (g["ow"], g["oh"]) == (8064, 6048) and not g["vaihtoehdot"]["4"]["ok"], g
g = kv.geometria_kuva({"leveys": 640, "korkeus": 480}, {"skaala": "naytto"}, (2560, 1440))
assert (g["ow"], g["oh"], g["kierrokset"]) == (1920, 1440, 2), g
from pixeli import mittaus, lisamallit  # noqa: E402
mm = mittaus.mittaa([(m.testikuva(120, 160)[0].transpose(1, 2, 0) * 255).astype(np.uint8)])
ol = mittaus.oletukset(mm)
assert set(ol) == {"2", "3", "4", "5"} and all(0 <= v["kohina"] <= 0.2 for v in ol.values()), (mm, ol)
assert set(lisamallit.LUETTELO) and all(t["sha256"] and t["koko"] for t in lisamallit.LUETTELO.values())
print("kuvat OK, mittaus", mm)

ymp = asennus.Ymparisto()
jono = tyojono.Jono(ymp)
sov = palvelin.Sovellus(jono, ymp)
palvelin.kaynnista(sov)
req = urllib.request.Request(f"http://127.0.0.1:{sov.portti}/api/tila", headers={"X-Pixeli-Avain": sov.avain})
tila = json.loads(urllib.request.urlopen(req, timeout=10).read())
assert tila["versio"] == m.VERSIO
html = urllib.request.urlopen(f"http://127.0.0.1:{sov.portti}/", timeout=10).read().decode("utf-8")
assert sov.avain in html and "app.js" in html
for f in ("app.js", "app.css", "kaannokset.js", "pixeli.svg", "fontit/Barlow-Regular.ttf"):
    assert urllib.request.urlopen(f"http://127.0.0.1:{sov.portti}/web/{f}", timeout=10).status == 200, f
print("Palvelin OK, versio", tila["versio"])
print("SAVUTESTI OK")
os._exit(0)
