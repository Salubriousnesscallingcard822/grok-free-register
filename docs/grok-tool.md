# Grok Tool

KeyHub 风格的本机 Grok 管理工具：把多个 OAuth token 收成 **一把 Master Key**，并显示号池余额。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\start-grok-tool-windows.ps1
```

浏览器会打开：

- UI: `http://127.0.0.1:8787/`
- OpenAI 兼容入口: `http://127.0.0.1:8787/v1`

Master Key 存储：

- Windows: `auth-local/token-manager/master-key.dpapi`，使用当前用户 DPAPI 加密
- 其他平台: `auth-local/token-manager/master-key.txt`，权限限制为当前用户

完整 Key 只在本机 UI 中显示。Windows 密文绑定当前用户和机器，复制数据目录到其他
用户或机器后不能解密。

## 你怎么用

### 1) 只给你一把 key
在 Codex / 任意 OpenAI 兼容客户端填：

- Base URL: `http://127.0.0.1:8787/v1`
- API Key: master key

### 2) 看余额
UI 概览页，或：

```powershell
curl http://127.0.0.1:8787/balance -H "Authorization: Bearer <master-key>"
```

返回重点字段：

- `free_units_remaining` 剩余额度
- `accounts_usable_now` 当前可用账号
- `accounts_total` 总账号
- `tokens[]` 每个号明细

### 3) 套壳 KeyHub
在 UI 的「连接」页复制 KeyHub Provider 参数并手动配置 Grok 渠道。它不是完整的
`keyhub-desktop-config` 导入文件；KeyHub 是否接受 `127.0.0.1` 地址取决于本地节点策略。

也可：

```powershell
curl http://127.0.0.1:8787/api/export/keyhub
```

## 自动能力

- 扫描 `auth-local/authenticated/*.json`
- 过期自动 refresh
- 新请求按 round-robin 选择账号；同一请求不会在 429 后跨账号重放
- 普通 429 进入冷却，明确的 `resource-exhausted` 才标记耗尽
- 状态持久化：`auth-local/token-manager/pool-state.json`
- 状态文件不保存 access/refresh token，并使用 Master Key 做完整性签名

## 和流水线关系

```text
authorized OAuth JSON
        |
        v
    Grok Tool
        |
master key + /v1 proxy
        |
Codex / KeyHub / 其他客户端
```

## 余额说明

xAI 免费 OAuth 通常没有稳定美元余额 API。  
Grok Tool 显示的是 **本地号池额度**（默认每号 100 free-units）+ 可用账号数。  
账号被上游 429/resource-exhausted 时会标记耗尽。
