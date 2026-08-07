#!/usr/bin/env bash
# Verifică postura de exploatare pe serverul de producție.
#
# Documentația descrie ce ar TREBUI configurat; scriptul verifică ce ESTE.
# Se rulează pe server:
#     bash ops/verify_deploy.sh
#     bash ops/verify_deploy.sh --origin-ip 203.0.113.10 --domain dunarea.exemplu.ro
#
# Nu modifică nimic. Ieșire diferită de zero dacă o verificare esențială pică.

set -uo pipefail

DOMAIN=""
ORIGIN_IP=""
BACKUP_DIR="${BACKUP_DIR:-/home/forge/backups}"
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
if [ -d "$APP_DIR/data/keys" ]; then
  perms=$(stat -c '%a' "$APP_DIR/data/keys")
  [ "$perms" = "700" ] && ok "data/keys/ are 700" || warn "data/keys/ are $perms (recomandat 700)"
  bad_files=$(find "$APP_DIR/data/keys" -type f ! -perm 600 2>/dev/null)
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

# Verificarea care contează cel mai mult: dacă IP-ul de origine răspunde direct
# pe 80/443, tot ce e în fața lui (Cloudflare, limitarea de rată) se ocolește
# cerând direct IP-ul. Se rulează din AFARA rețelei Cloudflare ca să fie
# concludentă — de pe server poate răspunde chiar serverul însuși.
if [ -n "$ORIGIN_IP" ]; then
  if curl -fsS --max-time 8 -o /dev/null "https://${ORIGIN_IP}/" --insecure 2>/dev/null; then
    bad "originea ${ORIGIN_IP}:443 răspunde direct — restrângeți la IP-urile Cloudflare"
  else
    ok "originea ${ORIGIN_IP}:443 nu răspunde direct"
  fi
else
  warn "fără --origin-ip: verificarea restricției Cloudflare nu s-a făcut"
fi

if [ -n "$DOMAIN" ]; then
  hdrs=$(curl -fsSI --max-time 10 "https://${DOMAIN}/" 2>/dev/null)
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
