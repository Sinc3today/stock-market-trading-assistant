# Cloudflare Tunnel Setup

Exposes the local FastAPI alert app (`alerts/web_app.py` on port 8000) to
the public internet at `https://alerts.nexus-lab.work`. Cloudflare handles
HTTPS, the domain, and the inbound connection — no Railway, no VPS, no
open inbound port on your router.

## One-time setup

1. Install cloudflared:

       winget install Cloudflare.cloudflared

2. Authenticate (opens a browser, pick the `nexus-lab.work` zone):

       cloudflared tunnel login

3. Create the tunnel:

       cloudflared tunnel create trading-assistant

4. Copy the tunnel UUID printed in the output and replace
   `TUNNEL_ID_PLACEHOLDER` in `cloudflare/tunnel_config.yml`
   (both the `tunnel:` line and the `credentials-file:` path).

5. Point DNS at the tunnel:

       cloudflared tunnel route dns trading-assistant alerts.nexus-lab.work

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
