#!/usr/bin/env python3
"""Matcher por correlacao para o FT9201 -- validacao offline.

Premissa, ja' medida: o NBIS extrai no maximo 3 minucias de um frame de 64x80
deste sensor e nunca casa. O blob do fabricante casa nos MESMOS frames, o que
indica matcher por correlacao/padrao. Este script testa se isso se sustenta,
antes de escrever qualquer codigo de driver.

Pontos de projeto que vieram do diagnostico anterior:

  - correlacionar na BANDA DA CRISTA, nao no pixel cru. O gradiente de brilho
    de fundo domina a correlacao crua e enterra o sinal (medido: offsets
    escolhidos com corr 0.10, ate' 66px errados).
  - buscar ROTACAO tambem. As direcoes de crista medidas variam de 0 a 105
    graus entre toques -- o dedo gira, e translacao sozinha nao alcanca.
  - pontuar so' na sobreposicao, exigindo area minima generosa: com pouca
    sobreposicao a correlacao sobe por acaso.

Diferenca crucial para o mosaico: aqui nao ha' MEDIA. Somar frames desalinhados
cancela crista; comparar dois frames nao tem esse risco. Por isso a correlacao
pode funcionar como matcher mesmo tendo falhado como alinhador de mosaico.
"""
import sys, glob, os, itertools, json
import numpy as np
from PIL import Image

