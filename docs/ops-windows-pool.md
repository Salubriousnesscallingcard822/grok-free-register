# Grok free-register Windows pool ops

## Software stack
1. Register worker: `grok-free-register` + CloakBrowser Chromium
2. Auth converter: `xai_enroller` service (SSO session -> OAuth key)
3. Cloud mailbox: `tempmail.lol` (local) / Azure broker `:8090`
4. Azure keypool + KeyHub gateway for unified key consumption

## Local paths
- project: `.`
- raw pool: `keys\accounts.txt`, `keys\auth-sessions.jsonl`
- auth pool: `auth-local\authenticated\`
- claimed pool: `auth-local\claimed\<batch-id>\`
- unified export: `auth-local\export\`
- Azure keypool: `/opt/grok-keypool` on `YOUR_VPS_IP`

## 1. Build number pool (raw accounts)
```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 --target 0
# smoke:
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 --target 1
```
Requires:
- local proxy via user-configured HTTP_PROXY/HTTPS_PROXY
- `EMAIL_MODE=tempmail` and `TEMPMAIL_PROVIDER_ORDER=lol,mailtm`
- custom domain email for long-run scale

## 2. Convert accounts to keys
```powershell
powershell -ExecutionPolicy Bypass -File .\start-auth-windows.ps1
```
Inside auth prompt:
- `s` status
- `take 20` claim 20 available keys into a batch
- `p` / `r` pause / resume
- `q` quit

One-shot conversion can also reuse existing sessions under `keys\auth-sessions.jsonl`.

## 3. Average consumption model
- register continuously fills raw sessions
- auth converts with min interval 10s, rate-limit probe 60s
- consumers ONLY take from available via `take N`
- keep available buffer ~= 2 * daily claim volume
- claim in fixed batches (`take 20` / `take 50`), never drain to 0
- if available falls below buffer, raise register `PHYSICAL_CAP` or keep register online

## 4. Unified key output
Local auth JSON fields (CPA compatible):
`type`, `access_token`, `refresh_token`, `id_token`, `expires_in`, `expired`,
`base_url=https://api.x.ai/v1`, `auth_kind=oauth`, `sub`, `token_endpoint`

Export jsonl:
```powershell
powershell -ExecutionPolicy Bypass -File .\ops\export-unified-keys.ps1 -Source available
powershell -ExecutionPolicy Bypass -File .\ops\export-unified-keys.ps1 -Source claimed-latest
```

Push to Azure keypool:
```powershell
powershell -ExecutionPolicy Bypass -File PATH/TO/YOUR/azure-vps/push-keys-to-azure.ps1 -Source export
powershell -ExecutionPolicy Bypass -File PATH/TO/YOUR/azure-vps/push-keys-to-azure.ps1 -Source authenticated
```

## 5. Server topology
```text
Windows local
  register (CloakBrowser) -> keys/auth-sessions.jsonl
  auth-service -> auth-local/authenticated/*.json
  export-unified-keys.ps1 -> auth-local/export/*.jsonl
  push-keys-to-azure.ps1 -> Azure /opt/grok-keypool

Your VPS (YOUR_VPS_IP)
  KeyHub gateway: https://node.example.com/v1
  keypool store: /opt/grok-keypool
  mailbox broker: :8090 (tempmail.lol wrapper)
```

Do **not** put CloakBrowser register on the 1GB Azure VM.

## 6. Status commands
```powershell
powershell -ExecutionPolicy Bypass -File .\ops\pool-status.ps1
powershell -ExecutionPolicy Bypass -File PATH/TO/YOUR/azure-vps/status-all.ps1
ssh -i PATH_TO_YOUR_SSH_KEY USER@YOUR_VPS_IP "sudo /opt/grok-keypool/bin/status.sh"
```

## 7. Grok Tool (KeyHub-style manager)

```powershell
powershell -ExecutionPolicy Bypass -File .\start-grok-tool-windows.ps1
```

- UI: `http://127.0.0.1:8787/`
- Unified key + balance panel
- Export KeyHub provider JSON from Connection page
