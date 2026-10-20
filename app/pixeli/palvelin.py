"""PixeliRemaster – paikallinen HTTP-palvelin käyttöliittymälle.

Kuuntelee vain osoitteessa 127.0.0.1. Jokainen API-kutsu vaatii satunnaisen avaimen
(otsake X-Pixeli-Avain tai media-osoitteissa ?a=), ja Host-otsake tarkistetaan, joten
selaimessa auki olevat verkkosivut eivät voi käyttää palvelinta.
"""
import json
import mimetypes
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import asennus
from . import kuvat
from . import moottori as m

WEB = m.SOVELLUS / "web"
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ttf": "font/ttf", ".ico": "image/x-icon", ".mp4": "video/mp4", ".json": "application/json"}


def jarjestelman_kieli():
    try:
        if os.name == "nt":
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "fi" if (lid & 0x3FF) == 0x0B else "en"
        import locale
        k = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
        return "fi" if k.startswith("fi") else "en"
    except Exception:  # noqa: BLE001
        return "en"


class Sovellus:
    """Kokoaa jonon, ympäristön ja ikkunan yhteen. Ikkunakerros asettaa `ikkuna`-olion."""

    def __init__(self, jono, ymparisto):
        self.jono = jono
        self.ymp = ymparisto
        self.avain = secrets.token_urlsafe(24)
        self.ikkuna = None          # olio: tila ("pywebview"/"selain"), valitse(kansio), nayta()
        self.kieli_jarjestelma = jarjestelman_kieli()
        self.portti = None

    def tila(self):
        def esi(kuva):
            tulos = {}
            for n, a in m.ASETUKSET.items():
                a = dict(a, **(kuvat.KUVA_MUUTOKSET if kuva else {}))
                tulos[str(n)] = {"ai": a.get("ai", 1.0) if a.get("malli") else None, "rae": a.get("rae", 0.0),
                                 "halo": a.get("halo", 0.0), "malli": a.get("malli")}
            return tulos
        return {"versio": m.VERSIO, "ymparisto": self.ymp.nayta(), "jono": self.jono.tila(),
                "esiasetukset": esi(False), "esiasetukset_kuva": esi(True),
                "kieli_jarjestelma": self.kieli_jarjestelma,
                "ikkuna": self.ikkuna.tila if self.ikkuna else "selain",
                "datakansio": str(m.DATA), "aika": time.time()}

    # ------------------------------------------------------------ API
    def api(self, polku, d):
        j = self.jono
        if polku == "tila":
            return self.tila()
        if polku == "asenna":
            self.ymp.aloita_asennus()
            return {}
        if polku == "tarkista":
            threading.Thread(target=self.ymp._tarkista, daemon=True).start()
            return {}
        if polku == "lisaa":
            return j.lisaa(d.get("polut") or [])
        if polku == "valitse":
            kansio = d.get("laji") == "kansio"
            if self.ikkuna and hasattr(self.ikkuna, "valitse"):
                polut = self.ikkuna.valitse(kansio)
            else:
                polut = asennus.tiedostovalinta_powershell(kansio)
            if kansio:
                return {"kansio": polut[0] if polut else None}
            return j.lisaa(polut) if polut else {"lisatyt": [], "ohitetut": []}
        if polku == "poista":
            j.poista(d["id"])
            return {}
        if polku == "tyhjenna_valmiit":
            j.tyhjenna_valmiit()
            return {}
        if polku == "siirra":
            j.siirra(d["id"], int(d.get("suunta", 1)))
            return {}
        if polku == "valinta":
            j.aseta_valinta(d["id"], {k: d[k] for k in ("asetus", "saato", "dar", "skaala") if k in d})
            return {}
        if polku == "esikatsele":
            j.esikatsele(d["id"])
            return {}
        if polku == "saatokuva":
            return {"url": j.saatokuva(d["id"], d["asetus"], d.get("saato"), d.get("k", 2))}
        if polku == "liike":
            j.liike(d["id"], int(d["asetus"]), d.get("saato"))
            return {}
        if polku == "aloita":
            j.aloita(d.get("id"))
            return {}
        if polku == "keskeyta":
            j.keskeyta(d.get("id"))
            return {}
        if polku == "asetukset":
            j.tallenna_asetukset(d)
            return {}
        if polku == "sammutus":
            j.aseta_sammutus(d.get("paalla"))
            return {}
        if polku == "peru_sammutus":
            j.peru_sammutus()
            return {}
        if polku == "avaa":
            if d.get("mita") == "loki":
                asennus.avaa_kansio(m.LOKI if m.LOKI.exists() else m.DATA)
            elif d.get("mita") == "data":
                asennus.avaa_kansio(m.DATA)
            elif d.get("mita") == "tiedosto":
                t = j._tyo(d["id"])
                ulos = (t.get("tulos") or {}).get("ulos")
                if not ulos or not Path(ulos).is_file():
                    raise KeyError("tiedostoa_ei_loydy")
                asennus.avaa_tiedosto(ulos)
            else:
                t = j._tyo(d["id"])
                kohde = (t.get("tulos") or {}).get("ulos") or t["polku"]
                asennus.avaa_kansio(kohde)
            return {}
        if polku == "nayta":
            if self.ikkuna and hasattr(self.ikkuna, "nayta"):
                self.ikkuna.nayta()
            return {}
        raise KeyError("tuntematon")

    def media(self, tid, suhteellinen):
        """Tiedosto elokuvan työkansiosta (koekuvat, liikepätkät, pikkukuva)."""
        wd = self.jono.wd(tid).resolve()
        p = (wd / suhteellinen).resolve()
        if wd not in p.parents or p.suffix.lower() not in (".png", ".mp4") or not p.is_file():
            raise FileNotFoundError(suhteellinen)
        return p


