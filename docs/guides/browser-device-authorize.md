# accounts.x.ai 浏览器批准（双路）

xAI 已把 Device OAuth **批准 UI** 放到 `accounts.x.ai`。  
`auth.x.ai` 只负责：

- discovery
- device_code 申请
- token 轮询

批准必须浏览器完成，否则 `poll_token` 会落到 `oauth_rejected`。

## Path A：只做浏览器批准

```powershell
cd E:\download\claude\CodeX\grok-free-register-main
.venv\Scripts\python.exe scripts\browser_device_authorize.py `
  --source-file auth-local\source-snapshot.jsonl `
  --source-index 0 `
  --user-code ABCD-EFGH `
  --headed
```

也支持：

```powershell
.venv\Scripts\python.exe scripts\browser_device_authorize.py `
  --sso "eyJ..." `
  --verification-url "https://accounts.x.ai/..." `
  --headed
```

## Path B：闭环（推荐抓问题）

```powershell
.venv\Scripts\python.exe scripts\device_flow_browser_complete.py `
  --source-file auth-local\source-snapshot.jsonl `
  --source-index 0 `
  --headed `
  --count 1
```

流程：

1. `auth.x.ai` 拿 `device_code/user_code/verification_url`
2. 浏览器打开并注入 source cookies/SSO，点 Allow
3. `poll_token` 换 `access_token/refresh_token`
4. 成功写入 `keys/oauth_credentials.jsonl` 与 `keys/refresh_tokens.txt`

## 代理注意

Playwright 对 `user:pass@host:port` 直接塞 `server` 容易：

`net::ERR_INVALID_AUTH_CREDENTIALS`

脚本与 `executors._playwright_proxy_settings` 已改为拆分：

```json
{"server":"http://host:port","username":"...","password":"..."}
```

## 和 oauth_rejected 的关系

若浏览器显示已授权但 poll 仍 `oauth_rejected`：

1. 看 reason 后缀（已增强日志：`oauth_rejected:<error>:<desc>`）
2. 检查浏览器与 httpx 是否同一代理出口
3. 检查 SSO cookies 是否含 `accounts.x.ai` 域
