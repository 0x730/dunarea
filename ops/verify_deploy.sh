#!/usr/bin/env bash
# Verifică postura de exploatare pe serverul de producție.
#
# Documentația descrie ce ar TREBUI configurat; scriptul verifică ce ESTE.
# Se rulează pe server:
#     bash ops/verify_deploy.sh
#     bash ops/verify_deploy.sh --origin-ip 157.90.144.210 --domain dunarea.info
#
# Nu modifică nimic. Ieșire diferită de zero dacă o verificare esențială pică.

set -uo pipefail

DOMAIN=""
ORIGIN_IP=""
BACKUP_DIR="${BACKUP_DIR:-/home/dunarea/dunarea.info/backups}"
APP_PORT="${PORT:-7300}"
FAIL=0
WARN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)    DOMAIN="$2"; shift 2 ;;
    --origin-ip) ORIGIN_IP="$2"; shift 2 ;;
    --backups)   BACKUP_DIR="$2"; shift 2 ;;
    *) echo "argument necunoscut: $1"; exit 2 ;;
  esac
done

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mPICĂ\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mATENȚIE\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# --------------------------------------------------------------- aplicație ---
head_ "Aplicație"
if curl -fsS --max-time 10 "http://127.0.0.1:${APP_PORT}/api/health" >/tmp/_health.$$ 2>/dev/null; then
  ok "/api/health răspunde pe 127.0.0.1:${APP_PORT}"
  EXPECTED_VERSION=$(tr -d '[:space:]' <"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/VERSION")
  if grep -q "\"version\": \"${EXPECTED_VERSION}\"" /tmp/_health.$$; then
    ok "runtime-ul raportează versiunea ${EXPECTED_VERSION}"
  else
    bad "versiunea runtime nu coincide cu VERSION (${EXPECTED_VERSION})"
  fi
  if grep -q '"warmup_done": true' /tmp/_health.$$; then
    ok "warmup terminat"
  else
    warn "warmup neterminat (normal în primele minute după pornire)"
  fi
else
  bad "aplicația nu răspunde pe 127.0.0.1:${APP_PORT}"
fi
rm -f /tmp/_health.$$

# Serverul TREBUIE să asculte doar pe loopback: dacă ascultă pe 0.0.0.0, portul
# aplicației e expus direct și ocolește nginx, limitarea de rată și Cloudflare.
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | grep -qE "0\.0\.0\.0:${APP_PORT}|\[::\]:${APP_PORT}"; then
    bad "aplicația ascultă pe toate interfețele, nu doar pe loopback"
  else
    ok "aplicația ascultă doar pe loopback"
  fi
fi

# ------------------------------------------------------------------ secrete ---
head_ "Secrete"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -e "$APP_DIR/.env" ]; then
  ENV_PERMS=$(stat -Lc '%a' "$APP_DIR/.env")
  [ "$ENV_PERMS" = "600" ] && ok ".env are 600" \
    || bad ".env are $ENV_PERMS (obligatoriu 600, inclusiv când este gol)"
fi
if [ -d "$APP_DIR/data/keys" ]; then
  KEY_DIR=$(readlink -f "$APP_DIR/data/keys")
  perms=$(stat -Lc '%a' "$KEY_DIR")
  [ "$perms" = "700" ] && ok "data/keys/ are 700" || warn "data/keys/ are $perms (recomandat 700)"
  bad_files=$(find "$KEY_DIR" -type f ! -perm 600 2>/dev/null)
  [ -z "$bad_files" ] && ok "fișierele de chei au 600" \
    || bad "chei cu permisiuni prea largi: $bad_files"
fi
# Un secret comis pe un repo PUBLIC e ars definitiv: rămâne în istoric,
# în forkuri și în arhive terțe.
if git -C "$APP_DIR" ls-files --error-unmatch data/keys >/dev/null 2>&1; then
  bad "data/keys este URMĂRIT de git — rotiți cheile imediat"
else
  ok "data/keys nu este urmărit de git"
