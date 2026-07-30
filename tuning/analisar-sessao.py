#!/usr/bin/env python3
"""Analisa uma sessao de captura: o que casou, o que falhou e por que.

O manifesto guarda dedo, fase, toque, qualidade e orientacao de cada amostra.
Com isso a analise nao para em "X% casou" -- ela atribui cada falha a uma causa:

  qualidade   a amostra nao tinha crista suficiente para ser comparada
  rotacao     casou melhor no limite da busca, sinal de que o alcance e' curto
  regiao      qualidade boa e rotacao folgada, mas o pedaco do dedo nao se
              sobrepoe ao que esta' cadastrado

A distincao importa porque cada causa tem conserto diferente: portao de
qualidade, alcance de rotacao, ou mais vistas no cadastro.

Uso:  python3 analisar-sessao.py <pasta-da-sessao> [dedo-genuino]
"""
import sys, os, csv, glob
import numpy as np
from PIL import Image
import importlib.util

_here = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("st", os.path.join(_here, "subtpl.py"))
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)

ROT_RANGE, ROT_STEP = st.ROT_RANGE, st.ROT_STEP
THRESHOLD = 0.50


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def match_detail(templates, probe):
    """Melhor NCC e em que rotacao ele aconteceu."""
    best, best_deg = -2.0, 0
    for deg in range(-ROT_RANGE, ROT_RANGE + 1, ROT_STEP):
        r, v = st.rot_valid(probe, deg)
        for t in templates:
            c, _, _ = st.slide_ncc(t, r, v)
            if c > best:
                best, best_deg = c, deg
    return best, best_deg


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "dados"
    genuino = sys.argv[2] if len(sys.argv) > 2 else "d1"
    mpath = os.path.join(d, "manifesto.csv")
    if not os.path.exists(mpath):
        print(f"sem manifesto em {d}/")
        return 1

    rows = list(csv.DictReader(open(mpath)))
    rows = [r for r in rows if os.path.exists(os.path.join(d, r["arquivo"]))]
    if not rows:
        print("manifesto sem arquivos correspondentes")
        return 1

    for r in rows:
        r["_im"] = st.prep(load(os.path.join(d, r["arquivo"])))
        r["_q"] = float(r["qualidade"])
        r["_ang"] = int(r["orientacao_deg"])

    dedos = sorted({r["dedo"] for r in rows})
    print(f"sessao: {d}/   {len(rows)} amostras")
    for dd in dedos:
        sub = [r for r in rows if r["dedo"] == dd]
        qs = np.array([r["_q"] for r in sub])
        angs = sorted({r["_ang"] for r in sub})
        print(f"  {dd:<6} {len(sub):>3} amostras  q media {qs.mean():.2f}  "
              f"orientacoes {angs}")

    # cadastro: so' o dedo genuino na fase de mesma posicao, que e' o que um
    # cadastro real teria. As amostras giradas ficam para o teste.
    enr = [r for r in rows if r["dedo"] == genuino]
    if not enr:
        print(f"\nsem amostras de '{genuino}' para cadastrar")
        return 1

    tpls, names = [], []
    for r in sorted(enr, key=lambda x: -x["_q"]):
        if r["_q"] < st.Q_GATE:
            continue
        t = np.empty((st.TPL, st.TPL))
        tt = st.center_tpl(r["_im"])
        if tt is None:
            continue
        t = (tt - tt.mean()) / (tt.std() + 1e-12)
        dup = any(match_detail([t2], r["_im"])[0] >= st.DUP_NCC for t2 in tpls)
        if dup:
            continue
        tpls.append(t)
        names.append(r["arquivo"])
        if len(tpls) >= st.N_MAX:
            break
    print(f"\ncadastro ('{genuino}', mesma posicao): {len(tpls)} subtemplates")
    print("  " + " ".join(names))

    print(f"\n{'amostra':<18}{'dedo':<6}{'fase':<15}{'q':>6}{'ori':>6}"
          f"{'NCC':>7}{'giro':>7}  veredito")
    print("-" * 82)

    gen_s, imp_s = [], []
    falhas = []
    for r in sorted(rows, key=lambda x: (x["dedo"], x["arquivo"])):
        if r["arquivo"] in names:
            continue                     # nao testar contra o proprio cadastro
        s, deg = match_detail(tpls, r["_im"])
        mesmo = r["dedo"].rstrip("r") == genuino.rstrip("r")
        aceito = s >= THRESHOLD
        (gen_s if mesmo else imp_s).append(s)

        if mesmo and not aceito:
            if r["_q"] < 0.60:
                causa = "\033[33mFALSA REJEICAO — qualidade baixa\033[0m"
            elif abs(deg) >= ROT_RANGE - ROT_STEP:
                causa = "\033[31mFALSA REJEICAO — rotacao no limite da busca\033[0m"
            else:
                causa = "\033[33mFALSA REJEICAO — regiao do dedo nao coberta\033[0m"
            falhas.append((r, s, deg, causa))
        elif mesmo:
            causa = "\033[32maceito (correto)\033[0m"
        elif aceito:
            causa = "\033[31mFALSA ACEITACAO\033[0m"
            falhas.append((r, s, deg, causa))
        else:
            causa = "rejeitado (correto)"

        print(f"{r['arquivo']:<18}{r['dedo']:<6}{r['fase']:<15}"
              f"{r['_q']:>6.2f}{r['_ang']:>6}{s:>7.3f}{deg:>+6}d  {causa}")

    gen_s, imp_s = np.array(gen_s), np.array(imp_s)
    print("\n== RESUMO ==")
    if len(gen_s):
        print(f"  genuino:  n={len(gen_s):<3} media {gen_s.mean():.3f}  "
              f"min {gen_s.min():.3f}  max {gen_s.max():.3f}")
    if len(imp_s):
        print(f"  impostor: n={len(imp_s):<3} media {imp_s.mean():.3f}  "
              f"max {imp_s.max():.3f}   \033[1m(mesmo sensor)\033[0m")

    if len(gen_s) and len(imp_s):
        print(f"\n{'limiar':>8}{'aceita genuino':>17}{'aceita impostor':>18}")
        print("  " + "-" * 40)
        melhor = None
        for thr in np.arange(0.30, 0.80, 0.05):
            tar = (gen_s >= thr).mean()
            far = (imp_s >= thr).mean()
            marca = ""
            if far == 0 and (melhor is None or tar > melhor[1]):
                melhor = (thr, tar); marca = "  <-"
            print(f"{thr:>8.2f}{tar*100:>16.0f}%{far*100:>17.0f}%{marca}")
        if melhor:
            print(f"\n  melhor ponto sem falsa aceitacao: limiar {melhor[0]:.2f} "
                  f"-> {melhor[1]*100:.0f}% de acerto")
        if gen_s.min() > imp_s.max():
            print(f"  \033[32mSEPARACAO COMPLETA\033[0m: qualquer limiar entre "
                  f"{imp_s.max():.3f} e {gen_s.min():.3f}")

    # de quanto a busca de rotacao precisa, medido e nao chutado
    girados = [r for r in rows if r["fase"] == "girado"]
    if girados:
        angs = [r["_ang"] for r in girados]
        base = [r["_ang"] for r in rows if r["fase"] == "mesma-posicao"]
        if base:
            ref = int(np.median(base))
            desv = [min(abs(a - ref), 180 - abs(a - ref)) for a in angs]
            print(f"\n== ALCANCE DE ROTACAO ==")
            print(f"  orientacao de referencia (mesma posicao): {ref}deg")
            print(f"  desvio das amostras giradas: max {max(desv)}deg, "
                  f"mediana {int(np.median(desv))}deg")
            print(f"  busca atual: +-{ROT_RANGE}deg  ->  "
                  + ("\033[32msuficiente\033[0m" if max(desv) <= ROT_RANGE
                     else f"\033[31mCURTA, precisa de +-{max(desv)}deg\033[0m"))

    if falhas:
        print("\n== O QUE FALHOU E POR QUE ==")
        for r, s, deg, causa in falhas:
            print(f"  {r['arquivo']:<18} q={r['_q']:.2f} ori={r['_ang']}deg "
                  f"NCC={s:.3f} giro={deg:+d}  {causa}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
