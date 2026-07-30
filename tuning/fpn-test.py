#!/usr/bin/env python3
"""O que a correlacao esta' medindo: identidade do dedo ou assinatura do sensor?

Com impostores de OUTROS sensores a correlacao separava bem (impostor medio
0.171). Com impostores do MESMO sensor ela desaba (0.375 de media, 0.744 de
maximo, 33% de falsa aceitacao). A diferenca so' pode vir do que os dois dedos
tem em comum por serem lidos pelo mesmo hardware.

Duas coisas sao testadas aqui, separadamente:

  FPN   ruido de padrao fixo -- a media de muitos quadros de dedos diferentes
        estima o que o sensor imprime em toda leitura. Subtrair isso deveria
        remover a parte da correlacao que nao e' do dedo.

  ROT   alcance de rotacao. O desvio medido chega a 90 graus e a busca ia so'
        ate' 45, entao parte das falsas rejeicoes e' de origem conhecida.

Rodar os dois juntos confundiria as causas, entao cada um sai numa coluna.
"""
import sys, os, csv, itertools
import numpy as np
from PIL import Image
import importlib.util

_here = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("st", os.path.join(_here, "subtpl.py"))
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)


def load(p):
    return np.asarray(Image.open(p).convert("L"), dtype=np.float64)


def best_ncc(tpls, probe, rot_range):
    best = -2.0
    for deg in range(-rot_range, rot_range + 1, st.ROT_STEP):
        r, v = st.rot_valid(probe, deg)
        for t in tpls:
            c, _, _ = st.slide_ncc(t, r, v)
            if c > best:
                best = c
    return best


def build(rows, d, fpn, genuine_label):
    """Prepara todas as amostras, opcionalmente removendo o padrao fixo."""
    out = []
    for r in rows:
        raw = load(os.path.join(d, r["arquivo"]))
        if fpn is not None:
            raw = raw - fpn
        out.append({**r, "_im": st.prep(raw), "_q": float(r["qualidade"])})
    return out


def enroll_from(samples, label):
    tpls, names = [], []
    for r in sorted([x for x in samples if x["dedo"] == label], key=lambda x: -x["_q"]):
        t = st.center_tpl(r["_im"])
        if t is None:
            continue
        t = (t - t.mean()) / (t.std() + 1e-12)
        tpls.append(t)
        names.append(r["arquivo"])
        if len(tpls) >= st.N_MAX:
            break
    return tpls, names


def evaluate(samples, tpls, names, genuine, rot_range):
    gen, imp = [], []
    for r in samples:
        if r["arquivo"] in names:
            continue
        s = best_ncc(tpls, r["_im"], rot_range)
        if r["dedo"].rstrip("r") == genuine.rstrip("r"):
            gen.append(s)
        else:
            imp.append(s)
    return np.array(gen), np.array(imp)


def report(nome, gen, imp):
    print(f"\n--- {nome} ---")
    if not len(gen) or not len(imp):
        print("  amostras insuficientes")
        return None
    print(f"  genuino  n={len(gen):<3} media {gen.mean():.3f}  min {gen.min():.3f}")
    print(f"  impostor n={len(imp):<3} media {imp.mean():.3f}  max {imp.max():.3f}")
    melhor = (0.0, 0.0)
    for thr in np.arange(0.30, 0.95, 0.025):
        tar, far = (gen >= thr).mean(), (imp >= thr).mean()
        if far == 0 and tar > melhor[1]:
            melhor = (thr, tar)
    # separabilidade independente de limiar: quanto as duas nuvens se afastam
    d = (gen.mean() - imp.mean()) / np.sqrt((gen.var() + imp.var()) / 2 + 1e-12)
    print(f"  separabilidade d' = {d:.2f}")
    if melhor[1] > 0:
        print(f"  melhor sem falsa aceitacao: limiar {melhor[0]:.2f} "
              f"-> {melhor[1]*100:.0f}% de acerto")
    else:
        print("  \033[31mnenhum limiar zera a falsa aceitacao\033[0m")
    return d


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "dados"
    genuine = sys.argv[2] if len(sys.argv) > 2 else "d1"
    rows = list(csv.DictReader(open(os.path.join(d, "manifesto.csv"))))
    rows = [r for r in rows if os.path.exists(os.path.join(d, r["arquivo"]))]

    raws = {r["arquivo"]: load(os.path.join(d, r["arquivo"])) for r in rows}
    print(f"{len(rows)} amostras de {d}/")

    # Estima o padrao fixo com a mediana de TODOS os dedos: o que sobra depois
    # de dedos diferentes se cancelarem e' o que o sensor imprime sozinho.
    # Mediana e nao media, para um quadro estranho nao arrastar a estimativa.
    fpn = np.median(np.stack(list(raws.values())), axis=0)
    print(f"padrao fixo estimado de {len(raws)} quadros: "
          f"media {fpn.mean():.1f}  desvio {fpn.std():.1f}")
    # quanto o padrao fixo pesa em um quadro tipico
    um = next(iter(raws.values()))
    print(f"  correlacao de um quadro qualquer com o padrao fixo: "
          f"{np.corrcoef(um.ravel(), fpn.ravel())[0,1]:.3f}")

    for nome, usar_fpn, rr in (
            ("sem correcao, rotacao +-45 (linha de base)", False, 45),
            ("rotacao ampliada +-90", False, 90),
            ("padrao fixo removido, rotacao +-45", True, 45),
            ("padrao fixo removido + rotacao +-90", True, 90)):
        samples = build(rows, d, fpn if usar_fpn else None, genuine)
        tpls, names = enroll_from(samples, genuine)
        gen, imp = evaluate(samples, tpls, names, genuine, rr)
        report(f"{nome}  ({len(tpls)} subtemplates)", gen, imp)

    print("\nd' compara as duas nuvens sem depender de limiar: quanto maior,")
    print("mais separaveis. Abaixo de ~1.5 nao ha' limiar que preste.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
