#!/bin/bash
# Motor comum das sessoes de captura. Nao rode direto -- use captura-dedo1.sh etc.
#
# Cada dedo passa por duas fases:
#   mesma-posicao  -> mede a repetibilidade: quanto o mesmo toque varia sozinho
#   girado         -> mede de quanto a busca de rotacao precisa
#
# A distincao vai para o manifesto, entao depois da' para dizer se uma falha foi
# por posicao ruim ou por rotacao alem do alcance -- e nao so' "falhou".
set -u

if [ "$(id -u)" -ne 0 ]; then
  echo "precisa de root (para liberar o sensor do fprintd):"
  echo "  sudo bash $0 $*"
  exit 1
fi

# NAO usar $HOME aqui: sob sudo ele pode virar /root, e ai' todos os caminhos
# apontam para lugar nenhum -- em silencio, porque o script so' nao acha nada.
# Resolve pelo usuario que invocou o sudo.
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
[ -d "$REAL_HOME" ] || { echo "nao achei o home de $REAL_USER"; exit 1; }

CAP="${CAP:-$REAL_HOME/projects/ft9201-libfprint/tuning/ft9201-capture.py}"
OUT="${OUT:-$REAL_HOME/captura/dados}"
MODE="${MODE:-$REAL_HOME/.local/bin/ft9201-mode}"

for f in "$CAP" "$MODE"; do
  [ -e "$f" ] || { echo "nao achei: $f"; exit 1; }
done

LABEL="${1:?rotulo}"      # d1, d2, d3
NOME="${2:?nome do dedo}" # "indicador direito"
N_SAME="${3:-5}"
N_ROT="${4:-5}"
WIN="${5:-35}"

restaurar() {
  echo
  echo "--- devolvendo a autenticacao normal ---"
  "$MODE" system 2>/dev/null | sed 's/^/  /'
  # o python rodou como root; devolve os arquivos ao dono da sessao
  [ -d "$OUT" ] && chown -R "$REAL_USER" "$OUT" 2>/dev/null
}
trap restaurar EXIT INT TERM

mkdir -p "$OUT"

echo "=== liberando o sensor (nenhum fprintd de pe') ==="
"$MODE" free 2>&1 | sed 's/^/  /'
echo

python3 "$CAP" "$OUT" "$LABEL" "$N_SAME" "$WIN" \
  "Dedo: $NOME|FASE 1 de 2 — MESMA POSICAO|Encoste sempre do mesmo jeito, o mais parecido que conseguir.|Isso mede o quanto o sensor varia sozinho, sem voce mudar nada." \
  "mesma-posicao"

echo
echo "########################################################"
echo "#  FASE 2 — agora GIRANDO o dedo                       #"
echo "#  Mesmo dedo, mas mude o angulo a cada toque:          #"
echo "#  um bem reto, um inclinado pra esquerda, um pra       #"
echo "#  direita, e dois em angulos exagerados.               #"
echo "#  E' isso que deixa o driver a prova de rotacao.        #"
echo "########################################################"
echo
sleep 2

python3 "$CAP" "$OUT" "${LABEL}r" "$N_ROT" "$WIN" \
  "Dedo: $NOME (GIRANDO)|FASE 2 de 2 — ANGULOS DIFERENTES|Varie bastante o angulo entre um toque e outro." \
  "girado"

echo
echo "=== total acumulado ==="
printf "  amostras: %s\n" "$(ls "$OUT"/*.pgm 2>/dev/null | wc -l)"
printf "  por dedo/fase:\n"
awk -F, 'NR>1 {c[$2"  ("$3")"]++} END {for (k in c) printf "    %-24s %d\n", k, c[k]}' \
  "$OUT/manifesto.csv" 2>/dev/null | sort
