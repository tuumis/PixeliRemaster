"""DVD-tekstitysten tunnistuksen testi Windowsissa (GitHub Actions): Windowsin tekstintunnistus
(pywinrt asennetaan kuten käyttäjällä), SPU-kuvan purku ja SRT. Testikuva piirretään ffmpegillä
(Arial) ja koodataan DVD:n SPU-muotoon reunuksineen, kuten levyillä."""
import difflib
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def _virhe(tyyppi, arvo, tb):
    rivit = "".join(traceback.format_exception(tyyppi, arvo, tb)).strip().replace("\n", "%0A")
    print(f"::error title=ocrtesti::{rivit[-3000:]}", flush=True)
    sys.__excepthook__(tyyppi, arvo, tb)


sys.excepthook = _virhe
juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "build" / "app" if (juuri / "build" / "app").exists() else juuri / "app"))
os.environ.setdefault("PIXELI_DATA", tempfile.mkdtemp(prefix="pixeli_ocr_"))

import numpy as np  # noqa: E402

from pixeli import tekstitys as tx  # noqa: E402

tx.varmista_ocr()
LAUSE = "Good morning, Mr. Jones! Where were you?"
W, H = 720, 60
fontti = "C\\:/Windows/Fonts/arial.ttf"
raaka = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:d=1", "-vf",
                        f"drawtext=fontfile='{fontti}':text='{LAUSE}':fontsize=30:fontcolor=white:x=10:y=14",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
kuva = np.frombuffer(raaka, np.uint8).reshape(H, W)
teksti = kuva > 128
reuna = np.zeros_like(teksti)
for dy in (-2, -1, 0, 1, 2):
    for dx in (-2, -1, 0, 1, 2):
        reuna |= np.roll(np.roll(teksti, dy, 0), dx, 1)
ind = np.zeros((H, W), np.uint8)
ind[reuna] = 2
ind[teksti] = 1
spu = tx.koodaa_spu(ind, 0, 480, 3.0)
s = tx.pura_spu(spu)
assert (s["indeksit"] == ind).all()
t = tx.Tunnistin("eng")
tulos = tx.siisti(t.tunnista(tx.tekstikuva(s)))
samuus = difflib.SequenceMatcher(None, tulos, LAUSE).ratio()
print(f"tunnistettu: {tulos!r} (kieli {t.kieli}, samuus {samuus:.2f})")
assert samuus > 0.85, (tulos, samuus)
print(f"::notice title=ocrtesti::Windowsin tekstintunnistus: {tulos!r} (samuus {samuus:.2f}, kieli {t.kieli})")
