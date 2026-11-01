# Destinations

Anything with `sshd` and `rsync` can receive the backups. The destination user needs the
public key in its `authorized_keys` and write permission on every destination folder; it
does not need to be root, and it is better when it is not.

## Why the user is not root, and what that means

A user that is not root cannot change the owner or group of the files it writes. With
rsync's plain `-a` every run would end in "partial transfer due to error" (exit 23) after
copying everything, because the ownership step fails. That is why `rsync.preserve_ownership`
defaults to `false`, which adds `--no-owner --no-group`: files on the destination belong to
the backup user, permissions and timestamps are kept. Set `preserve_ownership: true` only
when the destination user is root and you want the original owners on the copy.

## TrueNAS SCALE

1. **SSH service.** *System → Services*, enable *SSH* and set it to start automatically.
   Leave password login off; the key is enough.
2. **User.** *Credentials → Users → Add*. Give the user a home directory on a dataset (for
   example `/mnt/tank/home/backup`), tick *SSH password login enabled* only if you intend to
   use `SSH_PASSWORD`, and paste the public key (`~/.ssh/remotebackups.pub`) into *Authorized
   Keys*. The user needs a real shell, not `nologin`: rsync runs through it.
3. **Destination dataset.** Create a dataset for the backups, for example `tank/backup`,
   and give the user write permission on it: *Datasets → Edit permissions*, owner or an ACL
   entry with *Modify*. Each `destination:` in `config.yaml` is a path under it,
   `/mnt/tank/backup/documents`; rsync creates the last directory itself.
4. **Try it** from the server that will run the container:

   ```bash
   ssh -i ~/.ssh/remotebackups backup@192.168.1.20 'mkdir -p /mnt/tank/backup/test && ls -ld /mnt/tank/backup/test'
   ```

5. **The TrueNAS card** needs an API key. Create one for the same or any user (*Credentials →
   Users → the user → API Keys* on current releases, the user menu's *API Keys* on older
   ones), and pass it as `FILE__TRUENAS_API_KEY` as shown in [[Installation]]. The card then
   shows each pool's usage, health and last scrub plus the current alerts, and
   `/api/pool_status` carries the free space per pool for dashboards. The NAS is asked at
   most every `truenas.cache_seconds`.

   The certificate of a home NAS is usually self-signed, so `TRUENAS_VERIFY_TLS` defaults to
   `false`. If you have a real certificate, set it to `true`.

   `TRUENAS_POOL_ALIASES=tank:disk-0,vault:disk-1` renames pools in the card and the API, so
   dashboards keep their keys when a pool is rebuilt under another name.

A sleeping or rebooting NAS is normal. The Remote card says "no" with the ssh message, the
TrueNAS card says it could not connect, and both come back on their own; nothing is logged
as an error.

## Synology DSM

1. *Control Panel → Terminal & SNMP*: enable SSH.
2. *Control Panel → File Services → rsync*: enable the rsync service.
3. *Control Panel → User & Group*: create the user. DSM only lets members of the
   `administrators` group log in over SSH, so either add the user to that group or back up
   as an administrator you create for this purpose.
4. *Control Panel → User & Group → Advanced*: enable the user home service, then put the
   public key in `/volume1/homes/<user>/.ssh/authorized_keys` with the home directory at
   mode 755, `.ssh` at 700 and the file at 600. DSM ignores the key when these are looser.
5. Give the user write permission on the shared folder that receives the backups and use
   `/volume1/<shared folder>/<name>` as `destination:`.

## A plain Linux host

```bash
sudo adduser --disabled-password backup
sudo apt install rsync openssh-server          # or the distribution's equivalent
sudo install -d -m 700 -o backup -g backup /home/backup/.ssh
echo 'ssh-ed25519 AAAA... remotebackups' | sudo tee /home/backup/.ssh/authorized_keys
sudo chown backup:backup /home/backup/.ssh/authorized_keys && sudo chmod 600 /home/backup/.ssh/authorized_keys
sudo install -d -o backup -g backup /srv/backup
```

`destination: /srv/backup/documents` and so on. To restrict the key to rsync only, prefix
the `authorized_keys` line with `command="rrsync /srv/backup",restrict ` (`rrsync` ships
with rsync) and use destinations relative to that root.

## A second server at a friend's place

The same as a plain Linux host, reached through Tailscale: see [[Networking]]. Set
`REMOTE_HOST` to the machine's MagicDNS name or tailnet IP, and consider `--bwlimit` in
`rsync.extra_options` so the first full copy does not saturate either uplink.

## Checking the login from inside the container

The ssh configuration the runs use is generated under `/run/remotebackups/ssh/config`, so
the exact same login can be tried by hand:

```bash
docker exec -it remotebackups ssh -F /run/remotebackups/ssh/config backup@192.168.1.20 'echo ok'
```

The UI's **Check now** button does the same and shows the first useful line of any error.