def tee_kasittelija(sov):
    class Kasittelija(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "PixeliRemaster"

        def log_message(self, *a):
            pass

        def _host_ok(self):
            h = (self.headers.get("Host") or "").lower()
            return h in (f"127.0.0.1:{sov.portti}", f"localhost:{sov.portti}")

        def _laheta(self, koodi, data, tyyppi="application/json", lisa=None):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(koodi)
            self.send_header("Content-Type", tyyppi)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (lisa or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _tiedosto(self, p):
            koko = p.stat().st_size
            tyyppi = MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            alue = self.headers.get("Range")
            alku, loppu = 0, koko - 1
            koodi = 200
            if alue and alue.startswith("bytes="):
                try:
                    a, b = alue[6:].split(",")[0].split("-")
                    if a:
                        alku = int(a)
                        loppu = int(b) if b else koko - 1
                    else:
                        alku = max(0, koko - int(b))
                    loppu = min(loppu, koko - 1)
                    if alku > loppu:
                        raise ValueError
                    koodi = 206
                except ValueError:
                    self._laheta(416, b"", "text/plain", {"Content-Range": f"bytes */{koko}"})
                    return
            self.send_response(koodi)
            self.send_header("Content-Type", tyyppi)
            self.send_header("Content-Length", str(loppu - alku + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            if koodi == 206:
                self.send_header("Content-Range", f"bytes {alku}-{loppu}/{koko}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(p, "rb") as f:
                f.seek(alku)
                jaljella = loppu - alku + 1
                while jaljella > 0:
                    b = f.read(min(1 << 20, jaljella))
                    if not b:
                        break
                    try:
                        self.wfile.write(b)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    jaljella -= len(b)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._laheta(403, b"", "text/plain")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    html = (WEB / "index.html").read_text(encoding="utf-8")
                    html = html.replace("__AVAIN__", sov.avain)
                    return self._laheta(200, html.encode("utf-8"), MIME[".html"])
                if u.path.startswith("/web/"):
                    p = (WEB / unquote(u.path[5:])).resolve()
                    if WEB.resolve() not in p.parents or not p.is_file():
                        return self._laheta(404, b"", "text/plain")
                    return self._tiedosto(p)
                if u.path.startswith("/media/"):
                    if (q.get("a") or [""])[0] != sov.avain:
                        return self._laheta(403, b"", "text/plain")
                    _, _, tid, loput = u.path.split("/", 3)
                    return self._tiedosto(sov.media(tid, unquote(loput)))
                if u.path == "/api/tila":
                    if self.headers.get("X-Pixeli-Avain") != sov.avain:
                        return self._laheta(403, {"virhe": "avain"})
                    return self._laheta(200, sov.tila())
                return self._laheta(404, b"", "text/plain")
            except (FileNotFoundError, KeyError):
                return self._laheta(404, b"", "text/plain")
            except Exception:  # noqa: BLE001
                m.loki(f"GET {u.path}: {traceback.format_exc()}")
                return self._laheta(500, b"", "text/plain")

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Pixeli-Avain") != sov.avain:
                return self._laheta(403, {"virhe": "avain"})
            u = urlparse(self.path)
            if not u.path.startswith("/api/"):
                return self._laheta(404, {"virhe": "polku"})
            try:
                pituus = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(pituus) or b"{}") if pituus else {}
                tulos = sov.api(u.path[5:], d)
                return self._laheta(200, tulos if tulos is not None else {})
            except KeyError as e:
                return self._laheta(404, {"virhe": str(e).strip("'")})
            except RuntimeError as e:
                return self._laheta(409, {"virhe": str(e)})
            except Exception as e:  # noqa: BLE001
                m.loki(f"POST {u.path}: {traceback.format_exc()}")
                return self._laheta(500, {"virhe": "palvelin", "tieto": str(e)[-300:]})

    return Kasittelija


def kaynnista(sov, portti=0):
    palvelin = ThreadingHTTPServer(("127.0.0.1", portti), tee_kasittelija(sov))
    palvelin.daemon_threads = True
    sov.portti = palvelin.server_address[1]
    threading.Thread(target=palvelin.serve_forever, name="http", daemon=True).start()
    return palvelin
