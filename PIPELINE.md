# start-pipeline.ps1

独立流水线启动器（不改主 turbo/adaptive 脚本，不杀主 `xai_enroller.service`）。

## 功能
- `register`：本机 tempmail 注册（`grok_register.register`）
- `sync`：把 `keys/auth-sessions.jsonl` / `accounts.txt` 同步到手机 Termux
- `phone-auth`：手机 xAI 认证 worker → 导入 grok2api
- `status` / `stop`

## 用法
```powershell
cd .
.\start-pipeline.ps1 status
.\start-pipeline.ps1 all
.\start-pipeline.ps1 register -RegisterTarget 50
.\start-pipeline.ps1 stop -What register
```

## 日志
- `logs/pipeline-register.out.log`
- `logs/pipeline-sync.out.log`
- `logs/phone_sync.log`
- 手机：`~/gfr-phone/logs/phone_xai_w*.log`

## OAuth browser complete (accounts.x.ai)

```powershell
.venv\Scripts\python.exe scripts\device_flow_browser_complete.py --source-file auth-local\source-snapshot.jsonl --source-index 0 --count 1
.venv\Scripts\python.exe scripts\export_authenticated_json.py --from-jsonl keys\oauth_credentials.jsonl
```

Authenticated files land in `auth-local/authenticated/xai-*.json`.

## One-click: register → browser OAuth → grok2api

```powershell
cd .

# Full chain (default)
.\start-full-to-grok2api.ps1

# Modes
.\start-full-to-grok2api.ps1 status
.\start-full-to-grok2api.ps1 register-only -RegisterTarget 20
.\start-full-to-grok2api.ps1 auth-only -AuthCount 3
.\start-full-to-grok2api.ps1 import-only -ImportSinceMinutes 60
.\start-full-to-grok2api.ps1 stop
```

### Steps
1. **register** — `grok_register.register` (tempmail) → sessions in `keys/auth-sessions.jsonl` / `auth-local/source-snapshot.jsonl`
2. **auth** — Path B `scripts/device_flow_browser_complete.py` (accounts.x.ai browser Allow) → `auth-local/authenticated/xai-*.json`
3. **import** — `scripts/import_authenticated_to_grok2api.py` → local grok2api `POST /api/admin/v1/accounts/import`

### Credentials
Put admin login in `keys/.credentials`:
```
ADMIN_USER=admin
ADMIN_PASS=your_password
```
(auto-copied from `../grok-import/.credentials` if present)

### Logs
- `logs/full-register.out.log`
- `logs/full-auth.out.log`
- `logs/full-import.out.log`
- state: `keys/g2a-imported-subs.txt`

## Auth entry (Path B wired into start-auth-windows.ps1)

```powershell
# default = Path B (accounts.x.ai browser approve) continuous
.\start-auth-windows.ps1

# N successes then exit
.\start-auth-windows.ps1 -AuthCount 10

# background
.\start-auth-windows.ps1 -Background

# old interactive enroller (may oauth_rejected)
.\start-auth-windows.ps1 -LegacyService
```

Core script: `scripts/auth_pathb_daemon.py` → `scripts/device_flow_browser_complete.py`
Output: `auth-local/authenticated/xai-*.json`
State: `keys/pathb-auth-done.txt`


## Unified Windows entry (formal)

`powershell
.\start-all-windows.cmd status
.\start-all-windows.cmd up
.\start-all-windows.cmd import
.\start-all-windows.cmd stop
`

Or:

`powershell
.\.venv\Scripts\python.exe .\start_all_windows.py status
`

- CloakBrowser register: start-windows.ps1
- Path B auth: start-auth-windows.ps1
- Import: scripts/import_authenticated_to_grok2api.py (token reuse, 429 wait 90s)
- Guide: docs/guides/cloakbrowser-and-import.md

