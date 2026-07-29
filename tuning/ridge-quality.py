#!/usr/bin/env python3
"""Mede se um frame contem CRISTA de verdade, e nao so' borrao.

A assinatura de uma crista e' periodica: a autocorrelacao mergulha em meio
periodo e volta a subir em um periodo inteiro. Um dedo mal encostado da' um
gradiente suave, cuja autocorrelacao so' decai. Medir isso separa os frames
que servem dos que so' sujam a media do mosaico.

Como a crista tem orientacao, a busca varre varias direcoes e fica com a
melhor -- senao um dedo com cristas verticais seria reprovado por um teste
que so' olha na horizontal.
"""
import sys, glob, os
import numpy as np
from PIL import Image

PERIOD_MIN, PERIOD_MAX = 6, 14      # periodo de crista plausivel, em px (~500dpi)
NDIR = 12                           # direcoes varridas entre 0 e 180 graus


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def corr_along(im, dy, dx):
    """Correlacao do frame consigo mesmo deslocado de (dy,dx)."""
    h, w = im.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    if y1 - y0 < 16 or x1 - x0 < 16:
        return np.nan
    a = im[y0:y1, x0:x1]
    b = im[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return np.nan
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def ridge_score(im):
    """Melhor contraste periodico entre todas as direcoes.

    score = (pico em ~1 periodo) - (vale em ~meio periodo).
    Crista nitida passa de 1.0; borrao fica perto de 0.
    """
    best = (-9.0, None, None)
    for k in range(NDIR):
        th = np.pi * k / NDIR
        uy, ux = np.sin(th), np.cos(th)
        prof = []
        for d in range(1, PERIOD_MAX + PERIOD_MAX // 2 + 1):
            c = corr_along(im, int(round(uy * d)), int(round(ux * d)))
            prof.append(c)
        prof = np.array(prof)
        # vale procurado na primeira metade do periodo, pico logo depois
        vale_zone = prof[PERIOD_MIN // 2 - 1: PERIOD_MAX // 2]
        pico_zone = prof[PERIOD_MIN - 1: PERIOD_MAX]
        if np.all(np.isnan(vale_zone)) or np.all(np.isnan(pico_zone)):
            continue
        vale = np.nanmin(vale_zone)
        pico = np.nanmax(pico_zone)
        s = pico - vale
        if s > best[0]:
            per = int(np.nanargmax(pico_zone)) + PERIOD_MIN
            best = (s, int(round(np.degrees(th))), per)
    return best


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "frames"
    paths = sorted(glob.glob(os.path.join(src, "*.pgm")))
    if not paths:
        print(f"nenhum .pgm em {src}")
        return 1

    print(f"{len(paths)} frames em {src}/\n")
    print(f"{'frame':<8}{'qualidade':>10}{'direcao':>9}{'periodo':>9}   veredito")
    print("-" * 56)
    rows = []
    for p in paths:
        im = load(p)
        s, ang, per = ridge_score(im)
        rows.append((os.path.basename(p), s, ang, per))
        v = "CRISTA NITIDA" if s >= 0.80 else "crista fraca" if s >= 0.40 else "borrao (descartar)"
        print(f"{os.path.basename(p):<8}{s:>10.2f}{ang:>8}deg{per:>8}px   {v}")

    ss = np.array([r[1] for r in rows])
    bons = [r[0] for r in rows if r[1] >= 0.80]
    print("-" * 56)
    print(f"  media {ss.mean():.2f}   mediana {np.median(ss):.2f}   melhor {ss.max():.2f}")
    print(f"  frames com crista nitida (>=0.80): {len(bons)}/{len(rows)}")
    if bons:
        print("    " + " ".join(bons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
