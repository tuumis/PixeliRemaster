"""PixeliRemaster – työjono.

Elokuvat kulkevat tiloissa:
  analysoidaan -> odottaa -> jonossa -> kasitellaan -> valmis
                                     \\-> keskeytetty (jatkuu valmiista osista)
  virhe / hd (jo valmiiksi HD-tarkkuutta, ei käsitellä)

Säikeet:
  analyysi  – lyhyt ffmpeg-analyysi heti lisättäessä
  gpu       – varsinainen käsittely, yksi elokuva kerrallaan
  koe       – esikatselukuvat ja liikepätkät (oma Laite, voi toimia käsittelyn rinnalla)
"""
import json
import os
import queue
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import moottori as m

TILAT_KESKEN = ("analysoidaan", "kasitellaan")


def _uusi_id():
    return uuid.uuid4().hex[:10]


class Jono:
    def __init__(self, ymparisto):
        self.ymp = ymparisto
        self.lukko = threading.RLock()
        self.tyot = []
        self.asetukset = {"kohde": "auto", "ulos_kansio": None, "kieli": "auto"}
        self.sammuta_valmiina = False
        self.sammutus_ajastettu = False
        self._peruuta = threading.Event()
        self._kasiteltava = None
        self._herata = threading.Event()
        self._analyysi_q = queue.Queue()
        self._koe_q = queue.Queue()
        self._laitteet = {}
        self._lopeta = False
        self._lataa()
        for nimi, kohde in (("analyysi", self._analyysi_saie), ("gpu", self._gpu_saie), ("koe", self._koe_saie)):
            threading.Thread(target=kohde, name=nimi, daemon=True).start()
        for t in self.tyot:
            if t["tila"] == "analysoidaan" or (t["tila"] not in ("valmis", "virhe") and not t.get("analyysi")):
                t["tila"] = "analysoidaan"
                self._analyysi_q.put(t["id"])

    # ------------------------------------------------------------ pysyvyys
    def _tiedosto(self):
        return m.DATA / "jono.json"

    def _lataa(self):
        a = m.lue_json(m.DATA / "asetukset.json", {}) or {}
        self.asetukset.update({k: v for k, v in a.items() if k in self.asetukset})
        d = m.lue_json(self._tiedosto(), {}) or {}
        for t in d.get("tyot", []):
            if t.get("tila") == "kasitellaan" or t.get("tila") == "jonossa":
                t["tila"] = "keskeytetty"
            t["koe"] = {k: v for k, v in (t.get("koe") or {}).items() if k != "tila"} | {"tila": (
                "valmis" if (t.get("koe") or {}).get("tunniste") else "ei")}
            t["liike"] = {}
            t.pop("edistys", None)
            self.tyot.append(t)

    def _tallenna(self):
        with self.lukko:
            data = {"versio": m.VERSIO, "tyot": [{k: v for k, v in t.items() if k not in ("liike",)}
                                                 for t in self.tyot]}
        try:
            m.DATA.mkdir(parents=True, exist_ok=True)
            m.kirjoita_json(self._tiedosto(), data)
        except OSError:
            pass

    def tallenna_asetukset(self, uudet):
        with self.lukko:
            for k in ("kohde", "ulos_kansio", "kieli"):
                if k in uudet:
                    self.asetukset[k] = uudet[k]
            m.kirjoita_json(m.DATA / "asetukset.json", self.asetukset)
            # kohderesoluutio vaikuttaa kaikkien aloittamattomien geometriaan
            for t in self.tyot:
                if t.get("analyysi") and t["tila"] in ("odottaa", "keskeytetty", "jonossa"):
                    self._paivita_geometria(t)
        self._tallenna()

    # ------------------------------------------------------------ apurit
    def kohde(self):
        k = self.asetukset.get("kohde") or "auto"
        if k == "auto":
            return m.nayton_resoluutio()
        try:
            w, h = (int(x) for x in str(k).lower().split("x"))
            return w, h
        except ValueError:
            return 2560, 1440

    def _tyo(self, tid):
        for t in self.tyot:
            if t["id"] == tid:
                return t
        raise KeyError("tyota_ei_loydy")

    def _wd(self, t):
        return m.tyokansio(Path(t["polku"]))

    def wd(self, tid):
        with self.lukko:
            return self._wd(self._tyo(tid))

    def _paivita_geometria(self, t):
        an = t["analyysi"]
        geo = m.geometria(an, t.get("valinta") or {}, self.kohde())
        t["geo"] = geo
        n = int((t.get("valinta") or {}).get("asetus") or an["suositus"])
        t["arvio_nopeus"] = m.arvioi_nopeus(an, geo, n)

    def _laite(self, nimi):
        if nimi not in self._laitteet:
            self._laitteet[nimi] = m.Laite()
        return self._laitteet[nimi]

    # ------------------------------------------------------------ toiminnot
    def lisaa(self, polut):
        lisatyt, ohitetut = [], []
        tiedostot = []
        for p in polut:
            p = Path(str(p).strip().strip('"'))
            if p.is_dir():
                tiedostot += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in m.VIDEOPAATTEET)
            elif p.is_file():
                tiedostot.append(p)
        with self.lukko:
            olemassa = {os.path.normcase(str(Path(t["polku"]).resolve())) for t in self.tyot}
            for f in tiedostot:
                avain = os.path.normcase(str(f.resolve()))
                if f.suffix.lower() not in m.VIDEOPAATTEET or "[Remaster]" in f.name:
                    ohitetut.append({"nimi": f.name, "syy": "ei_video" if f.suffix.lower() not in m.VIDEOPAATTEET
                                     else "oma_tulos"})
                    continue
                if avain in olemassa:
                    ohitetut.append({"nimi": f.name, "syy": "jo_jonossa"})
                    continue
                t = {"id": _uusi_id(), "polku": str(f.resolve()), "nimi": f.name, "lisatty": time.time(),
                     "tila": "analysoidaan", "valinta": {}, "koe": {"tila": "ei"}, "liike": {}}
                self.tyot.append(t)
                olemassa.add(avain)
                lisatyt.append(t["id"])
                self._analyysi_q.put(t["id"])
        self._tallenna()
        return {"lisatyt": lisatyt, "ohitetut": ohitetut}

    def poista(self, tid):
        with self.lukko:
            t = self._tyo(tid)
            if self._kasiteltava == tid:
                self._peruuta.set()
            self.tyot.remove(t)
        self._tallenna()

    def tyhjenna_valmiit(self):
        with self.lukko:
            self.tyot = [t for t in self.tyot if t["tila"] not in ("valmis",)]
        self._tallenna()

    def siirra(self, tid, suunta):
        with self.lukko:
            t = self._tyo(tid)
            i = self.tyot.index(t)
            j = max(0, min(len(self.tyot) - 1, i + (1 if suunta > 0 else -1)))
            self.tyot[i], self.tyot[j] = self.tyot[j], self.tyot[i]
        self._tallenna()

    def aseta_valinta(self, tid, valinta):
        with self.lukko:
            t = self._tyo(tid)
            if t["tila"] == "kasitellaan":
                raise RuntimeError("kasittely_kaynnissa")
            v = dict(t.get("valinta") or {})
            if "asetus" in valinta:
                v["asetus"] = int(valinta["asetus"]) if valinta["asetus"] else None
            if "saato" in valinta:
                s = {k: float(x) for k, x in (valinta["saato"] or {}).items()
                     if k in m.SAATO_AVAIMET and x is not None}
                v["saato"] = s or None
            if "dar" in valinta:
                v["dar"] = valinta["dar"] if valinta["dar"] in ("4:3", "16:9") else None
            t["valinta"] = {k: x for k, x in v.items() if x is not None}
            if t.get("analyysi"):
                self._paivita_geometria(t)
                if "dar" in valinta:
                    t["koe"] = {"tila": "ei"}
        self._tallenna()

    def aloita(self, tid=None):
        with self.lukko:
            for t in self.tyot:
                if (tid is None or t["id"] == tid) and t["tila"] in ("odottaa", "keskeytetty", "virhe") \
                        and t.get("analyysi"):
                    t["tila"] = "jonossa"
                    t.pop("virhe", None)
        self._tallenna()
        self._herata.set()

    def keskeyta(self, tid=None):
        """Keskeyttää käsittelyn (valmiit osat säilyvät) ja poistaa jonosta odottavat."""
        with self.lukko:
            for t in self.tyot:
                if (tid is None or t["id"] == tid) and t["tila"] == "jonossa":
                    t["tila"] = "keskeytetty" if t.get("aloitettu") else "odottaa"
            if self._kasiteltava and (tid is None or tid == self._kasiteltava):
                self._peruuta.set()
        self._tallenna()

    def esikatsele(self, tid):
        with self.lukko:
            t = self._tyo(tid)
            if not t.get("analyysi") or t["tila"] == "hd":
                return
            if t["koe"].get("tila") in ("kaynnissa", "jonossa"):
                return
            t["koe"] = {"tila": "jonossa", "edistys": 0}
        self._koe_q.put(("koe", tid, None))

    def liike(self, tid, asetus, saato):
        with self.lukko:
            t = self._tyo(tid)
            t["liike"] = {"tila": "jonossa", "edistys": 0, "asetus": asetus, "saato": saato}
        self._koe_q.put(("liike", tid, (asetus, saato)))

    def saatokuva(self, tid, asetus, saato, k):
        with self.lukko:
            t = self._tyo(tid)
            an, geo, wd = t["analyysi"], t["geo"], self._wd(t)
        s = {kk: float(v) for kk, v in (saato or {}).items() if kk in m.SAATO_AVAIMET}
        L = self._laite("esikatselu")
        return m.saatokuva(Path(t["polku"]), an, geo, wd, L, int(asetus), s or None, int(k))

    def aseta_sammutus(self, paalla):
        self.sammuta_valmiina = bool(paalla)

    def peru_sammutus(self):
        if self.sammutus_ajastettu and os.name == "nt":
            subprocess.run(["shutdown", "/a"], capture_output=True)
        self.sammutus_ajastettu = False
        self.sammuta_valmiina = False

    def lopeta(self):
        self._lopeta = True
        self._peruuta.set()
        self._herata.set()
        self._koe_q.put(None)
        self._analyysi_q.put(None)
        self._tallenna()

    # ------------------------------------------------------------ tila käyttöliittymälle
    def tila(self):
        with self.lukko:
            tyot = []
            for t in self.tyot:
                d = {k: v for k, v in t.items()}
                an = t.get("analyysi")
                if an:
                    d["analyysi"] = {k: an.get(k) for k in (
                        "leveys", "korkeus", "koodekki", "kesto", "fps", "R", "lomitus", "desimointi",
                        "tyyppi", "suositus", "tekstityksia", "huomiot", "rajaus", "dar_levy")}
                tyot.append(d)
            jonossa = [t for t in self.tyot if t["tila"] in ("jonossa", "kasitellaan")]
            jaljella = 0.0
            for t in jonossa:
                e = t.get("edistys") or {}
                if t["tila"] == "kasitellaan" and e.get("jaljella_s"):
                    jaljella += e["jaljella_s"]
                elif t.get("arvio_nopeus") and t.get("analyysi"):
                    an = t["analyysi"]
                    from fractions import Fraction
                    jaljella += an["kesto"] * float(Fraction(an["R"])) / t["arvio_nopeus"]
            return {"tyot": tyot, "asetukset": self.asetukset, "kohde": list(self.kohde()),
                    "sammuta_valmiina": self.sammuta_valmiina, "sammutus_ajastettu": self.sammutus_ajastettu,
                    "jono_jaljella_s": jaljella if jonossa else None, "kasiteltava": self._kasiteltava}

    # ------------------------------------------------------------ säikeet
    def _analyysi_saie(self):
        while not self._lopeta:
            tid = self._analyysi_q.get()
            if tid is None:
                return
            try:
                self._analysoi(tid)
            except Exception:  # noqa: BLE001
                m.loki(f"Analyysisäie: {traceback.format_exc()}")

    def _analysoi(self, tid):
            try:
                with self.lukko:
                    t = self._tyo(tid)
                    polku, wd = Path(t["polku"]), self._wd(t)
                if not polku.exists():
                    raise RuntimeError("tiedostoa_ei_loydy")
                an = m.analysoi(polku, wd)
                with self.lukko:
                    t = self._tyo(tid)
                    t["analyysi"] = an
                    self._paivita_geometria(t)
                    if t["tila"] == "analysoidaan":
                        t["tila"] = "hd" if an["tyyppi"] == "hd" else "odottaa"
                    vanha = (t.get("koe") or {}).get("tunniste")
                    if vanha and vanha != m.koe_tunniste(t["geo"]):
                        t["koe"] = {"tila": "ei"}
            except KeyError:
                return
            except Exception as e:  # noqa: BLE001
                m.loki(f"Analyysi epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    try:
                        t = self._tyo(tid)
                        t["tila"] = "virhe"
                        t["virhe"] = {"avain": str(e) if str(e) in ("ffmpeg_puuttuu", "tiedostoa_ei_loydy")
                                      else "analyysi", "tieto": str(e)[-300:]}
                    except KeyError:
                        pass
            self._tallenna()

    def _seuraava(self):
        with self.lukko:
            for t in self.tyot:
                if t["tila"] == "jonossa":
                    return t
        return None

    def _gpu_saie(self):
        while not self._lopeta:
            self._herata.wait(2.0)
            self._herata.clear()
            try:
                self._kasittele_seuraava()
            except Exception:  # noqa: BLE001
                m.loki(f"Käsittelysäie: {traceback.format_exc()}")
                with self.lukko:
                    for t in self.tyot:
                        if t["tila"] == "kasitellaan":
                            t["tila"] = "virhe"
                            t["virhe"] = {"avain": "kasittely", "tieto": "sisäinen virhe"}
                    self._kasiteltava = None
                self._tallenna()

    def _kasittele_seuraava(self):
            if not self.ymp.valmis():
                return
            t = self._seuraava()
            if t is None:
                return
            tid = t["id"]
            self._peruuta.clear()
            with self.lukko:
                t["tila"] = "kasitellaan"
                t["aloitettu"] = t.get("aloitettu") or time.time()
                t["edistys"] = {"tehty": 0, "kaikki": 1}
                self._kasiteltava = tid
                polku, an, geo, valinta = Path(t["polku"]), t["analyysi"], t["geo"], dict(t.get("valinta") or {})
                wd = self._wd(t)
                ulos = m.ulostulo_polku(polku, self.asetukset.get("ulos_kansio"))
            self._tallenna()
            pk = wd / "pikkukuva.png"

            def edistys(e, t=t):
                with self.lukko:
                    t["edistys"] = e

            def pikkukuva(rgb):
                m.rgb_png(pk, rgb, leveys=640)
                with self.lukko:
                    t["pikkukuva"] = time.time()

            try:
                m.pysy_hereilla(True)
                if not polku.exists():
                    raise RuntimeError("tiedostoa_ei_loydy")
                L = self._laite("gpu")
                tulos = m.kasittele(polku, an, geo, valinta, wd, L, ulos, edistys=edistys,
                                    peruuta=self._peruuta, pikkukuva=pikkukuva)
                with self.lukko:
                    t["tila"] = "valmis"
                    t["tulos"] = tulos
                    t["valmistui"] = time.time()
                m.loki(f"Valmis: {tulos['ulos']}")
            except m.Peruutettu:
                with self.lukko:
                    if t in self.tyot:
                        t["tila"] = "keskeytetty"
            except Exception as e:  # noqa: BLE001
                m.loki(f"Käsittely epäonnistui ({polku}): {traceback.format_exc()}")
                with self.lukko:
                    t["tila"] = "virhe"
                    t["virhe"] = {"avain": str(e) if str(e) in ("ffmpeg_puuttuu", "tiedostoa_ei_loydy",
                                                                 "malli_puuttuu") else "kasittely",
                                  "tieto": str(e)[-600:]}
            finally:
                with self.lukko:
                    self._kasiteltava = None
                    t.pop("edistys", None) if t["tila"] != "keskeytetty" else None
                m.pysy_hereilla(False)
                self._tallenna()
            if self._seuraava() is None and self.sammuta_valmiina and t["tila"] == "valmis" and not self._lopeta:
                self._sammuta()
            self._herata.set()

    def _sammuta(self):
        self.sammutus_ajastettu = True
        m.loki("Jono valmis – kone sammutetaan 2 minuutin kuluttua.")
        if os.name == "nt":
            subprocess.run(["shutdown", "/s", "/t", "120", "/c", "PixeliRemaster: jono valmis"],
                           capture_output=True)

    def _koe_saie(self):
        while not self._lopeta:
            tehtava = self._koe_q.get()
            if tehtava is None:
                return
            try:
                self._esikatselu(*tehtava)
            except Exception:  # noqa: BLE001
                m.loki(f"Esikatselusäie: {traceback.format_exc()}")

    def _esikatselu(self, laji, tid, param):
            while not self.ymp.valmis() and not self._lopeta:
                time.sleep(1)
            try:
                with self.lukko:
                    t = self._tyo(tid)
                    polku, an, geo, wd = Path(t["polku"]), t["analyysi"], t["geo"], self._wd(t)
                    kohde = t["koe"] if laji == "koe" else t["liike"]
                    kohde["tila"] = "kaynnissa"
                L = self._laite("esikatselu")

                def ed(a, b, kohde=kohde):
                    with self.lukko:
                        kohde["edistys"] = a / max(1, b)

                if laji == "koe":
                    tiedot = m.koekuvat(polku, an, geo, wd, L, edistys=ed)
                    with self.lukko:
                        t["koe"] = {"tila": "valmis", "tunniste": tiedot["tunniste"],
                                    "nopeus": {str(k): v for k, v in tiedot["nopeus"].items()}}
                else:
                    asetus, saato = param
                    url = m.liikepatka(polku, an, geo, wd, L, int(asetus), saato or None, edistys=ed)
                    with self.lukko:
                        if t.get("liike") is kohde:
                            t["liike"] = {"tila": "valmis", "url": url, "asetus": asetus, "saato": saato}
            except KeyError:
                return
            except Exception as e:  # noqa: BLE001
                m.loki(f"Esikatselu epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    try:
                        t = self._tyo(tid)
                        (t["koe"] if laji == "koe" else t["liike"]).update(
                            {"tila": "virhe", "tieto": str(e)[-300:]})
                    except KeyError:
                        pass
            self._tallenna()
