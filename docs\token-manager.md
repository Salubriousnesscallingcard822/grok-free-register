# Grok Token Manager

把多个 Grok OAuth token 聚合成 **一个 master key**。

## 你怎么用

1. 启动:
```powershell
powershell -ExecutionPolicy Bypass -File .\start-token-manager-windows.ps1
```

2. 启动后终端只打印掩码，完整 key 在本机 UI 查看。Windows 磁盘文件为
`auth-local/token-manager/master-key.dpapi`，由当前用户 DPAPI 加密；其他平台使用
权限受限的 `master-key.txt`。

3. 在 Codex / KeyHub / 任意 OpenAI 兼容客户端里填:
- Base URL: `http://127.0.0.1:8787/v1`
- API Key: `（master key）`

4. 查余额/号池:
```powershell
curl http://127.0.0.1:8787/balance -H "Authorization: Bearer <master-key>"
```
或浏览器打开:
`http://127.0.0.1:8787/`

## 余额怎么理解

xAI 免费 OAuth 账号 **没有稳定的美元余额 API**。

本管理器提供的是 **本地号池额度**:
- 每个导入账号默认 `100 free-units`
- 每次成功请求扣 1
- 普通 `429` 进入冷却；明确的 `resource-exhausted` 才标记耗尽
- `/balance` 返回:
  - `free_units_remaining`
  - `accounts_usable_now`
  - `accounts_total`
  - 每个账号明细

所以你问“还有多少余额”，这里回答的是:
**还能撑多少本地额度 / 还有几个可用号**。

## 自动能力

- 扫描 `auth-local/authenticated/*.json`
- 过期自动 refresh
- 新请求按 round-robin 选择 token；同一请求不会跨账号重放
- 状态持久化到 `auth-local/token-manager/pool-state.json`
- 状态文件不保存 OAuth secret，并带完整性签名

## 和注册流水线的关系

```text
authorized OAuth JSON -> token_manager master key
                      -> Codex/KeyHub 只填一个 key
```
