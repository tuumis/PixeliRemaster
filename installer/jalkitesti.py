"""Jälkityöt, äänen puhdistus, tekstitykset netistä ja ilmaisinalueen kuvake (GitHub Actions, Windows).

Ajetaan oikealla ffmpegillä: ennen/jälkeen-pätkä, puhelinversio, puhdistettu ääniraita lopullisessa
tiedostossa, tekstityksen lisäys valmiiseen tiedostoon ja OpenSubtitles-lataus (rajapinta korvattu
paikallisella palvelimella; oikea haku vain, jos avain on asetettu). Lopuksi WinFormsin NotifyIcon
pythonnetin kautta samalla koodilla kuin ohjelmassa.
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=jalkitesti::{rivit[-3000:]}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"))
from pixeli import jalki, moottori as m, tekstihaku  # noqa: E402

k = Path(sys.argv[1])
k.mkdir(parents=True, exist_ok=True)
ff = m.ffbin("ffmpeg")


def aja(*a):
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


def tiedot(p):
    return json.loads(subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(p)], capture_output=True, text=True, encoding="utf-8").stdout)


# lähde (PAL-DVD:n kaltainen) ja "valmis" remaster
src, osa, ulos = k / "lahde.mkv", k / "osa_0000.mkv", k / "valmis.mkv"
aja("-f", "lavfi", "-i", "testsrc2=s=720x576:r=25:d=40", "-f", "lavfi", "-i", "anoisesrc=d=40:c=pink:a=0.05",
    "-filter_complex", "[1:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0[a]", "-map", "0:v", "-map", "[a]",
    "-c:v", "mpeg2video", "-b:v", "4M", "-c:a", "ac3", "-metadata:s:a:0", "language=fin", str(src))
aja("-i", str(src), "-map", "0:v", "-vf", "scale=1440:1080:flags=lanczos", "-c:v", "libx264", "-crf", "22", str(osa))
an = {"R": "25/1", "fps": "25/1", "lomitus": "progressiivinen", "leveys": 720, "korkeus": 576, "kesto": 40.0,
      "vstart": 0.0, "matriisi": "bt601", "tekstityksia": 0,
      "aaniraidat": [{"i": 0, "kanavat": 6, "kieli": "fin"}]}
geo = {"rajaus": [0, 0, 720, 576], "ow": 1440, "oh": 1080}

# 1) lopullinen tiedosto: alkuperäinen ääni + puhdistettu (5.1) + selkeä puhe (oletus)
r = m.yhdista(src, an, [osa], k / "lista.txt", ulos, {"aanipuhdistus": "voimakas"})
print("yhdista:", r)
a = [v for v in tiedot(ulos)["streams"] if v["codec_type"] == "audio"]
nimet = [(v.get("tags") or {}).get("title") for v in a]
print("ääniraidat:", nimet, [v["channels"] for v in a], [v["disposition"]["default"] for v in a])
assert len(a) == 3 and nimet[1] == "Puhdistettu ääni" and nimet[2] == "Selkeä puhe (stereo)", nimet
assert a[1]["channels"] == 6 and a[2]["channels"] == 2 and [v["disposition"]["default"] for v in a] == [0, 0, 1]
assert r["puhdistus"] == "voimakas" and r["puhe"]
r2 = m.yhdista(src, an, [osa], k / "lista.txt", k / "vain_puhdistus.mkv", {"aanipuhdistus": "kevyt", "puhe": False})
a2 = [v for v in tiedot(k / "vain_puhdistus.mkv")["streams"] if v["codec_type"] == "audio"]
assert len(a2) == 2 and a2[1]["disposition"]["default"] == 1 and not r2["puhe"], a2

# 2) tekstitys valmiiseen tiedostoon (oletusraidaksi)
srt = k / "testi.srt"
srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHyvää huomenta!\n\n2\n00:00:05,000 --> 00:00:07,000\nNäkemiin.\n",
               encoding="utf-8")
tekstihaku.lisaa_valmiiseen(ulos, srt, "fin", "Suomi (OpenSubtitles)")
s = [v for v in tiedot(ulos)["streams"] if v["codec_type"] == "subtitle"]
assert len(s) == 1 and s[0]["tags"].get("language") == "fin" and s[0]["disposition"]["default"] == 1, s
assert len([v for v in tiedot(ulos)["streams"] if v["codec_type"] == "audio"]) == 3

# 2b) oma .ass-tekstitys muunnetaan SRT:ksi ja lisätään valmiiseen tiedostoon
ass = k / "oma.fi.ass"
(k / "lahde_ass.srt").write_text("1\n00:00:02,000 --> 00:00:04,000\nTerve!\n", encoding="utf-8")
aja("-i", str(k / "lahde_ass.srt"), str(ass))
r = tekstihaku.tuo_tiedosto(ass, k / "oma.srt", videon_kesto=40, videon_fps=25)
assert "Terve!" in (k / "oma.srt").read_text(encoding="utf-8") and r["kerroin"] == 1.0
tekstihaku.lisaa_valmiiseen(ulos, k / "oma.srt", "fin", "Suomi")
s = [v for v in tiedot(ulos)["streams"] if v["codec_type"] == "subtitle"]
assert len(s) == 2 and [x["disposition"]["default"] for x in s] == [0, 1], s

# 3) ennen/jälkeen-pätkä ja puhelinversio
e = []
v = jalki.vertailupatka(src, an, geo, ulos, k / "vertailu.mp4", jalki.vertailun_kohta(src, an, geo, k), edistys=e.append)
t = tiedot(v)
vv = next(x for x in t["streams"] if x["codec_type"] == "video")
assert (vv["width"], vv["height"]) == (1440, 1080) and abs(float(t["format"]["duration"]) - 20) < 0.5, t["format"]
assert any(x["codec_type"] == "audio" for x in t["streams"]) and e[-1] == 1.0
aja("-ss", "5", "-i", str(v), "-frames:v", "1", str(k / "vertailu.png"))
for korkeus, koko in ((720, (960, 720)), (1080, (1440, 1080))):
    p = jalki.puhelinversio(ulos, k / f"puhelin_{korkeus}.mp4", korkeus)
    t = tiedot(p)
    vv = next(x for x in t["streams"] if x["codec_type"] == "video")
    aa = [x for x in t["streams"] if x["codec_type"] == "audio"]
    ss = [x for x in t["streams"] if x["codec_type"] == "subtitle"]
    print("puhelin", korkeus, (vv["width"], vv["height"]), vv["codec_name"], [x["channels"] for x in aa],
          [x["codec_name"] for x in ss], round(p.stat().st_size / 1e6, 1), "Mt")
    assert (vv["width"], vv["height"]) == koko and vv["codec_name"] == "h264"
    assert len(aa) == 1 and aa[0]["channels"] == 2 and (aa[0].get("tags") or {}).get("handler_name", "").startswith("Selkeä")
    assert [x["codec_name"] for x in ss] == ["mov_text", "mov_text"]
print("jälkityöt OK:", jalki.koodain_puhelin()[1])

# 4) OpenSubtitles-lataus: rajapinta korvattu, tiedosto paikalliselta palvelimelta (cp1252, 23,976 fps)
palvelu = k / "palvelu"
palvelu.mkdir(exist_ok=True)
(palvelu / "t.srt").write_bytes("1\r\n00:01:40,000 --> 00:01:42,000\r\nHyvää yötä\r\n".encode("cp1252"))
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler,
                                                                            directory=str(palvelu)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
alkuperainen = tekstihaku._pyynto
tekstihaku._pyynto = lambda avain, polku, parametrit=None, data=None, aikaraja=30: {
    "link": f"http://127.0.0.1:{httpd.server_address[1]}/t.srt", "remaining": 4, "reset_time": "x"}
r = tekstihaku.lataa({"opensubtitles_avain": "testi"}, 123, k / "lad" / "123.srt", videon_fps=25, tekstin_fps=23.976)
teksti = Path(r["polku"]).read_text(encoding="utf-8")
print("lataus:", r, teksti.splitlines()[:3])
assert "Hyvää yötä" in teksti and "00:01:35,904 --> 00:01:37,822" in teksti and r["jaljella"] == 4
tekstihaku._pyynto = alkuperainen
httpd.shutdown()
avain = tekstihaku.sovellusavain({})
if avain:
    try:
        tul = tekstihaku.hae({}, "Big Buck Bunny", "en")
        print("OpenSubtitles-haku:", len(tul), tul[:2])
    except tekstihaku.TekstiVirhe as e:
        print(f"::warning title=jalkitesti::OpenSubtitles-haku: {e.avain} {e.tieto[:200]}")
else:
    print("OpenSubtitles-avainta ei ole asetettu (salaisuus OPENSUBTITLES_API_KEY): oikea haku ohitettu")

# 5) ilmaisinalueen kuvake samalla koodilla kuin ohjelmassa
if os.name == "nt":
    import clr  # noqa: F401  (pythonnet, pywebviewin mukana)
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from pixeli import ikkuna

    class Vale:
        kieli = staticmethod(lambda: "fi")
        nayta = staticmethod(lambda: None)
        lopeta = staticmethod(lambda: None)
    il = ikkuna.Ilmaisin(Vale())
    il._luo()
    assert il.kuvake is not None and il.kuvake.Visible and il.kuvake.ContextMenuStrip.Items.Count == 2
    from System.Windows.Forms import ToolTipIcon  # type: ignore
    il.kuvake.ShowBalloonTip(1000, "PixeliRemaster", "testi", ToolTipIcon.Info)
    il.kuvake.Visible = False
    il.kuvake.Dispose()
    print("ilmaisinalueen kuvake OK")
print("jalkitesti OK")
