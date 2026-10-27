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
    if a.get("malli") == "monikuva":
        continue                          # ladattava lisämalli: testataan torchtestissä
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
assert set(ol) == {"2", "3", "4", "5", "6"} and all(0 <= v["kohina"] <= 0.2 for v in ol.values()), (mm, ol)
assert set(lisamallit.LUETTELO) and all(t["sha256"] and t["koko"] for t in lisamallit.LUETTELO.values())
print("kuvat OK, mittaus", mm)

# piirretyn tunnistus: tasavärisiä värikkäitä pintoja monessa sävyssä -> piirretty; luonnollinen kuva ei
_r = np.random.default_rng(4)


def _piirros():
    a = np.full((240, 320, 3), 200, np.float32)
    for _ in range(7):
        y0, x0 = _r.integers(0, 200), _r.integers(0, 280)
        hh, ww = _r.integers(30, 120, 2)
        sv = _r.random() * 2 * np.pi
        a[y0:y0 + hh, x0:x0 + ww] = [128 + 90 * np.cos(sv), 128 + 90 * np.cos(sv - 2.1), 128 + 90 * np.cos(sv + 2.1)]
        a[y0:y0 + 2, x0:x0 + ww] = 20
    return np.clip(a + _r.normal(0, 0.6, a.shape), 0, 255).astype(np.uint8)


def _luonto():
    z = np.kron(_r.normal(0, 1, (60, 80, 3)).cumsum(0).cumsum(1), np.ones((4, 4, 1)))
    z = (z - z.min()) / (z.max() - z.min()) * 180 + 40
    return np.clip(z + _r.normal(0, 3, z.shape), 0, 255).astype(np.uint8)


assert mittaus.piirretty([_piirros() for _ in range(8)]) and not mittaus.piirretty([_luonto() for _ in range(8)])
_mitat = [{"kohina": 0.3, "lohkot": 1.1, "teravyys": 0.5}] * 6 + [{"kohina": 0.3, "lohkot": 1.6, "teravyys": 0.5}]
assert mittaus.vaihtelee(_mitat) and not mittaus.vaihtelee(_mitat[:6])
# mustavalkoinen valokuva ei ole piirros (vähän värejä, mutta paljon välisävyjä); viivapiirros on
_valo = np.repeat(_luonto().mean(2, keepdims=True), 3, 2).astype(np.uint32).reshape(-1, 3)
_viiva = np.where(_r.random((240, 320, 1)) < 0.08, 10, 250).repeat(3, 2).astype(np.uint32).reshape(-1, 3)
assert not kv.on_piirros(_valo, 200, True, False) and kv.on_piirros(_viiva, 2, True, False)
print("piirretyn ja vaihtelun tunnistus OK")

# tehonkäyttö: tauko erän jälkeen valitun tason mukaan
import time as _t  # noqa: E402
m.aseta_teho("hiljainen")
_a = _t.perf_counter(); m.tehotauko(0.1); _k = _t.perf_counter() - _a
assert 0.12 < _k < 0.4, _k
m.aseta_teho("taysi")
_a = _t.perf_counter(); m.tehotauko(0.1); assert _t.perf_counter() - _a < 0.02
m.aseta_teho("outo")
assert m.teho_osuus() == 1.0
print("tehonkäyttö OK")

# värien hillintä: keskisävyjen maltilliset värit ennallaan, ylikylläinen rajataan, valkoinen pysyy lähes valkoisena
from pixeli import vari as _vr  # noqa: E402
_L = np.array([[50.0, 50.0, 95.0]])
_ab = np.array([[[15.0, 10.0], [60.0, 50.0], [30.0, -30.0]]])
_h = _vr.hillitse(_L, _ab)
_C = np.hypot(_h[..., 0], _h[..., 1])[0]
assert np.allclose(_h[0, 0], _ab[0, 0]) and 40 < _C[1] < _vr.MAKSIMI and _C[2] < 15, _C
print("värien hillintä OK")

# päivitys: versiovertailu ja julkaisutietojen muutokset (vain nykyistä uudemmat)
from pixeli import paivitys as _pv  # noqa: E402
assert _pv.versio("v1.10.0") > _pv.versio("1.9.3") and _pv.versio("mallit-1") is None
_mt = _pv.muutokset("**Uutta 1.7.0:** a\n**New in 1.7.0:** b\n**Korjattu 1.6.1:** c\n**Uutta 1.6.0:** d", (1, 6, 0))
assert [x["versio"] for x in _mt["fi"]] == ["1.7.0", "1.6.1"] and _mt["en"][0]["teksti"] == "b", _mt
assert not _pv.asennettu() or os.name == "nt"
print("päivitys OK")

# levyltä (MakeMKV:n robottituloste) ja soittimen komennot
from pixeli import levy as _lv, soitin as _so  # noqa: E402
_r = _lv.jasenna('DRV:0,2,999,1,"HL-DT-ST DVDRAM","ELOKUVA","F:"\nDRV:1,256,999,0,"","",""\n'
                 'CINFO:2,0,"Elokuva, osa 2"\nTINFO:0,9,0,"1:45:32"\nTINFO:0,8,0,"24"\nTINFO:1,9,0,"0:02:10"\n'
                 'MSG:5010,0,0,"Failed to open disc","x"')
assert _r["asemat"] == [{"indeksi": 0, "asema": "HL-DT-ST DVDRAM", "levy": "ELOKUVA", "polku": "F:", "levy_sisalla": True}], _r
assert _r["levy"][2] == "Elokuva, osa 2" and _lv.kesto_s(_r["nimikkeet"][0][9]) == 6332 and _r["viestit"] == ["Failed to open disc"]
assert _lv.turvallinen_nimi('Elokuva: "Paluu"?') == "Elokuva Paluu"
_g = _so.vertailusuodin({"leveys": 720, "korkeus": 576, "lomitus": "lomitettu"}, {"rajaus": [0, 72, 720, 432], "ow": 2560, "oh": 1440})
assert "[vid2]bwdif" in _g and "crop=720:432:0:72" in _g and _g.endswith("[aid1]anull[ao]"), _g
assert _so.rtx_suodin({"korkeus": 576, "lomitus": "lomitettu"}, 1440) == "@rtx:d3d11vpp=deint=yes:interlaced-only=yes:scale=2.5:scaling-mode=nvidia"
_lua = _so.rtx_skripti(_so.rtx_suodin({"korkeus": 576, "lomitus": "lomitettu"}, 1440), "fi")
assert 'local SKAALA = 2.5' in _lua and "RTX-parannus PÄÄLLÄ" in _lua and "%%" not in _lua and _lua.count("(") == _lua.count(")")
print("levy ja soitin OK")

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
req = urllib.request.Request(f"http://127.0.0.1:{sov.portti}/api/asetukset", data=json.dumps({"teho": "tasapaino"}).encode(),
                             headers={"X-Pixeli-Avain": sov.avain, "Content-Type": "application/json"}, method="POST")
urllib.request.urlopen(req, timeout=10).read()
assert m.teho_osuus() == 0.65 and jono.asetukset["teho"] == "tasapaino"
print("Palvelin OK, versio", tila["versio"])
print("SAVUTESTI OK")
os._exit(0)
