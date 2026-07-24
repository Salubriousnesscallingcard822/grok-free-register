# authenticated JSON 导出

认证成功后的标准落盘目录：

```text
auth-local/authenticated/xai-<hmac16>.json
```

## 字段（CPA 兼容）

- `type`: `xai`
- `access_token` / `refresh_token` / `id_token`
- `token_type`: `Bearer`
- `expires_in` / `expired` / `last_refresh`
- `sub`
- `base_url`: `https://cli-chat-proxy.grok.com/v1`
- `token_endpoint`: `https://auth.x.ai/oauth2/token`
- `auth_kind`: `oauth`

## 命令

### 从 Path B 单次结果导入

```powershell
.venv\Scripts\python.exe scripts\export_authenticated_json.py `
  --from-json E:\download\claude\IC_Free_Register\output\logs\pathb_once.json
```

### 从 jsonl 批量导入

```powershell
.venv\Scripts\python.exe scripts\export_authenticated_json.py `
  --from-jsonl keys\oauth_credentials.jsonl
```

### Path B 闭环会自动写

`scripts/device_flow_browser_complete.py` 成功后会同时写：

- `keys/oauth_credentials.jsonl`
- `keys/refresh_tokens.txt`
- `auth-local/authenticated/xai-*.json`
