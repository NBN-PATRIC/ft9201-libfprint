#!/usr/bin/env python3
"""Matcher de subtemplates: o sistema inteiro, medido com leave-one-out.

Ate' aqui so' havia estatistica de PARES. Isto monta o esquema de verdade --
cadastro seleciona N vistas parciais, verificacao pontua a sonda contra todas e
aceita pela melhor -- e mede taxa de acerto e de falsa aceitacao.

Cadastro nao guarda os N primeiros frames: guarda os que passam num portao de
qualidade E que nao sao quase-duplicatas de algum ja' guardado. Sem isso o
conjunto empilha na mesma regiao do dedo e N cresce sem cobrir mais nada -- que
e' exatamente a razao de o mosaico dentro de um mesmo toque nao render area.

Otimizacao que torna isto viavel: a sonda e' girada UMA vez por angulo e
comparada contra todos os subtemplates naquele angulo, em vez de girar por
template. E a correlacao deslizante sai por soma acumulada em vez de laco.
"""
import sys, glob, os, json, itertools
import numpy as np
from PIL import Image

MARGIN = 6
SIG_LO, SIG_HI = 1.0, 4.0
TPL = 36
ROT_RANGE, ROT_STEP = 45, 3
Q_GATE = 0.55          # portao de qualidade de crista no cadastro
DUP_NCC = 0.75         # acima disso conta como quase-duplicata
N_MAX = 12             # teto de subtemplates por dedo
PMIN, PMAX = 6, 14


