"""PixeliRemaster – DVD- ja Blu-ray-levyjen lisäys MakeMKV:n avulla.

MakeMKV ei ole avointa koodia eikä sitä saa jakaa muiden ohjelmien mukana, joten käytetään
käyttäjän koneelle asentamaa MakeMKV:tä sen komentorivityökalun (makemkvcon) robottitilassa:
  info disc:9999      -> asemat (DRV-rivit)
  info disc:N         -> levyn nimikkeet (TINFO-rivit)
  mkv disc:N T kansio -> nimike MKV-tiedostoksi, edistyminen PRGV-riveinä
Kopiosuojauksen käsittely ja sen lisenssi (Blu-ray-beta) ovat MakeMKV:n vastuulla.
"""
import csv
import os
import re
import subprocess
import time
from pathlib import Path

from . import moottori as m

# AP_ItemAttributeId (MakeMKV apdefs.h)
NIMI, LUVUT, KESTO, KOKO_TEKSTI, KOKO_TAVUT, LAHDE, OSIA, ULOS = 2, 8, 9, 10, 11, 16, 25, 27


def makemkvcon():
    """makemkvcon64.exe tavallisista asennuspaikoista (tai PATHista). None, jos ei asennettu."""
    ehdokkaat = []
    for juuri in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), r"C:\Program Files (x86)",
                  r"C:\Program Files"):
        if juuri:
            ehdokkaat += [Path(juuri) / "MakeMKV" / "makemkvcon64.exe", Path(juuri) / "MakeMKV" / "makemkvcon.exe"]
    for e in ehdokkaat:
        if e.exists():
            return str(e)
    import shutil
    return shutil.which("makemkvcon64") or shutil.which("makemkvcon")


def _kentat(loppu):
    """Robottirivin kentät: pilkuilla erotettuja, merkkijonot lainausmerkeissä."""
    return next(csv.reader([loppu], skipinitialspace=False))


def jasenna(teksti):
    """Robottitulosteen rivit -> {"asemat": [...], "levy": {...}, "nimikkeet": {id: {...}}, "viestit": [...]}"""
    tulos = {"asemat": [], "levy": {}, "nimikkeet": {}, "viestit": []}
    for rivi in teksti.splitlines():
        if ":" not in rivi:
            continue
        laji, loppu = rivi.split(":", 1)
        try:
            k = _kentat(loppu)
        except Exception:  # noqa: BLE001
            continue
        if laji == "DRV" and len(k) >= 6:
            levy = k[5].strip()
            if k[4].strip() or levy:
                tulos["asemat"].append({"indeksi": int(k[0]), "asema": k[4].strip(), "levy": levy,
                                        "polku": k[6].strip() if len(k) > 6 else "", "levy_sisalla": bool(levy)})
        elif laji == "CINFO" and len(k) >= 3:
            tulos["levy"][int(k[0])] = k[2]
        elif laji == "TINFO" and len(k) >= 4:
            tulos["nimikkeet"].setdefault(int(k[0]), {})[int(k[1])] = k[3]
        elif laji == "MSG" and len(k) >= 4:
            tulos["viestit"].append(k[3])
    return tulos


def kesto_s(s):
    """'1:45:32' -> 6332."""
    try:
        osat = [int(x) for x in str(s).split(":")]
    except ValueError:
        return 0
    t = 0
    for o in osat:
        t = t * 60 + o
    return t


