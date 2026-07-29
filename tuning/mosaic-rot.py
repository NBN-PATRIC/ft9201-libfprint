#!/usr/bin/env python3
"""Mosaico com rotacao: gira para orientacao comum, depois alinha por translacao.

Cadeia de diagnostico ate' aqui:
  1. a correlacao de Pearson escolhia offsets ate' 66px errados (sem pico);
  2. alinhar pela qualidade de crista achou offsets bons na sobreposicao
     (+1.10), mas o mosaico final continuou ruim (0.33);
  3. a causa apareceu nas direcoes de crista dos frames: 0, 45, 90 e 105 graus.
     Isso e' o DEDO GIRADO entre um toque e outro -- crista de dedo nao vira
     105 graus em 4mm. Translacao sozinha nao junta frames girados.

Girar cada frame para a orientacao da ancora faz as direcoes convergirem
(0/15/165 graus) e ate' melhora a qualidade individual. Este script fecha o
ciclo: gira, depois alinha por translacao usando a qualidade como metrica.
"""
import sys, glob, os
import numpy as np
from PIL import Image
import importlib.util

_s = importlib.util.spec_from_file_location("rq", os.path.join(os.path.dirname(__file__), "mosaic-rq.py"))
rq = importlib.util.module_from_spec(_s); _s.loader.exec_module(rq)

H, W = 80, 64
RANGE, COARSE = 48, 4
MIN_OV = 2000
Q_MIN = 0.60
PAD = RANGE + 8
CH, CW = H + 2 * PAD, W + 2 * PAD
MARGIN = 10          # descarta a borda que a rotacao encheu de preto


def rotate_to(im, deg):
    p = Image.fromarray(np.clip(im, 0, 255).astype(np.uint8))
    r = p.rotate(-deg, resample=Image.BICUBIC, expand=False, fillcolor=0)
    a = np.asarray(r, dtype=np.float64)
    return a[MARGIN:-MARGIN, MARGIN:-MARGIN]


class Mos:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.s = np.zeros((CH, CW)); self.n = np.zeros((CH, CW)); self.count = 0

    def paste(self, f, dy, dx):
        self.s[dy:dy + self.h, dx:dx + self.w] += f
        self.n[dy:dy + self.h, dx:dx + self.w] += 1
        self.count += 1

    def trial(self, f, dy, dx):
        s = self.s.copy(); n = self.n.copy()
        s[dy:dy + self.h, dx:dx + self.w] += f
        n[dy:dy + self.h, dx:dx + self.w] += 1
        ov = np.zeros((CH, CW), bool)
        ov[dy:dy + self.h, dx:dx + self.w] = True
        ov &= self.n > 0
        return np.divide(s, np.maximum(n, 1e-9)), ov, int(ov.sum())

    def best_offset(self, f, ang, per):
        best = (-9.0, None)
        for dy in range(PAD - RANGE, PAD + RANGE + 1, COARSE):
            for dx in range(PAD - RANGE, PAD + RANGE + 1, COARSE):
                img, ov, nov = self.trial(f, dy, dx)
                if nov < MIN_OV:
                    continue
                q = rq.ridge_fast(img, ov, ang, per)
                if q > best[0]:
                    best = (q, (dy, dx))
        if best[1] is None:
            return best
        by, bx = best[1]
        for dy in range(by - COARSE, by + COARSE + 1):
            for dx in range(bx - COARSE, bx + COARSE + 1):
                img, ov, nov = self.trial(f, dy, dx)
                if nov < MIN_OV:
                    continue
                q = rq.ridge_fast(img, ov, ang, per)
                if q > best[0]:
                    best = (q, (dy, dx))
        return best

    def crop(self):
        im = np.divide(self.s, np.maximum(self.n, 1e-9))
        ys, xs = np.where(self.n > 0)
        im = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = im.shape
        return im[:h - h % 4, :w - w % 4]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "frames"
    out = sys.argv[2] if len(sys.argv) > 2 else "mosaic_rot.pgm"
    paths = sorted(glob.glob(os.path.join(src, "*.pgm")))
    frames = [rq.load(p) for p in paths]
    qual = [rq.ridge_full(f) for f in frames]

    keep = [i for i, (q, _, _) in enumerate(qual) if q >= Q_MIN]
    if len(keep) < 2:
        print(f"menos de 2 frames acima de {Q_MIN}")
        return 1
    keep.sort(key=lambda i: -qual[i][0])
    anchor = keep[0]
    _, a0, p0 = qual[anchor]
    print(f"{len(frames)} frames; {len(keep)} acima de {Q_MIN}")
    print(f"ancora: {os.path.basename(paths[anchor])} ({a0}deg, {p0}px)\n")

    rot, rq_after = {}, {}
    for i in keep:
        rot[i] = rotate_to(frames[i], a0 - qual[i][1])
        rq_after[i] = rq.ridge_full(rot[i])[0]
        print(f"  {os.path.basename(paths[i]):<14} gira {a0-qual[i][1]:+5}deg   "
              f"q {qual[i][0]:.2f} -> {rq_after[i]:.2f}")

    h, w = rot[anchor].shape
    m = Mos(h, w)
    m.paste(rot[anchor], PAD, PAD)
    placed = {anchor}
    print()
    while True:
        cand = (-9.0, None, None)
        for k in keep:
            if k in placed:
                continue
            q, off = m.best_offset(rot[k], a0, p0)
            if off and q > cand[0]:
                cand = (q, k, off)
        if cand[1] is None:
            break
        q, k, (dy, dx) = cand
        m.paste(rot[k], dy, dx)
        placed.add(k)
        print(f"  + {os.path.basename(paths[k]):<14} offset=({dy-PAD:+4d},{dx-PAD:+4d})  "
              f"qual_sobrep={q:+.2f}")

    im = m.crop()
    Image.fromarray(np.clip(im, 0, 255).astype(np.uint8)).save(out)
    qf, af, pf = rq.ridge_full(im)
    qin = np.mean([rq_after[i] for i in placed])
    print(f"\nmosaico: {im.shape[1]}x{im.shape[0]} de {len(placed)} frames "
          f"({im.shape[0]*im.shape[1]/(h*w):.2f}x a area de um frame girado) -> {out}")
    print(f"  qualidade dos frames girados: {qin:.2f}")
    print(f"  qualidade do mosaico:         {qf:.2f}  ({af}deg, {pf}px)")
    print(f"  => {'PRESERVOU' if qf >= qin*0.85 else 'degradou'} "
          f"({qf/qin*100:.0f}% da entrada)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
