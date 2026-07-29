#!/usr/bin/env python3
"""Matcher por correlacao, v2 -- com janela de tamanho FIXO.

A v1 pontuava sobre a sobreposicao, cujo tamanho variava com a rotacao e o
deslocamento. Isso premia justamente as combinacoes que sobrepoem pouco: os
maximos cairam quase todos em +-60deg, a borda da janela de busca. Mesma
patologia que ja' tinha derrubado o alinhamento do mosaico.

Aqui a pontuacao usa sempre exatamente TPL x TPL pixels, todos validos. Todo
score fica comparavel entre si, e nao ha' como ganhar reduzindo a area.

Desenho vindo do que o proprio fabricante faz: as strings da libfprint da
FocalTech mostram MAX_SUBTEMPLATES_PER_FINGER e alg_max_tpl_count, ou seja
varios subtemplates por dedo em vez de um mosaico. Comparar recorte contra
frame e' exatamente essa operacao.
"""
import sys, glob, os, itertools, json
import numpy as np
from PIL import Image

MARGIN = 6
SIG_LO, SIG_HI = 1.0, 4.0
TPL = 36                      # janela fixa de pontuacao
ROT_RANGE, ROT_STEP = 45, 3


def load(p):
    im = Image.open(p).convert("L")
    return np.asarray(im, dtype=np.float64)


