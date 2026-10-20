"""Kokoaa lisämallien julkaisun (GitHub Actions): lataa mallit alkuperäisistä lähteistä,
tarkistaa koon ja SHA-256-summan ja kirjoittaa ne kansioon, josta ne ladataan julkaisuun.

    python installer/mallit.py <kansio>
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "app"))
from pixeli.lisamallit import LUETTELO  # noqa: E402

kohde = Path(sys.argv[1])
kohde.mkdir(parents=True, exist_ok=True)
for nimi, t in LUETTELO.items():
    p = kohde / t["tiedosto"]
    print(f"{nimi}: {t['lahde']}", flush=True)
    urllib.request.urlretrieve(t["lahde"], p)
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if p.stat().st_size != t["koko"] or h != t["sha256"]:
        print(f"::error title=mallit::{t['tiedosto']}: koko {p.stat().st_size}, sha256 {h} ei vastaa luetteloa")
        sys.exit(1)
    print(f"::notice title=mallit::{t['tiedosto']} OK ({t['koko']} tavua)")
