#!/usr/bin/env python3
"""Mosaico v3: busca grosseira→fina com correlação de Pearson na sobreposição.

A v1 acertou a ideia (pontuar só onde há sobreposição) mas usava uma métrica
que não era correlação de verdade e um alcance curto demais. A v2 falhou porque
correlação de fase sobre um canvas quase todo zero sempre aponta para offset 0.
"""
import sys, glob, os, itertools
import numpy as np
from PIL import Image

SRC = sys.argv[1] if len(sys.argv) > 1 else "frames"
OUT = sys.argv[2] if len(sys.argv) > 2 else "mosaic3.pgm"
RANGE = 56          # deslocamento máximo procurado
COARSE = 4          # passo da busca grosseira
MIN_OV = 600        # px mínimos de sobreposição
MIN_CORR = 0.30     # correlação mínima para aceitar um frame


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def overlap_corr(canvas, cnt, f, dy, dx):
    """Pearson entre o mosaico e f, só onde ambos têm pixel."""
    H, W = canvas.shape
    h, w = f.shape
    y0, x0 = max(0, dy), max(0, dx)
    y1, x1 = min(H, dy + h), min(W, dx + w)
    if y1 - y0 < 12 or x1 - x0 < 12:
        return -2.0, 0
    msk = cnt[y0:y1, x0:x1] > 0
    n_ov = int(msk.sum())
    if n_ov < MIN_OV:
        return -2.0, n_ov
    base = (canvas[y0:y1, x0:x1] / np.maximum(cnt[y0:y1, x0:x1], 1e-9))[msk]
    sub = f[y0 - dy:y1 - dy, x0 - dx:x1 - dx][msk]
    if base.std() < 1e-6 or sub.std() < 1e-6:
        return -2.0, n_ov
    return float(np.corrcoef(base, sub)[0, 1]), n_ov


def find_offset(canvas, cnt, f, cy, cx):
    """Busca grosseira e depois refino ±COARSE em torno do melhor."""
    best = (-2.0, None, 0)
    for dy in range(cy - RANGE, cy + RANGE + 1, COARSE):
        for dx in range(cx - RANGE, cx + RANGE + 1, COARSE):
            c, ov = overlap_corr(canvas, cnt, f, dy, dx)
            if c > best[0]:
                best = (c, (dy, dx), ov)
    if best[1] is None:
        return best
    by, bx = best[1]
    for dy in range(by - COARSE, by + COARSE + 1):
        for dx in range(bx - COARSE, bx + COARSE + 1):
            c, ov = overlap_corr(canvas, cnt, f, dy, dx)
            if c > best[0]:
                best = (c, (dy, dx), ov)
    return best


def paste(canvas, cnt, f, dy, dx):
    H, W = canvas.shape
    h, w = f.shape
    ys, xs = slice(max(0, dy), min(H, dy + h)), slice(max(0, dx), min(W, dx + w))
    fy = slice(max(0, -dy), h - max(0, (dy + h) - H))
    fx = slice(max(0, -dx), w - max(0, (dx + w) - W))
    canvas[ys, xs] += f[fy, fx]
    cnt[ys, xs] += 1.0


def main():
    paths = sorted(glob.glob(os.path.join(SRC, "*.pgm")))
    frames = [load(p) for p in paths]
    h, w = frames[0].shape
    print(f"  {len(frames)} frames de {w}x{h}")

    PAD = RANGE + 8
    H, W = h + 2 * PAD, w + 2 * PAD
    canvas = np.zeros((H, W)); cnt = np.zeros((H, W))
    paste(canvas, cnt, frames[0], PAD, PAD)
    placed, order = {0}, [0]

    while len(placed) < len(frames):
        cand = (-2.0, None, None)
        for k in range(len(frames)):
            if k in placed:
                continue
            c, off, ov = find_offset(canvas, cnt, frames[k], PAD, PAD)
            if c > cand[0]:
                cand = (c, k, off)
        c, k, off = cand
        if k is None or c < MIN_CORR:
            print(f"    (restantes com correlação < {MIN_CORR}, parando)")
            break
        paste(canvas, cnt, frames[k], off[0], off[1])
        placed.add(k); order.append(k)
        print(f"    + frame {k+1}: corr={c:+.2f} offset=({off[0]-PAD:+d},{off[1]-PAD:+d})")

    out = np.divide(canvas, np.maximum(cnt, 1e-9))
    ys, xs = np.where(cnt > 0)
    out = out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    oh, ow = out.shape
    out = out[:oh - oh % 4, :ow - ow % 4]
    out = np.clip(out, 0, 255).astype(np.uint8)
    Image.fromarray(out).save(OUT)
    print(f"  mosaico: {out.shape[1]}x{out.shape[0]} de {len(placed)} frames "
          f"({out.shape[0]*out.shape[1]/(h*w):.2f}x a area) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
