"""Videon lataus linkistä (GitHub Actions, Windows): yt-dlp ja Deno ladataan kuten käyttäjällä, ja
paikalliselta HTTP-palvelimelta ladataan testivideo MKV:ksi. YouTube-haku kokeillaan lopuksi, mutta sen
epäonnistuminen on vain varoitus (YouTube estää usein pilvikoneiden osoitteet)."""
import functools
import http.server
import json
import subprocess
import sys
import threading
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=lataustesti::{rivit[-3000:]}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"))
from pixeli import lataus, moottori as m  # noqa: E402

data = Path(sys.argv[1])
palvelu = data / "verkko"
palvelu.mkdir(parents=True, exist_ok=True)
subprocess.run([m.ffbin("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=8",
                "-f", "lavfi", "-i", "sine=d=8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(palvelu / "vanha video.mp4")], check=True)
kasittelija = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(palvelu))
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), kasittelija)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{httpd.server_address[1]}/vanha%20video.mp4"

edistys = []
exe = lataus.varmista(edistys.append)
print("yt-dlp:", exe, lataus.versio(), "deno:", lataus.deno_exe())
assert Path(exe).exists() and lataus.deno_exe() and Path(lataus.deno_exe()).parent == lataus.kansio()
d = subprocess.run([lataus.deno_exe(), "--version"], capture_output=True, text=True)
assert d.returncode == 0 and "deno" in d.stdout, d
assert lataus.varmista() == exe                     # toinen kerta ei lataa uudelleen

t = lataus.tiedot(url)
print("tiedot:", t)
assert t["nimi"] and t["osat"] and t["korkeus"] in (0, 360), t      # suora tiedosto: koko ei aina tiedossa

kohde = data / "ladatut"
ed = []
polku = lataus.lataa(url, kohde, t["osat"], True, ed.append)
print("ladattu:", polku, polku.stat().st_size, ed[-2:])
assert polku.suffix == ".mkv" and polku.parent == kohde and ed[-1] == 1.0
pr = json.loads(subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                str(polku)], capture_output=True, text=True).stdout)
assert sorted(v["codec_type"] for v in pr["streams"]) == ["audio", "video"], pr["streams"]
assert "matroska" in pr["format"]["format_name"] and abs(float(pr["format"]["duration"]) - 8) < 1

ev = threading.Event()
ev.set()
try:
    lataus.lataa(url, data / "peruttu", t["osat"], True, None, ev)
    raise AssertionError("peruutus ei toiminut")
except m.Peruutettu:
    pass

try:
    lataus.tiedot(f"http://127.0.0.1:{httpd.server_address[1]}/ei_ole.mp4")
    raise AssertionError("puuttuva video ei antanut virhettä")
except lataus.LatausVirhe as e:
    print("puuttuva video:", e.avain)
    assert e.avain in ("ei_saatavilla", "lataus", "ei_tuettu"), e.avain

# YouTube (Blender Foundationin Big Buck Bunny, CC BY): vain tietojen haku, ei latausta
try:
    yt = lataus.tiedot("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
    print("YouTube:", yt["nimi"], yt["korkeus"], yt["osat"], yt["tekstit"])
    assert yt["korkeus"] >= 720 and yt["kesto"] > 500
except lataus.LatausVirhe as e:
    print(f"::warning title=lataustesti::YouTube-haku ei onnistunut pilvikoneelta ({e.avain}): {e.tieto[-300:]}")
httpd.shutdown()
print("lataustesti OK")
