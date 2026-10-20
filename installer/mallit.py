"""Kokoaa lisämallien julkaisun (GitHub Actions): lataa mallit alkuperäisistä lähteistä,
tekee tarvittavat muunnokset, tarkistaa koon ja SHA-256-summan ja kirjoittaa tiedostot
kansioon, josta ne ladataan julkaisuun.

    python installer/mallit.py <kansio>

Muunnokset (tarvitsevat PyTorchin):
  model_state   tarkistuspisteestä vain mallin painot (ilman optimoijan tilaa)
  generaattori  PyTorch Lightning -tarkistuspisteestä vain "generator."-alkuiset painot
  fp16          painot puolitarkkuuteen (puolittaa latauksen koon; lasketaan silti fp32:na)

Jos luettelon koko/sha256 puuttuu (uusi malli), arvot tulostetaan luetteloon lisättäviksi.
"""
import hashlib
import pickle
import sys
import urllib.request
import zipfile
from pathlib import Path

juuri = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(juuri / "app"))
from pixeli.lisamallit import LUETTELO  # noqa: E402


class _Tyhja:
    def __init__(self, *a, **k):
        pass

    def __setstate__(self, s):
        pass

    def __call__(self, *a, **k):
        return self


class _Lukija(pickle.Unpickler):
    """Lightning-tarkistuspisteiden hyperparametrit (omegaconf ym.) korvataan tyhjillä olioilla."""
    def find_class(self, mod, name):
        try:
            return super().find_class(mod, name)
        except Exception:
            return _Tyhja


class _Pickle:
    Unpickler = _Lukija
    load = staticmethod(pickle.load)
    __name__ = "pickle"


def muunna(lahde, kohde, muunnos):
    import torch
    ck = torch.load(lahde, map_location="cpu", weights_only=False, pickle_module=_Pickle)
    if muunnos == "model_state":
        sd = ck["model_state"]
    elif muunnos == "generaattori":
        sd = {k[len("generator."):]: v for k, v in ck["state_dict"].items() if k.startswith("generator.")}
    elif muunnos == "fp16":
        sd = ck.get("params", ck) if isinstance(ck, dict) else ck
        sd = {k: (v.half() if torch.is_tensor(v) and v.is_floating_point() else v) for k, v in sd.items()}
    else:
        raise ValueError(muunnos)
    sd = {k: v.contiguous() for k, v in sd.items() if torch.is_tensor(v)}
    torch.save(sd, kohde)
    print(f"  {muunnos}: {len(sd)} tensoria", flush=True)


def main():
    kohde = Path(sys.argv[1])
    kohde.mkdir(parents=True, exist_ok=True)
    valiaikainen = kohde.parent / (kohde.name + "_lahteet")
    valiaikainen.mkdir(exist_ok=True)
    virheita = 0
    for nimi, t in LUETTELO.items():
        p = kohde / t["tiedosto"]
        print(f"{nimi}: {t['lahde']}", flush=True)
        lataus = valiaikainen / Path(t["lahde"].split("?")[0]).name
        if not lataus.exists():
            urllib.request.urlretrieve(t["lahde"], lataus)
        lahde = lataus
        if t.get("jasen"):
            with zipfile.ZipFile(lataus) as zf:
                nimet = [n for n in zf.namelist() if n.endswith(t["jasen"])]
                if len(nimet) != 1:
                    print(f"::error title=mallit::{nimi}: {t['jasen']} ei löydy yksiselitteisesti ({nimet})")
                    sys.exit(1)
                lahde = valiaikainen / f"{nimi}_{Path(nimet[0]).name}"
                lahde.write_bytes(zf.read(nimet[0]))
        if t.get("muunnos"):
            muunna(lahde, p, t["muunnos"])
        else:
            p.write_bytes(lahde.read_bytes())
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        koko = p.stat().st_size
        if t["koko"] is None or t["sha256"] is None:
            print(f"::warning title=mallit::{nimi} uusi: \"koko\": {koko}, \"sha256\": \"{h}\"")
        elif koko != t["koko"] or h != t["sha256"]:
            print(f"::error title=mallit::{t['tiedosto']}: koko {koko}, sha256 {h} ei vastaa luetteloa")
            virheita += 1
        else:
            print(f"::notice title=mallit::{t['tiedosto']} OK ({koko} tavua)")
    sys.exit(1 if virheita else 0)


if __name__ == "__main__":
    main()
