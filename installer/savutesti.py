"""Asennuspaketin savutesti (GitHub Actions, ilman näytönohjainta ja ffmpegiä).

Tarkistaa, että upotettu Python löytää sovelluksen ja kirjastot, että AI-mallit latautuvat ja
laskevat järkevän tuloksen numpy-referenssillä, ja että palvelin vastaa.
"""
import json
import os
import sys
import time
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
from pixeli import tekstitys as _tx  # noqa: E402
_t = np.zeros((24, 90), bool); _t[6:18, 5:20] = True; _t[8:10, 40:85] = True  # noqa: E702
_r = np.zeros_like(_t)
for _dy in (-1, 0, 1):
    for _dx in (-1, 0, 1):
        _r |= np.roll(np.roll(_t, _dy, 0), _dx, 1)
_ind = np.zeros(_t.shape, np.uint8); _ind[_r] = 2; _ind[_t] = 1  # noqa: E702
_s = _tx.pura_spu(_tx.koodaa_spu(_ind, 120, 450, 2.5))
assert (_s["indeksit"] == _ind).all() and abs(_s["loppu"] - 2.5) < 0.02 and (_s["x"], _s["y"]) == (120, 450)
assert int((_tx.tekstikuva(_s, 1) == 0).sum()) == int(_t.sum())          # täyte erotettu reunuksesta
assert _tx.siisti("Hei ,  maailma !\n-Mitä?") == "Hei, maailma!\n- Mitä?", _tx.siisti("Hei ,  maailma !\n-Mitä?")
assert "pan=stereo" in m.puhe_suodin(6) and "pan" not in m.puhe_suodin(2)
_rl = _so.rtx_skripti(_so.rtx_suodin({"korkeus": 576, "lomitus": "lomitettu"}, 1440), "fi", 6332)
assert "local KESTO = 6332.0" in _rl and "edition-list" in _rl and "disc-titles" not in _rl and "%%" not in _rl
assert '"edition"' in _so._NIMIKE_LUA and "disc-titles" not in _so._NIMIKE_LUA
# DVD ilman MakeMKV:tä: nimikkeet soittimen editioneista, VIDEO_TS.IFO:n nimiketaulukko, kielet ja luvut
import struct as _st  # noqa: E402
import tempfile as _tf  # noqa: E402
_dvd = _lv.jasenna_dvd({"editions": [{"id": 0, "title": "title: 1 (00:00:15.000)"},
                                     {"id": 1, "title": "title: 2 (01:45:32.120)"},
                                     {"id": 2, "title": "title: 3 (00:22:10.000)"}, {"id": 3, "title": "Disc Menu"}],
                        "tracks": [{"type": "video", "src-id": 224}, {"type": "audio", "src-id": 128, "lang": "en"},
                                   {"type": "audio", "src-id": 129, "lang": "fi"}, {"type": "sub", "src-id": 32, "lang": "fi"}],
                        "chapters": [{"time": 0}, {"time": 300.5}, {"time": 900}], "duration": 6332.1},
                       {1: {"vts": 1, "luvut": 24}, 2: {"vts": 1, "luvut": 3}}, {1: 5_000_000_000})
assert [x["id"] for x in _dvd["nimikkeet"]] == [1, 2] and _dvd["nimikkeet"][0]["paaelokuva"], _dvd
assert _dvd["nimikkeet"][0]["luvut"] == 24 and 4.0e9 < _dvd["nimikkeet"][0]["koko"] < 4.3e9, _dvd
assert _lv.kielimetat([{"index": 0, "id": "0x1e0", "codec_type": "video"}, {"index": 1, "id": "0x20", "codec_type": "subtitle"},
                       {"index": 2, "id": "0x80", "codec_type": "audio"}, {"index": 3, "id": "0x81", "codec_type": "audio"}],
                      _dvd["raidat"]) == ["-metadata:s:s:0", "language=fin", "-metadata:s:a:0", "language=eng",
                                          "-metadata:s:a:1", "language=fin"]
