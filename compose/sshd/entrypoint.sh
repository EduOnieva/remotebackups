#!/bin/sh
# Install the generated test key for the key user, prepare destinations, start sshd.
set -eu

until [ -f /keys/ssh_key.pub ]; do sleep 0.2; done
mkdir -p /home/backup/.ssh
cp /keys/ssh_key.pub /home/backup/.ssh/authorized_keys
chown -R backup:backup /home/backup/.ssh
chmod 700 /home/backup/.ssh
chmod 600 /home/backup/.ssh/authorized_keys

mkdir -p /mnt/backup/key /mnt/backup/password
chown backup:backup /mnt/backup/key
chown pwuser:pwuser /mnt/backup/password

ssh-keygen -A
exec /usr/sbin/sshd -D -e
