FROM alpine:3.20

RUN apk add --no-cache python3 squid

COPY generate_blocklists.py /usr/local/bin/generate-blocklists
COPY squid.conf /etc/squid/squid.conf
COPY custom-blocked-domains.txt /etc/squid/custom-blocked-domains.txt
COPY entrypoint.sh /usr/local/bin/network-filter-entrypoint

RUN chmod +x /usr/local/bin/generate-blocklists /usr/local/bin/network-filter-entrypoint

EXPOSE 3128

USER squid

ENTRYPOINT ["/usr/local/bin/network-filter-entrypoint"]