def blur(im, sigma):
    r = max(1, int(round(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2)); k /= k.sum()
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    o = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(o, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def prep(im):
    b = blur(im, SIG_LO) - blur(im, SIG_HI)
    b = b[MARGIN:-MARGIN, MARGIN:-MARGIN]
    s = b.std()
    return (b - b.mean()) / (s if s > 1e-9 else 1.0)


def rot_with_mask(im, deg):
    """Gira; devolve (imagem, mascara de pixels validos)."""
    if deg == 0:
        return im.copy(), np.ones(im.shape, bool)
    lo, hi = im.min(), im.max()
    sc = 254.0 / max(hi - lo, 1e-9)
    q = Image.fromarray(np.clip((im - lo) * sc, 0, 254).astype(np.uint8))
    r = np.asarray(q.rotate(-deg, resample=Image.BICUBIC, expand=False,
                            fillcolor=255), dtype=np.float64)
    valid = r < 254.5
    return r / sc + lo, valid


def center_template(a, n=TPL):
    h, w = a.shape
    if h < n or w < n:
        return None
    y, x = (h - n) // 2, (w - n) // 2
    t = a[y:y + n, x:x + n]
    s = t.std()
    if s < 1e-9:
        return None
    return (t - t.mean()) / s


def best_ncc(tpl, probe):
    """Maior NCC do recorte fixo sobre o probe, varrendo rotacao e posicao."""
    n = tpl.shape[0]
    tv = tpl.ravel()
    best = (-2.0, 0, 0, 0)
    for deg in range(-ROT_RANGE, ROT_RANGE + 1, ROT_STEP):
        r, valid = rot_with_mask(probe, deg)
        H, W = r.shape
        for y in range(0, H - n + 1):
            for x in range(0, W - n + 1):
                if not valid[y:y + n, x:x + n].all():
                    continue
                w_ = r[y:y + n, x:x + n]
                sd = w_.std()
                if sd < 1e-9:
                    continue
                c = float(np.dot(tv, ((w_ - w_.mean()) / sd).ravel()) / (n * n))
                if c > best[0]:
                    best = (c, deg, y, x)
    return best


def score(a, b):
    t = center_template(a)
    if t is None:
        return -2.0, 0
    c, deg, _, _ = best_ncc(t, b)
    return c, deg


def load_dir(d, crop=None, tiles=1):
    """crop=(h,w) recorta para o mesmo tamanho dos nossos frames, senao um probe
    maior teria mais chances de casar por acaso e inflaria o score do impostor.
    tiles>1 pega varios recortes de imagens grandes, para render mais amostras."""
    ps = sorted(glob.glob(os.path.join(d, "*.pgm")) + glob.glob(os.path.join(d, "*.png")))
    out = {}
    for p in ps:
        im = load(p)
        if crop is None:
            if min(im.shape) < 2 * MARGIN + TPL + 4:
                continue
            out[os.path.basename(p)] = prep(im)
            continue
        ch, cw = crop
        H, W = im.shape
        if H < ch or W < cw:
            continue
        n = 0
        for gy in range(tiles):
            for gx in range(tiles):
                y = (H - ch) * gy // max(tiles - 1, 1) if tiles > 1 else (H - ch) // 2
                x = (W - cw) * gx // max(tiles - 1, 1) if tiles > 1 else (W - cw) // 2
                out[f"{os.path.basename(p)}#{n}"] = prep(im[y:y + ch, x:x + cw])
                n += 1
    return out


def main():
    gen_dir = sys.argv[1] if len(sys.argv) > 1 else "frames"
    imp_dir = sys.argv[2] if len(sys.argv) > 2 else None

    G = load_dir(gen_dir)
    names = list(G)
    print(f"genuino: {len(names)} frames de {gen_dir}/ "
          f"(recorte {G[names[0]].shape[1]}x{G[names[0]].shape[0]}, janela {TPL}x{TPL})")
    print(f"busca: rotacao +-{ROT_RANGE}deg passo {ROT_STEP}, "
          f"posicao livre, sempre {TPL*TPL} px validos\n")

    print("== GENUINO (mesmo dedo, todos os pares, nos dois sentidos) ==")
    gen = []
    for a, b in itertools.combinations(names, 2):
        c1, d1 = score(G[a], G[b])
        c2, d2 = score(G[b], G[a])
        c = max(c1, c2)
        gen.append(c)
        print(f"  {a[:11]} x {b[:11]:<12} {c:>6.3f}  ({d1 if c1>=c2 else d2:+3d}deg)")
    gen = np.array(gen)

    imp = []
    if imp_dir and os.path.isdir(imp_dir):
        I = load_dir(imp_dir, crop=(80, 64), tiles=3)
        print(f"\n== IMPOSTOR REAL ({len(I)} recortes 64x80 de {imp_dir}/, outros dedos) ==")
        for a in names:
            for b in I:
                c, d = score(G[a], I[b])
                imp.append(c)
        print(f"  {len(imp)} comparacoes")

    # impostor sintetico: embaralha blocos, destruindo a crista mas mantendo
    # a estatistica local. piso, nao substituto de dedo de verdade.
    print("\n== IMPOSTOR SINTETICO (blocos 6x6 embaralhados) ==")
    rng = np.random.default_rng(7)
    syn = []
    for a in names:
        A = G[a]
        h, w = A.shape
        bs = 6
        ny, nx = h // bs, w // bs
        blocks = [A[y*bs:(y+1)*bs, x*bs:(x+1)*bs] for y in range(ny) for x in range(nx)]
        rng.shuffle(blocks)
        S = np.block([[blocks[r*nx + c] for c in range(nx)] for r in range(ny)])
        c, d = score(A, S)
        syn.append(c)
    syn = np.array(syn)
    print(f"  media {syn.mean():.3f}  max {syn.max():.3f}")

    print("\n== VEREDITO ==")
    print(f"  genuino:   n={len(gen):<4} media {gen.mean():.3f}  mediana "
          f"{np.median(gen):.3f}  min {gen.min():.3f}  max {gen.max():.3f}")
    if len(imp):
        imp = np.array(imp)
        print(f"  impostor:  n={len(imp):<4} media {imp.mean():.3f}  "
              f"p95 {np.percentile(imp,95):.3f}  max {imp.max():.3f}")
        print(f"  sintetico: n={len(syn):<4} media {syn.mean():.3f}  max {syn.max():.3f}")
        thr = np.percentile(imp, 95)
        tar = float((gen >= thr).mean())
        print(f"\n  com limiar no p95 do impostor ({thr:.3f}): "
              f"{tar*100:.0f}% dos pares genuinos passam")
        if gen.min() > imp.max():
            print("  => SEPARACAO PERFEITA nesta amostra.")
        elif tar >= 0.5:
            print("  => separa parcialmente. E' o caso de VARIOS subtemplates por")
            print("     dedo: basta UM casar, entao o que importa e' o melhor de N,")
            print("     nao o par medio.")
        else:
            print("  => nao separa o suficiente.")
    else:
        print(f"  sintetico: media {syn.mean():.3f}  max {syn.max():.3f}")
        print(f"  margem genuino-sintetico: {gen.mean()-syn.mean():+.3f}")

    json.dump({"gen": gen.tolist(), "imp": list(map(float, imp)),
               "syn": syn.tolist()}, open("ncc2-results.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