_lm = _lv.lukumetat(_dvd["luvut"], 6332.1)
assert _lm.startswith(";FFMETADATA1") and _lm.count("[CHAPTER]") == 3 and "END=6332100" in _lm
assert _lv.lukumetat([0.0], 100) == ""
with _tf.TemporaryDirectory() as _d:
    (Path(_d) / "VIDEO_TS").mkdir()
    _ifo = bytearray(4096)
    _ifo[:12] = b"DVDVIDEO-VMG"
    _ifo[0xC4:0xC8] = _st.pack(">I", 1)
    _ifo[2048:2056] = _st.pack(">HHI", 2, 0, 31)
    _ifo[2056:2068] = bytes([0x3C, 1]) + _st.pack(">HH", 12, 0) + bytes([1, 1]) + _st.pack(">I", 0)
    _ifo[2068:2080] = bytes([0x3C, 1]) + _st.pack(">HH", 4, 0) + bytes([2, 1]) + _st.pack(">I", 0)
    (Path(_d) / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(bytes(_ifo))
    (Path(_d) / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"x" * 1000)
    (Path(_d) / "VIDEO_TS" / "VTS_01_2.VOB").write_bytes(b"x" * 500)
    assert _lv.ifo_nimikkeet(_d) == {0: {"kulmat": 1, "luvut": 12, "vts": 1}, 1: {"kulmat": 1, "luvut": 4, "vts": 2}}
    assert _lv.vts_koko(_d, 1) == 1500 and _lv.levyn_laji(_d) == "dvd"
    (Path(_d) / "BDMV").mkdir()
    assert _lv.levyn_laji(_d) == "bluray"
# Blu-ray ilman MakeMKV:tä: selkeä viesti; musiikki-/datalevy tunnistetaan
_mk = _lv.makemkvcon
_lv.makemkvcon = lambda: None
assert _lv.nimikkeet_asemalta("X:", "bluray")["virhe"] == "bluray_makemkv"
assert _lv.nimikkeet_asemalta("X:", "muu")["virhe"] == "ei_videolevy"
_lv.makemkvcon = _mk
assert _lv.kesto_otsikosta("title: 3 (01:45:32.120) (00800.mpls)") == 6332
_nl = _lv.NimikkeenLoppu(0)
_nr = ["[dvdnav] switched to VTS: 1 (old=0) domain=VTS",
       "[dvdnav] cell change: cell=1 pg=1 pgc_len=150.000 cur_time=0.000 domain=VTS", "[dvdnav] new title 1",
       "[dvdnav] cell change: cell=2 pg=2 pgc_len=150.000 cur_time=50.000 domain=VTS",
       "[dvdnav] cell change: cell=1 pg=1 pgc_len=150.000 cur_time=0.000 domain=VTS"]
assert [_nl.rivi(x) for x in _nr] == [False, False, False, False, True] and _nl.syy.startswith("solu"), _nl.syy
_nl = _lv.NimikkeenLoppu(0)
assert not _nl.rivi(_nr[1]) and _nl.rivi("[dvdnav] new title 2") and _nl.syy == "nimike 2"
# tallennuskansio ei käytettävissä (esim. ulkoinen asema irti) -> oletuskansio
from pixeli import tyojono as _tj  # noqa: E402
with _tf.TemporaryDirectory() as _d:
    (Path(_d) / "tiedosto").write_bytes(b"x")
    assert not _tj.Jono.kansio_saatavilla(str(Path(_d) / "tiedosto" / "ali"))
    assert _tj.Jono.kansio_saatavilla(str(Path(_d) / "uusi"))
    if os.name == "nt":
        import string as _sg  # noqa: E402
        _vapaa = next(k for k in reversed(_sg.ascii_uppercase) if not Path(f"{k}:\\").exists())
        assert not _tj.Jono.kansio_saatavilla(f"{_vapaa}:\\Elokuvat")
# aika-arvion korjaus toteutuneesta käsittelystä
_ka = m.kalibroi_aika(2, 6000, 300, 0.65, 40, 120, 3600)
assert abs(_ka["suhde"]["2"] - 0.769) < 0.01 and abs(_ka["loppu_s_min"] - 2.0) < 0.01, _ka
assert m.aika_korjaus()["suhde"]["2"] == _ka["suhde"]["2"]
(m.DATA / m.AIKA_ARVIO).unlink()
assert m.aika_korjaus()["loppu_s_min"] == m.LOPPU_OLETUS_S_MIN
print("levy ja soitin OK")

# lataus linkistä: yt-dlp:n tiedot, edistys ja virheet (oikea lataus CI:n lataustestissä)
from pixeli import lataus as _la  # noqa: E402
_li = _la.jasenna_tiedot({"title": "Vanha video", "uploader": "Kanava", "duration": 245.0, "id": "abc",
                          "extractor_key": "Youtube", "webpage_url": "https://www.youtube.com/watch?v=abc",
                          "requested_formats": [{"format_id": "135", "vcodec": "avc1", "height": 480, "width": 854,
                                                 "fps": 30, "filesize": 20_000_000},
                                                {"format_id": "140", "vcodec": "none", "filesize_approx": 4_000_000}],
                          "subtitles": {"fi": [], "en-US": [], "de": [], "live_chat": []}})
assert (_li["korkeus"], _li["koko"], _li["tekstit"], _li["fps"]) == (480, 24_000_000, ["en-US", "fi"], 30.0), _li
assert _la.suositus(480) == "hyva" and _la.suositus(1080) == "hd"
for _d, _a in (({"_type": "playlist"}, "soittolista"), ({"live_status": "is_live"}, "suora")):
    try:
        _la.jasenna_tiedot(_d)
        raise AssertionError(_a)
    except _la.LatausVirhe as _e:
        assert _e.avain == _a
_le = _la.Edistys(_li["osat"])
assert _le.rivi("[download] muu") is None
assert abs(_le.rivi("PIXELI|10000000|20000000|NA|135") - 10 / 24) < 1e-6
assert abs(_le.rivi("PIXELI|20000000|20000000|NA|135") - 20 / 24) < 1e-6
assert _le.rivi("PIXELI|4000000|NA|4100000|140") > 0.98
assert _la.virheavain("ERROR: [youtube] abc: Sign in to confirm your age") == "ika"
assert _la.virheavain("ERROR: [youtube] abc: Video unavailable") == "ei_saatavilla"
assert _la.virheavain("ERROR: something else") == "lataus"
try:
    _la.tiedot("ei osoite")
    raise AssertionError("ei_linkki")
except _la.LatausVirhe as _e:
    assert _e.avain == "ei_linkki"
print("lataus OK")

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
# linkistä jonoon (lataus korvattu): tallennus valittuun kansioon, "heti" käynnistää käsittelyn analyysin jälkeen
_vanha_lataa = _la.lataa
with tempfile.TemporaryDirectory() as _kd:
    def _vale_lataa(url, kansio, osat=None, tekstit=True, edistys=None, peruuta=None):
        Path(kansio).mkdir(parents=True, exist_ok=True)
        q = Path(kansio) / "Vanha video [abc].mkv"
        q.write_bytes(b"ei oikea video")
        edistys and edistys(1.0)
        return q
    _la.lataa = _vale_lataa
    _r = jono.lisaa_linkista("https://example.com/v", {"nimi": "Vanha video", "korkeus": 480}, heti=True,
                             kansio=_kd)
    for _ in range(100):
        _t = jono._tyo(_r["id"])
        if _t["tila"] != "ladataan":
            break
        time.sleep(0.1)
    assert Path(_t["polku"]) == Path(_kd) / "Vanha video [abc].mkv" and _t["tila"] in ("analysoidaan", "virhe"), _t
    assert not any(Path(_kd).glob(".pixeli_lataus_*"))
    _r2 = jono.lisaa_linkista("https://example.com/hd", {"nimi": "HD", "korkeus": 1080}, heti=True, kansio=_kd)
    assert jono._tyo(_r2["id"])["lataus"]["kasittele"] is False      # HD:tä ei käsitellä
    time.sleep(1.0)
    for _x in (_r, _r2):
        jono.poista(_x["id"])
_la.lataa = _vanha_lataa
print("Palvelin OK, versio", tila["versio"])
print("SAVUTESTI OK")
os._exit(0)
