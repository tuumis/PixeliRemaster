"""Staattinen tarkistus: funktion sisäinen import, joka varjostaa moduulitason nimen.

Python tekee funktiossa tuodusta nimestä paikallisen koko funktiolle, joten saman nimen käyttö ennen
tuontia (tai toisessa haarassa) kaatuu UnboundLocalErroriin (esim. 1.14.1:n "Etsi selaimessa").
"""
import ast
import sys
from pathlib import Path


def tuodut(solmut):
    nimet = {}
    for s in solmut:
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            for a in s.names:
                nimet[(a.asname or a.name).split(".")[0]] = s.lineno
    return nimet


def tarkista(polku):
    puu = ast.parse(Path(polku).read_text(encoding="utf-8"))
    moduuli = tuodut(puu.body)
    virheet = []
    for f in ast.walk(puu):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sisaiset = {}
        pino = list(f.body)
        while pino:
            s = pino.pop()
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(s, (ast.Import, ast.ImportFrom)):
                sisaiset.update(tuodut([s]))
            pino.extend(ast.iter_child_nodes(s))
        for nimi, rivi in sisaiset.items():
            if nimi in moduuli:
                virheet.append(f"{polku}:{rivi}: funktio {f.name} tuo nimen '{nimi}' uudelleen (moduulitasolla jo)")
    return virheet


if __name__ == "__main__":
    kaikki = [v for p in sorted(Path(sys.argv[1]).rglob("*.py")) for v in tarkista(p)]
    print("\n".join(kaikki) or "tuonnit OK")
    sys.exit(1 if kaikki else 0)
