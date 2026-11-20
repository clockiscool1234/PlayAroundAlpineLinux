#!/bin/sh
set -eu

/usr/local/bin/generate-blocklists \
  --bad-websites /blocklists/bad-websites/websites.json \
  --phishing-domains /blocklists/Phishing.Database/phishing-domains-ACTIVE.txt \
  --phishing-ips /blocklists/Phishing.Database/phishing-IPs-ACTIVE.txt \
  --custom /etc/squid/custom-blocked-domains.txt \
  --bad-websites-output /run/squid/bad-websites.txt \
  --phishing-domains-output /run/squid/phishing-domains.txt \
  --custom-domains-output /run/squid/custom-domains.txt \
  --phishing-ips-output /run/squid/phishing-ips.txt

: "${POLICY_BAD_WEBSITES:=deny}"
: "${POLICY_PHISHING_DOMAINS:=deny}"
: "${POLICY_CUSTOM_DOMAINS:=deny}"
: "${POLICY_PHISHING_IPS:=deny}"
: "${POLICY_LITERAL_IPS:=deny}"
: "${POLICY_PRIVATE_IPS:=deny}"
: "${POLICY_NON_HTTP:=deny}"
: "${POLICY_UNSAFE_PORTS:=deny}"

policy_file=/run/squid/policy.conf
: > "$policy_file"

add_policy() {
  name=$1
  value=$2
  rule=$3
  case "$value" in
    deny)
      echo "$rule" >> "$policy_file"
      ;;
    allow)
      ;;
    *)
      echo "Invalid $name=$value (expected 'allow' or 'deny')" >&2
      exit 2
      ;;
  esac
}

add_policy POLICY_UNSAFE_PORTS "$POLICY_UNSAFE_PORTS" "http_access deny !Safe_ports"
add_policy POLICY_NON_HTTP "$POLICY_NON_HTTP" "http_access deny CONNECT !SSL_ports"
add_policy POLICY_LITERAL_IPS "$POLICY_LITERAL_IPS" "http_access deny literal_ipv4"
add_policy POLICY_LITERAL_IPS "$POLICY_LITERAL_IPS" "http_access deny literal_ipv6"
add_policy POLICY_PRIVATE_IPS "$POLICY_PRIVATE_IPS" "http_access deny private_destinations"
add_policy POLICY_BAD_WEBSITES "$POLICY_BAD_WEBSITES" "http_access deny bad_websites"
add_policy POLICY_PHISHING_DOMAINS "$POLICY_PHISHING_DOMAINS" "http_access deny phishing_domains"
add_policy POLICY_CUSTOM_DOMAINS "$POLICY_CUSTOM_DOMAINS" "http_access deny custom_domains"
add_policy POLICY_PHISHING_IPS "$POLICY_PHISHING_IPS" "http_access deny phishing_ips"

/usr/local/bin/configure-upstream /run/squid/upstream.conf

printf '%s\n' \
  "Loaded network policy:" \
  "  POLICY_BAD_WEBSITES=$POLICY_BAD_WEBSITES" \
  "  POLICY_PHISHING_DOMAINS=$POLICY_PHISHING_DOMAINS" \
  "  POLICY_CUSTOM_DOMAINS=$POLICY_CUSTOM_DOMAINS" \
  "  POLICY_PHISHING_IPS=$POLICY_PHISHING_IPS" \
  "  POLICY_LITERAL_IPS=$POLICY_LITERAL_IPS" \
  "  POLICY_PRIVATE_IPS=$POLICY_PRIVATE_IPS" \
  "  POLICY_NON_HTTP=$POLICY_NON_HTTP" \
  "  POLICY_UNSAFE_PORTS=$POLICY_UNSAFE_PORTS"

exec squid -NYCd 1
