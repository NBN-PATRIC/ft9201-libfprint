#!/usr/bin/env python3
"""A janela de pontuacao esta' jogando fora a informacao que distingue?

Todas as tentativas ate' aqui pontuaram numa janela fixa de 36x36 px -- 1.8 x
1.8 mm, cerca de quatro cristas. A janela fixa resolveu um problema real (a
pontuacao sobre sobreposicao variavel premiava as posicoes de pouca area), mas
ao custo de usar 1.8 dos 13 mm2 que o sensor entrega.

Isso importa porque quatro cristas paralelas de um dedo sao iguais as de outro:
o que distingue sao terminacoes e bifurcacoes, raras nessa area. O realce de
Gabor tornou isso pior, nao melhor -- ele limpa as cristas e apaga justamente a
textura residual que ainda diferenciava.

Aqui a janela varre de 24 a 48 px e o viés de area e' controlado de outro jeito:
todas as comparacoes de um mesmo tamanho usam a mesma contagem de pixels, entao
os d' de cada linha sao comparaveis entre si.
"""
import sys, os, csv
import numpy as np
from PIL import Image
import importlib.util

_here = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("st", os.path.join(_here, "subtpl.py"))
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def center_tpl_n(a, n):
    h, w = a.shape
    if h < n or w < n:
        return None
    y, x = (h - n) // 2, (w - n) // 2
    t = a[y:y + n, x:x + n]
    if t.std() < 1e-9:
        return None
    return (t - t.mean()) / t.std()


def slide_n(tpl, probe, valid, n):
    """Mesma NCC deslizante do matcher, com a janela parametrizada."""
    H, W = probe.shape
    if H < n or W < n:
        return -2.0
    t = tpl
    I1, I2 = st._integral(probe), st._integral(probe ** 2)
    s1, s2 = st._boxsum(I1, n), st._boxsum(I2, n)
    cnt = n * n
    mean = s1 / cnt
    sd = np.sqrt(np.maximum(s2 / cnt - mean ** 2, 0.0))
    fs = (H, W)
    num = np.fft.irfft2(np.fft.rfft2(probe, fs) * np.conj(np.fft.rfft2(t, fs)), fs)
    num = num[:H - n + 1, :W - n + 1]
    Iv = st._integral(valid.astype(np.float64))
    ok = st._boxsum(Iv, n) >= cnt - 0.5
    with np.errstate(divide="ignore", invalid="ignore"):
        ncc = (num / cnt - mean * t.mean()) / np.where(sd > 1e-9, sd, np.inf)
    ncc = np.where(ok, ncc, -2.0)
    return float(ncc.max())


def best(tpls, probe, n, rot):
    b = -2.0
    for deg in range(-rot, rot + 1, st.ROT_STEP):
        r, v = st.rot_valid(probe, deg)
        for t in tpls:
            c = slide_n(t, r, v, n)
            if c > b:
                b = c
    return b


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "dados"
    genuine = sys.argv[2] if len(sys.argv) > 2 else "d1"
    rot = int(sys.argv[3]) if len(sys.argv) > 3 else 45

    rows = list(csv.DictReader(open(os.path.join(d, "manifesto.csv"))))
    rows = [r for r in rows if os.path.exists(os.path.join(d, r["arquivo"]))]
    ims = {r["arquivo"]: st.prep(load(os.path.join(d, r["arquivo"]))) for r in rows}
    h, w = next(iter(ims.values())).shape
    print(f"{len(rows)} amostras de {d}/   imagem util {w}x{h}   rotacao +-{rot}deg\n")
    print(f"{'janela':>8}{'area mm2':>10}{'genuino':>10}{'impostor':>10}{'d prime':>10}")
    print("-" * 48)

    # ~500 dpi: 19.685 px por mm
    for n in (24, 30, 36, 42, 48):
        if n > min(h, w):
            continue
        tpls = []
        for r in sorted(rows, key=lambda x: -float(x["qualidade"])):
            if r["dedo"] != genuine:
                continue
            t = center_tpl_n(ims[r["arquivo"]], n)
            if t is not None:
                tpls.append((r["arquivo"], t))
            if len(tpls) >= st.N_MAX:
                break
        if not tpls:
            continue
        names = {a for a, _ in tpls}
        T = [t for _, t in tpls]
        gen, imp = [], []
        for r in rows:
            if r["arquivo"] in names:
                continue
            s = best(T, ims[r["arquivo"]], n, rot)
            (gen if r["dedo"].rstrip("r") == genuine.rstrip("r") else imp).append(s)
        gen, imp = np.array(gen), np.array(imp)
        if not len(gen) or not len(imp):
            continue
        dp = (gen.mean() - imp.mean()) / np.sqrt((gen.var() + imp.var()) / 2 + 1e-12)
        area = (n / 19.685) ** 2
        print(f"{n:>6}px{area:>10.2f}{gen.mean():>10.3f}{imp.mean():>10.3f}{dp:>10.2f}")

    print("\nSe d' subir junto com a area, o limite e' de informacao e a saida e'")
    print("um sensor maior ou juntar quadros. Se ficar plano, a area nao e' o gargalo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
