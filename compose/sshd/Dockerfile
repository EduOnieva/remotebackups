# A destination host for the integration stack: sshd + rsync, a key user and a password user.
FROM alpine:3.24.3
RUN apk add --no-cache openssh-server rsync \
    && adduser -D -u 1000 -s /bin/sh backup \
    && adduser -D -u 1001 -s /bin/sh pwuser \
    && echo "pwuser:testpass" | chpasswd \
    && passwd -u backup \
    && sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
