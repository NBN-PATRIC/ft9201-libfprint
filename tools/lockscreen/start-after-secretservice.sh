#!/bin/bash
# Espera a carteira do KDE estar ABERTA e so entao lanca o app.
# Apps Electron/Chromium checam isEncryptionAvailable apenas no startup:
# subir com a carteira fechada = sessao nao persiste + segredos em texto puro.
#
# TIMEOUT=0 (padrao) = espera INDEFINIDAMENTE.
# Antes eram 300s e depois disso o app subia mesmo assim. Isso derrotava o
# proposito no caso mais provavel: ligar a maquina e sair de perto. Voltando 30
# min depois, o OmniRoute ja' estava de pe' ha' muito, sem cripto, e a falha era
# silenciosa. Esperar para sempre e' estritamente melhor -- o app so' nao roda,
# e a notificacao fica na tela dizendo o porque; basta destravar a carteira.
WALLET=kdewallet
TIMEOUT=${SECRETGATE_TIMEOUT:-0}
TAG=start-after-secretservice
avisou=0

# NameHasOwner nao ATIVA o servico; 'gdbus call --dest org.kde.kwalletd6' ativa.
# Subir o kwalletd6 por D-Bus antes do pam_kwallet impede a entrega da chave e a
# carteira nunca abre (incidente de 2026-07-30). So' consultamos a carteira
# quando o daemon ja' esta' de pe' por conta propria.
kwalletd_de_pe() {
  [[ "$(gdbus call --session --dest org.freedesktop.DBus \
        --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner org.kde.kwalletd6 \
        2>/dev/null)" == "(true,)" ]]
}

aberta() {
  kwalletd_de_pe || return 1
  [[ "$(gdbus call --session --dest org.kde.kwalletd6 \
        --object-path /modules/kwalletd6 \
        --method org.kde.KWallet.isOpen "$WALLET" 2>/dev/null)" == "(true,)" ]]
}

# notificacao pelo D-Bus direto: notify-send/kdialog/zenity nao estao
# instalados nesta maquina, mas org.freedesktop.Notifications responde.
notificar() {  # $1=titulo  $2=corpo  $3=timeout_ms
  gdbus call --session --dest org.freedesktop.Notifications \
    --object-path /org/freedesktop/Notifications \
    --method org.freedesktop.Notifications.Notify \
    "KWallet" 0 "dialog-password" "$1" "$2" "[]" "{}" "${3:-8000}" >/dev/null 2>&1 || true
}

for ((i = 0; TIMEOUT <= 0 || i < TIMEOUT; i++)); do
  if aberta; then
    logger -t "$TAG" "carteira aberta apos ${i}s; lancando: $*"
    [[ $avisou -eq 1 ]] && notificar "Carteira aberta" "Subindo $(basename "$1")." 4000
    exec "$@"
  fi
  # avisa uma vez, depois de 5s, que esta esperando o unlock
  if [[ $i -eq 5 && $avisou -eq 0 ]]; then
    avisou=1
    notificar "Destrave a carteira do KDE" \
      "$(basename "$1") esta esperando. Digite a SENHA no primeiro desbloqueio — a digital autentica mas nao abre a carteira." 0
  fi
  # de 10 em 10 min relembra, senao a notificacao some e o app parece travado
  if [[ $((i % 600)) -eq 0 && $i -gt 0 ]]; then
    notificar "Ainda esperando a carteira" \
      "$(basename "$1") nao subiu: a carteira do KDE continua fechada. Destrave com SENHA." 0
  fi
  sleep 1
done

logger -t "$TAG" "TIMEOUT ${TIMEOUT}s sem a carteira abrir; lancando mesmo assim: $*"
notificar "Carteira nao abriu em ${TIMEOUT}s" \
  "$(basename "$1") vai subir sem cripto: a sessao dele nao vai persistir." 0
exec "$@"
