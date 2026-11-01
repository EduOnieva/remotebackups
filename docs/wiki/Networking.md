# Networking

Three questions decide the setup: how the container reaches the destination, who can reach
the UI, and whether a reverse proxy sits in front of it.

## Reaching the destination

- **LAN.** The plain compose file with the destination's LAN address or name in
  `REMOTE_HOST`.
- **Host already on the tailnet or a VPN.** The plain compose file; the container reaches
  whatever the host can reach, so `REMOTE_HOST` can be a MagicDNS name or a tailnet IP.
- **Tailscale sidecar.** When the server itself is not on the tailnet, or you want the UI
  reachable only from the tailnet, `docker-compose.tailscale.yaml` runs the official
  `tailscale/tailscale` container and puts remotebackups in its network namespace.

## The Tailscale sidecar

```yaml
services:
  tailscale:
    image: tailscale/tailscale:v1.104.4
    hostname: remotebackups            # the node name on the tailnet
    environment:
      - TS_AUTHKEY=tskey-auth-...
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
      - TS_AUTH_ONCE=true
      - TS_ENABLE_HEALTH_CHECK=true
      - TS_LOCAL_ADDR_PORT=127.0.0.1:9002
    volumes:
      - ./config/tailscale:/var/lib/tailscale
    devices:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - net_admin
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:9002/healthz"]
  remotebackups:
    network_mode: service:tailscale
    depends_on:
      tailscale:
        condition: service_healthy
```

- **Auth key.** Create it at https://login.tailscale.com/admin/settings/keys. A reusable,
  pre-authorised key avoids a visit to the admin console at the first start. With
  `TS_AUTH_ONCE=true` the identity in the state volume is reused on every restart, so the key
  is needed once; it can expire afterwards.
- **Kernel networking.** `TS_USERSPACE=false` with `/dev/net/tun` and `net_admin` gives the
  sidecar real networking; the userspace mode works without them but is slower and cannot be
  reached as a plain IP by everything.
- **The UI** is at `http://<node-name>:8080` from any device on the tailnet. Nothing is
  published on the LAN unless you uncomment `ports:` on the *tailscale* service, since that
  is the container that owns the network namespace.
- **Health.** remotebackups waits for the sidecar's health check, so a run cannot start
  before the tailnet is up.
- **HTTPS.** Tailscale Serve terminates TLS with a certificate for your tailnet name:
  `docker exec remotebackups-tailscale tailscale serve --bg 8080`, then the UI is at
  `https://<node-name>.<tailnet>.ts.net`. Serve passes the original `Host`, so the UI needs
  nothing else.

## Who can reach the UI

There is no login. The UI starts and stops backups, deletes history and shows your paths,
so it belongs where only you can reach it: on the tailnet, on the LAN, or behind a reverse
proxy that authenticates. Do not publish port 8080 on a public address.

What the UI does enforce: every state-changing call (`POST`, `PUT`, `DELETE`) must carry
`Content-Type: application/json` and come from the page's own origin, checked through the
browser's `Sec-Fetch-Site` and `Origin` headers. A page on another site cannot start, stop
or clear anything through your browser. Scripts add the header and are not browsers, so they
pass:

```bash
curl -X POST -H 'Content-Type: application/json' http://remotebackups:8080/api/runs -d '{"dry_run": true}'
```

## Reverse proxies

The origin check compares the browser's `Origin` with the `Host` header the application
receives, or with `X-Forwarded-Host` when the proxy sets it. Caddy, Traefik and nginx with
the usual configuration pass the original `Host` and need nothing. A proxy that rewrites
`Host` to the upstream name must add `X-Forwarded-Host`, or every button answers "cross-site
request refused".

The live log is a Server-Sent Events stream. The application sends `X-Accel-Buffering: no`
and `Cache-Control: no-cache`, which nginx and most proxies honour, so the stream is not
buffered. If a proxy still shows the log only when the run ends, turn buffering off for that
location.

**Caddy**, with basic authentication:

```
backups.example.net {
    basic_auth {
        you $2a$14$...    # caddy hash-password
    }
    reverse_proxy remotebackups:8080
}
```

**Traefik** (labels on the remotebackups service):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.remotebackups.rule=Host(`backups.example.net`)
      - traefik.http.routers.remotebackups.entrypoints=websecure
      - traefik.http.routers.remotebackups.tls.certresolver=letsencrypt
      - traefik.http.routers.remotebackups.middlewares=auth
      - traefik.http.middlewares.auth.basicauth.users=you:$$apr1$$...
      - traefik.http.services.remotebackups.loadbalancer.server.port=8080
```

**nginx**:

```nginx
location / {
    auth_basic           "backups";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass           http://remotebackups:8080;
    proxy_http_version   1.1;
    proxy_set_header     Host $host;
    proxy_set_header     X-Forwarded-Host $host;
    proxy_set_header     X-Forwarded-Proto $scheme;
    proxy_read_timeout   1h;    # the log stream stays open for the whole run
}
```

With the Tailscale sidecar, point the proxy at the sidecar's node name, since that is where
port 8080 answers.

## Ports and the healthcheck

The web UI listens on 8080 inside the container; `WEB_PORT` changes that when something
else in the same network namespace already uses it. The Docker `HEALTHCHECK` calls
`remotebackups healthcheck`, which asks `/healthz` on that port, so it follows `WEB_PORT` on
its own. `/healthz` answers 503 only when the scheduler loop has died; an unreachable
destination is shown in the UI and is not a health failure, because a sleeping NAS is normal.
