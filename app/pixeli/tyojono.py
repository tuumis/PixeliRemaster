"""PixeliRemaster – työjono.

Videot ja kuvat kulkevat tiloissa:
  analysoidaan -> odottaa -> jonossa -> kasitellaan -> valmis
                                     \\-> keskeytetty (video jatkuu valmiista osista)
  virhe / hd (video jo valmiiksi HD-tarkkuutta, ei käsitellä)

Säikeet:
  analyysi  – lyhyt analyysi heti lisättäessä
  gpu       – videoiden käsittely, yksi kerrallaan
  koe       – esikatselukuvat, liikepätkät ja kuvien käsittely (oma Laite, toimii videon rinnalla,
              joten kuva ei jää odottamaan pitkää elokuvaa)
"""
import json
import os
import queue
import subprocess
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import kuvat
from . import moottori as m

TILAT_KESKEN = ("analysoidaan", "kasitellaan")
TUETUT = m.VIDEOPAATTEET | kuvat.KUVAPAATTEET


AJASTUS_OLETUS = {"paalla": False, "alku": "23:00", "loppu": "07:00"}


def _minuutit(hhmm):
    try:
        h, mm = str(hhmm).strip().split(":")
        h, mm = int(h), int(mm)
        if 0 <= h < 24 and 0 <= mm < 60:
            return h * 60 + mm
    except (ValueError, AttributeError):
        pass
    return None


def siisti_ajastus(a):
    a = a or {}
    alku = a.get("alku") if _minuutit(a.get("alku")) is not None else AJASTUS_OLETUS["alku"]
    loppu = a.get("loppu") if _minuutit(a.get("loppu")) is not None else ""
    if loppu and _minuutit(loppu) == _minuutit(alku):
        loppu = ""
    f = lambda x: "%02d:%02d" % divmod(_minuutit(x), 60) if x else ""  # noqa: E731
    return {"paalla": bool(a.get("paalla")), "alku": f(alku), "loppu": f(loppu)}


def ajastus_ikkunassa(a, nyt=None):
    """Onko kellonaika ajastusikkunassa [alku, loppu). Yön yli menevä ikkuna (23–07) tuettu.
    Ilman loppuaikaa ikkuna on auki alusta vuorokauden loppuun (käsittely jatkuu, kun se on alkanut)."""
    nyt = nyt or time.localtime()
    n = nyt.tm_hour * 60 + nyt.tm_min
    alku = _minuutit(a.get("alku"))
    loppu = _minuutit(a.get("loppu")) if a.get("loppu") else None
    if alku is None:
        return True
    if loppu is None:
        return n >= alku
    if alku < loppu:
        return alku <= n < loppu
    return n >= alku or n < loppu


def _uusi_id():
    return uuid.uuid4().hex[:10]


