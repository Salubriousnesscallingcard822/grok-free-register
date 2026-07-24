# One-click: Register → Authenticated → grok2api

## Entry

```powershell
cd .
.\start-full-to-grok2api.ps1
```

## Chain

```
tempmail register
    → keys/auth-sessions.jsonl  /  auth-local/source-snapshot.jsonl
Path B browser OAuth (accounts.x.ai Allow)
    → auth-local/authenticated/xai-*.json
import_authenticated_to_grok2api.py
    → grok2api :8000  (provider Build / multipart files)
```

## Manual pieces

```powershell
# Path B only
.\.venv\Scripts\python.exe scripts\device_flow_browser_complete.py `
  --source-file auth-local\source-snapshot.jsonl --source-index 0 --count 1

# Import only
.\.venv\Scripts\python.exe scripts\import_authenticated_to_grok2api.py --limit 50

# Or via launcher
.\start-full-to-grok2api.ps1 import-only -ImportSinceMinutes 30
```

## Requirements

- Proxy is optional; set HTTP_PROXY/HTTPS_PROXY yourself if needed
- grok2api healthy on `http://127.0.0.1:8000/healthz`
- `keys/.credentials` with admin user/pass
- `.venv` with project deps + Playwright/Chromium for Path B

## Stop

```powershell
.\start-full-to-grok2api.ps1 stop
```

Does **not** kill main `xai_enroller.service` unless you pass `-StopAuthService`.

## Standalone auth (same Path B)

```powershell
.\start-auth-windows.ps1 -AuthCount 5
.\start-auth-windows.ps1 -Background
```n

## Unified entry

`powershell
.\start-all-windows.cmd up
.\start-all-windows.cmd import
`

CloakBrowser + import details: docs/guides/cloakbrowser-and-import.md.
