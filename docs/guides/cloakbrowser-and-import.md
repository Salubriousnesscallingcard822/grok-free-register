# CloakBrowser 反指纹 + 一键导入 grok2api
`
本指南把近期落地的能力正式纳入 grok-free-register 范围。
`
## 范围
`
| 能力 | 作用 | 入口 |
|---|---|---|
| CloakBrowser 反指纹 Chromium | 注册/认证降低浏览器指纹暴露 | 自动: CLOAKBROWSER_CACHE_DIR=.cloakbrowser |
| Path B 浏览器 OAuth | accounts.x.ai 批准设备码 -> OAuth JSON | start-auth-windows.ps1 / scripts/device_flow_browser_complete.py |
| 标准凭证导出 | auth-local/authenticated/xai-*.json | scripts/export_authenticated_json.py |
| 一键导入 grok2api | multipart 扫入本地 :8000 | scripts/import_authenticated_to_grok2api.py |
| 全链路一键 | 注册 -> 认证 -> 导入 | start-full-to-grok2api.ps1 |
| 统一入口 | 状态/拉起/停止 | start-all-windows.ps1 |
| 自建邮箱（可选） | @your.domain 长跑收码 | start-email-service-windows.ps1 + CF Worker |
`
## CloakBrowser（反指纹浏览器）
`
### 为什么用它
- 项目注册与认证默认走本机 Chromium 自动化
- CloakBrowser 提供更接近真实环境的浏览器配置，降低纯原生 Playwright 指纹特征
- Windows 启动脚本已固定缓存目录到项目内 .cloakbrowser
`
### 相关位置
- 注册启动: start-windows.ps1（启动前 python -m cloakbrowser info --quick）
- 认证执行器: xai_enroller/executors.py 从以下路径查找可执行文件
  - CLOAKBROWSER_CACHE_DIR
  - ~/.cloakbrowser
  - 项目 .cloakbrowser
- Path B 设备授权: scripts/browser_device_authorize.py / scripts/device_flow_browser_complete.py
`
### 检查
``powershell
cd E:\download\claude\CodeX\grok-free-register-main
.\.venv\Scripts\python.exe -m cloakbrowser info --quick
`
`
## 一键导入 grok2api
`
### 输入
uth-local/authenticated/xai-*.json
`
### 过程
1. POST /api/admin/v1/auth/login
2. POST /api/admin/v1/accounts/import（multipart files=）
3. 解析 SSE 中的 created/updated/synced
4. 状态去重: keys/g2a-imported-subs.txt
5. 429 时固定等待 90 秒重试（--rate-limit-wait 90）
`
### 凭证
keys/.credentials:
`
ADMIN_USER=admin
ADMIN_PASS=your_password
`
`
### 命令
``powershell
.\start-full-to-grok2api.ps1 import-only
# 或
.\.venv\Scripts\python.exe scripts\import_authenticated_to_grok2api.py --batch 40 --batch-pause 1.5 --rate-limit-wait 90
`
`
## 推荐 Windows 全链路
`
``powershell
cd E:\download\claude\CodeX\grok-free-register-main
`
# 统一入口
.\start-all-windows.ps1 status
.\start-all-windows.ps1 up
`
# 一键全链路
.\start-full-to-grok2api.ps1
`
# 分项
.\start-windows.ps1
.\start-auth-windows.ps1 -Background
.\start-full-to-grok2api.ps1 import-only
`
`
## 输出目录
- 注册会话: keys/accounts.txt, keys/auth-sessions.jsonl, auth-local/source-snapshot.jsonl
- 认证成功: auth-local/authenticated/xai-*.json
- 导入状态: keys/g2a-imported-subs.txt
- Cloak 缓存: .cloakbrowser/
`
## 注意
- grok2api 需健康: http://127.0.0.1:8000/healthz
- Path B 依赖代理（默认 http://127.0.0.1:7897）
- 导入器已 token 复用 + 429 退避，避免 admin 登录限流
- 自建邮箱 WEBHOOK_URL 必须是域名，不能裸 IP
