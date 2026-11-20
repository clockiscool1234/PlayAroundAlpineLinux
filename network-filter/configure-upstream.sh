#!/bin/sh
set -eu

upstream_file=${1:-/run/squid/upstream.conf}
: > "$upstream_file"

if [ -z "${VPN_UPSTREAM_PROXY:-}" ]; then
  echo "Egress: direct (no VPN upstream configured)"
  exit 0
fi

vpn_host=${VPN_UPSTREAM_PROXY%:*}
vpn_port=${VPN_UPSTREAM_PROXY##*:}
case "$vpn_host" in
  ""|"$VPN_UPSTREAM_PROXY")
    echo "Invalid VPN_UPSTREAM_PROXY=$VPN_UPSTREAM_PROXY (expected host:port)" >&2
    exit 2
    ;;
esac
case "$vpn_port" in
  ""|*[!0-9]*)
    echo "Invalid VPN_UPSTREAM_PROXY port: $vpn_port" >&2
    exit 2
    ;;
esac

login=""
if [ -n "${HTTPPROXY_USER:-}" ] || [ -n "${HTTPPROXY_PASSWORD:-}" ]; then
  if [ -z "${HTTPPROXY_USER:-}" ] || [ -z "${HTTPPROXY_PASSWORD:-}" ]; then
    echo "HTTPPROXY_USER and HTTPPROXY_PASSWORD must both be set" >&2
    exit 2
  fi
  case "$HTTPPROXY_USER$HTTPPROXY_PASSWORD" in
    *[!A-Za-z0-9._~-]*)
      echo "VPN proxy credentials may contain only letters, digits, '.', '_', '~', and '-'" >&2
      exit 2
      ;;
  esac
  login=" login=$HTTPPROXY_USER:$HTTPPROXY_PASSWORD"
fi

cat > "$upstream_file" <<EOF
cache_peer $vpn_host parent $vpn_port 0 no-query default name=vpn_upstream$login
never_direct allow all
EOF
echo "Egress: WireGuard HTTP proxy $vpn_host:$vpn_port (fail closed)"