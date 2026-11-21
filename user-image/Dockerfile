# tmate is not packaged in Alpine 3.20, so build the pinned client. The public
# tmate.io relay was shut down in 2025; this client talks to the self-hosted
# relay in compose.tmate.yaml (or any compatible server).
FROM alpine:3.20 AS tmate-build

ARG TMATE_VERSION=2.4.0
ARG TMATE_SHA256=62b61eb12ab394012c861f6b48ba0bc04ac8765abca13bdde5a4d9105cb16138

# Alpine names the msgpack pkg-config file msgpack-c.pc; tmate looks for msgpack.pc.
RUN apk add --no-cache --virtual .build-deps \
        autoconf \
        automake \
        build-base \
        libevent-dev \
        libssh-dev \
        linux-headers \
        msgpack-c-dev \
        ncurses-dev \
        openssl-dev \
        pkgconf \
        wget \
        zlib-dev \
    && wget -q -O /tmp/tmate.tar.gz \
        "https://github.com/tmate-io/tmate/archive/refs/tags/${TMATE_VERSION}.tar.gz" \
    && echo "${TMATE_SHA256}  /tmp/tmate.tar.gz" | sha256sum -c - \
    && mkdir -p /src \
    && tar -xzf /tmp/tmate.tar.gz -C /src --strip-components=1 \
    && ln -sf /usr/lib/pkgconfig/msgpack-c.pc /usr/lib/pkgconfig/msgpack.pc \
    && cd /src \
    && ./autogen.sh \
    && ./configure --prefix=/usr \
    && make -j"$(nproc)" \
    && make install \
    && strip /usr/bin/tmate

FROM alpine:3.20

# Xvfb is only reachable inside its user container (-nolisten tcp). xdotool
# injects input and ImageMagick captures the framebuffer for Discord.
RUN apk add --no-cache \
        imagemagick \
        libevent \
        libssh \
        msgpack-c \
        ncurses-libs \
        openbox \
        openssl \
        xdotool \
        xterm \
        xvfb \
        zlib

COPY --from=tmate-build /usr/bin/tmate /usr/bin/tmate

ENV DISPLAY=:99 \
    TERM=xterm-256color

CMD ["sleep", "infinity"]
