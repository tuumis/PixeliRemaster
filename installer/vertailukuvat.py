"""Vertailukuvat Katso heti -parannuksista (GitHub Actions, Windows, ilman näytönohjainta: mpv WARP-tilassa).

Lähde: Sintel-traileri (Blender Foundation, CC BY 3.0, 854x480 – DVD:n tarkkuusluokkaa).
Samasta rajauksesta tehdään 2,5-kertainen suurennus: tavallinen skaalaus, FSRCNNX, Anime4K
(soittimen oma parannus) ja PixeliRemasterin käsittely (DVD-elokuva-asetus, prosessorilla).
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "app"))
ulos = Path(sys.argv[1])
ulos.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PIXELI_DATA", str(ulos / "data"))
from pixeli import kuva, moottori as m, soitin  # noqa: E402

LAHDE = "https://download.blender.org/durian/trailer/sintel_trailer-480p.mp4"
KOHDAT = [("kasvot", 16.0, (300, 60)), ("maisema", 38.0, (200, 100))]
CW, CH, K = 384, 288, 2.5
OW, OH = int(CW * K), int(CH * K)

mpv = soitin.lataa()
print("mpv", mpv)
LUA = ulos / "kuva.lua"
LUA.write_text('local mp = require "mp"\nmp.register_event("playback-restart", function()\n'
               '  mp.add_timeout(2.0, function()\n'
               '    mp.commandv("screenshot-to-file", mp.get_opt("kohde"), "window")\n'
               '    mp.add_timeout(0.5, function() mp.command("quit") end)\n  end)\nend)\n', encoding="utf-8")


def kuvakaappaus(lahde, kohde, varjostimet):
    cmd = [mpv, "--no-config", "--vo=gpu-next", "--gpu-api=d3d11", "--d3d11-warp=yes", f"--geometry={OW}x{OH}+0+0",
           "--no-border", "--image-display-duration=inf", "--scale=ewa_lanczossharp", "--cscale=ewa_lanczossharp",
           "--dscale=mitchell", "--osd-level=0", "--no-osc", f"--script={LUA}", f"--script-opts=kohde={kohde}",
           *[f"--glsl-shader={juuri / 'app' / 'shaders' / v}" for v in varjostimet], str(lahde)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not Path(kohde).exists():
        raise RuntimeError(f"mpv ei tallentanut kuvaa ({p.returncode}): {(p.stdout + p.stderr)[-1500:]}")
    return np.asarray(Image.open(kohde).convert("RGB").resize((OW, OH), Image.LANCZOS))


def pixeliremaster(rgb):
    o = kuva.TorchOps("cpu", puolitarkkuus=False) if "torch" in sys.modules or _torch() else kuva.NumpyOps()
    sd = kuva.lataa_pth(m.MALLIT / m.MALLITIEDOSTOT[m.ASETUKSET[2]["malli"]])
    a = dict(m.ASETUKSET[2])
    a["malli"] = kuva.Malli(o, sd)
    k = kuva.Ketju(o, CH, CW, OH, OW, {x: y for x, y in a.items() if x not in ("nimi", "kuvaus")}, yuv=False)
    return o.uint8_hwc(k.rgb_lopullinen(o.from_uint8(rgb[None]), 0))


def _torch():
    try:
        import torch  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


try:
    fontti = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
except Exception:  # noqa: BLE001
    fontti = ImageFont.load_default()
for nimi, t, (x, y) in KOHDAT:
    raaka = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", LAHDE, "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    kehys = np.frombuffer(raaka, np.uint8).reshape(480, 854, 3)
    rajaus = np.ascontiguousarray(kehys[y:y + CH, x:x + CW])
    lahde = ulos / f"{nimi}_lahde.png"
    Image.fromarray(rajaus).save(lahde)
    kuvat = [("Tavallinen skaalaus", kuvakaappaus(lahde, ulos / f"{nimi}_tavallinen.png", [])),
             ("Soittimen oma: FSRCNNX (elokuvat)", kuvakaappaus(lahde, ulos / f"{nimi}_fsrcnnx.png", soitin.VARJOSTIMET["elokuva"])),
             ("Soittimen oma: Anime4K (piirretyt)", kuvakaappaus(lahde, ulos / f"{nimi}_anime4k.png", soitin.VARJOSTIMET["piirretty"])),
             ("PixeliRemaster-käsittely (DVD-elokuva)", pixeliremaster(rajaus))]
    koonti = Image.new("RGB", (OW * 2, (OH + 34) * 2), "white")
    d = ImageDraw.Draw(koonti)
    for i, (otsikko, a) in enumerate(kuvat):
        px, py = (i % 2) * OW, (i // 2) * (OH + 34)
        koonti.paste(Image.fromarray(a), (px, py + 34))
        d.text((px + 8, py + 6), otsikko, fill="black", font=fontti)
    koonti.save(ulos / f"vertailu_{nimi}.png")
    print("tallennettu", ulos / f"vertailu_{nimi}.png")
