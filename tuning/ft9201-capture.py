#!/usr/bin/env python3
"""Captura dirigida por EVENTO de toque, nao por relogio.

O sensor detecta presenca a ~33 Hz e a maquina de estados so' avanca quando o
dedo aparece ou some. Nada de "encoste por 1s, solte por 2s": voce toca, ele le'
tudo que consegue enquanto o dedo esta' la', percebe a retirada e ja' fica pronto
para o proximo. O ritmo e' seu.

Detalhes de protocolo que fazem isso funcionar (de PROTOCOL.md):
  - presenca sai do request 0x43; 0x00 = sem dedo.
  - a deteccao ADORMECE se nao for re-armada: um read_status a cada ~1s a
    mantem viva. Isso so' e' feito entre toques, nunca durante a rajada, para
    nao atrapalhar a leitura.
  - a retirada precisa de anti-repique: um unico 0x00 no meio de um toque e'
    comum, entao so' conta como retirada apos varias leituras seguidas em zero.

Cada frame sai etiquetado com o numero do toque -- <dedo>_p03_f02.pgm -- e
acompanhado de uma linha no manifesto com dedo, toque, fase, instante,
qualidade, orientacao, media e desvio. Sem isso a analise depois so' responde
"quantas prestaram"; com isso responde QUAIS falharam, DE QUAL dedo, em QUE
momento e POR QUE -- e permite separar a variacao DENTRO de um toque da
variacao ENTRE toques, que e' o que dita o alcance da busca de rotacao.

Uso:
    python3 ft9201-capture.py <pasta> <rotulo> <toques> [segundos] [dica] [fase]
"""
import sys, os, time, csv
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference"))
from ft9201_read import FT9201, IMG_LEN, IMG_W, IMG_H

OUT       = sys.argv[1] if len(sys.argv) > 1 else "captura"
LABEL     = sys.argv[2] if len(sys.argv) > 2 else "d1"
N_PRESSES = int(sys.argv[3]) if len(sys.argv) > 3 else 10
WINDOW    = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
HINT      = sys.argv[5] if len(sys.argv) > 5 else ""
PHASE     = sys.argv[6] if len(sys.argv) > 6 else "livre"
MANIFEST  = "manifesto.csv"

POLL          = 0.03      # ~33 Hz, o mesmo ritmo do driver
REARM_EVERY   = 1.0       # a deteccao dorme sem isso
RELEASE_POLLS = 5         # zeros seguidos que contam como retirada
MAX_PER_PRESS = 8         # o resto de um toque longo e' quase-duplicata
MIN_STD       = 20.0      # abaixo disso o dedo nem encostou direito
HARD_CAP      = 1.6       # multiplicador do tempo antes de desistir

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
    """Mesma medida do matcher, para o aviso sair na hora."""
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


def open_manifest(outdir):
    """Acrescenta ao manifesto, criando o cabecalho so' na primeira vez."""
    path = os.path.join(outdir, MANIFEST)
    novo = not os.path.exists(path)
    fh = open(path, "a", newline="")
    w = csv.writer(fh)
    if novo:
        w.writerow(["arquivo", "dedo", "fase", "toque", "quadro",
                    "t_seg", "qualidade", "orientacao_deg", "media", "desvio"])
    return fh, w


