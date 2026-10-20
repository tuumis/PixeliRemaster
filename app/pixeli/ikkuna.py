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

VIDEOSUODATIN = ("Video (*.mkv;*.mp4;*.avi;*.vob;*.mpg;*.mpeg;*.m2v;*.ts;*.m4v;*.mov;*.wmv)",
                 "Video files (*.mkv;*.mp4;*.avi;*.vob;*.mpg;*.mpeg;*.m2v;*.ts;*.m4v;*.mov;*.wmv)")

SULJE_TEKSTI = {
    "fi": ("Käsittely on kesken", "Elokuvan käsittely on kesken. Suljetaanko ohjelma?\n\n"
           "Käsittely jatkuu samasta kohdasta, kun avaat ohjelman uudelleen."),
    "en": ("Processing in progress", "A movie is being processed. Close the program?\n\n"
           "Processing will continue where it left off the next time you open the program."),
}


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
        self.ikkuna.events.closing += self._sulkeutuu
        self.ikkuna.events.shown += self._kuvake

    def _kuvake(self):
        """Ikkunan ja tehtäväpalkin kuvake (WinForms-ikkuna pythonnetin kautta)."""
        try:
            from System.Drawing import Icon  # type: ignore
            ico = m.SOVELLUS / "web" / "pixeli.ico"
            if ico.exists():
                self.ikkuna.native.Icon = Icon(str(ico))
        except Exception:  # noqa: BLE001
            pass

    def _sulkeutuu(self):
        if self.sov.jono._kasiteltava:
            otsikko, teksti = SULJE_TEKSTI.get(self.kieli(), SULJE_TEKSTI["en"])
            try:
                return bool(self.ikkuna.create_confirmation_dialog(otsikko, teksti))
            except Exception:  # noqa: BLE001
                return True
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
            suodin = VIDEOSUODATIN[0] if self.kieli() == "fi" else VIDEOSUODATIN[1]
            r = self.ikkuna.create_file_dialog(getattr(wv, "OPEN_DIALOG", 10), allow_multiple=True,
                                               file_types=(suodin, "All files (*.*)"))
        if not r:
            return []
        return [r] if isinstance(r, str) else list(r)

    def nayta(self):
        try:
            self.ikkuna.restore()
            self.ikkuna.show()
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
