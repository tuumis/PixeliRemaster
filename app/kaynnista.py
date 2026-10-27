"""PixeliRemaster – käynnistys.

    pythonw.exe kaynnista.py [video- tai kuvatiedostoja ...]

Jos ohjelma on jo auki, tiedostot lisätään avoinna olevan ikkunan jonoon ja se tuodaan esiin.
"""
import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _ilmoitus(otsikko, teksti):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, teksti, otsikko, 0x10)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"{otsikko}: {teksti}", file=sys.stderr)


def _laheta_avoimelle(tiedosto, polut):
    """Palauttaa True, jos ohjelma oli jo käynnissä ja tiedostot välitettiin sille."""
    try:
        t = json.loads(tiedosto.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{t['portti']}/api/"

        def kutsu(polku, data):
            req = urllib.request.Request(url + polku, data=json.dumps(data).encode("utf-8"), method="POST",
                                         headers={"X-Pixeli-Avain": t["avain"], "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()

        if polut:
            kutsu("lisaa", {"polut": polut})
        kutsu("nayta", {})
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PixeliRemaster.Sovellus")
        except Exception:  # noqa: BLE001
            pass
    from pixeli import moottori as m
    m.DATA.mkdir(parents=True, exist_ok=True)
    polut = [a for a in sys.argv[1:] if not a.startswith("--")]
    instanssi = m.DATA / "instanssi.json"
    if instanssi.exists() and _laheta_avoimelle(instanssi, polut):
        return
    # Explorer voi käynnistää ohjelman monta kertaa yhtä aikaa (useita tiedostoja valittuna):
    # vain ensimmäinen käynnistyy, muut odottavat sitä ja välittävät tiedostonsa sille.
    lukko = m.DATA / "kaynnistys.lukko"
    for _ in range(2):
        try:
            os.close(os.open(lukko, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                tuore = time.time() - lukko.stat().st_mtime < 30
            except OSError:
                tuore = False
            if tuore:
                for _ in range(60):
                    time.sleep(0.5)
                    if instanssi.exists() and _laheta_avoimelle(instanssi, polut):
                        return
            try:
                lukko.unlink()
            except OSError:
                pass

    from pixeli import asennus, ikkuna, palvelin, tyojono
    m.loki(f"Käynnistys, versio {m.VERSIO}, Python {sys.version.split()[0]}")
    ymp = asennus.Ymparisto()
    jono = tyojono.Jono(ymp)
    sov = palvelin.Sovellus(jono, ymp)
    srv = palvelin.kaynnista(sov)
    m.kirjoita_json(instanssi, {"portti": sov.portti, "avain": sov.avain, "pid": os.getpid()})
    try:
        lukko.unlink()
    except OSError:
        pass
    if polut:
        jono.lisaa(polut)
    from pixeli import paivitys
    paivitys.jatka(jono)                 # päivityksen jälkeen käsittely jatkuu
    paivitys.kaynnista_tarkistus()

    def kieli():
        k = jono.asetukset.get("kieli") or "auto"
        return sov.kieli_jarjestelma if k == "auto" else k

    url = f"http://127.0.0.1:{sov.portti}/"
    ik = ikkuna.luo(sov, url, kieli)
    sov.ikkuna = ik
    try:
        ik.aja()
    finally:
        try:
            instanssi.unlink()
        except OSError:
            pass
        jono.lopeta()
        # annetaan käsittelyn pysähtyä hallitusti (valmiit osat säilyvät)
        loppu = time.time() + 8
        while jono._kasiteltava and time.time() < loppu:
            time.sleep(0.2)
        srv.shutdown()
        m.loki("Suljettu")
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        virhe = traceback.format_exc()
        try:
            from pixeli import moottori as m
            m.loki("KAATUMINEN: " + virhe)
            polku = m.LOKI
        except Exception:  # noqa: BLE001
            polku = "loki.txt"
        _ilmoitus("PixeliRemaster", f"Ohjelma ei käynnistynyt / The program failed to start.\n\n"
                                    f"{virhe.strip().splitlines()[-1]}\n\nLoki: {polku}")
        sys.exit(1)