fi
if git -C "$APP_DIR" log --all --oneline -- 'data/keys/*' 2>/dev/null | grep -q .; then
  bad "istoricul git conține data/keys — cheile trebuie considerate compromise"
else
  ok "niciun secret în istoricul git"
fi

# ----------------------------------------------------------------- backupuri ---
head_ "Backupuri"
if [ -d "$BACKUP_DIR" ]; then
  newest=$(ls -1t "$BACKUP_DIR"/cache-*.db 2>/dev/null | head -1)
  if [ -n "$newest" ]; then
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
    if [ "$age_h" -le 26 ]; then ok "cel mai recent backup are ${age_h}h: $(basename "$newest")"
    else bad "cel mai recent backup are ${age_h}h — cronul nu rulează"; fi
    # Un backup neverificat e o ipoteză, nu un backup.
    if python3 "$APP_DIR/ops/backup.py" --verify-only "$newest" >/dev/null 2>&1; then
      ok "cel mai recent backup trece verificarea de integritate"
    else
      bad "cel mai recent backup NU trece verificarea"
    fi
    perms=$(stat -c '%a' "$newest")
    [ "$perms" = "600" ] && ok "backupul are 600" || bad "backupul are $perms (conține aceleași date ca baza)"
  else
    bad "niciun backup în $BACKUP_DIR"
  fi
else
  bad "directorul de backup $BACKUP_DIR nu există"
fi
crontab -l 2>/dev/null | grep -q "ops/backup.py" \
  && ok "cronul de backup e instalat" \
  || warn "niciun cron cu ops/backup.py în crontab-ul acestui utilizator"

# --------------------------------------------------------------------- rețea ---
head_ "Rețea"
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>/dev/null | grep -qi "^Status: active" \
    && ok "ufw activ" || warn "ufw inactiv sau inaccesibil fără sudo"
fi

# Verificarea folosește domeniul ca SNI/Host, dar conectează direct la IP. Un
# simplu https://IP/ ar lovi vhost-ul catch-all și ar da un fals sentiment de
# siguranță pe un server care găzduiește mai multe site-uri.
if [ -n "$ORIGIN_IP" ]; then
  if [ -z "$DOMAIN" ]; then
    warn "--origin-ip are nevoie și de --domain pentru testul SNI concludent"
  else
    direct_code=$(curl -sS --max-time 8 --resolve "${DOMAIN}:443:${ORIGIN_IP}" \
      -o /dev/null -w '%{http_code}' "https://${DOMAIN}/" 2>/dev/null || true)
    case "$direct_code" in
      000|403|444) ok "originea ${ORIGIN_IP}:443 respinge accesul direct pentru ${DOMAIN}" ;;
      2??|3??) bad "originea ${ORIGIN_IP}:443 servește direct ${DOMAIN} (HTTP ${direct_code})" ;;
      *) warn "răspuns direct neașteptat de la origine: HTTP ${direct_code:-necunoscut}" ;;
    esac
  fi
else
  warn "fără --origin-ip: verificarea restricției Cloudflare nu s-a făcut"
fi

if [ -n "$DOMAIN" ]; then
  hdrs=$(curl -fsS --max-time 10 -D - -o /dev/null "https://${DOMAIN}/" 2>/dev/null)
  echo "$hdrs" | grep -qi "^cf-ray:" \
    && ok "traficul trece prin Cloudflare (cf-ray prezent)" \
    || warn "fără antet cf-ray — norul portocaliu poate fi dezactivat"
  for h in content-security-policy x-content-type-options referrer-policy; do
    echo "$hdrs" | grep -qi "^${h}:" && ok "antet $h prezent" || bad "antet $h lipsă"
  done
fi

# -------------------------------------------------------------------- raport ---
printf '\n'
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31m%d verificări au picat\033[0m, %d avertismente\n' "$FAIL" "$WARN"
  exit 1
fi
printf '\033[32mToate verificările esențiale trec\033[0m (%d avertismente)\n' "$WARN"
exit 0
