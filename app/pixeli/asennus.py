"""PixeliRemaster – ympäristön tarkistus ja ensimmäisen käynnistyksen asennus.

Asennusohjelma sisältää Pythonin, käyttöliittymän ja AI-mallit. Ensimmäisellä käynnistyksellä
ladataan lisäksi:
  1. ffmpeg (videon purku ja pakkaus, ~90 Mt)
  2. PyTorch NVIDIA-tuella (~3 Gt)
ja ajetaan itsetesti, joka vertaa näytönohjaimen laskuja referenssiin ja mittaa nopeuden.
"""
import importlib
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

from . import moottori as m

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
TORCH_INDEKSI = "https://download.pytorch.org/whl/cu128"
MIN_AJURI = 528.33          # CUDA 12 -ajurivaatimus Windowsissa

_PIILOTA = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW


def _python_exe():
    """Komentorivi-Python (pythonw.exe ei sovi pipille)."""
    p = Path(sys.executable)
    if p.name.lower() == "pythonw.exe" and (p.parent / "python.exe").exists():
        return str(p.parent / "python.exe")
    return sys.executable


class Ymparisto:
    def __init__(self):
        self.lukko = threading.Lock()
        self.tila = {
            "tarkistettu": False,
            "gpu": None,            # {"nimi", "ajuri", "ajuri_ok"} tai None jos NVIDIAa ei löytynyt
            "torch": "tarkistetaan",  # ok | puuttuu | ei_cudaa | tarkistetaan
            "ffmpeg": m.ffmpeg_ok(),
            "itsetesti": None,
            "asennus": {"kaynnissa": False, "vaihe": None, "edistys": None, "virhe": None, "tieto": None},
            "testitila": os.environ.get("PIXELI_LAITE") == "numpy",
        }
        threading.Thread(target=self._tarkista, daemon=True).start()

    # ------------------------------------------------------------ tarkistus
    def _tarkista(self):
        gpu = self._nvidia()
        torch_tila = self._torch_tila()
        it = m.lue_json(m.DATA / "itsetesti.json")
        if it and it.get("versio") != m.VERSIO:
            it = None
        with self.lukko:
            self.tila.update({"gpu": gpu, "torch": torch_tila, "ffmpeg": m.ffmpeg_ok(),
                              "itsetesti": it, "tarkistettu": True})
            # päivityksen jälkeen kaikki on jo ladattu: itsetesti ajetaan uudelle versiolle kysymättä
            paivitys = (not it and gpu and gpu.get("ajuri_ok") and torch_tila == "ok" and self.tila["ffmpeg"]
                        and (m.DATA / "itsetesti.json").exists() and not self.tila["testitila"])
        if paivitys:
            m.loki("Uusi versio: ajetaan itsetesti")
            self.aloita_asennus()
        elif self.valmis() and not self.tila["testitila"] and not self._lisakirjastot_ok():
            threading.Thread(target=self._asenna_lisakirjastot, daemon=True).start()

    @staticmethod
    def _nvidia():
        if os.environ.get("PIXELI_LAITE") == "numpy":
            return {"nimi": "Testitila (prosessori)", "ajuri": None, "ajuri_ok": True}
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=15, creationflags=_PIILOTA)
            rivi = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
            if not rivi:
                return None
            nimi, ajuri = [x.strip() for x in rivi.split(",")[:2]]
            try:
                ok = float(re.match(r"[\d.]+", ajuri).group(0)) >= MIN_AJURI
            except (AttributeError, ValueError):
                ok = True
            return {"nimi": nimi, "ajuri": ajuri, "ajuri_ok": ok}
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            return None

    @staticmethod
    def _torch_tila():
        if os.environ.get("PIXELI_LAITE") == "numpy":
            return "ok"
        # Jo ladattua PyTorchia ei saa ladata uudelleen samaan prosessiin (C-laajennus rekisteröityy
        # vain kerran), joten onnistunut tuonti käytetään sellaisenaan.
        ladattu = sys.modules.get("torch")
        if ladattu is not None and hasattr(ladattu, "cuda"):
            return "ok" if ladattu.cuda.is_available() else "ei_cudaa"
        try:
            importlib.invalidate_caches()
            for k in [k for k in sys.modules if k == "torch" or k.startswith("torch.")]:
                del sys.modules[k]          # epäonnistuneen tuonnin jäänteet pois
            import torch
            return "ok" if torch.cuda.is_available() else "ei_cudaa"
        except Exception:  # noqa: BLE001
            if any(p.name.startswith("torch-") for p in (Path(sys.prefix) / "Lib" / "site-packages").glob("torch-*")):
                m.loki(f"PyTorch on asennettu, mutta sen lataus epäonnistui: {traceback.format_exc()}")
            return "puuttuu"

    def valmis(self):
        with self.lukko:
            t = self.tila
            if t["testitila"]:
                return t["ffmpeg"]
            return bool(t["tarkistettu"] and t["gpu"] and t["torch"] == "ok" and t["ffmpeg"]
                        and t["itsetesti"] and t["itsetesti"].get("ok"))

    def nayta(self):
        with self.lukko:
            d = {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.tila.items()}
        d["valmis"] = self.valmis()
        it = d.get("itsetesti") or {}
        d["itsetesti"] = {k: it.get(k) for k in ("ok", "nopeus", "naytonohjain", "virheet")} if it else None
        return d

    # ------------------------------------------------------------ asennus
    def aloita_asennus(self):
        with self.lukko:
            if self.tila["asennus"]["kaynnissa"]:
                return
            self.tila["asennus"] = {"kaynnissa": True, "vaihe": "valmistellaan", "edistys": None,
                                    "virhe": None, "tieto": None}
        threading.Thread(target=self._asenna, daemon=True).start()

    def _aseta(self, **kw):
        with self.lukko:
            self.tila["asennus"].update(kw)

    def _asenna(self):
        try:
            if not m.ffmpeg_ok():
                self._lataa_ffmpeg()
            with self.lukko:
                self.tila["ffmpeg"] = m.ffmpeg_ok()
            if self._torch_tila() != "ok" and not self.tila["testitila"]:
                self._asenna_torch()
                tila = self._torch_tila()
                with self.lukko:
                    self.tila["torch"] = tila
                if tila == "ei_cudaa":
                    raise RuntimeError("ei_cudaa")
                if tila != "ok":
                    raise RuntimeError("torch_asennus")
            with self.lukko:
                self.tila["torch"] = "ok"
            if not self.tila["testitila"]:
                self._asenna_lisakirjastot()
                # kohinanpoistomalli (130 Mt) ja monikuvamalli (noin 20 Mt) kuuluvat perusasennukseen
                from . import lisamallit
                for nimi in ("kohina", "monikuva"):
                    if not lisamallit.polku(nimi):
                        self._aseta(vaihe="mallit", edistys=0.0, tieto=None)
                        lisamallit.varmista(nimi, edistys=lambda a, b: self._aseta(
                            edistys=a / b if b else None, tieto={"tehty": a, "koko": b}))
            self._aseta(vaihe="itsetesti", edistys=0.0)
            if self.tila["testitila"]:
                tulos = {"versio": m.VERSIO, "ok": True, "virheet": [], "nopeus": {}, "naytonohjain": "testi"}
                m.kirjoita_json(m.DATA / "itsetesti.json", tulos)
            else:
                vaiheet = {"tarkkuus": 0.0, "nopeus": 0.5}

                def ed(laji, n, kaikki):
                    self._aseta(edistys=vaiheet[laji] + 0.5 * n / kaikki)

                tulos = m.itsetesti(edistys=ed)
            with self.lukko:
                self.tila["itsetesti"] = tulos
            if not tulos.get("ok"):
                raise RuntimeError("itsetesti")
            self._aseta(kaynnissa=False, vaihe="valmis", edistys=1.0)
        except Exception as e:  # noqa: BLE001
            m.loki(f"Asennus epäonnistui: {traceback.format_exc()}")
            avain = str(e) if str(e) in ("ei_cudaa", "torch_asennus", "itsetesti", "ffmpeg_lataus",
                                         "mallin_lataus") else "yleinen"
            self._aseta(kaynnissa=False, virhe=avain, tieto=str(e)[-400:])

    def _lataa_ffmpeg(self):
        self._aseta(vaihe="ffmpeg", edistys=0.0)
        m.TOOLS.mkdir(parents=True, exist_ok=True)
        z = m.TOOLS / "ffmpeg.zip.lataus"
        try:
            with urllib.request.urlopen(FFMPEG_URL, timeout=60) as r, open(z, "wb") as f:
                koko = int(r.headers.get("Content-Length") or 0)
                tehty = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    tehty += len(b)
                    self._aseta(edistys=(tehty / koko) if koko else None,
                                tieto={"tehty": tehty, "koko": koko})
            with zipfile.ZipFile(z) as zf:
                for nimi in zf.namelist():
                    if nimi.endswith(("bin/ffmpeg.exe", "bin/ffprobe.exe")):
                        (m.TOOLS / Path(nimi).name).write_bytes(zf.read(nimi))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("ffmpeg_lataus") from e
        finally:
            try:
                z.unlink()
            except OSError:
                pass

    @staticmethod
    def _lisakirjastot_ok():
        try:
            importlib.invalidate_caches()
            import pillow_heif  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def _asenna_lisakirjastot(self):
        """iPhone-kuvien (HEIC) tuki: pillow-heif ladataan PyPI:stä samaan tapaan kuin PyTorch
        (ei jaeta asennusohjelmassa, koska mukana tulevilla koodekeilla on omat lisenssinsä).
        Epäonnistuminen ei estä muuta käyttöä."""
        if self._lisakirjastot_ok():
            return
        self._aseta(vaihe="torch_asennus", edistys=None)
        cmd = [_python_exe(), "-m", "pip", "install", "pillow-heif", "--no-warn-script-location",
               "--disable-pip-version-check"]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=_PIILOTA)
        m.loki("pip pillow-heif: " + (p.stdout + p.stderr)[-1500:])

    def _asenna_torch(self):
        """pip --progress-bar raw tulostaa 'Progress X of Y' -rivejä, joista saadaan edistyminen."""
        self._aseta(vaihe="torch", edistys=0.0, tieto=None)
        cmd = [_python_exe(), "-m", "pip", "install", "--upgrade", "torch", "--index-url", TORCH_INDEKSI,
               "--progress-bar", "raw", "--no-warn-script-location", "--disable-pip-version-check"]
        m.loki("Asennetaan PyTorch: " + " ".join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=_PIILOTA, bufsize=1)
        tiedosto, suurin, viimeiset = None, 0, []
        for rivi in p.stdout:
            rivi = rivi.strip()
            viimeiset = (viimeiset + [rivi])[-30:]
            md = re.match(r"Downloading (\S+)", rivi)
            if md:
                tiedosto = md.group(1).rsplit("/", 1)[-1]
                continue
            mp = re.match(r"Progress (\d+) of (\d+)", rivi)
            if mp:
                tehty, koko = int(mp.group(1)), int(mp.group(2))
                if koko >= suurin:                      # näytetään suurimman tiedoston (torch) lataus
                    suurin = koko
                    self._aseta(edistys=tehty / koko if koko else None,
                                tieto={"tehty": tehty, "koko": koko, "tiedosto": tiedosto})
            elif rivi.startswith("Installing collected packages"):
                self._aseta(vaihe="torch_asennus", edistys=None)
        p.wait()
        m.loki("pip: " + "\n".join(viimeiset))
        if p.returncode != 0:
            raise RuntimeError("torch_asennus")


