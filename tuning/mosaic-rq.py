#!/usr/bin/env python3
"""Mosaico alinhado pela QUALIDADE DE CRISTA, nao pela correlacao.

O align-probe mostrou que a correlacao de Pearson sobre a sobreposicao escolhe
offsets ate' 66px errados, com correlacao 0.10 (ou seja, sem pico), enquanto no
offset certo o composto fica com qualidade 0.99-1.15 -- igual ou melhor que os
frames de entrada. Entao o alvo certo de otimizacao e' a qualidade, nao a
correlacao.

Medir a qualidade completa em cada offset seria caro demais. Mas o periodo e a
direcao da crista ja' sao conhecidos por frame, entao basta avaliar

    score = corr(composto, deslocado de 1 periodo) - corr(idem, meio periodo)

e apenas na regiao de SOBREPOSICAO, que e' a unica que muda de offset para
offset. Duas correlacoes por candidato em vez de 84.

Tambem filtra por qualidade antes de fundir: um frame borrado so' suja a media.
"""
import sys, glob, os
import numpy as np
from PIL import Image

H, W = 80, 64
RANGE, COARSE = 56, 4
MIN_OV = 2000            # muito acima dos 600 do driver: 600/5120 casa por acaso
Q_MIN = 0.60             # descarta borrao antes de fundir
PMIN, PMAX = 6, 14
PAD = RANGE + 8
CH, CW = H + 2 * PAD, W + 2 * PAD


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def corr_shift(a, m, dy, dx):
    """Correlacao de a consigo mesmo deslocado, so' onde a mascara m vale."""
    h, w = a.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    if y1 - y0 < 10 or x1 - x0 < 10:
        return np.nan
    ma = m[y0:y1, x0:x1] & m[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    if ma.sum() < 200:
        return np.nan
    u = a[y0:y1, x0:x1][ma]
    v = a[y0 - dy:y1 - dy, x0 - dx:x1 - dx][ma]
    if u.std() < 1e-9 or v.std() < 1e-9:
        return np.nan
    return float(np.corrcoef(u, v)[0, 1])


def ridge_full(im, mask=None, ndir=12):
    """Qualidade completa: varre direcoes e periodos. Usada para medir, nao no laco."""
    if mask is None:
        mask = np.ones_like(im, bool)
    best = (-9.0, 0, 0)
    for k in range(ndir):
        th = np.pi * k / ndir
        uy, ux = np.sin(th), np.cos(th)
        prof = np.array([corr_shift(im, mask, int(round(uy * d)), int(round(ux * d)))
                         for d in range(1, PMAX + PMAX // 2 + 1)])
        vz, pz = prof[PMIN // 2 - 1: PMAX // 2], prof[PMIN - 1: PMAX]
        if np.all(np.isnan(vz)) or np.all(np.isnan(pz)):
            continue
        s = np.nanmax(pz) - np.nanmin(vz)
        if s > best[0]:
            best = (s, int(round(np.degrees(th))), int(np.nanargmax(pz)) + PMIN)
    return best


def ridge_fast(im, mask, ang_deg, period):
    """Duas correlacoes na direcao/periodo ja' conhecidos. E' o que roda no laco."""
    th = np.radians(ang_deg)
    uy, ux = np.sin(th), np.cos(th)
    p, hp = period, max(2, period // 2)
    a = corr_shift(im, mask, int(round(uy * p)), int(round(ux * p)))
    b = corr_shift(im, mask, int(round(uy * hp)), int(round(ux * hp)))
    if np.isnan(a) or np.isnan(b):
        return -9.0
    return a - b


class Mosaic:
    def __init__(self):
        self.s = np.zeros((CH, CW))
        self.n = np.zeros((CH, CW))
        self.count = 0

    def paste(self, f, dy, dx):
        self.s[dy:dy + H, dx:dx + W] += f
        self.n[dy:dy + H, dx:dx + W] += 1
        self.count += 1

    def image(self):
        return np.divide(self.s, np.maximum(self.n, 1e-9))

    def mask(self):
        return self.n > 0

    def trial(self, f, dy, dx):
        """Composto hipotetico e a mascara da sobreposicao (o que muda)."""
        s = self.s.copy(); n = self.n.copy()
        s[dy:dy + H, dx:dx + W] += f
        n[dy:dy + H, dx:dx + W] += 1
        ov = np.zeros((CH, CW), bool)
        ov[dy:dy + H, dx:dx + W] = True
        ov &= self.n > 0
        return np.divide(s, np.maximum(n, 1e-9)), ov, int(ov.sum())

    def best_offset(self, f, ang, per):
        best = (-9.0, None)
        for dy in range(PAD - RANGE, PAD + RANGE + 1, COARSE):
            for dx in range(PAD - RANGE, PAD + RANGE + 1, COARSE):
                img, ov, nov = self.trial(f, dy, dx)
                if nov < MIN_OV:
                    continue
                q = ridge_fast(img, ov, ang, per)
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
                q = ridge_fast(img, ov, ang, per)
                if q > best[0]:
                    best = (q, (dy, dx))
        return best


def crop(m):
    im, msk = m.image(), m.mask()
    ys, xs = np.where(msk)
    im = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = im.shape
    return im[:h - h % 4, :w - w % 4]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "frames"
    out = sys.argv[2] if len(sys.argv) > 2 else "mosaic_rq.pgm"
    paths = sorted(glob.glob(os.path.join(src, "*.pgm")))
    frames = [load(p) for p in paths]

    qual = [ridge_full(f) for f in frames]
    print(f"{len(frames)} frames em {src}/\n")
    print("qualidade por frame:")
    for p, (q, a, pe) in zip(paths, qual):
        mark = "usa" if q >= Q_MIN else "DESCARTA"
        print(f"  {os.path.basename(p):<14} {q:5.2f}  {a:>4}deg {pe:>3}px   {mark}")

    keep = [i for i, (q, _, _) in enumerate(qual) if q >= Q_MIN]
    if len(keep) < 2:
        print(f"\nmenos de 2 frames acima de {Q_MIN}; nada a fundir")
        return 1
    keep.sort(key=lambda i: -qual[i][0])
    anchor = keep[0]
    _, ang, per = qual[anchor]
    print(f"\nancora: {os.path.basename(paths[anchor])} "
          f"(q={qual[anchor][0]:.2f}, {ang}deg, {per}px) — direcao/periodo do alinhamento\n")

    m = Mosaic()
    m.paste(frames[anchor], PAD, PAD)
    placed = {anchor}

    while True:
        cand = (-9.0, None, None)
        for k in keep:
            if k in placed:
                continue
            q, off = m.best_offset(frames[k], ang, per)
            if off and q > cand[0]:
                cand = (q, k, off)
        if cand[1] is None:
            break
        q, k, (dy, dx) = cand
        m.paste(frames[k], dy, dx)
        placed.add(k)
        print(f"  + {os.path.basename(paths[k]):<14} offset=({dy-PAD:+4d},{dx-PAD:+4d})  "
              f"qual_sobrep={q:+.2f}")

    im = crop(m)
    Image.fromarray(np.clip(im, 0, 255).astype(np.uint8)).save(out)
    qf, af, pf = ridge_full(im)
    qin = np.mean([qual[i][0] for i in placed])

    print(f"\nmosaico: {im.shape[1]}x{im.shape[0]} de {len(placed)} frames "
          f"({im.shape[0]*im.shape[1]/(H*W):.2f}x a area) -> {out}")
    print(f"  qualidade dos frames usados: {qin:.2f}")
    print(f"  qualidade do mosaico:        {qf:.2f}  ({af}deg, {pf}px)")
    print(f"  => {'PRESERVOU a crista' if qf >= qin * 0.85 else 'ainda degrada'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