def main():
    os.makedirs(OUT, exist_ok=True)
    mf, mw = open_manifest(OUT)
    try:
        dev = FT9201()
    except Exception as e:
        print(f"\n[!] nao abri o sensor: {e}")
        print("    o fprintd provavelmente esta' segurando o device. rode:")
        print("      sudo ft9201-mode dev\n")
        return 2
    dev.read_status()

    print()
    print("\033[1m" + "=" * 64 + "\033[0m")
    print(f"  \033[1m{LABEL}\033[0m — {N_PRESSES} toques")
    if HINT:
        for line in HINT.split("|"):
            print(f"  {line}")
    print()
    print("  Toque quando quiser, segure um instante, levante. No seu ritmo —")
    print("  ele espera o dedo, nao o relogio. Ctrl-C encerra e salva.")
    print("\033[1m" + "=" * 64 + "\033[0m")
    print()

    press = 0
    saved = 0
    t0 = time.time()
    last_rearm = 0.0
    zeros = 0
    state = "wait_touch"
    burst = []

    try:
        while press < N_PRESSES and time.time() - t0 < WINDOW * HARD_CAP:
            try:
                p = dev.finger_present()
            except Exception:
                time.sleep(POLL)
                continue

            if state == "wait_touch":
                # re-arma so' entre toques: durante a rajada atrapalharia a leitura
                if time.time() - last_rearm > REARM_EVERY:
                    try:
                        dev.read_status()
                    except Exception:
                        pass
                    last_rearm = time.time()

                if p == 0x00:
                    time.sleep(POLL)
                    continue

                press += 1
                burst = []
                zeros = 0
                state = "reading"
                print(f"  \033[1mtoque {press}/{N_PRESSES}\033[0m  ", end="", flush=True)
                # cai direto na leitura, sem esperar nada

            if state == "reading":
                if p == 0x00:
                    zeros += 1
                    if zeros >= RELEASE_POLLS:
                        state = "released"
                    else:
                        time.sleep(POLL)
                        continue
                else:
                    zeros = 0
                    if len(burst) < MAX_PER_PRESS:
                        try:
                            img = dev.read_image()
                        except Exception:
                            img = None
                        if img and len(img) == IMG_LEN:
                            a = np.frombuffer(img, np.uint8).reshape(IMG_H, IMG_W)
                            if a.astype(float).std() >= MIN_STD:
                                burst.append(img)
                                print(".", end="", flush=True)
                    else:
                        time.sleep(POLL)
                        continue

            if state == "released":
                if not burst:
                    print("  \033[33mnada legivel — encoste com um pouco mais de firmeza\033[0m")
                    press -= 1
                    state = "wait_touch"
                    continue

                qs = []
                for i, img in enumerate(burst, 1):
                    a = np.frombuffer(img, np.uint8).reshape(IMG_H, IMG_W).astype(float)
                    q, ang = ridge_quality(a)
                    qs.append((q, ang))
                    name = f"{LABEL}_p{press:02d}_f{i:02d}.pgm"
                    with open(os.path.join(OUT, name), "wb") as f:
                        f.write(b"P5\n%d %d\n255\n" % (IMG_W, IMG_H))
                        f.write(img)
                    mw.writerow([name, LABEL, PHASE, press, i,
                                 f"{time.time()-t0:.2f}", f"{q:.3f}", ang,
                                 f"{a.mean():.1f}", f"{a.std():.1f}"])
                    saved += 1
                mf.flush()

                qv = [q for q, _ in qs]
                best_q, best_a = max(qs, key=lambda t: t[0])
                marca = ("\033[32mbom\033[0m" if best_q >= 0.80 else
                         "\033[33mfraco\033[0m" if best_q >= 0.45 else
                         "\033[31mruim\033[0m")
                print(f"  {len(burst)} quadros  melhor q={best_q:.2f} "
                      f"({best_a}deg)  {marca}")
                if best_q < 0.45:
                    print("      \033[33maperte um pouco mais firme no proximo\033[0m")
                state = "wait_touch"
                last_rearm = 0.0     # re-arma ja' no proximo laco

    except KeyboardInterrupt:
        print("\n  (interrompido)")
    finally:
        try:
            dev.close()
        except Exception:
            pass
        mf.close()

    el = time.time() - t0
    print()
    print(f"  \033[1m{saved} amostras\033[0m de {press} toques em {el:.0f}s -> {OUT}/")
    print(f"  manifesto: {os.path.join(OUT, MANIFEST)}")
    if press < N_PRESSES:
        print(f"  (faltaram {N_PRESSES - press} toques; rode de novo para completar,")
        print("   os arquivos novos nao sobrescrevem os antigos se mudar o rotulo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
