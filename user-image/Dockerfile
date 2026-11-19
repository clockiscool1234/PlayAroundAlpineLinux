FROM alpine:3.20

# Xvfb is only reachable inside its user container (-nolisten tcp). xdotool
# injects input and ImageMagick captures the framebuffer for Discord.
RUN apk add --no-cache \
        imagemagick \
        openbox \
        xdotool \
        xterm \
        xvfb

ENV DISPLAY=:99 \
    TERM=xterm-256color

CMD ["sleep", "infinity"]
