"""PixeliRemaster – sovellusikkuna.

Ensisijaisesti pywebview (Microsoft Edge WebView2 -ikkuna): oikea sovellusikkuna, vedä ja pudota
täysillä tiedostopoluilla sekä Windowsin omat tiedostovalinnat. Jos pywebview ei käynnisty,
käytetään Edgen sovellustilaa (ikkuna ilman osoiteriviä) tai oletusselainta.
"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import moottori as m

_VIDEO = "*.mkv;*.mp4;*.avi;*.vob;*.mpg;*.mpeg;*.m2v;*.ts;*.m4v;*.mov;*.wmv"
_KUVA = "*.jpg;*.jpeg;*.png;*.heic;*.heif;*.webp;*.bmp;*.tif;*.tiff"
SUODATTIMET = {
    "fi": (f"Videot ja kuvat ({_VIDEO};{_KUVA})", f"Videot ({_VIDEO})", f"Kuvat ({_KUVA})", "Kaikki tiedostot (*.*)"),
    "en": (f"Videos and images ({_VIDEO};{_KUVA})", f"Videos ({_VIDEO})", f"Images ({_KUVA})", "All files (*.*)"),
}

SULJE_TEKSTI = {
    "fi": ("Käsittely on kesken", "Käsittely on kesken. Suljetaanko ohjelma?\n\n"
           "Videon käsittely jatkuu samasta kohdasta, kun avaat ohjelman uudelleen."),
    "en": ("Processing in progress", "Processing is in progress. Close the program?\n\n"
           "Video processing will continue where it left off the next time you open the program."),
}


ILMAISIN_TEKSTIT = {
    "fi": {"avaa": "Avaa PixeliRemaster", "lopeta": "Lopeta", "taustalla": "PixeliRemaster toimii taustalla",
           "taustalla_ohje": "Käsittely ja lataukset jatkuvat. Avaa ohjelma kellon vierestä löytyvästä kuvakkeesta.",
           "valmis": "Valmis: {n}", "valmis_ohje": "Avaa PixeliRemaster napsauttamalla."},
    "en": {"avaa": "Open PixeliRemaster", "lopeta": "Quit", "taustalla": "PixeliRemaster is running in the background",
           "taustalla_ohje": "Processing and downloads continue. Open the program from the icon next to the clock.",
           "valmis": "Finished: {n}", "valmis_ohje": "Click to open PixeliRemaster."},
}


class Ilmaisin:
    """Ilmaisinalueen (kellon vieressä) kuvake WinFormsin NotifyIconilla (pythonnet, ei lisäkirjastoja).
    Luodaan ja käsitellään ikkunan omassa käyttöliittymäsäikeessä."""

    def __init__(self, ik):
        self.ik = ik
        self.kuvake = None

    def _tekstit(self):
        return ILMAISIN_TEKSTIT.get(self.ik.kieli(), ILMAISIN_TEKSTIT["en"])

    def luo(self):
        from System import Action  # type: ignore
        lomake = self.ik.ikkuna.native
        lomake.Invoke(Action(self._luo))

    def _luo(self):
        from System import EventHandler  # type: ignore
        from System.Drawing import Icon  # type: ignore
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem  # type: ignore
        tx = self._tekstit()
        k = NotifyIcon()
        ico = m.SOVELLUS / "web" / "pixeli.ico"
        if ico.exists():
            k.Icon = Icon(str(ico))
        k.Text = "PixeliRemaster"
        valikko = ContextMenuStrip()
        avaa = ToolStripMenuItem(tx["avaa"])
        avaa.Click += EventHandler(lambda s, e: threading.Thread(target=self.ik.nayta, daemon=True).start())
        lopeta = ToolStripMenuItem(tx["lopeta"])
        lopeta.Click += EventHandler(lambda s, e: threading.Thread(target=self.ik.lopeta, daemon=True).start())
        valikko.Items.Add(avaa)
        valikko.Items.Add(lopeta)
        k.ContextMenuStrip = valikko
        k.DoubleClick += EventHandler(lambda s, e: threading.Thread(target=self.ik.nayta, daemon=True).start())
        k.BalloonTipClicked += EventHandler(lambda s, e: threading.Thread(target=self.ik.nayta, daemon=True).start())
        k.Visible = True
        self.kuvake = k
        try:
            # Windowsin sammutus tai uloskirjautuminen: ikkuna saa sulkeutua oikeasti (ei piiloon)
            from Microsoft.Win32 import SessionEndingEventHandler, SystemEvents  # type: ignore
            SystemEvents.SessionEnding += SessionEndingEventHandler(
                lambda s, e: setattr(self.ik, "_lopetetaan", True))
        except Exception as e:  # noqa: BLE001
            m.loki(f"Sammutustapahtumaa ei voitu seurata: {e}")

    def ilmoita(self, otsikko, teksti):
        if self.kuvake is None:
            return
        try:
            from System import Action  # type: ignore
            from System.Windows.Forms import ToolTipIcon  # type: ignore
            self.ik.ikkuna.native.Invoke(Action(lambda: self.kuvake.ShowBalloonTip(6000, otsikko, teksti,
                                                                                  ToolTipIcon.Info)))
        except Exception as e:  # noqa: BLE001
            m.loki(f"Ilmoitusta ei voitu näyttää: {e}")

    def poista(self):
        if self.kuvake is None:
            return
        try:
            from System import Action  # type: ignore

            def pois():
                self.kuvake.Visible = False
                self.kuvake.Dispose()
            self.ik.ikkuna.native.Invoke(Action(pois))
        except Exception:  # noqa: BLE001
            pass
        self.kuvake = None


class PywebviewIkkuna:
    tila = "pywebview"

    def __init__(self, sov, url, kieli):
        import webview
        self.webview = webview
        self.sov = sov
        self.kieli = kieli
        self.ikkuna = webview.create_window(
            "PixeliRemaster", url, width=1320, height=860, min_size=(1000, 660),
            background_color="#17181A", text_select=False)
        self.ilmaisin = Ilmaisin(self) if os.name == "nt" else None
        self.piilossa = False
        self._lopetetaan = False
        self.ikkuna.events.closing += self._sulkeutuu
        self.ikkuna.events.shown += self._kuvake

    def _kuvake(self):
        """Ikkunan ja tehtäväpalkin kuvake (WinForms-ikkuna pythonnetin kautta) ja ilmaisinalueen kuvake."""
        try:
            from System.Drawing import Icon  # type: ignore
            ico = m.SOVELLUS / "web" / "pixeli.ico"
            if ico.exists():
                self.ikkuna.native.Icon = Icon(str(ico))
        except Exception:  # noqa: BLE001
            pass
        if self.ilmaisin is not None and self.ilmaisin.kuvake is None:
            try:
                self.ilmaisin.luo()
                threading.Thread(target=self._valmistumiset, name="ilmaisin", daemon=True).start()
            except Exception as e:  # noqa: BLE001
                m.loki(f"Ilmaisinalueen kuvaketta ei voitu luoda: {e}")
                self.ilmaisin = None

    def _valmistumiset(self):
        """Taustalla ollessa ilmoitus jokaisesta valmistuneesta videosta, kuvasta, latauksesta ja jälkityöstä."""
        def tunnisteet():
            with self.sov.jono.lukko:
                r = set()
                for t in self.sov.jono.tyot:
                    if t["tila"] == "valmis":
                        r.add((t["id"], "tyo", t.get("nimi")))
                    for laji, jt in (t.get("jalki") or {}).items():
                        if jt.get("tila") == "valmis":
                            r.add((t["id"], laji, Path(jt.get("polku") or "").name))
                return r
        nahdyt = tunnisteet()
        while not self._lopetetaan:
            time.sleep(3)
            try:
                nyt = tunnisteet()
                uudet = nyt - nahdyt
                nahdyt = nyt
                if uudet and self.piilossa and self.ilmaisin is not None:
                    tx = ILMAISIN_TEKSTIT.get(self.kieli(), ILMAISIN_TEKSTIT["en"])
                    nimet = ", ".join(sorted(str(u[2]) for u in uudet))[:120]
                    self.ilmaisin.ilmoita(tx["valmis"].format(n=nimet), tx["valmis_ohje"])
            except Exception:  # noqa: BLE001
                pass

    def _piilota(self):
        try:
            self.ikkuna.hide()
            self.piilossa = True
            jono = self.sov.jono
            if not jono.asetukset.get("ilmaisin_ohjeistettu"):
                tx = ILMAISIN_TEKSTIT.get(self.kieli(), ILMAISIN_TEKSTIT["en"])
                self.ilmaisin.ilmoita(tx["taustalla"], tx["taustalla_ohje"])
                with jono.lukko:
                    jono.asetukset["ilmaisin_ohjeistettu"] = True
                    m.kirjoita_json(m.DATA / "asetukset.json", jono.asetukset)
        except Exception as e:  # noqa: BLE001
            m.loki(f"Ikkunan piilotus epäonnistui: {e}")

    def lopeta(self):
        """Ohjelman lopetus ilmaisinalueen valikosta tai asetuksista."""
        self._lopetetaan = True
        if self.piilossa:
            self.nayta()          # mahdollinen "käsittely on kesken" -kysymys näkyy ikkunan päällä
        try:
            self.ikkuna.destroy()
        except Exception:  # noqa: BLE001
            os._exit(0)

    def _sulkeutuu(self):
        if (not self._lopetetaan and self.ilmaisin is not None and self.ilmaisin.kuvake is not None
                and self.sov.jono.asetukset.get("sulje", "ilmaisin") == "ilmaisin"):
            # X pienentää ilmaisinalueelle; piilotus omassa säikeessään, koska sulkeminen odottaa vastausta
            threading.Thread(target=self._piilota, daemon=True).start()
            return False
        if self.sov.jono._kasiteltava or self.sov.jono._kasiteltava_kuva:
            otsikko, teksti = SULJE_TEKSTI.get(self.kieli(), SULJE_TEKSTI["en"])
            try:
                ok = bool(self.ikkuna.create_confirmation_dialog(otsikko, teksti))
            except Exception:  # noqa: BLE001
                ok = True
            if not ok:
                self._lopetetaan = False
            elif self.ilmaisin is not None:
                self.ilmaisin.poista()
            return ok
        if self.ilmaisin is not None:
            self.ilmaisin.poista()
        return True

    def _sido(self, ikkuna):
        """Vedä ja pudota: pywebview antaa pudotettujen tiedostojen täydet polut."""
        try:
            from webview.dom import DOMEventHandler

            def pudotus(e):
                polut = []
                for f in (e.get("dataTransfer") or {}).get("files") or []:
                    p = f.get("pywebviewFullPath")
                    if p:
                        polut.append(p)
                if polut:
                    tulos = self.sov.jono.lisaa(polut)
                    try:
                        import json
                        ikkuna.evaluate_js(f"window.pixeliPudotettu && window.pixeliPudotettu({json.dumps(tulos)})")
                    except Exception:  # noqa: BLE001
                        pass

            def ohita(e):
                pass

            ikkuna.dom.document.events.dragenter += DOMEventHandler(ohita, True, True)
            ikkuna.dom.document.events.dragover += DOMEventHandler(ohita, True, True, debounce=500)
            ikkuna.dom.document.events.drop += DOMEventHandler(pudotus, True, True)
        except Exception as e:  # noqa: BLE001
            m.loki(f"Vedä ja pudota ei käytössä: {e}")

    def valitse(self, kansio=False):
        wv = self.webview
        if kansio:
            r = self.ikkuna.create_file_dialog(getattr(wv, "FOLDER_DIALOG", 20))
        else:
            r = self.ikkuna.create_file_dialog(getattr(wv, "OPEN_DIALOG", 10), allow_multiple=True,
                                               file_types=SUODATTIMET.get(self.kieli(), SUODATTIMET["en"]))
        if not r:
            return []
        return [r] if isinstance(r, str) else list(r)

    def nayta(self):
        self.piilossa = False
        try:
            self.ikkuna.show()
            self.ikkuna.restore()
            self.ikkuna.on_top = True
            time.sleep(0.2)
            self.ikkuna.on_top = False
        except Exception:  # noqa: BLE001
            pass

    def aja(self):
        tallennus = m.DATA / "webview"
        tallennus.mkdir(parents=True, exist_ok=True)
        self.webview.start(self._sido, self.ikkuna, private_mode=False, storage_path=str(tallennus))


class SelainIkkuna:
    """Edgen sovellustila tai oletusselain. Vedä ja pudota ei anna polkuja – käytetään Lisää-painiketta."""
    tila = "selain"

    def __init__(self, sov, url):
        self.sov = sov
        self.url = url
        self.prosessi = None

    @staticmethod
    def _edge():
        ehdokkaat = [shutil.which("msedge")]
        for juuri in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                      os.environ.get("LOCALAPPDATA")):
            if juuri:
                ehdokkaat.append(str(Path(juuri) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        for e in ehdokkaat:
            if e and Path(e).exists():
                return e
        return None

    def nayta(self):
        pass

    def aja(self):
        edge = self._edge() if os.name == "nt" else None
        if edge:
            profiili = m.DATA / "edge"
            self.prosessi = subprocess.Popen([edge, f"--app={self.url}", f"--user-data-dir={profiili}",
                                              "--window-size=1320,860", "--no-first-run",
                                              "--disable-features=Translate"])
            self.prosessi.wait()
            # Edge voi luovuttaa ikkunan jo käynnissä olevalle prosessille; odotetaan profiilin lukkoa
            lukko = profiili / "lockfile"
            while lukko.exists():
                try:
                    lukko.unlink()
                    break
                except OSError:
                    time.sleep(2)
        else:
            import webbrowser
            webbrowser.open(self.url)
            tapahtuma = threading.Event()
            self.sov.lopeta_tapahtuma = tapahtuma
            tapahtuma.wait()


def luo(sov, url, kieli):
    if os.environ.get("PIXELI_IKKUNA") != "selain":
        try:
            return PywebviewIkkuna(sov, url, kieli)
        except Exception as e:  # noqa: BLE001
            m.loki(f"pywebview ei käytettävissä ({e}), käytetään selainta")
    return SelainIkkuna(sov, url)
