"""PixeliRemaster – lisämallit, jotka ladataan vasta tarvittaessa.

Mallit julkaistaan ohjelman omassa GitHub-julkaisussa (mallit-1), jonne ne kopioi
.github/workflows/mallit.yml alkuperäisistä lähteistä. Jokainen tiedosto tarkistetaan
SHA-256-summalla ennen käyttöä.
"""
import hashlib
import os
import threading
import time
import urllib.request
from pathlib import Path

from . import moottori as m

JULKAISU = "https://github.com/tuumis/PixeliRemaster/releases/download/mallit-1/"

# nimi -> tiedot. "lahde" on alkuperäinen osoite; "jasen" on tiedosto zip-paketin sisällä ja "muunnos"
# julkaisua koottaessa tehtävä muunnos (installer/mallit.py). Muunnettuja malleja ladataan vain
# ohjelman omasta julkaisusta.
LUETTELO = {
    "kohina": {
        "tiedosto": "drunet_color.pth",
        "koko": 130579305,
        "sha256": "479abe3c5327dfd10ff54a80ec7d4098ca80752a5c9492cdff31cee430bec4b4",
        "lahde": "https://github.com/cszn/KAIR/releases/download/v1.0/drunet_color.pth",
        "kuvaus": "DRUNet (Kai Zhang ym., KAIR), MIT",
    },
    "kasvot": {
        "tiedosto": "GFPGANv1.4.pth",
        "koko": 348632874,
        "sha256": "e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad",
        "lahde": "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
        "kuvaus": "GFPGAN v1.4 (Tencent ARC), Apache 2.0",
    },
    "tunnistin": {
        "tiedosto": "detection_Resnet50_Final.pth",
        "koko": 109497761,
        "sha256": "6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d",
        "lahde": "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
        "kuvaus": "RetinaFace ResNet-50 (facexlib, Xintao Wang), MIT",
    },
    "naarmut": {
        "tiedosto": "bopbtl_naarmut.pth",
        "koko": 150642101,
        "sha256": "fd629d570294aa47bc3c0924728dab2c95b35a2289b9da334ccaf277c079bba3",
        "lahde": "https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life/releases/download/v1.0/global_checkpoints.zip",
        "jasen": "detection/FT_Epoch_latest.pt",
        "muunnos": "model_state",
        "kuvaus": "Naarmujen tunnistus (Bringing Old Photos Back to Life, Microsoft), MIT",
    },
    "taytto": {
        "tiedosto": "big_lama.pth",
        "koko": 204537128,
        "sha256": "a7138ca2369b400825a63c025b9c3c176160206c3a80422ef15fa32941eca83c",
        "lahde": "https://huggingface.co/smartywu/big-lama/resolve/main/big-lama.zip",
        "jasen": "models/best.ckpt",
        "muunnos": "generaattori",
        "kuvaus": "Big LaMa (Suvorov ym., Samsung AI), Apache 2.0",
    },
    "monikuva": {
        "tiedosto": "realbasicvsr_ema.pth",
        "koko": 25286837,
        "sha256": "0c7256093e7a05030c4c64c38ab074d0365046721281d33a869c1558ddc37e4e",
        "lahde": "https://download.openmmlab.com/mmediting/restorers/real_basicvsr/"
                 "realbasicvsr_c64b20_1x30x8_lr5e-5_150k_reds_20211104-52f77c2c.pth",
        "muunnos": "generator_ema",
        "kuvaus": "RealBasicVSR (Chan ym., OpenMMLab), Apache 2.0",
    },
    "vari": {
        "tiedosto": "ddcolor_modelscope_fp16.pth",
        "koko": 456060711,
        "sha256": "f10e36eef5aeaf1ae843e3e24db65b164b5ecca0dd14c602c7efa4d85bef1eb3",
        "lahde": "https://huggingface.co/piddnad/DDColor-models/resolve/main/ddcolor_modelscope.pth",
        "muunnos": "fp16",
        "kuvaus": "DDColor (Kang ym., DAMO Academy), Apache 2.0",
    },
}

KANSIO = m.DATA / "mallit"
_lukko = threading.Lock()
_lataukset = {}          # nimi -> {"tehty", "koko", "virhe"}


def polku(nimi):
    """Ladatun mallin polku tai None. Ohjelman mukana tuleva malli kelpaa myös (kehitys ja testit)."""
    t = LUETTELO[nimi]
    for p in (KANSIO / t["tiedosto"], m.MALLIT / t["tiedosto"]):
        if p.exists() and t["koko"] and p.stat().st_size == t["koko"]:
            return p
    return None


def tila():
    with _lukko:
        return {n: {"ladattu": polku(n) is not None, "koko": t["koko"],
                    "lataus": dict(_lataukset[n]) if n in _lataukset else None}
                for n, t in LUETTELO.items()}


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for osa in iter(lambda: f.read(1 << 20), b""):
            h.update(osa)
    return h.hexdigest()


def varmista(nimi, edistys=None, peruuta=None):
    """Palauttaa mallin polun; lataa sen ensin, jos puuttuu. Virheessä RuntimeError("mallin_lataus")."""
    p = polku(nimi)
    if p:
        return p
    t = LUETTELO[nimi]
    KANSIO.mkdir(parents=True, exist_ok=True)
    kohde = KANSIO / t["tiedosto"]
    tmp = kohde.with_name(kohde.name + ".lataus")
    with _lukko:
        _lataukset[nimi] = {"tehty": 0, "koko": t["koko"], "virhe": None}
    m.loki(f"Ladataan malli {t['tiedosto']}")
    try:
        # ensin ohjelman oma julkaisu, sitten alkuperäinen lähde (summa tarkistetaan kummastakin)
        osoitteet = [JULKAISU + t["tiedosto"]]
        if not t.get("jasen") and not t.get("muunnos"):
            osoitteet.append(t["lahde"])
        for yritys in range(4):
            try:
                with urllib.request.urlopen(osoitteet[yritys % len(osoitteet)], timeout=60) as r, open(tmp, "wb") as f:
                    tehty = 0
                    while True:
                        if peruuta is not None and peruuta.is_set():
                            raise m.Peruutettu()
                        osa = r.read(1 << 20)
                        if not osa:
                            break
                        f.write(osa)
                        tehty += len(osa)
                        with _lukko:
                            _lataukset[nimi]["tehty"] = tehty
                        if edistys:
                            edistys(tehty, t["koko"])
                break
            except (OSError, ValueError) as e:
                m.loki(f"Mallin lataus epäonnistui ({osoitteet[yritys % len(osoitteet)]}): {e}")
                if yritys == 3:
                    raise RuntimeError("mallin_lataus") from e
                time.sleep(2)
        if tmp.stat().st_size != t["koko"] or _sha256(tmp) != t["sha256"]:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("mallin_lataus")
        os.replace(tmp, kohde)
        return kohde
    except Exception as e:
        with _lukko:
            _lataukset[nimi]["virhe"] = str(e)
        raise
    finally:
        with _lukko:
            if not (_lataukset.get(nimi) or {}).get("virhe"):
                _lataukset.pop(nimi, None)
