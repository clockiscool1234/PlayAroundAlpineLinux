#!/bin/sh
set -eu

/usr/local/bin/generate-blocklists \
  --bad-websites /blocklists/bad-websites/websites.json \
  --phishing-domains /blocklists/Phishing.Database/phishing-domains-ACTIVE.txt \
  --phishing-ips /blocklists/Phishing.Database/phishing-IPs-ACTIVE.txt \
  --custom /etc/squid/custom-blocked-domains.txt \
  --domains-output /run/squid/blocked-domains.txt \
  --ips-output /run/squid/blocked-ips.txt

exec squid -NYCd 1
