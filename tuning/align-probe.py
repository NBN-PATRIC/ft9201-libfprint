#!/usr/bin/env python3
"""O offset que o driver escolhe e' o mesmo que preserva a crista?

O driver alinha maximizando a correlacao de Pearson na sobreposicao. Mas o que
importa para o NBIS nao e' a correlacao -- e' o composto continuar tendo crista
nitida. Com periodo de ~10px, um erro de 5px faz crista cancelar crista na
media, e a correlacao mal percebe (fica alta mesmo com a crista somindo).

Este teste varre os offsets medindo AS DUAS COISAS. Se o maximo de correlacao
cair longe do maximo de qualidade, o alinhamento e' o bug -- e tem conserto.
Se coincidirem e a qualidade cair mesmo assim, a media e' o bug.
"""
import itertools
import numpy as np
from PIL import Image

H, W = 80, 64
RANGE, STEP, MIN_OV = 40, 2, 600
PMIN, PMAX = 6, 14
NDIR_FAST = 6


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


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


def ridge_score(im, ndir=NDIR_FAST):
    best = -9.0
    for k in range(ndir):
        th = np.pi * k / ndir
        uy, ux = np.sin(th), np.cos(th)
        prof = np.array([corr_along(im, int(round(uy * d)), int(round(ux * d)))
                         for d in range(1, PMAX + PMAX // 2 + 1)])
        vz = prof[PMIN // 2 - 1: PMAX // 2]
        pz = prof[PMIN - 1: PMAX]
        if np.all(np.isnan(vz)) or np.all(np.isnan(pz)):
            continue
        s = np.nanmax(pz) - np.nanmin(vz)
        if s > best:
            best = s
    return best


def compose(a, b, dy, dx, how="mean"):
    """Junta os dois frames no offset dado e devolve o recorte preenchido."""
    P = RANGE + 8
    ch, cw = H + 2 * P, W + 2 * P
    A = np.full((ch, cw), np.nan)
    B = np.full((ch, cw), np.nan)
    A[P:P + H, P:P + W] = a
    B[P + dy:P + dy + H, P + dx:P + dx + W] = b
    if how == "mean":
        out = np.nanmean(np.stack([A, B]), axis=0)
    else:                                   # o pixel do frame mais contrastado
        out = np.where(np.isnan(A), B, np.where(np.isnan(B), A, np.maximum(A, B)))
    m = ~np.isnan(out)
    ys, xs = np.where(m)
    out = np.nan_to_num(out[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
    return out


def overlap_corr(a, b, dy, dx):
    """Exatamente a metrica de alinhamento do driver."""
    y0, y1 = max(0, dy), min(H, H + dy)
    x0, x1 = max(0, dx), min(W, W + dx)
    if (y1 - y0) * (x1 - x0) < MIN_OV:
        return -2.0
    u = a[y0:y1, x0:x1].ravel()
    v = b[y0 - dy:y1 - dy, x0 - dx:x1 - dx].ravel()
    if u.std() < 1e-9 or v.std() < 1e-9:
        return -2.0
    return float(np.corrcoef(u, v)[0, 1])


def main():
    F = {i: load(f"frames/frame{i:02d}.pgm") for i in (1, 5, 7)}
    print("Offsets: o que o DRIVER escolhe (max correlacao) vs o que PRESERVA")
    print("a crista (max qualidade do composto).\n")
    print(f"{'par':<7}{'q(a)':>6}{'q(b)':>6} | {'drv':>10}{'corr':>6}{'qual':>6} |"
          f" {'otimo':>10}{'qual':>6} | {'erro':>8}")
    print("-" * 74)

    for i, j in itertools.combinations((1, 5, 7), 2):
        a, b = F[i], F[j]
        qa, qb = ridge_score(a), ridge_score(b)
        best_c = (-9.0, (0, 0))
        best_q = (-9.0, (0, 0))
        for dy in range(-RANGE, RANGE + 1, STEP):
            for dx in range(-RANGE, RANGE + 1, STEP):
                c = overlap_corr(a, b, dy, dx)
                if c < -1:
                    continue
                if c > best_c[0]:
                    best_c = (c, (dy, dx))
                q = ridge_score(compose(a, b, dy, dx))
                if q > best_q[0]:
                    best_q = (q, (dy, dx))
        cy, cx = best_c[1]
        qy, qx = best_q[1]
        q_at_c = ridge_score(compose(a, b, cy, cx))
        dist = np.hypot(cy - qy, cx - qx)
        print(f"{i}+{j:<5}{qa:>6.2f}{qb:>6.2f} | ({cy:+3d},{cx:+3d}){best_c[0]:>6.2f}"
              f"{q_at_c:>6.2f} | ({qy:+3d},{qx:+3d}){best_q[0]:>6.2f} | {dist:>7.1f}px")

    print("\nMesmo no offset OTIMO, se a qualidade do composto ficar abaixo da")
    print("dos frames de entrada, a media e' que apaga a crista -- nao o alinhamento.")


if __name__ == "__main__":
    main()