class Jono:
    def __init__(self, ymparisto):
        self.ymp = ymparisto
        self.lukko = threading.RLock()
        self.tyot = []
        self.asetukset = {"kohde": "auto", "ulos_kansio": None, "kieli": "auto", "teho": "taysi", "katselu": "oma",
                          "sulje": "ilmaisin", "puhelin_korkeus": 720, "ajastus": dict(AJASTUS_OLETUS),
                          "opensubtitles_avain": ""}
        self.sammuta_valmiina = False
        self.sammutus_ajastettu = False
        self._peruuta = threading.Event()
        self._peruuta_kuva = threading.Event()
        self._kasiteltava = None
        self._kasiteltava_kuva = None
        self._herata = threading.Event()
        self._analyysi_q = queue.Queue()
        self._koe_q = queue.Queue()
        self._laitteet = {}
        self._lopeta = False
        self._levy_q = queue.Queue()
        self._peruuta_levy = threading.Event()
        self._ripattava = None
        self._lataus_q = queue.Queue()
        self._peruuta_lataus = threading.Event()
        self._ladattava = None
        self._jalki_q = queue.Queue()
        self._peruuta_jalki = threading.Event()
        self._jalkityo = None
        self._ajastus_pysaytti = False
        self._ajastus_kaynnissa = False
        self._lataa()
        for nimi, kohde in (("analyysi", self._analyysi_saie), ("gpu", self._gpu_saie), ("koe", self._koe_saie),
                            ("levy", self._levy_saie), ("lataus", self._lataus_saie), ("jalki", self._jalki_saie),
                            ("ajastus", self._ajastus_saie)):
            threading.Thread(target=kohde, name=nimi, daemon=True).start()
        for t in self.tyot:
            if t["tila"] == "ripataan":          # keskeytynyt rippaus aloitetaan alusta
                t.setdefault("levy", {})["edistys"] = 0.0
                self._levy_q.put(t["id"])
                continue
            for laji, jt in (t.get("jalki") or {}).items():    # kesken jääneet jälkityöt uudelleen
                if jt.get("tila") in ("jonossa", "tehdaan"):
                    jt.update({"tila": "jonossa", "edistys": 0.0})
                    self._jalki_q.put((t["id"], laji))
            if t["tila"] == "ladataan":          # keskeytynyt lataus jatkuu (yt-dlp jatkaa osittaisesta)
                t.setdefault("lataus", {})["edistys"] = 0.0
                self._lataus_q.put(t["id"])
                continue
            if t["tila"] == "analysoidaan" or (t["tila"] not in ("valmis", "virhe") and not t.get("analyysi")):
                t["tila"] = "analysoidaan"
                self._analyysi_q.put(t["id"])

    # ------------------------------------------------------------ pysyvyys
    def _tiedosto(self):
        return m.DATA / "jono.json"

    def _lataa(self):
        a = m.lue_json(m.DATA / "asetukset.json", {}) or {}
        self.asetukset.update({k: v for k, v in a.items() if k in self.asetukset})
        m.aseta_teho(self.asetukset.get("teho"))
        d = m.lue_json(self._tiedosto(), {}) or {}
        for t in d.get("tyot", []):
            if t.get("tila") == "kasitellaan" or t.get("tila") == "jonossa":
                # ajastettu jono odottaa edelleen ajastusta, muuten käyttäjä jatkaa itse
                ajastettu = (self.asetukset.get("ajastus") or {}).get("paalla")
                t["tila"] = "odottaa" if t.get("laji") == "kuva" else "jonossa" if ajastettu else "keskeytetty"
            t["koe"] = {k: v for k, v in (t.get("koe") or {}).items() if k != "tila"} | {"tila": (
                "valmis" if (t.get("koe") or {}).get("tunniste") else "ei")}
            t["koe"].pop("siirretaan", None)
            t["liike"] = {}
            t.pop("edistys", None)
            # uusi versio muuttaa esikatselun tunnisteen: vanhat esikatselut eivät enää vastaa asetuksia
            try:
                if t.get("geo") and t["koe"].get("tunniste") and t["koe"]["tunniste"] != self._koe_tunniste(t):
                    t["koe"] = {"tila": "ei"}
            except Exception:  # noqa: BLE001
                t["koe"] = {"tila": "ei"}
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
            if "katselu" in uudet and uudet["katselu"] not in ("oma", "rtx"):
                uudet = dict(uudet, katselu="oma")
            for k in ("kohde", "ulos_kansio", "kieli", "teho", "katselu"):
                if k in uudet:
                    self.asetukset[k] = uudet[k]
            if "sulje" in uudet:
                self.asetukset["sulje"] = "lopeta" if uudet["sulje"] == "lopeta" else "ilmaisin"
            if "puhelin_korkeus" in uudet:
                self.asetukset["puhelin_korkeus"] = 1080 if int(uudet["puhelin_korkeus"] or 720) == 1080 else 720
            if "opensubtitles_avain" in uudet:
                self.asetukset["opensubtitles_avain"] = str(uudet["opensubtitles_avain"] or "").strip()[:64]
            if "ajastus" in uudet:
                self.asetukset["ajastus"] = siisti_ajastus(uudet["ajastus"])
                self._herata.set()
            if "teho" in uudet:
                m.aseta_teho(self.asetukset["teho"])
                self.asetukset["teho"] = m._TEHO["taso"]
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
        if t.get("laji") == "kuva":
            t["geo"] = kuvat.geometria_kuva(an, t.get("valinta") or {}, self.kohde())
            t["arvio_nopeus"] = None
            return
        geo = m.geometria(an, t.get("valinta") or {}, self.kohde())
        t["geo"] = geo
        n = int((t.get("valinta") or {}).get("asetus") or an["suositus"])
        saato = (t.get("valinta") or {}).get("saato") or ((t.get("koe") or {}).get("oletukset") or {}).get(str(n)) or {}
        t["arvio_nopeus"] = m.arvioi_nopeus(an, geo, n, float(saato.get("kohina", 0) or 0) > 0)

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
            if p.is_dir():   # kansio alikansioineen (esim. sarjan kaudet)
                tiedostot += sorted((f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in TUETUT
                                     and "[Remaster]" not in f.name and "[AI-skaalattu]" not in f.name),
                                    key=lambda f: [f.parent.as_posix().lower(), f.name.lower()])
            elif p.is_file():
                tiedostot.append(p)
        with self.lukko:
            olemassa = {os.path.normcase(str(Path(t["polku"]).resolve())) for t in self.tyot}
            for f in tiedostot:
                avain = os.path.normcase(str(f.resolve()))
                if f.suffix.lower() not in TUETUT or "[Remaster]" in f.name:
                    ohitetut.append({"nimi": f.name, "syy": "ei_tuettu" if f.suffix.lower() not in TUETUT
                                     else "oma_tulos"})
                    continue
                if avain in olemassa:
                    ohitetut.append({"nimi": f.name, "syy": "jo_jonossa"})
                    continue
                t = {"id": _uusi_id(), "polku": str(f.resolve()), "nimi": f.name, "lisatty": time.time(),
                     "laji": "kuva" if kuvat.on_kuva(f) else "video",
                     "tila": "analysoidaan", "valinta": {}, "koe": {"tila": "ei"}, "liike": {}}
                self.tyot.append(t)
                olemassa.add(avain)
                lisatyt.append(t["id"])
                self._analyysi_q.put(t["id"])
        self._tallenna()
        return {"lisatyt": lisatyt, "ohitetut": ohitetut}

    @staticmethod
    def oletus_levykansio():
        return Path.home() / "Videos" / "PixeliRemaster"

    @staticmethod
    def kansio_saatavilla(k):
        """Onko kansio käytettävissä (esim. ulkoinen asema kiinni) ja voiko sinne kirjoittaa."""
        if not k:
            return False
        p = Path(k)
        try:
            if p.anchor and not Path(p.anchor).exists():
                return False
            p.mkdir(parents=True, exist_ok=True)
            testi = p / ".pixeli_kirjoitustesti"
            testi.write_bytes(b"")
            testi.unlink()
            return True
        except OSError:
            return False

    def levykansio(self):
        """Levyltä kopioidut elokuvat: valittu tallennuskansio tai Videot\\PixeliRemaster."""
        k = self.asetukset.get("ulos_kansio")
        return Path(k) if k else self.oletus_levykansio()

    def levykansio_tiedot(self):
        """Levyltä-ikkunalle: asetuksissa valittu kansio, onko se nyt käytettävissä ja mihin tallennetaan."""
        k = self.asetukset.get("ulos_kansio")
        ok = self.kansio_saatavilla(k) if k else True
        return {"asetettu": k, "saatavilla": ok, "oletus": str(self.oletus_levykansio()),
                "kansio": str(self.levykansio() if ok else self.oletus_levykansio())}

    def lisaa_levylta(self, indeksi, nimike, levyn_nimi, kesto=0, kasittele=True, sailyta=True, asema="",
                      tapa="makemkv", koko=0, kansio=None):
        """Levyn nimike kopioidaan jonoon. tapa "oma" = DVD soittimen kirjastoilla, "makemkv" = MakeMKV."""
        from . import levy
        nimi = levy.turvallinen_nimi(levyn_nimi)
        with self.lukko:
            t = {"id": _uusi_id(), "polku": "", "nimi": nimi + ".mkv", "lisatty": time.time(), "laji": "video",
                 "tila": "ripataan", "valinta": {}, "koe": {"tila": "ei"}, "liike": {},
                 "levy": {"indeksi": None if indeksi is None else int(indeksi), "nimike": int(nimike), "nimi": nimi,
                          "kesto": kesto, "asema": str(asema or ""), "tapa": "oma" if tapa == "oma" else "makemkv",
                          "koko": int(koko or 0), "kasittele": bool(kasittele), "sailyta": bool(sailyta),
                          "kansio": str(kansio) if kansio else None,
                          "edistys": 0.0}}
            self.tyot.append(t)
        self._levy_q.put(t["id"])
        self._tallenna()
        return {"id": t["id"]}

    def _levy_saie(self):
        from . import levy
        while not self._lopeta:
            tid = self._levy_q.get()
            if tid is None:
                return
            try:
                with self.lukko:
                    t = self._tyo(tid)
                    if t["tila"] != "ripataan":
                        continue
                    lv = t["levy"]
                    self._ripattava = tid
                self._peruuta_levy.clear()
                kansio = Path(lv.get("kansio") or self.levykansio())
                if not self.kansio_saatavilla(kansio):
                    # esim. ulkoinen asema irrotettu: kopio oletuskansioon, ettei levyn kopiointi epäonnistu
                    m.loki(f"Tallennuskansio {kansio} ei ole käytettävissä, kopioidaan kansioon "
                           f"{self.oletus_levykansio()}")
                    kansio = self.oletus_levykansio()
                    with self.lukko:
                        lv["kansio_vaihtui"] = str(lv.get("kansio") or self.levykansio())
                        lv["kansio"] = str(kansio)
                tmp = kansio / f".pixeli_rippaus_{tid}"

                def ed(f, lv=lv):
                    with self.lukko:
                        lv["edistys"] = round(min(1.0, max(0.0, f)), 4)
                alku = time.time()
                if lv.get("tapa") == "oma":
                    mkv = levy.rippaa_dvd(lv["asema"], lv["nimike"], lv.get("kesto"), tmp, ed, self._peruuta_levy,
                                          arvio=lv.get("koko") or 0)
                else:
                    mkv = levy.rippaa(lv["indeksi"], lv["nimike"], tmp, ed, self._peruuta_levy)
                kohde = kansio / f"{lv['nimi']}.mkv"
                n = 2
                while kohde.exists():
                    kohde = kansio / f"{lv['nimi']} ({n}).mkv"
                    n += 1
                mkv.replace(kohde)
                try:
                    for q in tmp.iterdir():
                        q.unlink()
                    tmp.rmdir()
                except OSError:
                    pass
                m.loki(f"Levyltä kopioitu {kohde} ({time.time() - alku:.0f} s)")
                with self.lukko:
                    t["polku"] = str(kohde)
                    t["nimi"] = kohde.name
                    t["tila"] = "analysoidaan"
                    lv.pop("edistys", None)
                self._analyysi_q.put(tid)
            except KeyError:
                pass
            except m.Peruutettu:
                with self.lukko:
                    if t in self.tyot and not self._lopeta:      # suljettaessa jatketaan seuraavalla kerralla
                        self.tyot.remove(t)
            except Exception as e:  # noqa: BLE001
                m.loki(f"Levyn kopiointi epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    t["tila"] = "virhe"
                    t["virhe"] = {"avain": "makemkv_puuttuu" if str(e) == "makemkv_puuttuu" else
                                  "rippaus_dvd" if lv.get("tapa") == "oma" else "rippaus",
                                  "tieto": str(e)[-400:]}
            finally:
                with self.lukko:
                    self._ripattava = None
                self._tallenna()

    # ------------------------------------------------------------ lataus verkosta (YouTube ym.)
    def lisaa_linkista(self, url, tiedot, heti=True, tekstit=True, kansio=None):
        """Video linkistä jonoon. heti=True: remasterointi alkaa latauksen ja analyysin jälkeen suositellulla
        asetuksella; muuten video jää odottamaan asetuksen valintaa. Tallennus asetusten kansioon."""
        from . import levy
        tiedot = tiedot or {}
        nimi = levy.turvallinen_nimi(tiedot.get("nimi") or "Video", "Video")
        with self.lukko:
            t = {"id": _uusi_id(), "polku": "", "nimi": nimi + ".mkv", "lisatty": time.time(), "laji": "video",
                 "tila": "ladataan", "valinta": {}, "koe": {"tila": "ei"}, "liike": {},
                 "lataus": {"url": str(url), "nimi": nimi, "kanava": tiedot.get("kanava") or "",
                            "sivu": tiedot.get("sivu") or "", "kesto": float(tiedot.get("kesto") or 0),
                            "korkeus": int(tiedot.get("korkeus") or 0), "osat": tiedot.get("osat") or [],
                            "kasittele": bool(heti) and int(tiedot.get("korkeus") or 0) < 720,
                            "tekstit": bool(tekstit), "kansio": str(kansio) if kansio else None, "edistys": 0.0}}
            self.tyot.append(t)
        self._lataus_q.put(t["id"])
        self._tallenna()
        return {"id": t["id"]}

    def _latauskansio(self, t):
        lt = t.get("lataus") or {}
        return Path(lt.get("kansio") or self.levykansio()) / f".pixeli_lataus_{t['id']}"

    def _poista_latauskansio(self, t):
        shutil.rmtree(self._latauskansio(t), ignore_errors=True)

    def _lataus_saie(self):
        from . import lataus
        while not self._lopeta:
            tid = self._lataus_q.get()
            if tid is None:
                return
            t = None
            try:
                with self.lukko:
                    t = self._tyo(tid)
                    if t["tila"] != "ladataan":
                        continue
                    lt = t["lataus"]
                    self._ladattava = tid
                self._peruuta_lataus.clear()
                kansio = Path(lt.get("kansio") or self.levykansio())
                if not self.kansio_saatavilla(kansio):
                    m.loki(f"Tallennuskansio {kansio} ei ole käytettävissä, ladataan kansioon "
                           f"{self.oletus_levykansio()}")
                    with self.lukko:
                        lt["kansio_vaihtui"] = str(kansio)
                        lt["kansio"] = str(self.oletus_levykansio())
                    kansio = self.oletus_levykansio()
                with self.lukko:
                    lt["kansio"] = str(kansio)
                tmp = self._latauskansio(t)

                def ed(f, lt=lt):
                    with self.lukko:
                        lt["edistys"] = round(min(1.0, max(0.0, f)), 4)
                alku = time.time()
                valmis = lataus.lataa(lt["url"], tmp, lt.get("osat"), lt.get("tekstit", True), ed,
                                      self._peruuta_lataus)
                kohde = kansio / valmis.name
                n = 2
                while kohde.exists():
                    kohde = kansio / f"{valmis.stem} ({n}){valmis.suffix}"
                    n += 1
                valmis.replace(kohde)
                shutil.rmtree(tmp, ignore_errors=True)
                m.loki(f"Ladattu {lt['url']} -> {kohde} ({time.time() - alku:.0f} s)")
                with self.lukko:
                    t["polku"] = str(kohde)
                    t["nimi"] = kohde.name
                    t["tila"] = "analysoidaan"
                    lt.pop("edistys", None)
                self._analyysi_q.put(tid)
            except KeyError:
                pass
            except m.Peruutettu:
                with self.lukko:
                    if t is not None and t in self.tyot and not self._lopeta:
                        self.tyot.remove(t)
                if t is not None and not self._lopeta:
                    self._poista_latauskansio(t)              # suljettaessa osittainen lataus jatkuu myöhemmin
            except Exception as e:  # noqa: BLE001
                m.loki(f"Lataus epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    if t is not None:
                        t["tila"] = "virhe"
                        t["virhe"] = {"avain": "lataus_" + str(getattr(e, "avain", "lataus")),
                                      "tieto": (getattr(e, "tieto", "") or str(e))[-500:]}
            finally:
                with self.lukko:
                    self._ladattava = None
                self._tallenna()

    def poista(self, tid):
        with self.lukko:
            t = self._tyo(tid)
            if self._kasiteltava == tid:
                self._peruuta.set()
            if self._kasiteltava_kuva == tid:
                self._peruuta_kuva.set()
            if self._ripattava == tid:
                self._peruuta_levy.set()
            if self._ladattava == tid:
                self._peruuta_lataus.set()
            if self._jalkityo == tid:
                self._peruuta_jalki.set()
            elif t["tila"] == "ladataan":
                self._poista_latauskansio(t)
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
            if "skaala" in valinta:
                v["skaala"] = valinta["skaala"] if valinta["skaala"] in kuvat.SKAALAT else None
            if "kierto" in valinta:
                v["kierto"] = int(valinta["kierto"]) % 360 if int(valinta["kierto"] or 0) % 360 in kuvat.KIERROT else None
                v["kierto"] = v["kierto"] or None
            if "muoto" in valinta:
                v["muoto"] = valinta["muoto"] if valinta["muoto"] in ("jpg", "png") else None
            if "laatu" in valinta:
                v["laatu"] = "pieni" if valinta["laatu"] == "pieni" else None
            for k in ("aanet", "tekstit"):        # None = kaikki raidat
                if k in valinta:
                    v[k] = sorted({int(i) for i in valinta[k]}) if valinta[k] is not None else None
            if "ulkoiset" in valinta:
                v["ulkoiset"] = False if valinta["ulkoiset"] is False else None
            for k in ("naarmut", "vari"):          # vanhan kuvan korjaukset
                if k in valinta:
                    v[k] = True if valinta[k] else None
            if "aanipuhdistus" in valinta:
                v["aanipuhdistus"] = valinta["aanipuhdistus"] if valinta["aanipuhdistus"] in m.AANIPUHDISTUS else None
            for k in ("vertailu", "puhelin"):      # jälkityöt valmiista videosta: tallennetaan vain valinta
                if k in valinta:
                    v[k] = True if valinta[k] else None
            for k in ("puhe", "ocr"):              # oletuksena päällä: tallennetaan vain poisvalinta
                if k in valinta:
                    v[k] = False if valinta[k] is False else None
            t["valinta"] = {k: x for k, x in v.items() if x is not None}
            if t.get("analyysi"):
                vanha = (t.get("koe") or {}).get("tunniste")
                self._paivita_geometria(t)
                if any(k in valinta for k in ("dar", "skaala", "kierto", "naarmut", "vari")) \
                        and vanha != self._koe_tunniste(t):
                    t["koe"] = {"tila": "ei"}
        self._tallenna()

    def valinta_kaikkiin(self, tid):
        """Kopioi tiedoston asetuksen (ja käyttäjän säädöt) kaikkiin saman lajin käsittelemättömiin.
        Säätämättömät tiedostot saavat edelleen omaan laatuunsa sovitetut oletukset."""
        with self.lukko:
            lahde = self._tyo(tid)
            v = dict(lahde.get("valinta") or {})
            if not v.get("asetus") and lahde.get("analyysi"):
                v["asetus"] = lahde["analyysi"]["suositus"]
            kopio = {k: v.get(k) for k in ("asetus", "saato", "skaala", "muoto", "laatu", "naarmut", "vari", "puhe",
                                           "ocr", "aanipuhdistus", "vertailu", "puhelin")}
            n = 0
            for t in self.tyot:
                if t is lahde or t.get("laji") != lahde.get("laji") or t["tila"] not in ("odottaa", "keskeytetty", "virhe"):
                    continue
                if t["tila"] == "keskeytetty" and t.get("aloitettu"):
                    continue          # osittain käsiteltyä ei muuteta (osat on tehty vanhoilla asetuksilla)
                uusi = dict(t.get("valinta") or {})
                uusi.update({k: x for k, x in kopio.items()
                             if k not in ("skaala", "muoto", "naarmut", "vari") or t.get("laji") == "kuva"})
                vanha = (t.get("koe") or {}).get("tunniste")
                t["valinta"] = {k: x for k, x in uusi.items() if x is not None}
                if t.get("analyysi"):
                    self._paivita_geometria(t)
                    if t.get("laji") == "kuva" and vanha != self._koe_tunniste(t):
                        t["koe"] = {"tila": "ei"}
                n += 1
        self._tallenna()
        return n

    def aloita(self, tid=None):
        with self.lukko:
            for t in self.tyot:
                # valmiin kuvan voi käsitellä uudelleen toisilla asetuksilla (vain yksittäin)
                uudelleen = tid is not None and t.get("laji") == "kuva" and t["tila"] == "valmis"
                if (tid is None or t["id"] == tid) and (t["tila"] in ("odottaa", "keskeytetty", "virhe") or uudelleen) \
                        and t.get("analyysi"):
                    t["tila"] = "jonossa"
                    t.pop("virhe", None)
                    t.pop("tulos", None)
                    if t.get("laji") == "kuva":
                        self._koe_q.put(("kuva", t["id"], None))
        self._tallenna()
        self._herata.set()

    def keskeyta(self, tid=None):
        """Keskeyttää käsittelyn (valmiit osat säilyvät) ja poistaa jonosta odottavat."""
        with self.lukko:
            for t in self.tyot:
                if (tid is None or t["id"] == tid) and t["tila"] == "jonossa":
                    t["tila"] = "keskeytetty" if t.get("aloitettu") and t.get("laji") != "kuva" else "odottaa"
            if self._kasiteltava and (tid is None or tid == self._kasiteltava):
                self._peruuta.set()
            if self._kasiteltava_kuva and (tid is None or tid == self._kasiteltava_kuva):
                self._peruuta_kuva.set()
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

    def alue(self, tid, k, x, y):
        """Kuvan esikatselualueen siirto (yleiskuvasta valittu kohta)."""
        with self.lukko:
            t = self._tyo(tid)
            if t.get("laji") != "kuva" or (t.get("koe") or {}).get("tila") != "valmis":
                return
            t["koe"]["siirretaan"] = int(k)
        self._koe_q.put(("alue", tid, (int(k), float(x), float(y))))

    def kohta(self, tid, k, aika):
        """Videon esikatselukohdan siirto (aikajanalta valittu kohta, sekunteina)."""
        with self.lukko:
            t = self._tyo(tid)
            if t.get("laji") == "kuva" or (t.get("koe") or {}).get("tila") != "valmis":
                return
            t["koe"]["siirretaan"] = int(k)
        self._koe_q.put(("kohta", tid, (int(k), float(aika))))

    def _siirra_kohta(self, tid, k, aika):
        try:
            with self.lukko:
                t = self._tyo(tid)
                polku, an, geo, wd = Path(t["polku"]), t["analyysi"], t["geo"], self._wd(t)
                koe = t["koe"]
            kohdat = m.siirra_kohta(polku, an, geo, wd, self._laite("esikatselu"), k, aika)
            with self.lukko:
                if t.get("koe") is koe:
                    koe["kohdat"] = kohdat
                    koe["versio"] = int(time.time() * 1000)
        except KeyError:
            return
        except Exception:  # noqa: BLE001
            m.loki(f"Esikatselukohdan siirto epäonnistui: {traceback.format_exc()}")
        finally:
            with self.lukko:
                try:
                    self._tyo(tid).get("koe", {}).pop("siirretaan", None)
                except KeyError:
                    pass
        self._tallenna()

    def saatokuva(self, tid, asetus, saato, k):
        with self.lukko:
            t = self._tyo(tid)
            an, geo, wd = t["analyysi"], t["geo"], self._wd(t)
        s = {kk: float(v) for kk, v in (saato or {}).items() if kk in m.SAATO_AVAIMET}
        L = self._laite("esikatselu")
        if t.get("laji") == "kuva":
            return kuvat.saatokuva_kuva(Path(t["polku"]), an, geo, wd, L, int(asetus), s or None, int(k))
        return m.saatokuva(Path(t["polku"]), an, geo, wd, L, int(asetus), s or None, int(k))

    def aseta_sammutus(self, paalla):
        self.sammuta_valmiina = bool(paalla)

    def peru_sammutus(self):
        if self.sammutus_ajastettu and os.name == "nt":
            subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=m.PIILOTA)
        self.sammutus_ajastettu = False
        self.sammuta_valmiina = False

    def lopeta(self):
        self._lopeta = True
        self._peruuta.set()
        self._peruuta_kuva.set()
        self._herata.set()
        self._peruuta_levy.set()
        self._peruuta_lataus.set()
        self._lataus_q.put(None)
        self._peruuta_jalki.set()
        self._jalki_q.put(None)
        self._koe_q.put(None)
        self._analyysi_q.put(None)
        self._levy_q.put(None)
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
                        "tyyppi", "suositus", "tekstityksia", "huomiot", "rajaus", "dar_levy",
                        "laji", "muoto", "laatu", "alfa", "aaniraidat", "tekstiraidat", "harmaa")}
                d.setdefault("laji", "video")
                tyot.append(d)
            jonossa = [t for t in self.tyot if t["tila"] in ("jonossa", "kasitellaan")]
            jaljella = 0.0
            for t in jonossa:
                e = t.get("edistys") or {}
                if t["tila"] == "kasitellaan" and e.get("jaljella_s"):
                    jaljella += e["jaljella_s"]
                elif t.get("laji") == "kuva":
                    n = str((t.get("valinta") or {}).get("asetus") or (t.get("analyysi") or {}).get("suositus"))
                    jaljella += ((t.get("koe") or {}).get("aika") or {}).get(n) or 5.0
                elif t.get("analyysi"):
                    jaljella += self.arvioitu_aika(t) or 0.0
            return {"tyot": tyot, "asetukset": self.asetukset, "kohde": list(self.kohde()),
                    "sammuta_valmiina": self.sammuta_valmiina, "sammutus_ajastettu": self.sammutus_ajastettu,
                    "jono_jaljella_s": jaljella if jonossa else None, "kasiteltava": self._kasiteltava,
                    "ajastus": self.ajastus_tila(),
                    "kasiteltava_kuva": self._kasiteltava_kuva}

    # ------------------------------------------------------------ jälkityöt (ennen/jälkeen-pätkä, puhelinversio)
    def jalkityo(self, tid, laji):
        if laji not in ("vertailu", "puhelin"):
            raise KeyError("tuntematon_jalkityo")
        with self.lukko:
            t = self._tyo(tid)
            if t["tila"] != "valmis" or t.get("laji") == "kuva":
                raise RuntimeError("ei_valmis")
            jt = t.setdefault("jalki", {}).get(laji) or {}
            if jt.get("tila") in ("jonossa", "tehdaan"):
                return dict(jt)
            t["jalki"][laji] = {"tila": "jonossa", "edistys": 0.0}
        self._jalki_q.put((tid, laji))
        self._tallenna()
        return {"tila": "jonossa"}

    def _jalki_saie(self):
        from . import jalki
        while not self._lopeta:
            tyo = self._jalki_q.get()
            if tyo is None:
                return
            tid, laji = tyo
            try:
                with self.lukko:
                    t = self._tyo(tid)
                    jt = (t.get("jalki") or {}).get(laji)
                    if not jt or jt.get("tila") != "jonossa":
                        continue
                    jt["tila"] = "tehdaan"
                    self._jalkityo = tid
                    tk = json.loads(json.dumps({k: t.get(k) for k in ("id", "polku", "analyysi", "geo", "tulos")}))
                    wd = self._wd(t)
                    korkeus = int(self.asetukset.get("puhelin_korkeus") or 720)
                    kieli = self.asetukset.get("kieli") if self.asetukset.get("kieli") in ("fi", "en") else "fi"
                self._peruuta_jalki.clear()

                def ed(f, jt=jt):
                    with self.lukko:
                        jt["edistys"] = round(min(1.0, max(0.0, f)), 3)
                polku = jalki.aja_tyo(tk, wd, laji, korkeus, ed, self._peruuta_jalki, kieli)
                with self.lukko:
                    jt.update({"tila": "valmis", "polku": str(polku), "edistys": 1.0, "valmistui": time.time()})
            except KeyError:
                pass
            except m.Peruutettu:
                with self.lukko:
                    if not self._lopeta and (t := next((x for x in self.tyot if x["id"] == tid), None)):
                        (t.get("jalki") or {}).pop(laji, None)
            except Exception as e:  # noqa: BLE001
                m.loki(f"Jälkityö {laji} epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    t = next((x for x in self.tyot if x["id"] == tid), None)
                    if t and (t.get("jalki") or {}).get(laji):
                        t["jalki"][laji].update({"tila": "virhe", "virhe": str(e)[-400:]})
            finally:
                with self.lukko:
                    self._jalkityo = None
                self._tallenna()
                if self._kaikki_valmiit() and self.sammuta_valmiina and not self._lopeta \
                        and not self.sammutus_ajastettu and self._kasiteltava is None:
                    self._sammuta()

    # ------------------------------------------------------------ tekstitykset netistä (OpenSubtitles)
    def tekstihaku_ehdotus(self, tid):
        from . import tekstihaku
        with self.lukko:
            t = self._tyo(tid)
            nimi = (t.get("levy") or {}).get("nimi") or (t.get("lataus") or {}).get("nimi") or t.get("nimi") or ""
        kysely, vuosi = tekstihaku.ehdotettu_haku(nimi)
        return {"kysely": kysely, "vuosi": vuosi}

    def hae_tekstit(self, tid, kysely, kieli="fi", vuosi=None):
        from . import tekstihaku
        with self.lukko:
            t = self._tyo(tid)
            polku = t.get("polku")
        return tekstihaku.hae(self.asetukset, kysely, kieli, vuosi, polku)

    def lisaa_nettiteksti(self, tid, tieto, kieli="fi"):
        """Lataa valitun tekstityksen. Käsittelemättömään videoon se tulee lopulliseen tiedostoon,
        valmiiseen se lisätään heti."""
        from fractions import Fraction
        from . import tekstihaku
        kieli = kieli if kieli in tekstihaku.KIELET else "fi"
        with self.lukko:
            t = self._tyo(tid)
            if t["tila"] == "kasitellaan":
                raise RuntimeError("kasittely_kaynnissa")
            wd = self._wd(t)
            try:
                fps = float(Fraction((t.get("analyysi") or {}).get("fps") or "0"))
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        fid = int(tieto["file_id"])
        r = tekstihaku.lataa(self.asetukset, fid, wd / "nettitekstit" / f"{fid}.srt", fps, tieto.get("fps"))
        k3, fi_nimi, en_nimi = tekstihaku.KIELET[kieli]
        merkinta = {"file_id": fid, "srt": r["polku"], "kieli": k3, "nimi": str(tieto.get("nimi") or "")[:200],
                    "otsikko": f"{fi_nimi if self.asetukset.get('kieli') != 'en' else en_nimi} (OpenSubtitles)",
                    "kerroin": r["kerroin"]}
        with self.lukko:
            t = self._tyo(tid)
            valmis = t["tila"] == "valmis" and (t.get("tulos") or {}).get("ulos")
            if valmis:
                tekstihaku.lisaa_valmiiseen(t["tulos"]["ulos"], r["polku"], k3, merkinta["otsikko"])
                merkinta["valmiiseen"] = True
                t.setdefault("tulos", {}).setdefault("nettitekstit", []).append(merkinta)
            else:
                v = t.setdefault("valinta", {})
                v["nettitekstit"] = [x for x in v.get("nettitekstit") or [] if x.get("file_id") != fid] + [merkinta]
        self._tallenna()
        return {"jaljella": r.get("jaljella"), "nollaus": r.get("nollaus"), "kerroin": r["kerroin"],
                "valmiiseen": bool(valmis)}

    def poista_nettiteksti(self, tid, file_id):
        with self.lukko:
            t = self._tyo(tid)
            v = t.get("valinta") or {}
            v["nettitekstit"] = [x for x in v.get("nettitekstit") or [] if x.get("file_id") != int(file_id)]
            if not v["nettitekstit"]:
                v.pop("nettitekstit")
        self._tallenna()

    # ------------------------------------------------------------ ajastus
    def ajastus_sallii(self, nyt=None):
        """Saako videoiden käsittely edetä nyt (ajastus pois tai ajastusikkuna auki)."""
        a = self.asetukset.get("ajastus") or {}
        if not a.get("paalla"):
            return True
        if self._ajastus_kaynnissa and not a.get("loppu"):
            return True                          # ilman loppuaikaa jatketaan, kunnes jono on tyhjä
        return ajastus_ikkunassa(a, nyt)

    def ajastus_tila(self):
        a = dict(self.asetukset.get("ajastus") or AJASTUS_OLETUS)
        jonossa = any(t["tila"] == "jonossa" and t.get("laji") != "kuva" for t in self.tyot)
        a["odottaa"] = bool(a.get("paalla") and jonossa and not self.ajastus_sallii() and not self._kasiteltava)
        return a

    def _ajastus_saie(self):
        """Ajastusikkunan alku ja loppu: käsittely käynnistyy tai pysähtyy hallitusti (valmiit osat säilyvät).
        Kone pidetään hereillä, kun käsittelyä odottaa ajastettu alku."""
        hereilla = False
        while not self._lopeta:
            time.sleep(15)
            try:
                a = self.asetukset.get("ajastus") or {}
                with self.lukko:
                    jonossa = any(t["tila"] in ("jonossa", "kasitellaan") and t.get("laji") != "kuva"
                                  for t in self.tyot)
                if not a.get("paalla"):
                    self._ajastus_kaynnissa = False
                elif ajastus_ikkunassa(a):
                    if jonossa and not self._ajastus_kaynnissa:
                        m.loki("Ajastettu käsittely alkaa")
                        self._ajastus_kaynnissa = True
                    self._herata.set()
                elif a.get("loppu") and self._kasiteltava and not self._ajastus_pysaytti:
                    m.loki("Ajastuksen loppu: käsittely pysähtyy ja jatkuu seuraavassa ajastusikkunassa")
                    self._ajastus_pysaytti = True
                    self._ajastus_kaynnissa = False
                    self._peruuta.set()
                if not jonossa:
                    self._ajastus_kaynnissa = False
                odottaa = bool(a.get("paalla") and jonossa and not self._kasiteltava)
                if odottaa != hereilla:
                    m.pysy_hereilla(odottaa)
                    hereilla = odottaa
            except Exception:  # noqa: BLE001
                m.loki(f"Ajastus: {traceback.format_exc()}")

    # ------------------------------------------------------------ aika-arvio
    @staticmethod
    def ennuste_nopeus(t, n):
        """Esikatselussa mitattu nopeus (kuvaa/s täydellä teholla) tai itsetestin arvio; sama kuin näkymässä."""
        nop = ((t.get("koe") or {}).get("nopeus") or {}).get(str(n))
        if nop:
            return float(nop) * 1.15            # erissä hieman nopeampi kuin yksittäin mitattuna
        an = t.get("analyysi") or {}
        if t.get("arvio_nopeus") and int(n) == int((t.get("valinta") or {}).get("asetus") or an.get("suositus") or 2):
            return float(t["arvio_nopeus"])
        return None

    def arvioitu_aika(self, t, n=None):
        """Videon käsittelyn arvioitu kesto sekunteina toteutuneilla korjauksilla ja viimeistelyllä."""
        from fractions import Fraction
        an = t.get("analyysi") or {}
        n = int(n or (t.get("valinta") or {}).get("asetus") or an.get("suositus") or 2)
        nop = self.ennuste_nopeus(t, n)
        if not nop or not an.get("kesto"):
            return None
        k = m.aika_korjaus()
        suhde = float(k["suhde"].get(str(n)) or 1.0)
        return (an["kesto"] * float(Fraction(an["R"])) / (nop * suhde * m.teho_osuus())
                + k["loppu_s_min"] * an["kesto"] / 60)

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
                    laji = t.get("laji") or ("kuva" if kuvat.on_kuva(polku) else "video")
                if not polku.exists():
                    raise RuntimeError("tiedostoa_ei_loydy")
                an = kuvat.analysoi_kuva(polku, wd) if laji == "kuva" else m.analysoi(polku, wd)
                ulkoiset = [] if laji == "kuva" else [p.name for p in m.ulkoiset_tekstitykset(polku)]
                with self.lukko:
                    t = self._tyo(tid)
                    t["laji"] = laji
                    t["analyysi"] = an
                    t["ulkoiset_tekstit"] = ulkoiset
                    self._paivita_geometria(t)
                    if t["tila"] == "analysoidaan":
                        t["tila"] = "hd" if an["tyyppi"] == "hd" else "odottaa"
                        auto = bool((t.get("levy") or {}).pop("kasittele", False)) | \
                            bool((t.get("lataus") or {}).pop("kasittele", False))
                        if t["tila"] == "odottaa" and auto:
                            t["tila"] = "jonossa"          # levyltä / linkistä: käsittely alkaa suositellulla asetuksella
                            self._herata.set()
                    vanha = (t.get("koe") or {}).get("tunniste")
                    if vanha and vanha != self._koe_tunniste(t):
                        t["koe"] = {"tila": "ei"}
            except KeyError:
                return
            except Exception as e:  # noqa: BLE001
                m.loki(f"Analyysi epäonnistui: {traceback.format_exc()}")
                with self.lukko:
                    try:
                        t = self._tyo(tid)
                        t["tila"] = "virhe"
                        t["virhe"] = {"avain": str(e) if str(e) in ("ffmpeg_puuttuu", "tiedostoa_ei_loydy",
                                                                     "liian_suuri", "heic_puuttuu")
                                      else "analyysi", "tieto": str(e)[-300:]}
                    except KeyError:
                        pass
            self._tallenna()

    def _koe_tunniste(self, t):
        return kuvat.koe_tunniste(t["geo"]) if t.get("laji") == "kuva" else m.koe_tunniste(t["geo"])

    def _seuraava(self):
        """Seuraava video (kuvat käsitellään koe-säikeessä)."""
        with self.lukko:
            for t in self.tyot:
                if t["tila"] == "jonossa" and t.get("laji") != "kuva":
                    return t
        return None

    def _kaikki_valmiit(self):
        with self.lukko:
            return not any(t["tila"] in ("jonossa", "kasitellaan", "ripataan", "ladataan")
                           or any(j.get("tila") in ("jonossa", "tehdaan") for j in (t.get("jalki") or {}).values())
                           for t in self.tyot)

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
            if not self.ajastus_sallii():
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
                try:
                    ma = tulos.get("mittaus_aika") or {}
                    n = int(valinta.get("asetus") or an["suositus"])
                    d = m.kalibroi_aika(n, ma.get("kuvia", 0), ma.get("kuvat_s", 0), ma.get("teho"),
                                        self.ennuste_nopeus(t, n), ma.get("loppu_s"), an.get("kesto"))
                    m.loki(f"Aika-arvion korjaus: {ma} -> {d}")
                except Exception as e:  # noqa: BLE001
                    m.loki(f"Aika-arvion korjausta ei päivitetty: {e}")
                for laji in ("vertailu", "puhelin"):
                    if valinta.get(laji):
                        self.jalkityo(tid, laji)
                if t.get("levy") and not t["levy"].get("sailyta", True):
                    try:
                        polku.unlink()                  # levyltä kopioitu välitiedosto
                        with self.lukko:
                            t["levy"]["poistettu"] = True
                    except OSError:
                        pass
            except m.Peruutettu:
                with self.lukko:
                    if t in self.tyot:
                        # ajastuksen loppu: video jatkuu valmiista osista seuraavassa ajastusikkunassa
                        t["tila"] = "jonossa" if self._ajastus_pysaytti else "keskeytetty"
                    self._ajastus_pysaytti = False
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
            if self._kaikki_valmiit() and self.sammuta_valmiina and t["tila"] == "valmis" and not self._lopeta \
                    and not self.sammutus_ajastettu:
                self._sammuta()
            self._herata.set()

    def _siirra_alue(self, tid, k, x, y):
        try:
            with self.lukko:
                t = self._tyo(tid)
                polku, an, geo, wd = Path(t["polku"]), t["analyysi"], t["geo"], self._wd(t)
                koe = t["koe"]
            alueet = kuvat.siirra_alue(polku, an, geo, wd, self._laite("esikatselu"), k, x, y)
            with self.lukko:
                if t.get("koe") is koe:
                    koe["alueet"] = alueet
                    koe["versio"] = int(time.time() * 1000)
        except KeyError:
            return
        except Exception:  # noqa: BLE001
            m.loki(f"Alueen siirto epäonnistui: {traceback.format_exc()}")
        finally:
            with self.lukko:
                try:
                    self._tyo(tid).get("koe", {}).pop("siirretaan", None)
                except KeyError:
                    pass
        self._tallenna()

    def _kasittele_kuva(self, tid):
        with self.lukko:
            try:
                t = self._tyo(tid)
            except KeyError:
                return
            if t["tila"] != "jonossa" or self._lopeta:
                return
            self._peruuta_kuva.clear()
            t["tila"] = "kasitellaan"
            t["edistys"] = {"tehty": 0, "kaikki": 1000}
            self._kasiteltava_kuva = tid
            polku, an, geo, valinta = Path(t["polku"]), t["analyysi"], t["geo"], dict(t.get("valinta") or {})
            wd = self._wd(t)
            ulos = m.ulostulo_polku(polku, self.asetukset.get("ulos_kansio"), kuvat.ulostulo_paate(an, valinta))
        self._tallenna()

        def edistys(e, t=t):
            with self.lukko:
                t["edistys"] = e

        try:
            if not polku.exists():
                raise RuntimeError("tiedostoa_ei_loydy")
            L = self._laite("esikatselu")
            tulos = kuvat.kasittele_kuva(polku, an, geo, valinta, wd, L, ulos, edistys=edistys,
                                         peruuta=self._peruuta_kuva)
            with self.lukko:
                t["tila"] = "valmis"
                t["tulos"] = tulos
                t["valmistui"] = time.time()
            m.loki(f"Valmis: {tulos['ulos']}")
        except m.Peruutettu:
            with self.lukko:
                t["tila"] = "odottaa"
        except Exception as e:  # noqa: BLE001
            m.loki(f"Kuvan käsittely epäonnistui ({polku}): {traceback.format_exc()}")
            muisti = "out of memory" in str(e).lower() or type(e).__name__ == "OutOfMemoryError"
            with self.lukko:
                t["tila"] = "virhe"
                t["virhe"] = {"avain": "muisti" if muisti else str(e) if str(e) in (
                    "ffmpeg_puuttuu", "tiedostoa_ei_loydy", "malli_puuttuu", "mallin_lataus") else "kuva", "tieto": str(e)[-600:]}
            try:
                self._laite("esikatselu").tyhjenna()
            except Exception:  # noqa: BLE001
                pass
        finally:
            with self.lukko:
                self._kasiteltava_kuva = None
                t.pop("edistys", None)
            self._tallenna()
        if self._kaikki_valmiit() and self.sammuta_valmiina and t["tila"] == "valmis" and not self._lopeta \
                and not self.sammutus_ajastettu:
            self._sammuta()

    def _sammuta(self):
        self.sammutus_ajastettu = True
        m.loki("Jono valmis – kone sammutetaan 2 minuutin kuluttua.")
        if os.name == "nt":
            subprocess.run(["shutdown", "/s", "/t", "120", "/c", "PixeliRemaster: jono valmis"], creationflags=m.PIILOTA,
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
            if laji == "kuva":
                return self._kasittele_kuva(tid)
            if laji == "alue":
                return self._siirra_alue(tid, *param)
            if laji == "kohta":
                return self._siirra_kohta(tid, *param)
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

                if laji == "koe" and t.get("laji") == "kuva":
                    tiedot = kuvat.koekuvat_kuva(polku, an, geo, wd, L, edistys=ed)
                    with self.lukko:
                        if t.get("koe") is kohde:
                            t["koe"] = {"tila": "valmis", "tunniste": tiedot["tunniste"], "aika": tiedot["aika"],
                                        "alueet": tiedot["alueet"], "mittaus": tiedot.get("mittaus"),
                                        "kuvaus": tiedot.get("kuvaus"), "oletukset": tiedot.get("oletukset"),
                                        "alkuperainen": tiedot.get("alkuperainen", False)}
                elif laji == "koe":
                    tiedot = m.koekuvat(polku, an, geo, wd, L, edistys=ed)
                    with self.lukko:
                        t["koe"] = {"tila": "valmis", "tunniste": tiedot["tunniste"],
                                    "nopeus": {str(k): v for k, v in tiedot["nopeus"].items()},
                                    "mittaus": tiedot.get("mittaus"), "kuvaus": tiedot.get("kuvaus"),
                                    "oletukset": tiedot.get("oletukset"), "kohdat": tiedot.get("kohdat"),
                                    "vaihtelee": tiedot.get("vaihtelee", False),
                                    "piirretty": tiedot.get("piirretty", False)}
                        # piirretty tunnistetaan vasta mittauksessa: suositus päivittyy (valinta säilyy)
                        if tiedot.get("piirretty") and an.get("tyyppi") != "hd":
                            an["suositus"] = 5
                            if "piirretty" not in an.setdefault("huomiot", []):
                                an["huomiot"].append("piirretty")
                        self._paivita_geometria(t)
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
