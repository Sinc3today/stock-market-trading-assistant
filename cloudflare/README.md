# Cloudflare Tunnel Setup

Exposes the local FastAPI alert app (`alerts/web_app.py` on port 8000) to
the public internet at `https://alerts.nexus-lab.work`. Cloudflare handles
HTTPS, the domain, and the inbound connection — no Railway, no VPS, no
open inbound port on your router.

## Current status (verified 2026-04-30)

- cloudflared installed: `C:\Program Files (x86)\cloudflared\cloudflared.exe`
- Authenticated: `~/.cloudflared/cert.pem` present
- Tunnel `trading-alerts` (UUID `e8b94fb3-1227-4958-9d26-a57c6952adbd`) created
- DNS: `alerts.nexus-lab.work` → Cloudflare edge → tunnel
- Credentials file: `~/.cloudflared/e8b94fb3-1227-4958-9d26-a57c6952adbd.json`

If you're on this machine, the one-time setup below is already done.
Skip to "Daily startup".

## One-time setup (already done on alexr's box)

1. Install cloudflared:

       winget install Cloudflare.cloudflared

2. Authenticate (opens a browser, pick the `nexus-lab.work` zone):

       cloudflared tunnel login

3. Create the tunnel:

       cloudflared tunnel create trading-alerts

4. Copy the tunnel UUID printed in the output and update
   `cloudflare/tunnel_config.yml` — replace the UUID in both the
   `tunnel:` line and the `credentials-file:` path.

5. Point DNS at the tunnel:

       cloudflared tunnel route dns trading-alerts alerts.nexus-lab.work

## Daily startup

Two processes — start both when you start trading.

### Start the FastAPI app
The trading bot starts this automatically when you run `python main.py`.
To run it standalone:

    uvicorn alerts.web_app:app --host 127.0.0.1 --port 8000 --reload

### Start the tunnel

    cloudflared tunnel --config cloudflare/tunnel_config.yml run

Once both are running, every Pushover alert link
(`https://alerts.nexus-lab.work/alerts/<id>`) hits the local web app.

## Troubleshooting

- 502 from Cloudflare: FastAPI isn't running on port 8000 locally.
- 404 from the web app: alert ID doesn't exist in `logs/alert_store.db`.
- 1033 from Cloudflare: tunnel itself isn't running locally.
- DNS not resolving: re-run `cloudflared tunnel route dns ...` (step 5).
