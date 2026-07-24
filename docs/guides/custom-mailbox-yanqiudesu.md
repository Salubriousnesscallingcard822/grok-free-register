# 自建临时邮箱：yanqiudesu.top

## 链路
xAI -> CF Email Routing catch-all -> Worker -> https://mailhook.YOUR_DOMAIN/webhook -> 本机 :8088 -> register custom

## 本机已就绪
- 收信: grok_register/email_server.py (:8088)
- 启动: .\\start-email-service-windows.ps1
- 隧道: .\\start-mailhook-tunnel-windows.ps1
- Worker: cloudflare/mail-worker/
- token: keys/email-webhook-token.txt = `<see keys/email-webhook-token.txt>`

## 你要做的 CF 步骤
1. 域名 Active + Email Routing 开启 + MX 正确
2. 部署 Worker:
```powershell
cd E:\\download\\claude\\CodeX\\grok-free-register-main\\cloudflare\\mail-worker
npm install
npx wrangler login
npx wrangler deploy
npx wrangler secret put WEBHOOK_URL
# https://mailhook.YOUR_DOMAIN/webhook
npx wrangler secret put WEBHOOK_TOKEN
# paste token above
```
3. Catch-all -> Send to Worker `yanqiudesu-mail-webhook`
4. 隧道:
```powershell
cloudflared tunnel login
cloudflared tunnel create your-mailhook-tunnel
cloudflared tunnel route dns your-mailhook-tunnel mailhook.YOUR_DOMAIN
.\\start-email-service-windows.ps1
.\\start-mailhook-tunnel-windows.ps1 -Named
```
临时: `.\\start-mailhook-tunnel-windows.ps1 -Quick`

## 注册机 .env
```
EMAIL_MODE=custom
EMAIL_DOMAIN=yanqiudesu.top
EMAIL_API=http://127.0.0.1:8088
```

## 验收
1. http://127.0.0.1:8088/health
2. https://mailhook.YOUR_DOMAIN/health
3. 发信 any@yanqiudesu.top
4. logs/email-server.out.log 有 code
5. /check/any@yanqiudesu.top

注意: WEBHOOK_URL 不能用裸 IP。
