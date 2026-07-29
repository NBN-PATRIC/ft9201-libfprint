#!/usr/bin/env python3
"""Alinhar pela banda da crista, em vez do pixel cru.

Diagnostico anterior: a correlacao de Pearson sobre o pixel cru escolhe offsets
com corr 0.10-0.17, ou seja nao ha pico -- o gradiente de brilho de fundo
domina o calculo e enterra o sinal periodico da crista.

Aqui a mesma correlacao roda sobre a imagem filtrada na banda de ~6-14px
(diferenca de gaussianas). Some o fundo, sobra a crista. Se o offset escolhido
passar a bater com o que preserva a crista, esse e' o conserto do driver.
"""
import itertools
import numpy as np
from PIL import Image

H, W = 80, 64
RANGE, STEP, MIN_OV = 56, 2, 600
PMIN, PMAX = 6, 14
SIG_LO, SIG_HI = 1.2, 5.0        # DoG: passa ~6-14px, corta DC e gradiente


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def gauss_blur(im, sigma):
    """Gaussiana separavel; em C sai como dois lacos 1D."""
    r = max(1, int(round(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2))
    k /= k.sum()
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    out = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(out, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def bandpass(im):
    return gauss_blur(im, SIG_LO) - gauss_blur(im, SIG_HI)


def corr_along(im, dy, dx):
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


def ridge_score(im, ndir=6):
    best = -9.0
    for k in range(ndir):
        th = np.pi * k / ndir
        uy, ux = np.sin(th), np.cos(th)
        prof = np.array([corr_along(im, int(round(uy * d)), int(round(ux * d)))
                         for d in range(1, PMAX + PMAX // 2 + 1)])
        vz, pz = prof[PMIN // 2 - 1: PMAX // 2], prof[PMIN - 1: PMAX]
        if np.all(np.isnan(vz)) or np.all(np.isnan(pz)):
            continue
        best = max(best, np.nanmax(pz) - np.nanmin(vz))
    return best


def compose(a, b, dy, dx):
    P = RANGE + 8
    A = np.full((H + 2 * P, W + 2 * P), np.nan)
    B = np.full((H + 2 * P, W + 2 * P), np.nan)
    A[P:P + H, P:P + W] = a
    B[P + dy:P + dy + H, P + dx:P + dx + W] = b
    both = np.stack([A, B])
    out = np.where(np.all(np.isnan(both), axis=0), 0.0,
                   np.nanmean(np.where(np.isnan(both), np.nan, both), axis=0))
    ys, xs = np.where(~np.all(np.isnan(both), axis=0))
    return np.nan_to_num(out[ys.min():ys.max() + 1, xs.min():xs.max() + 1])


def overlap_corr(a, b, dy, dx):
    y0, y1 = max(0, dy), min(H, H + dy)
    x0, x1 = max(0, dx), min(W, W + dx)
    if (y1 - y0) * (x1 - x0) < MIN_OV:
        return -2.0
    u = a[y0:y1, x0:x1].ravel()
    v = b[y0 - dy:y1 - dy, x0 - dx:x1 - dx].ravel()
    if u.std() < 1e-9 or v.std() < 1e-9:
        return -2.0
    return float(np.corrcoef(u, v)[0, 1])


def best_by_corr(a, b):
    best = (-9.0, (0, 0))
    for dy in range(-RANGE, RANGE + 1, STEP):
        for dx in range(-RANGE, RANGE + 1, STEP):
            c = overlap_corr(a, b, dy, dx)
            if c > best[0]:
                best = (c, (dy, dx))
    return best


def main():
    F = {i: load(f"frames/frame{i:02d}.pgm") for i in (1, 5, 7)}
    B = {i: bandpass(v) for i, v in F.items()}

    print("Alinhamento: pixel cru (driver hoje) vs banda da crista (proposto).")
    print("A qualidade e' sempre medida no composto dos frames ORIGINAIS.\n")
    print(f"{'par':<7} | {'--- pixel cru ---':^25} | {'--- banda da crista ---':^25}")
    print(f"{'':<7} | {'offset':>10}{'corr':>7}{'qual':>8} | {'offset':>10}{'corr':>7}{'qual':>8}")
    print("-" * 76)

    ganho = []
    for i, j in itertools.combinations((1, 5, 7), 2):
        cr, (ry, rx) = best_by_corr(F[i], F[j])
        cb, (by, bx) = best_by_corr(B[i], B[j])
        qr = ridge_score(compose(F[i], F[j], ry, rx))
        qb = ridge_score(compose(F[i], F[j], by, bx))
        ganho.append((qr, qb))
        print(f"{i}+{j:<5} | ({ry:+3d},{rx:+3d}){cr:>7.2f}{qr:>8.2f} |"
              f" ({by:+3d},{bx:+3d}){cb:>7.2f}{qb:>8.2f}")

    qr = np.mean([g[0] for g in ganho])
    qb = np.mean([g[1] for g in ganho])
    print("-" * 76)
    print(f"  qualidade media do composto: cru {qr:.2f}  ->  banda {qb:.2f}")
    print(f"  qualidade media dos frames de entrada: "
          f"{np.mean([ridge_score(v) for v in F.values()]):.2f}")


if __name__ == "__main__":
    main()
