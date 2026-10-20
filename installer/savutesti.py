"""Asennuspaketin savutesti (GitHub Actions, ilman näytönohjainta ja ffmpegiä).

Tarkistaa, että upotettu Python löytää sovelluksen ja kirjastot, että AI-mallit latautuvat ja
laskevat järkevän tuloksen numpy-referenssillä, ja että palvelin vastaa.
"""
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

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
