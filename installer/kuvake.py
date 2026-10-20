"""PixeliRemasterin kuvake: tuottaa app/web/pixeli.svg, pixeli-28.svg, pixeli-64.png ja pixeli.ico.

Kuvake on täysi syaani laatta (erottuu sekä tummasta että vaaleasta tehtäväpalkista), jonka päällä
tumma levy: oikea puoli sileä, vasen puoli pikseleinä. Jokainen koko piirretään omalle
pikseliruudukolleen, joten pienetkin koot (16 px) ovat teräviä.

Kehittäjän työkalu (vaatii Playwrightin ja Chromiumin):  python installer/kuvake.py
"""
import io
import math
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
KOOT = [16, 20, 24, 32, 40, 48, 64, 128, 256]

LAATTA_YLA, LAATTA_ALA = "#5ADCDB", "#22ADB0"
TUMMA = "#0D2527"
PALKIT = ["#C0C0C0", "#C0C000", "#00C0C0", "#00C000", "#C000C0", "#C00000", "#0000C0"]


def svg(S):
    """Kuvake S×S pikselin ruudukolle."""
    iso = S >= 48
    rx = S * 0.22
    palkki = round(S * 0.075) if iso else 0
    solu = max(1, round(S / 16)) if not iso else S / 18          # pikselipuolen ruutu
    korkeus = S - palkki
    cx = S / 2
    cy = round(korkeus / 2 / solu) * solu if not iso else korkeus / 2
    r = S * (0.40 if not iso else 0.355)
    rako = 0 if S < 32 else S * 0.035                              # puoliskojen väli
    napa = S * (0.07 if not iso else 0.065)
    osat = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}">',
            '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{LAATTA_YLA}"/><stop offset="1" stop-color="{LAATTA_ALA}"/>'
            f'</linearGradient><clipPath id="c"><rect width="{S}" height="{S}" rx="{rx:.2f}"/></clipPath></defs>',
            '<g clip-path="url(#c)">', f'<rect width="{S}" height="{S}" fill="url(#g)"/>']
    if palkki:
        lev = S / len(PALKIT)
        for i, v in enumerate(PALKIT):
            osat.append(f'<rect x="{i * lev:.3f}" y="{S - palkki}" width="{lev + 0.05:.3f}" height="{palkki}" fill="{v}"/>')
    osat.append("</g>")
    # oikea puoli: sileä puoliympyrä
    x0 = cx + rako / 2
    osat.append(f'<path d="M{x0:.3f} {cy - r:.3f} A{r:.3f} {r:.3f} 0 0 1 {x0:.3f} {cy + r:.3f} Z" fill="{TUMMA}"/>')
    # vasen puoli: sama ympyrä pikseliruudukossa
    rak = solu * 0.12 if iso else 0                                # pikselien välinen rako isoissa koissa
    oikea = cx - rako / 2
    sarake = 0
    pikselit = []
    while True:
        xa = oikea - (sarake + 1) * solu
        dx = cx - (xa + solu / 2) - rako / 2
        if dx >= r:
            break
        puoli = math.sqrt(r * r - dx * dx)
        n = max(1, round(puoli / solu))
        for j in range(-n, n):
            pikselit.append((xa, cy + j * solu))
        sarake += 1
    if iso:
        for (x, y) in pikselit:
            osat.append(f'<rect x="{x + rak / 2:.3f}" y="{y + rak / 2:.3f}" width="{solu - rak:.3f}" '
                        f'height="{solu - rak:.3f}" fill="{TUMMA}"/>')
    else:
        for (x, y) in pikselit:
            osat.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{solu:.3f}" height="{solu:.3f}" fill="{TUMMA}" '
                        'shape-rendering="crispEdges"/>')
    # keskireikä
    osat.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{napa:.3f}" fill="#3CC6C6"/>')
    osat.append("</svg>")
    return "".join(osat)


def renderoi(koot):
    from playwright.sync_api import sync_playwright
    kuvat = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        sivu = b.new_page(device_scale_factor=1)
        for S in koot:
            sivu.set_viewport_size({"width": S, "height": S})
            sivu.set_content(f'<html><body style="margin:0;background:transparent">{svg(S)}</body></html>')
            kuvat[S] = sivu.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": S, "height": S})
        b.close()
    return kuvat


def main():
    from PIL import Image
    kuvat = renderoi(KOOT)
    (WEB / "pixeli.svg").write_text(svg(64), encoding="utf-8")
    (WEB / "pixeli-28.svg").write_text(svg(28), encoding="utf-8")        # yläpalkin merkki
    (WEB / "pixeli-64.png").write_bytes(kuvat[64])
    kuvat_pil = [Image.open(io.BytesIO(kuvat[S])).convert("RGBA") for S in KOOT]
    suurin = kuvat_pil[-1]
    suurin.save(WEB / "pixeli.ico", format="ICO", sizes=[(S, S) for S in KOOT], append_images=kuvat_pil[:-1])
    if "--esikatselu" in sys.argv:
        ulos = Path(sys.argv[sys.argv.index("--esikatselu") + 1])
        ulos.mkdir(parents=True, exist_ok=True)
        for S in KOOT:
            (ulos / f"kuvake_{S}.png").write_bytes(kuvat[S])
    print("kuvake kirjoitettu:", ", ".join(str(S) for S in KOOT))


if __name__ == "__main__":
    main()
