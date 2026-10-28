"""DVD-levyn luku ja kopiointi ilman MakeMKV:tä (GitHub Actions, Windows).

Testilevy (VIDEO_TS-kansio) tehdään Linuxissa dvdauthorilla: kolme nimikettä (150 s, 125 s ja 20 s),
kaksi äänikieltä (fi, en) ja pääelokuvassa kolme lukua. Soitin (mpv) lukee kansiota kuten levyä
(--dvd-device=kansio), joten testi kulkee samaa polkua kuin oikea levyasema.
"""
import json
import subprocess
import sys
import threading
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=levytesti::{rivit[-3000:]}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"))
from pixeli import levy, moottori as m, soitin  # noqa: E402

levyjuuri = Path(sys.argv[1])
kohde = Path(sys.argv[2])
assert (levyjuuri / "VIDEO_TS" / "VIDEO_TS.IFO").exists(), list(levyjuuri.rglob("*"))
print("mpv:", soitin.mpv_exe() or soitin.lataa(lambda f: None))

assert levy.levyn_laji(str(levyjuuri)) == "dvd"
ifo = levy.ifo_nimikkeet(str(levyjuuri))
print("IFO:", ifo)
assert len(ifo) == 3 and ifo[0]["luvut"] == 3 and ifo[0]["vts"] == 1, ifo

# nimikkeet: 20 s nimike suodatetaan pois, pisin on pääelokuva
d = levy.nimikkeet_asemalta(str(levyjuuri), "dvd")
print("nimikkeet:", json.dumps(d, ensure_ascii=False))
assert d["tapa"] == "oma" and not d["virhe"], d
n = d["nimikkeet"]
assert [x["id"] for x in n] == [0, 1], n
assert abs(n[0]["kesto"] - 150) <= 2 and abs(n[1]["kesto"] - 125) <= 2 and n[0].get("paaelokuva"), n
assert n[0]["luvut"] == 3 and n[0]["koko"] > 1_000_000, n

# valitun nimikkeen raidat ja luvut
t = levy.dvd_tiedot(str(levyjuuri), 0)
print("raidat:", t["raidat"], "luvut:", t["luvut"])
assert sorted(r["kieli"] for r in t["raidat"] if r["tyyppi"] == "audio") == ["en", "fi"], t["raidat"]
assert len(t["luvut"]) == 3, t["luvut"]

# kopiointi: edistyminen etenee, MKV:ssä video, kaksi kielimerkittyä ääniraitaa ja kolme lukua
edistykset = []
mkv = levy.rippaa_dvd(str(levyjuuri), 0, n[0]["kesto"], kohde, edistykset.append, threading.Event(),
                      arvio=n[0]["koko"])
print("kopio:", mkv, mkv.stat().st_size, "edistys:", edistykset[:3], "...", edistykset[-3:])
assert edistykset and edistykset[-1] == 1.0 and max(edistykset[:-1] or [0]) < 1.0
assert not (kohde / "nimike.vob").exists()
pr = json.loads(subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-show_streams", "-show_chapters", "-show_format",
                                "-of", "json", str(mkv)], capture_output=True, text=True).stdout)
virrat = pr["streams"]
print([(v["codec_type"], v.get("codec_name"), (v.get("tags") or {}).get("language")) for v in virrat])
assert [v["codec_type"] for v in virrat].count("video") == 1
kielet = sorted((v.get("tags") or {}).get("language") for v in virrat if v["codec_type"] == "audio")
assert kielet == ["eng", "fin"], kielet
assert len(pr["chapters"]) == 3, pr["chapters"]
assert abs(float(pr["format"]["duration"]) - 150) < 3, pr["format"]["duration"]

# peruutus: keskeytetty kopiointi ei jätä tiedostoja
ev = threading.Event()
ev.set()
kohde2 = kohde.parent / (kohde.name + "_peruutus")
try:
    levy.rippaa_dvd(str(levyjuuri), 1, n[1]["kesto"], kohde2, None, ev)
    raise AssertionError("peruutus ei toiminut")
except m.Peruutettu:
    pass
assert not (kohde2 / "nimike.vob").exists() and not (kohde2 / "nimike.mkv").exists()

# katselu suoraan nimikkeellä (dvd://N, ei kestohakua)
cmd = soitin.komento_oma("dvd://1", {"korkeus": 576, "lomitus": "lomitettu"}, "testi",
                         lisat=[f"--dvd-device={levyjuuri}"])
cmd = [c for c in cmd if not str(c).startswith(("--force-window", "--keep-open", "--fs", "--fullscreen"))]
i = cmd.index("--") if "--" in cmd else len(cmd)
cmd = cmd[:i] + ["--vo=null", "--ao=null", "--frames=30", "--msg-level=all=warn"] + cmd[i:]
r = subprocess.run(cmd, capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=120)
print("dvd://1 katselu:", r.returncode, r.stdout[-500:], r.stderr[-500:])
assert r.returncode == 0
# kestohaku (Blu-ray / MakeMKV-nimike): kestoltaan lähin nimike levyn omalla numerolla
ed = levy.soittimen_tiedot("dvd://", f"--dvd-device={levyjuuri}")["editions"]
assert levy.nimike_kestolla(ed, 125) == 1 and levy.nimike_kestolla(ed, 149) == 0, ed
print("levytesti OK")
