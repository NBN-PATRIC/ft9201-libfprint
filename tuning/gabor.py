#!/usr/bin/env python3
"""Realce de crista por Gabor -- o pre-processamento que faltava.

Diagnostico ate' aqui: correlacao sobre intensidade filtrada nao separa dedos no
mesmo sensor (d' = 0.2). A causa medida e' que dois dedos lidos pelo mesmo
hardware compartilham ruido de padrao fixo, iluminacao e estatistica de textura,
e a NCC nao tem como distinguir isso de identidade.

O filtro de Gabor ataca exatamente essa confusao. Ele e' sintonizado, em cada
ponto, na ORIENTACAO e na FREQUENCIA da crista local -- entao amplifica o que
tem estrutura de crista coerente e suprime o que nao tem. Ruido de padrao fixo
nao tem orientacao coerente: e' justamente o que o filtro joga fora.

Pipeline classico (Hong-Wan-Jain), cada etapa medida separadamente:
  1. normalizacao local     tira gradiente de iluminacao
  2. campo de orientacao    gradientes ao quadrado, suavizados (angulo dobrado)
  3. frequencia da crista   ja' medida neste sensor: periodo 8-14 px
  4. banco de Gabor         um filtro por orientacao, aplicado onde ela vale
  5. binarizacao            opcional, para alimentar extrator de minucias

Sem dependencia de scipy: as convolucoes sao separaveis ou pequenas o bastante.
"""
import sys, os, glob
import numpy as np
from PIL import Image

BLK = 8               # bloco do campo de orientacao
NORI = 16             # orientacoes discretas no banco
PERIOD = 10.0         # periodo de crista medido neste sensor (8-14 px)
GABOR_SZ = 15
SIG_X, SIG_Y = 3.5, 3.5


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def gauss1d(sigma):
    r = max(1, int(round(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * sigma ** 2))
    return k / k.sum(), r


def blur(im, sigma):
    k, r = gauss1d(sigma)
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    o = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(o, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def normalize_local(im, sigma=6.0):
    """Media zero e variancia um em vizinhanca -- mata o gradiente de brilho."""
    m = blur(im, sigma)
    v = np.sqrt(np.maximum(blur((im - m) ** 2, sigma), 1e-9))
    return (im - m) / v


def orientation_field(im, blk=BLK, smooth=2.0):
    """Orientacao local da crista.

    Gradiente elevado ao quadrado em forma complexa: dobrar o angulo resolve a
    ambiguidade de 180 graus (uma crista nao tem 'para cima'), o que permite
    suavizar o campo sem que direcoes opostas se cancelem.
    """
    gy, gx = np.gradient(im)
    gxx, gyy, gxy = gx * gx, gy * gy, gx * gy
    # o angulo dobrado vive no plano (gxx-gyy, 2gxy)
    num = blur(2 * gxy, smooth)
    den = blur(gxx - gyy, smooth)
    ang2 = np.arctan2(num, den)
    # coerencia: quanto o campo concorda consigo mesmo na vizinhanca
    mag = np.sqrt(num ** 2 + den ** 2)
    energia = blur(gxx + gyy, smooth) + 1e-9
    coer = mag / energia
    # a orientacao da CRISTA e' perpendicular ao gradiente
    theta = ang2 / 2.0 + np.pi / 2.0
    return theta, coer


def gabor_bank(period=PERIOD, nori=NORI, size=GABOR_SZ):
    """Um filtro por orientacao, todos na mesma frequencia."""
    r = size // 2
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    bank = []
    for k in range(nori):
        th = np.pi * k / nori
        # x' perpendicular a crista: e' nele que a onda oscila
        xr = x * np.cos(th) + y * np.sin(th)
        yr = -x * np.sin(th) + y * np.cos(th)
        g = np.exp(-0.5 * (xr ** 2 / SIG_X ** 2 + yr ** 2 / SIG_Y ** 2)) \
            * np.cos(2 * np.pi * xr / period)
        g -= g.mean()                      # resposta nula em area lisa
        bank.append(g)
    return bank


def enhance(im, period=PERIOD, coer_min=0.05):
    """Aplica, em cada pixel, o filtro sintonizado na orientacao local."""
    n = normalize_local(im)
    theta, coer = orientation_field(n)
    bank = gabor_bank(period)
    r = GABOR_SZ // 2
    pad = np.pad(n, r, mode="reflect")

    # indice do filtro mais proximo da orientacao de cada pixel
    idx = np.mod(np.round(theta / (np.pi / NORI)).astype(int), NORI)

    # aplica cada filtro na imagem inteira e coleta so' onde ele e' o escolhido:
    # NORI convolucoes completas saem mais rapido que um laco por pixel.
    out = np.zeros_like(n)
    H, W = n.shape
    for k, g in enumerate(bank):
        sel = idx == k
        if not sel.any():
            continue
        acc = np.zeros_like(n)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                w = g[dy + r, dx + r]
                if w == 0.0:
                    continue
                acc += w * pad[r + dy:r + dy + H, r + dx:r + dx + W]
        out[sel] = acc[sel]

    out[coer < coer_min] = 0.0            # area sem crista coerente vira neutra
    s = out.std()
    return out / (s if s > 1e-9 else 1.0), theta, coer


def to_u8(a):
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    if hi - lo < 1e-9:
        hi = lo + 1
    return np.clip((a - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "dados"
    out = sys.argv[2] if len(sys.argv) > 2 else "realcado"
    os.makedirs(out, exist_ok=True)
    ps = sorted(glob.glob(os.path.join(src, "*.pgm")))
    if not ps:
        print(f"nenhum .pgm em {src}/")
        return 1

    print(f"realcando {len(ps)} imagens de {src}/ -> {out}/")
    print(f"  banco: {NORI} orientacoes, periodo {PERIOD}px, janela {GABOR_SZ}px\n")
    print(f"{'arquivo':<20}{'coerencia':>11}{'contraste apos':>16}")
    print("-" * 48)
    for p in ps:
        im = load(p)
        e, theta, coer = enhance(im)
        Image.fromarray(to_u8(e)).save(
            os.path.join(out, os.path.basename(p)))
        print(f"{os.path.basename(p):<20}{coer.mean():>11.3f}{e.std():>16.3f}")
    print(f"\ngravado em {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
