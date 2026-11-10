"""PixeliRemaster – jälkityöt valmiista videosta.

Ennen/jälkeen-pätkä: lyhyt MP4, jossa sama kohta on vasemmalla alkuperäisenä ja oikealla
remasteroituna (jaettu kuva, nimet kulmissa). Alkuperäinen puretaan ja rajataan täsmälleen samoin kuin
käsittelyssä ja skaalataan tavallisesti samaan kokoon, joten puolikkaat osuvat kohdalleen.

Puhelinversio: H.264 + AAC stereo MP4 (720p tai 1080p), joka toistuu kaikilla puhelimilla, tableteilla ja
televisioilla; tekstiraidat mukaan (mov_text). Selkeä puhe -raita valitaan ääneksi, jos se on olemassa.
"""
import json
import re
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

from . import moottori as m

VERTAILU_KESTO = 20
PUHELIN_KORKEUDET = (720, 1080)
TEKSTIKOODEKIT = ("subrip", "ass", "ssa", "mov_text", "webvtt", "text")

_KOODAIN = {}


def koodain_puhelin():
    """H.264 puhelimille: NVENC, varalla prosessori. Kohtuullinen koko (n. 1–2 Gt / 2 h 720p)."""
    if "e" in _KOODAIN:
        return _KOODAIN["e"]
    ehdokkaat = [
        ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "24", "-b:v", "0",
         "-maxrate", "8M", "-bufsize", "16M", "-profile:v", "high", "-pix_fmt", "yuv420p"],
        ["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-maxrate", "8M", "-bufsize", "16M",
         "-profile:v", "high", "-pix_fmt", "yuv420p"],
    ]
    for e in ehdokkaat:
        p = m.aja([m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                   "testsrc2=s=640x360:d=0.5", *e, "-f", "null", "-"])
        if p.returncode == 0:
            _KOODAIN["e"] = e
            return e
    _KOODAIN["e"] = ehdokkaat[-1]
    return ehdokkaat[-1]


