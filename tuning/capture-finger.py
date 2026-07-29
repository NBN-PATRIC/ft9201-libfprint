#!/usr/bin/env python3
"""Captura frames de UM dedo com aviso de qualidade na hora.

Diferenca para o batch-capture.py: aquele so' filtrava por desvio padrao, que
aceita borrao com bastante contraste. Aqui cada frame passa pela mesma medida de
crista que o matcher usa, entao o frame ruim e' recusado na hora e voce sabe se
precisa apertar mais ou mudar a posicao -- em vez de descobrir depois que a
captura inteira nao presta.

Uso:
    sudo python3 capture-finger.py <pasta-de-saida> [quantos] [segundos]

Precisa do fprintd parado, senao ele segura o device:
    sudo ft9201-mode dev     # ou: sudo systemctl stop fprintd
"""
import sys, time, os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ft9201_read import FT9201, IMG_LEN, IMG_W, IMG_H

OUT = sys.argv[1] if len(sys.argv) > 1 else "frames2"
WANT = int(sys.argv[2]) if len(sys.argv) > 2 else 12
WINDOW = int(sys.argv[3]) if len(sys.argv) > 3 else 180

# 0.45 medido sobre os frames de referencia: recusa exatamente os tres que o
# diagnostico marcou como borrao e nenhum dos uteis. Nao confundir com o portao
# de mesmo nome no matcher, que roda sobre a imagem ja' filtrada e por isso usa
# outra escala.
Q_GATE = 0.45
PMIN, PMAX = 6, 14


def corr_shift(im, dy, dx):
    h, w = im.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    if y1 - y0 < 12 or x1 - x0 < 12:
        return np.nan
    a = im[y0:y1, x0:x1]
    b = im[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return np.nan
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def ridge_quality(im, ndir=12):
    """Mesma medida do matcher: contraste periodico na melhor direcao."""
    best, ang = -9.0, 0
    for k in range(ndir):
        th = np.pi * k / ndir
        uy, ux = np.sin(th), np.cos(th)
        prof = np.array([corr_shift(im, int(round(uy * d)), int(round(ux * d)))
                         for d in range(1, PMAX + PMAX // 2 + 1)])
        vz, pz = prof[PMIN // 2 - 1: PMAX // 2], prof[PMIN - 1: PMAX]
        if np.all(np.isnan(vz)) or np.all(np.isnan(pz)):
            continue
        s = np.nanmax(pz) - np.nanmin(vz)
        if s > best:
            best, ang = s, int(round(np.degrees(th)))
    return best, ang


def main():
    os.makedirs(OUT, exist_ok=True)
    d = FT9201()
    d.read_status()

    print()
    print("=" * 62)
    print(f"  Vou guardar {WANT} frames BONS em {OUT}/")
    print("  Encoste o dedo, segure ~1s, levante. Repita.")
    print("  MUDE UM POUCO A POSICAO a cada toque -- o cadastro precisa")
    print("  de vistas de partes diferentes do dedo, nao da mesma.")
    print("  Aviso a qualidade de cada toque na hora.")
    print("=" * 62)
    print()

    got, tried, t0, last = 0, 0, time.time(), 0.0
    rejeitados = 0
    try:
        while got < WANT and time.time() - t0 < WINDOW:
            try:
                p = d.finger_present()
            except Exception:
                time.sleep(0.05)
                continue

            if time.time() - last > 1.0:      # re-arma, senao a deteccao dorme
                try:
                    d.read_status()
                except Exception:
                    pass
                last = time.time()

            if p == 0x00:
                time.sleep(0.03)
                continue

            try:
                img = d.read_image()
            except Exception:
                continue
            if len(img) != IMG_LEN:
                continue

            a = np.frombuffer(img, dtype=np.uint8).reshape(IMG_H, IMG_W).astype(float)
            if a.std() < 20:                  # nem chegou a encostar direito
                continue

            tried += 1
            q, ang = ridge_quality(a)
            if q < Q_GATE:
                rejeitados += 1
                print(f"    x recusado (crista fraca: {q:.2f}) — "
                      f"aperte um pouco mais firme", flush=True)
                time.sleep(0.7)
                continue

            got += 1
            path = os.path.join(OUT, f"frame{got:02d}.pgm")
            with open(path, "wb") as f:
                f.write(b"P5\n%d %d\n255\n" % (IMG_W, IMG_H))
                f.write(img)
            barra = "#" * got + "." * (WANT - got)
            print(f"    ok  [{barra}] {got}/{WANT}   qualidade {q:.2f}  "
                  f"crista a {ang}deg", flush=True)
            time.sleep(0.7)                   # espera levantar
    except KeyboardInterrupt:
        print("\n  (interrompido)")
    finally:
        d.close()

    print()
    if got >= WANT:
        print(f"[ok] {got} frames bons em {OUT}/  ({rejeitados} recusados de {tried})")
    else:
        print(f"[!] so' {got} de {WANT} frames bons em {OUT}/ "
              f"({rejeitados} recusados de {tried})")
        if rejeitados > got:
            print("    muitos recusados: dedo seco ou pouca pressao costumam ser a causa.")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
