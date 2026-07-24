# Cloud Mailbox / OTP

## Working path (verified 2026-07-17)

x.ai rejects many public temp domains (example: `duckmail.sbs` -> `email-domain-rejected`).

Verified working cloud mailbox provider:

- **tempmail.lol** API
  - create: `POST https://api.tempmail.lol/v2/inbox/create`
  - poll: `GET https://api.tempmail.lol/v2/inbox?token=...`
  - x.ai `CreateEmailValidationCode` accepted
  - OTP mail arrives quickly (example subject `QCS-BYD xAI confirmation code`)

## Register config

```env
EMAIL_MODE=tempmail
TEMPMAIL_PROVIDER_ORDER=lol,mailtm
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897
```

Code prefers `tempmail.lol` first, then mail.tm-compatible providers as fallback.

## Optional local/Azure broker

```powershell
# local
$env:MAILBOX_BROKER_PORT=8090
$env:HTTP_PROXY=http://127.0.0.1:7897
$env:HTTPS_PROXY=http://127.0.0.1:7897
python ops/cloud_mailbox_broker.py
```

Endpoints:
- `POST /mailbox/create`
- `GET /mailbox/<handle>`
- `GET /health`

Azure helper: `.tools/azure-vps/deploy_mailbox_broker.sh` (via `az vm run-command`).

## Durable custom domain path (needs CF token)

Still the ideal long-run path:
1. Cloudflare Email Routing on your domain
2. Worker `cloudflare/email-worker.js`
3. Local/Azure `email_server.py` webhook
4. `EMAIL_MODE=custom` + `EMAIL_DOMAIN=...` + `EMAIL_API=...`

Blocked currently by missing Cloudflare API token / Email Routing domain credentials.
Domain ecosystem present: `kdns.fr` / `node.yanqiudesu.kdns.fr` (used for KeyHub relay, not Email Routing yet).