def _virrat(polku):
    p = subprocess.run([m.ffbin("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                        str(polku)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=m.PIILOTA)
    try:
        return json.loads(p.stdout or "{}")
    except ValueError:
        return {}


def _aja_edistyen(cmd, kesto, edistys=None, peruuta=None):
    """ffmpeg -progress -tulosteella: edistys 0..1, peruutus kesken."""
    p = subprocess.Popen(cmd + ["-progress", "pipe:1", "-nostats"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, creationflags=m.PIILOTA)
    virhe = []
    threading.Thread(target=lambda: virhe.append(p.stderr.read()), daemon=True).start()
    for rivi in iter(p.stdout.readline, b""):
        if peruuta is not None and peruuta.is_set():
            p.kill()
            p.wait()
            raise m.Peruutettu()
        r = re.match(rb"out_time_(?:us|ms)=(\d+)", rivi)
        if r and edistys and kesto:
            edistys(min(0.99, int(r.group(1)) / 1e6 / float(kesto)))
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("jalkityo_epaonnistui: " + (virhe[0] if virhe else b"").decode("utf-8", "replace")[-600:])


def _fonttipolku():
    f = m.SOVELLUS / "web" / "fontit" / "Barlow-SemiBold.ttf"
    # ffmpegin suodinkielessä kaksoispiste ja kenoviiva on suojattava (Windowsin C:/…)
    return f.as_posix().replace("\\", "/").replace(":", "\\:")


def _teksti(s):
    return str(s).replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")


def vertailun_kohta(src, an, geo, wd):
    """Kohta, josta pätkä tehdään: esikatselun ensimmäinen (yksityiskohtainen) kohta, muuten 30 %."""
    kesto = float(an.get("kesto") or 0)
    try:
        kd = wd / f"koe_{m.koe_tunniste(geo)}"
        ajat = m.esikatselukohdat(src, an, geo, kd)
        t = float(ajat[0]) - VERTAILU_KESTO / 2
    except Exception:  # noqa: BLE001
        t = kesto * 0.3
    return max(0.0, min(t, max(0.0, kesto - VERTAILU_KESTO - 1)))


def vertailupatka(src, an, geo, valmis, kohde, alku, kesto=VERTAILU_KESTO, nimet=("Alkuperäinen", "PixeliRemaster"),
                  edistys=None, peruuta=None):
    src, valmis, kohde = Path(src), Path(valmis), Path(kohde)
    ow, oh = int(geo["ow"]), int(geo["oh"])
    R = Fraction(an["R"])
    rate = f"{R.numerator}/{R.denominator}"
    puoli = (ow // 2) // 2 * 2
    koko = max(18, oh // 22)
    fontti = _fonttipolku()
    nimi = (f"drawtext=fontfile='{fontti}':text='{{t}}':fontsize={koko}:fontcolor=white:box=1:"
            f"boxcolor=black@0.55:boxborderw={koko // 3}:y=h-{2 * koko + koko // 3}")
    kaavio = (f"[0:v]{m.kuvasuodin(an, geo)},scale={ow}:{oh}:flags=bicubic,setsar=1,fps={rate},"
              f"setpts=PTS-STARTPTS,crop={puoli}:{oh}:0:0[a];"
              f"[1:v]scale={ow}:{oh}:flags=bicubic,setsar=1,fps={rate},setpts=PTS-STARTPTS,"
              f"crop={ow - puoli}:{oh}:{puoli}:0[b];"
              f"[a][b]hstack=inputs=2,drawbox=x={puoli - 1}:y=0:w=3:h=ih:color=white@0.85:t=fill,"
              + nimi.format(t=_teksti(nimet[0])) + f":x={koko}," + nimi.format(t=_teksti(nimet[1]))
              + f":x=w-tw-{koko},format=yuv420p[v]")
    kohde.parent.mkdir(parents=True, exist_ok=True)
    tmp = kohde.with_name(kohde.stem + ".kesken.mp4")
    vstart = float(an.get("vstart") or 0)
    pohja = [m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{alku + vstart:.3f}", "-t", f"{kesto + 1:.3f}", "-i", str(src),
             "-ss", f"{alku:.3f}", "-t", f"{kesto + 1:.3f}", "-i", str(valmis),
             "-filter_complex", kaavio, "-map", "[v]", "-map", "1:a:0?"]
    loppu = ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-t", f"{kesto:.3f}", "-movflags", "+faststart", str(tmp)]
    try:
        _aja_edistyen(pohja + m.koodain("h264") + loppu, kesto, edistys, peruuta)
    except RuntimeError:
        # varalla prosessorikoodain (esim. NVENC varattu)
        _aja_edistyen(pohja + ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"] + loppu,
                      kesto, edistys, peruuta)
    tmp.replace(kohde)
    if edistys:
        edistys(1.0)
    return kohde


def puhelinversio(valmis, kohde, korkeus=720, edistys=None, peruuta=None):
    valmis, kohde = Path(valmis), Path(kohde)
    korkeus = int(korkeus) if int(korkeus) in PUHELIN_KORKEUDET else 720
    tiedot = _virrat(valmis)
    virrat = tiedot.get("streams") or []
    kesto = float((tiedot.get("format") or {}).get("duration") or 0)
    aanet = [v for v in virrat if v.get("codec_type") == "audio"]
    # ääneksi selkeä puhe (stereo, tasattu: kuuluu puhelimen kaiuttimesta), muuten oletusraita
    valittu = next((i for i, v in enumerate(aanet) if "selkeä puhe" in ((v.get("tags") or {}).get("title") or "").lower()),
                   next((i for i, v in enumerate(aanet) if (v.get("disposition") or {}).get("default")), 0))
    tekstivirrat = [v for v in virrat if v.get("codec_type") == "subtitle"]
    tekstit = [i for i, v in enumerate(tekstivirrat) if v.get("codec_name") in TEKSTIKOODEKIT]
    kohde.parent.mkdir(parents=True, exist_ok=True)
    tmp = kohde.with_name(kohde.stem + ".kesken.mp4")
    cmd = [m.ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(valmis), "-map", "0:v:0",
           "-vf", f"scale=-2:'min({korkeus},ih)':flags=lanczos,setsar=1",
           *koodain_puhelin()]
    if aanet:
        nimi = (aanet[valittu].get("tags") or {}).get("title") or "Stereo"
        cmd += ["-map", f"0:a:{valittu}", "-c:a", "aac", "-b:a", "160k", "-ac", "2",
                "-metadata:s:a:0", f"handler_name={nimi}", "-metadata:s:a:0", f"title={nimi}"]
    for i in tekstit:
        cmd += ["-map", f"0:s:{i}"]
    if tekstit:
        cmd += ["-c:s", "mov_text"]
    cmd += ["-map_metadata", "0", "-map_chapters", "0", "-movflags", "+faststart", str(tmp)]
    _aja_edistyen(cmd, kesto, edistys, peruuta)
    tmp.replace(kohde)
    if edistys:
        edistys(1.0)
    return kohde


def kohdepolku(valmis, laji, korkeus=720):
    valmis = Path(valmis)
    nimi = f"{valmis.stem} (ennen ja jälkeen).mp4" if laji == "vertailu" else f"{valmis.stem} (puhelin {korkeus}p).mp4"
    return valmis.with_name(nimi)


def aja_tyo(t, wd, laji, korkeus=720, edistys=None, peruuta=None, kieli="fi"):
    """Yksi jälkityö työjonon tiedostolle. Palauttaa luodun tiedoston polun."""
    valmis = Path((t.get("tulos") or {}).get("ulos") or "")
    if not valmis.exists():
        raise RuntimeError("valmista_ei_loydy")
    kohde = kohdepolku(valmis, laji, korkeus)
    alku = time.time()
    if laji == "vertailu":
        src = Path(t["polku"])
        if not src.exists():
            raise RuntimeError("alkuperaista_ei_loydy")
        kohta = vertailun_kohta(src, t["analyysi"], t["geo"], Path(wd))
        nimet = ("Alkuperäinen", "PixeliRemaster") if kieli == "fi" else ("Original", "PixeliRemaster")
        vertailupatka(src, t["analyysi"], t["geo"], valmis, kohde, kohta, nimet=nimet, edistys=edistys,
                      peruuta=peruuta)
    else:
        puhelinversio(valmis, kohde, korkeus, edistys, peruuta)
    m.loki(f"Jälkityö {laji} valmis: {kohde} ({time.time() - alku:.0f} s)")
    return kohde
