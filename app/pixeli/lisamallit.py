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

# nimi -> tiedot. "lahde" on alkuperäinen osoite (käytetään vain julkaisun kokoamisessa).
LUETTELO = {
    "kohina": {
        "tiedosto": "drunet_color.pth",
        "koko": 130579305,
        "sha256": "479abe3c5327dfd10ff54a80ec7d4098ca80752a5c9492cdff31cee430bec4b4",
        "lahde": "https://github.com/cszn/KAIR/releases/download/v1.0/drunet_color.pth",
        "kuvaus": "DRUNet (Kai Zhang ym., KAIR), MIT",
    },
}

KANSIO = m.DATA / "mallit"
_lukko = threading.Lock()
_lataukset = {}          # nimi -> {"tehty", "koko", "virhe"}


def polku(nimi):
    """Ladatun mallin polku tai None. Ohjelman mukana tuleva malli kelpaa myös (kehitys ja testit)."""
    t = LUETTELO[nimi]
    for p in (KANSIO / t["tiedosto"], m.MALLIT / t["tiedosto"]):
        if p.exists() and p.stat().st_size == t["koko"]:
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
        for yritys in range(3):
            try:
                with urllib.request.urlopen(JULKAISU + t["tiedosto"], timeout=60) as r, open(tmp, "wb") as f:
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
                m.loki(f"Mallin lataus epäonnistui ({yritys + 1}/3): {e}")
                if yritys == 2:
                    raise RuntimeError("mallin_lataus") from e
                time.sleep(3)
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
