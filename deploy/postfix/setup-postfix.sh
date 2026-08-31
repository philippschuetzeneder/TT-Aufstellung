#!/usr/bin/env bash
# Install Postfix on the production VPS for outbound mail from @tt-aufstellung.at.
# Run as root on the VPS: sudo bash deploy/postfix/setup-postfix.sh
set -euo pipefail

DOMAIN="${MAIL_DOMAIN:-tt-aufstellung.at}"
FROM_ADDR="${MAIL_FROM:-tt-aufstellung@${DOMAIN}}"
ROOT="${ROOT:-/opt/tt-aufstellung}"
ENV_FILE="${ROOT}/.env"

export DEBIAN_FRONTEND=noninteractive
debconf-set-selections <<< "postfix postfix/mailname string ${DOMAIN}"
debconf-set-selections <<< "postfix postfix/main_mailer_type string 'Internet Site'"

apt-get update
apt-get install -y postfix mailutils

cat >/etc/postfix/main.cf <<EOF
# Managed by tt-aufstellung deploy/postfix/setup-postfix.sh
smtpd_banner = \$myhostname ESMTP
biff = no
append_dot_mydomain = no
readme_directory = no
compatibility_level = 3.6

myhostname = ${DOMAIN}
mydomain = ${DOMAIN}
myorigin = \$mydomain
mydestination = \$myhostname, localhost.\$mydomain, localhost
relayhost =
mynetworks = 127.0.0.0/8 [::1]/128 172.16.0.0/12 10.0.0.0/8
mailbox_size_limit = 0
recipient_delimiter = +
inet_interfaces = all
inet_protocols = ipv4
smtpd_relay_restrictions = permit_mynetworks defer_unauth_destination
EOF

systemctl enable postfix
systemctl restart postfix

# Allow Docker containers to reach Postfix on the host.
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow from 172.16.0.0/12 to any port 25 proto tcp comment "Docker to Postfix" >/dev/null 2>&1 || true
fi

# SMTP_HOST = gateway IP of the compose network (container -> host Postfix).
cd "${ROOT}"
docker compose -f docker-compose.prod.yml up -d --build >/dev/null 2>&1 || true
NET_NAME="$(docker network ls --format '{{.Name}}' | grep '_internal$' | head -1)"
if [[ -n "$NET_NAME" ]]; then
  SMTP_HOST="$(
    docker run --rm --network "$NET_NAME" alpine:3.20 ip route 2>/dev/null \
      | awk '/default/ {print $3; exit}'
  )"
fi
SMTP_HOST="${SMTP_HOST:-host.docker.internal}"

upsert_env() {
  local key="$1"
  local value="$2"
  if [[ -f "$ENV_FILE" ]] && grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    echo "${key}=${value}" >>"$ENV_FILE"
  fi
}

if [[ -f "$ENV_FILE" ]]; then
  upsert_env "REFRESH_REPORT_EMAIL_TO" "p.schuetzeneder@gmail.com"
  upsert_env "SMTP_HOST" "${SMTP_HOST}"
  upsert_env "SMTP_PORT" "25"
  upsert_env "SMTP_USE_TLS" "0"
  upsert_env "SMTP_USE_AUTH" "0"
  upsert_env "SMTP_FROM" "${FROM_ADDR}"
  upsert_env "SMTP_USER" ""
  upsert_env "SMTP_PASSWORD" ""
fi

PUBLIC_IP="$(curl -4 -fsS ifconfig.me || true)"
echo
echo "Postfix installed. Outbound sender: ${FROM_ADDR}"
echo "Updated ${ENV_FILE} for Docker -> host.docker.internal:25"
echo
echo "DNS (Cloudflare): add SPF so Gmail accepts mail from this server:"
echo "  Type: TXT   Name: @"
echo "  Value: v=spf1 ip4:${PUBLIC_IP} -all"
echo
echo "Optional: ask Netcup to set reverse DNS (PTR) for ${PUBLIC_IP} -> ${DOMAIN}."
echo "Then rebuild app: cd ${ROOT} && docker compose -f docker-compose.prod.yml up -d --build"
echo "Test: bash ${ROOT}/scripts/test-refresh-report-email.sh"