# ---------------------------------------------------------------- pre-processo
def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def blur(im, s):
    r = max(1, int(round(3 * s)))
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * s ** 2)); k /= k.sum()
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    o = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(o, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def prep(im):
    b = blur(im, SIG_LO) - blur(im, SIG_HI)
    b = b[MARGIN:-MARGIN, MARGIN:-MARGIN]
    s = b.std()
    return (b - b.mean()) / (s if s > 1e-9 else 1.0)


# ------------------------------------------------------------------ qualidade
def _corr_shift(im, dy, dx):
    h, w = im.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    if y1 - y0 < 12 or x1 - x0 < 12:
        return np.nan
    a = im[y0:y1, x0:x1]; b = im[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return np.nan
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def ridge_quality(im, ndir=12):
    best = -9.0
    for k in range(ndir):
        th = np.pi * k / ndir
        uy, ux = np.sin(th), np.cos(th)
        prof = np.array([_corr_shift(im, int(round(uy * d)), int(round(ux * d)))
                         for d in range(1, PMAX + PMAX // 2 + 1)])
        vz, pz = prof[PMIN // 2 - 1: PMAX // 2], prof[PMIN - 1: PMAX]
        if np.all(np.isnan(vz)) or np.all(np.isnan(pz)):
            continue
        best = max(best, np.nanmax(pz) - np.nanmin(vz))
    return best


# ------------------------------------------------------------------ correlacao
def rot_valid(im, deg):
    if deg == 0:
        return im.copy(), np.ones(im.shape, bool)
    lo, hi = im.min(), im.max()
    sc = 254.0 / max(hi - lo, 1e-9)
    q = Image.fromarray(np.clip((im - lo) * sc, 0, 254).astype(np.uint8))
    r = np.asarray(q.rotate(-deg, resample=Image.BICUBIC, expand=False,
                            fillcolor=255), dtype=np.float64)
    return r / sc + lo, r < 254.5


def _integral(a):
    return np.pad(np.cumsum(np.cumsum(a, 0), 1), ((1, 0), (1, 0)))


def _boxsum(I, n):
    return I[n:, n:] - I[:-n, n:] - I[n:, :-n] + I[:-n, :-n]


def slide_ncc(tpl, probe, valid):
    """NCC do recorte fixo em toda posicao valida do probe, vetorizado.

    Soma acumulada da' media e variancia de cada janela de uma vez; o produto
    cruzado sai por correlacao FFT. Evita o laco em Python, que dominava o custo.
    """
    n = tpl.shape[0]
    H, W = probe.shape
    if H < n or W < n:
        return -2.0, 0, 0
    t = tpl - tpl.mean()
    ts = t.std()
    if ts < 1e-9:
        return -2.0, 0, 0
    t = t / ts

    I1, I2 = _integral(probe), _integral(probe ** 2)
    s1, s2 = _boxsum(I1, n), _boxsum(I2, n)
    cnt = n * n
    mean = s1 / cnt
    var = np.maximum(s2 / cnt - mean ** 2, 0.0)
    sd = np.sqrt(var)

    # produto cruzado por FFT (correlacao, nao convolucao)
    fs = (H, W)
    num = np.fft.irfft2(np.fft.rfft2(probe, fs) * np.conj(np.fft.rfft2(t, fs)), fs)
    num = num[:H - n + 1, :W - n + 1]

    # so' posicoes 100% validas
    Iv = _integral(valid.astype(np.float64))
    ok = _boxsum(Iv, n) >= cnt - 0.5

    with np.errstate(divide="ignore", invalid="ignore"):
        ncc = (num / cnt - mean * t.mean()) / np.where(sd > 1e-9, sd, np.inf)
    ncc = np.where(ok, ncc, -2.0)
    k = int(np.argmax(ncc))
    y, x = divmod(k, ncc.shape[1])
    return float(ncc[y, x]), y, x


def match_best(templates, probe):
    """Melhor NCC entre a sonda e QUALQUER subtemplate, varrendo rotacao."""
    best = -2.0
    for deg in range(-ROT_RANGE, ROT_RANGE + 1, ROT_STEP):
        r, v = rot_valid(probe, deg)          # gira UMA vez por angulo
        for t in templates:
            c, _, _ = slide_ncc(t, r, v)
            if c > best:
                best = c
    return best


# -------------------------------------------------------------------- cadastro
def center_tpl(a, n=TPL):
    h, w = a.shape
    if h < n or w < n:
        return None
    y, x = (h - n) // 2, (w - n) // 2
    t = a[y:y + n, x:x + n]
    return t if t.std() > 1e-9 else None


def enroll(frames, verbose=False):
    """Escolhe subtemplates: passa no portao de qualidade e nao duplica."""
    cand = []
    for name, im in frames:
        q = ridge_quality(im)
        if q >= Q_GATE:
            cand.append((q, name, im))
    cand.sort(key=lambda r: -r[0])            # melhores primeiro
    kept = []
    for q, name, im in cand:
        t = center_tpl(im)
        if t is None:
            continue
        dup = False
        for _, _, kt in kept:
            c = match_best([kt], im)
            if c >= DUP_NCC:
                dup = True
                break
        if dup:
            if verbose:
                print(f"      - {name} (q={q:.2f}) quase-duplicata, fora")
            continue
        kept.append((q, name, t))
        if verbose:
            print(f"      + {name} (q={q:.2f})")
        if len(kept) >= N_MAX:
            break
    return [t for _, _, t in kept], [n for _, n, _ in kept]


def load_dir(d, crop=None, tiles=1):
    ps = sorted(glob.glob(os.path.join(d, "*.pgm")) + glob.glob(os.path.join(d, "*.png")))
    out = []
    for p in ps:
        im = load(p)
        if crop is None:
            if min(im.shape) >= 2 * MARGIN + TPL + 4:
                out.append((os.path.basename(p), prep(im)))
            continue
        ch, cw = crop
        H, W = im.shape
        if H < ch or W < cw:
            continue
        for gy in range(tiles):
            for gx in range(tiles):
                y = (H - ch) * gy // max(tiles - 1, 1) if tiles > 1 else (H - ch) // 2
                x = (W - cw) * gx // max(tiles - 1, 1) if tiles > 1 else (W - cw) // 2
                out.append((f"{os.path.basename(p)}#{gy}{gx}", prep(im[y:y + ch, x:x + cw])))
    return out


def main():
    gen_dir = sys.argv[1] if len(sys.argv) > 1 else "frames"
    imp_dir = sys.argv[2] if len(sys.argv) > 2 else None

    G = load_dir(gen_dir)
    print(f"{len(G)} frames de {gen_dir}/   janela {TPL}x{TPL}, "
          f"portao de qualidade {Q_GATE}, anti-duplicata {DUP_NCC}\n")

    print("== LEAVE-ONE-OUT: cadastra com os outros, verifica com o retido ==")
    print(f"{'retido':<14}{'N subtpl':>9}{'melhor NCC':>12}")
    print("-" * 37)
    gen_scores = []
    for i, (name, im) in enumerate(G):
        rest = [g for j, g in enumerate(G) if j != i]
        tpls, used = enroll(rest)
        if not tpls:
            print(f"{name:<14}{0:>9}   (cadastro vazio)")
            continue
        s = match_best(tpls, im)
        gen_scores.append(s)
        print(f"{name:<14}{len(tpls):>9}{s:>12.3f}")
    gen_scores = np.array(gen_scores)

    # cadastro completo, para as sondas impostoras
    tpls_all, used_all = enroll(G, verbose=True)
    print(f"\ncadastro com todos os frames: {len(tpls_all)} subtemplates")
    print("  " + " ".join(used_all))

    imp_scores = []
    if imp_dir and os.path.isdir(imp_dir):
        I = load_dir(imp_dir, crop=(80, 64), tiles=3)
        print(f"\n== IMPOSTORES: {len(I)} sondas de {imp_dir}/ contra o cadastro ==")
        for name, im in I:
            imp_scores.append(match_best(tpls_all, im))
        imp_scores = np.array(imp_scores)
        print(f"  media {imp_scores.mean():.3f}  p95 {np.percentile(imp_scores,95):.3f}"
              f"  max {imp_scores.max():.3f}")

    print("\n== DESEMPENHO DO SISTEMA ==")
    print(f"  genuino (leave-one-out): media {gen_scores.mean():.3f}  "
          f"min {gen_scores.min():.3f}  max {gen_scores.max():.3f}")
    if len(imp_scores):
        print(f"  impostor:                media {imp_scores.mean():.3f}  "
              f"max {imp_scores.max():.3f}")
        print(f"\n  {'limiar':>8}{'aceita genuino':>17}{'aceita impostor':>18}")
        print("  " + "-" * 42)
        for thr in np.arange(0.30, 0.75, 0.05):
            tar = float((gen_scores >= thr).mean())
            far = float((imp_scores >= thr).mean())
            flag = "  <- separa" if far == 0 and tar == 1 else ""
            print(f"  {thr:>8.2f}{tar*100:>16.0f}%{far*100:>17.0f}%{flag}")
        if gen_scores.min() > imp_scores.max():
            print(f"\n  => SEPARACAO COMPLETA nesta amostra: qualquer limiar entre "
                  f"{imp_scores.max():.3f} e {gen_scores.min():.3f}")
        else:
            print(f"\n  => sobreposicao: pior genuino {gen_scores.min():.3f} "
                  f"<= melhor impostor {imp_scores.max():.3f}")

    json.dump({"gen": gen_scores.tolist(),
               "imp": list(map(float, imp_scores))},
              open("subtpl-results.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