MARGIN = 6                  # ignora borda do sensor
SIG_LO, SIG_HI = 1.0, 4.0   # DoG: passa a banda de ~6-14px
ROT_RANGE, ROT_STEP = 60, 3     # graus
TR_RANGE, TR_STEP = 20, 2       # px
MIN_OV_FRAC = 0.45          # fracao minima da area do template que precisa sobrepor


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def gauss1d(sigma):
    r = max(1, int(round(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2))
    return k / k.sum()


def blur(im, sigma):
    k = gauss1d(sigma)
    r = len(k) // 2
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    o = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(o, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def prep(im):
    """Banda da crista + normalizacao. E' o que entra na correlacao."""
    b = blur(im, SIG_LO) - blur(im, SIG_HI)
    b = b[MARGIN:-MARGIN, MARGIN:-MARGIN]
    s = b.std()
    return (b - b.mean()) / (s if s > 1e-9 else 1.0)


def rotate(im, deg):
    """Gira mantendo o tamanho; NaN fora do dado valido para nao poluir a NCC."""
    if deg == 0:
        return im.copy()
    p = Image.fromarray(((np.clip(im, -4, 4) + 4) * 31.875).astype(np.uint8))
    r = np.asarray(p.rotate(-deg, resample=Image.BICUBIC, expand=False, fillcolor=0),
                   dtype=np.float64)
    valid = np.asarray(
        Image.fromarray(np.full(im.shape, 255, np.uint8))
        .rotate(-deg, resample=Image.NEAREST, expand=False, fillcolor=0)) > 127
    out = r / 31.875 - 4.0
    out[~valid] = np.nan
    return out


def ncc(a, b, dy, dx):
    """Correlacao normalizada de b deslocado sobre a, so' onde os dois valem."""
    h, w = a.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    if y1 <= y0 or x1 <= x0:
        return -2.0, 0
    u = a[y0:y1, x0:x1]
    v = b[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    m = ~(np.isnan(u) | np.isnan(v))
    n = int(m.sum())
    if n < MIN_OV_FRAC * a.size:
        return -2.0, n
    uu, vv = u[m], v[m]
    if uu.std() < 1e-9 or vv.std() < 1e-9:
        return -2.0, n
    return float(np.corrcoef(uu, vv)[0, 1]), n


def match(a, b):
    """Melhor NCC sobre rotacao e translacao. Grosseiro e depois refino."""
    best = (-2.0, 0, 0, 0)
    for deg in range(-ROT_RANGE, ROT_RANGE + 1, ROT_STEP):
        rb = rotate(b, deg)
        for dy in range(-TR_RANGE, TR_RANGE + 1, TR_STEP):
            for dx in range(-TR_RANGE, TR_RANGE + 1, TR_STEP):
                c, _ = ncc(a, rb, dy, dx)
                if c > best[0]:
                    best = (c, deg, dy, dx)
    _, bd, by, bx = best
    for deg in range(bd - ROT_STEP, bd + ROT_STEP + 1):
        rb = rotate(b, deg)
        for dy in range(by - TR_STEP, by + TR_STEP + 1):
            for dx in range(bx - TR_STEP, bx + TR_STEP + 1):
                c, _ = ncc(a, rb, dy, dx)
                if c > best[0]:
                    best = (c, deg, dy, dx)
    return best


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "frames"
    paths = sorted(glob.glob(os.path.join(src, "*.pgm")))
    frames = {os.path.basename(p): prep(load(p)) for p in paths}
    names = list(frames)
    print(f"{len(names)} frames de {src}/  (apos recorte: "
          f"{frames[names[0]].shape[1]}x{frames[names[0]].shape[0]})")
    print(f"busca: rotacao +-{ROT_RANGE}deg passo {ROT_STEP}, "
          f"translacao +-{TR_RANGE}px passo {TR_STEP}\n")

    print("== GENUINO: todos os pares do mesmo dedo ==")
    print(f"{'par':<26}{'NCC':>7}{'giro':>7}{'desloc':>12}")
    print("-" * 54)
    gen = []
    for a, b in itertools.combinations(names, 2):
        c, deg, dy, dx = match(frames[a], frames[b])
        gen.append(c)
        print(f"{a[:11]}+{b[:11]:<14}{c:>7.3f}{deg:>6}deg  ({dy:+3d},{dx:+3d})")
    gen = np.array(gen)

    # Impostor sintetico: embaralha blocos de 8x8, o que destroi a estrutura de
    # crista mas preserva histograma e estatistica local. E' um piso, nao um
    # substituto para dedo de verdade.
    print("\n== IMPOSTOR SINTETICO (blocos embaralhados) ==")
    rng = np.random.default_rng(12345)
    imp = []
    for a in names[:6]:
        A = frames[a]
        h, w = A.shape
        bs = 8
        blocks = [A[y:y+bs, x:x+bs] for y in range(0, h-bs+1, bs)
                  for x in range(0, w-bs+1, bs)]
        rng.shuffle(blocks)
        nby, nbx = h // bs, w // bs
        S = np.block([[blocks[r*nbx + c] for c in range(nbx)] for r in range(nby)])
        S = (S - np.nanmean(S)) / (np.nanstd(S) + 1e-9)
        c, deg, dy, dx = match(A, S)
        imp.append(c)
        print(f"{a[:11]} vs embaralhado   {c:>7.3f}{deg:>6}deg  ({dy:+3d},{dx:+3d})")
    imp = np.array(imp)

    print("\n== VEREDITO ==")
    print(f"  genuino:  media {gen.mean():.3f}  mediana {np.median(gen):.3f}  "
          f"min {gen.min():.3f}  max {gen.max():.3f}")
    print(f"  impostor: media {imp.mean():.3f}  max {imp.max():.3f}")
    sep = gen.min() - imp.max()
    print(f"  separacao (pior genuino - melhor impostor) = {sep:+.3f}")
    if sep > 0.10:
        print("  => SEPARA. correlacao e' caminho viavel para este sensor.")
    elif gen.mean() - imp.mean() > 0.15:
        print("  => separa NA MEDIA mas nao no pior caso: precisa de multiplos")
        print("     templates por dedo (varias vistas), como fazem os sensores pequenos.")
    else:
        print("  => NAO separa. correlacao simples nao basta.")

    json.dump({"genuino": gen.tolist(), "impostor": imp.tolist()},
              open("ncc-results.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
