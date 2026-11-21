FROM alpine:3.20

RUN apk add --no-cache openssh-client

COPY generate-keys.sh /usr/local/bin/generate-keys.sh
RUN chmod 0755 /usr/local/bin/generate-keys.sh

ENTRYPOINT ["/usr/local/bin/generate-keys.sh"]
