# Cloudflare Tunnel — PARKED

**Status as of 2026-05-16:** PARKED. The dashboard is served over Tailscale
instead. This directory and config are kept intentionally so the tunnel
can be revived in one command if/when this project grows beyond a single
user.

## Why we parked it

- Single user, Tailscale already on every device I'd use to view the
  dashboard (Linux box, iPhone, Windows laptop).
- `alerts/web_app.py` has no application-layer auth. Exposing it over a
  public tunnel without Cloudflare Access in front meant anyone with the
  URL could read alerts/journal entries and burn Anthropic credits by
  POSTing to `/alerts/{id}/chat`.
- Cloudflare Access would close that hole, but adds an OAuth/email-code
  redirect to every Pushover notification tap on a new device or after
  cookie eviction. Tailscale "just works" on devices I already trust.

## What's running instead

- Web app binds to `0.0.0.0:8002` (see `.env: WEB_SERVER_HOST=0.0.0.0`).
- Tailscale ACL = default (full mesh, my tailnet only).
- Pushover deep links use `http://nexus-nucbox-k8-plus:8002` (set in
  `.env: PUSHOVER_BASE_URL`).
- Access from iPhone / Windows: open Tailscale → tap link or visit
  `http://nexus-nucbox-k8-plus:8002` directly.

## When to un-park (revival triggers)

Revive Cloudflare if any of these become true:

- A second person needs to see the dashboard (spouse, accountant, CPA).
- You want to check it from a device that can't run Tailscale (corporate
  laptop, kiosk, someone else's phone).
- You add real per-user auth to `alerts/web_app.py` and want a clean
  public URL for sharing.
- You ship something multi-tenant on top of this codebase.

## Revival — one-time setup status (already done)

These steps were completed on this Linux box; the artifacts still exist:

- `cloudflared` installed at `/usr/local/bin/cloudflared` (v2026.5.0)
- Authenticated: `~/.cloudflared/cert.pem` present
- Tunnel `trading-alerts` UUID `e8b94fb3-1227-4958-9d26-a57c6952adbd`
  created in Cloudflare account
- DNS routed: `alerts.nexus-lab.work` → tunnel
- Credentials: `~/.cloudflared/e8b94fb3-1227-4958-9d26-a57c6952adbd.json`
- `cloudflare/tunnel_config.yml` ingress targets `http://127.0.0.1:8002`

If you wiped the machine, redo from `cloudflared tunnel login` onwards —
see the "Original one-time setup" section at the bottom for the exact
commands.

## Revival — daily startup (when un-parked)

1. **Flip `.env` back to loopback + public URL:**

       WEB_SERVER_HOST=127.0.0.1
       PUSHOVER_BASE_URL=https://alerts.nexus-lab.work

2. **Restart main.py** so the web app rebinds.

3. **Start the tunnel** (in a separate terminal or under systemd):

       cloudflared tunnel --config cloudflare/tunnel_config.yml run

4. **Strongly recommended before re-exposing publicly:** stand up
   Cloudflare Access in front of `alerts.nexus-lab.work` (Zero Trust →
   Access → Applications → Add a self-hosted application, policy = your
   email only). The `/chat` endpoint will burn Anthropic credits on
   anonymous traffic otherwise.

5. **Verify:**

       curl https://alerts.nexus-lab.work/health   # expect {"status":"ok"}

## Troubleshooting (kept for revival)

- 502 from Cloudflare: FastAPI isn't running on `127.0.0.1:8002`.
- 404 from the web app: alert ID doesn't exist in `logs/alert_store.db`.
- 1033 from Cloudflare: tunnel itself isn't running.
- DNS not resolving: re-run `cloudflared tunnel route dns trading-alerts alerts.nexus-lab.work`.

## Original one-time setup (for a fresh machine)

```sh
# 1. Install cloudflared (Linux)
curl -L -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb

# 2. Authenticate (opens a browser; pick the nexus-lab.work zone)
cloudflared tunnel login

# 3. Create the tunnel
cloudflared tunnel create trading-alerts
# → prints UUID; copy it into tunnel_config.yml `tunnel:` and `credentials-file:` paths

# 4. Route DNS
cloudflared tunnel route dns trading-alerts alerts.nexus-lab.work

# 5. Run
cloudflared tunnel --config cloudflare/tunnel_config.yml run
```