def avaa_kansio(polku):
    """Näyttää tiedoston Resurssienhallinnassa."""
    polku = Path(polku)
    if os.name == "nt":
        if polku.is_file():
            subprocess.Popen(["explorer", "/select,", str(polku)])
        else:
            os.startfile(str(polku if polku.exists() else polku.parent))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(polku.parent if polku.is_file() else polku)])


def avaa_tiedosto(polku):
    """Avaa tiedoston oletusohjelmalla (esim. valmis kuva Valokuvat-sovelluksessa)."""
    if os.name == "nt":
        os.startfile(str(polku))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(polku)])


def tiedostovalinta_powershell(kansio=False):
    """Windowsin tiedosto-/kansiovalinta ilman lisäkirjastoja (varalla, jos pywebview ei ole käytössä)."""
    if os.name != "nt":
        return []
    if kansio:
        skripti = ("Add-Type -AssemblyName System.Windows.Forms;"
                   "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                   "if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.SelectedPath}")
    else:
        skripti = ("Add-Type -AssemblyName System.Windows.Forms;"
                   "$d=New-Object System.Windows.Forms.OpenFileDialog;$d.Multiselect=$true;"
                   "$d.Filter='Video / Kuva|*.mkv;*.mp4;*.avi;*.vob;*.mpg;*.mpeg;*.m2v;*.ts;*.m4v;*.mov;*.wmv;"
                   "*.jpg;*.jpeg;*.png;*.heic;*.heif;*.webp;*.bmp;*.tif;*.tiff|*|*.*';"
                   "if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                   "$d.FileNames -join \"`n\"}")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", skripti], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=_PIILOTA)
    return [x.strip() for x in r.stdout.splitlines() if x.strip()]


def odota(sekuntia):
    time.sleep(sekuntia)