def _aja(args, aikaraja=300):
    exe = makemkvcon()
    if not exe:
        raise RuntimeError("makemkv_puuttuu")
    p = subprocess.run([exe, "-r", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=aikaraja, creationflags=m.PIILOTA)
    return p.stdout


def asemat():
    """Optiset asemat ja niissä olevat levyt."""
    return jasenna(_aja(["--cache=1", "info", "disc:9999"], 120))["asemat"]


def nimikkeet(indeksi):
    """Levyn nimikkeet pisimmästä lyhimpään; pääelokuva (pisin) merkitään suositukseksi."""
    d = jasenna(_aja(["--cache=1", "--minlength=120", "info", f"disc:{int(indeksi)}"], 600))
    lista = []
    for tid, a in d["nimikkeet"].items():
        lista.append({"id": tid, "nimi": a.get(NIMI) or "", "kesto": kesto_s(a.get(KESTO)),
                      "luvut": int(a.get(LUVUT) or 0), "koko": int(a.get(KOKO_TAVUT) or 0),
                      "koko_teksti": a.get(KOKO_TEKSTI) or "", "lahde": a.get(LAHDE) or "",
                      "tiedosto": a.get(ULOS) or ""})
    lista.sort(key=lambda x: (-x["kesto"], x["id"]))
    if lista:
        lista[0]["paaelokuva"] = True
    levyn_nimi = d["levy"].get(NIMI) or d["levy"].get(32) or ""
    virhe = None
    if not lista:
        # tyypillisesti: ei levyä, Blu-ray-betan avain vanhentunut tai levyä ei voitu lukea
        virhe = next((v for v in reversed(d["viestit"]) if v), None)
    tyyppi = (d["levy"].get(1) or "").lower()       # esim. "DVD disc" / "Blu-ray disc"
    return {"levy": levyn_nimi, "nimikkeet": lista, "virhe": virhe,
            "bluray": "blu" in tyyppi or any(x["lahde"].lower().endswith(".mpls") for x in lista)}


def turvallinen_nimi(s, oletus="Levy"):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(s or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip()
    return (s[:80] or oletus)


def rippaa(indeksi, nimike, kansio, edistys=None, peruuta=None):
    """Nimike MKV-tiedostoksi kansioon. Palauttaa luodun tiedoston polun."""
    exe = makemkvcon()
    if not exe:
        raise RuntimeError("makemkv_puuttuu")
    kansio = Path(kansio)
    kansio.mkdir(parents=True, exist_ok=True)
    ennen = {p.name for p in kansio.glob("*.mkv")}
    alku = time.time()
    cmd = [exe, "-r", "--progress=-same", "--cache=512", "mkv", f"disc:{int(indeksi)}", str(int(nimike)), str(kansio)]
    loki = m.DATA / "lokit"
    loki.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=m.PIILOTA)
    viestit = []
    with open(loki / "makemkv.log", "w", encoding="utf-8") as lf:
        for rivi in p.stdout:
            lf.write(rivi)
            if peruuta is not None and peruuta.is_set():
                p.kill()
                break
            if rivi.startswith("PRGV:"):
                try:
                    nyt, kaikki, maks = (int(x) for x in rivi[5:].strip().split(","))
                    if edistys and maks:
                        edistys(kaikki / maks)
                except ValueError:
                    pass
            elif rivi.startswith("MSG:"):
                try:
                    viestit.append(_kentat(rivi[4:])[3])
                except Exception:  # noqa: BLE001
                    pass
    p.wait()
    if peruuta is not None and peruuta.is_set():
        for q in kansio.glob("*.mkv"):
            if q.name not in ennen and q.stat().st_mtime >= alku - 5:
                try:
                    q.unlink()
                except OSError:
                    pass
        raise m.Peruutettu()
    uudet = [q for q in kansio.glob("*.mkv") if q.name not in ennen and q.stat().st_size > 0]
    if p.returncode != 0 or not uudet:
        viesti = next((v for v in reversed(viestit) if v), "")
        raise RuntimeError(f"rippaus_epaonnistui: {viesti[-300:]}")
    return max(uudet, key=lambda q: q.stat().st_size)


# ------------------------------------------------------------------ suoratoisto (katselu suoraan levyltä)

import html.parser  # noqa: E402
import threading  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402


class _Solut(html.parser.HTMLParser):
    """MakeMKV:n suoratoistosivut ovat avain-arvo-taulukoita: <td>avain</td><td>arvo tai <a href></td>."""

    def __init__(self):
        super().__init__()
        self.solut, self._nyt, self._href = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._nyt, self._href = "", None
        elif tag == "a" and self._nyt is not None:
            self._href = dict(attrs).get("href")

    def handle_data(self, data):
        if self._nyt is not None:
            self._nyt += data

    def handle_endtag(self, tag):
        if tag == "td" and self._nyt is not None:
            self.solut.append((self._nyt.strip(), self._href))
            self._nyt = None


def _sivu(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        p = _Solut()
        p.feed(r.read().decode("utf-8", "replace"))
    s = p.solut
    return {s[i][0]: (urllib.parse.urljoin(url, s[i + 1][1]) if s[i + 1][1] else s[i + 1][0])
            for i in range(0, len(s) - 1, 2)}


def suoratoiston_nimikkeet(juuri):
    etusivu = _sivu(juuri)
    lista = _sivu(etusivu["titles"])
    tulos = []
    for i in range(int(lista.get("titlecount") or 0)):
        try:
            t = _sivu(lista[f"title{i}"])
        except Exception:  # noqa: BLE001
            continue
        if t.get("file0"):
            tulos.append({"i": i, "kesto": kesto_s(t.get("duration")), "url": t["file0"]})
    return tulos


class Suoratoisto:
    """makemkvcon stream: levyn nimikkeet HTTP-palvelimena vain tälle koneelle (127.0.0.1)."""

    def __init__(self, indeksi):
        exe = makemkvcon()
        if not exe:
            raise RuntimeError("makemkv_puuttuu")
        self.p = subprocess.Popen([exe, "-r", "--bindip=127.0.0.1", "stream", f"disc:{int(indeksi)}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace", creationflags=m.PIILOTA)
        self.juuri, self.viestit = None, []
        valmis = threading.Event()

        def lue():
            for rivi in self.p.stdout:
                if rivi.startswith("MSG:"):
                    try:
                        k = _kentat(rivi[4:])
                    except Exception:  # noqa: BLE001
                        continue
                    self.viestit.append(k[3] if len(k) > 3 else rivi)
                    if k[0] == "4500" and not self.juuri:
                        portti = next((x for x in k[5:] if x.isdigit()), None)
                        r = re.search(r":(\d{2,5})", " ".join(k[3:]))
                        portti = portti or (r.group(1) if r else "51000")
                        self.juuri = f"http://127.0.0.1:{portti}/"
                        valmis.set()
            valmis.set()
        threading.Thread(target=lue, daemon=True).start()
        if not valmis.wait(120) or not self.juuri:
            self.lopeta()
            raise RuntimeError("suoratoisto_ei_kaynnistynyt: " + (self.viestit[-1] if self.viestit else ""))

    def lopeta(self):
        try:
            self.p.kill()
        except Exception:  # noqa: BLE001
            pass
