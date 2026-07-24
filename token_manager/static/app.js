const state = {
  bootstrap: null,
  page: "overview",
  lastBootstrapSignature: "",
  lastPoolSignature: "",
  lastPipelineSignature: "",
  refreshTimer: 0,
  inFlight: null,
  actionInFlight: false,
};

const API_TIMEOUT_MS = 20000;
const byId = (id) => document.getElementById(id);

function toast(message) {
  const host = byId("toastHost");
  if (!host) return;
  const item = document.createElement("div");
  item.className = "toast";
  item.textContent = message;
  host.appendChild(item);
  window.setTimeout(() => {
    item.classList.add("out");
    window.setTimeout(() => item.remove(), 180);
  }, 1800);
}

async function copyText(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("已复制");
}

async function api(path, options = {}) {
  const {
    timeoutMs = API_TIMEOUT_MS,
    headers = {},
    ...fetchOptions
  } = options;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...fetchOptions,
      headers: { "Content-Type": "application/json", ...headers },
      signal: controller.signal,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json")
      ? response.json()
      : response.text();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`请求超时（${Math.ceil(timeoutMs / 1000)} 秒）`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setBusy(busy) {
  byId("refreshDot")?.classList.toggle("busy", busy);
  document.body.classList.toggle("updating", busy);
  [
    "btnReload",
    "btnRefreshTokens",
    "btnRefreshLogs",
    "btnResetDepleted",
    "btnExportKeyhub",
    "btnRotateKey",
    "btnRotateKey2",
    "btnStartRegister",
    "btnStopRegister",
    "btnStartAuth",
    "btnStopAuth",
    "btnStartPipeline",
    "btnStopPipeline",
    "btnReloadAfterAuth",
    "btnRefreshPipeline",
  ].forEach((id) => {
    const element = byId(id);
    if (element) element.disabled = busy;
  });
}

function setActivePage(page) {
  state.page = page;
  document.querySelectorAll(".sidebar nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === `page-${page}`);
  });

  const titles = {
    overview: ["OVERVIEW", "概览"],
    pipeline: ["PIPELINE", "注册机"],
    pool: ["POOL", "号池"],
    keys: ["MASTER KEY", "统一 Key"],
    connection: ["CONNECTION", "连接"],
    logs: ["LOGS", "日志"],
  };
  const [eyebrow, title] = titles[page] || ["PAGE", page];
  byId("pageEyebrow").textContent = eyebrow;
  byId("pageTitle").textContent = title;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setText(id, value) {
  const element = byId(id);
  if (!element) return;
  const next = value == null ? "" : String(value);
  if (element.textContent !== next) element.textContent = next;
}

function bootstrapSignature(bootstrap) {
  const balance = bootstrap.balance || {};
  const pipeline = bootstrap.pipeline || {};
  return [
    bootstrap.master_key,
    bootstrap.base_url,
    bootstrap.version,
    balance.free_units_remaining,
    balance.accounts_usable_now,
    balance.accounts_total,
    balance.requests_total,
    balance.success_total,
    pipeline?.register?.running,
    pipeline?.auth?.running,
    pipeline?.bridge?.oauth_files,
    pipeline?.bridge?.sessions_total,
    pipeline?.bridge?.pending_convert_estimate,
    (balance.tokens || [])
      .map(
        (token) =>
          `${token.id}:${token.free_units_remaining}:${token.usable}:${token.depleted}:${token.request_count}:${token.last_error || ""}`,
      )
      .join("|"),
  ].join("::");
}

function tokenStatus(token) {
  if (token.depleted) return ["耗尽", "bad"];
  if (token.usable) return ["可用", "ok"];
  if (token.expired) return ["过期", "warn"];
  return ["不可用", "warn"];
}

function renderPool(tokens) {
  const poolBody = byId("poolBody");
  if (!poolBody) return;
  const list = tokens || [];
  const signature = list
    .map(
      (token) =>
        `${token.id}|${token.email}|${token.usable}|${token.depleted}|${token.expired}|${token.free_units_remaining}|${token.free_units_total}|${token.request_count}|${token.success_count}|${token.prompt_tokens}|${token.completion_tokens}|${token.expires_at || ""}|${token.last_error || ""}`,
    )
    .join("\n");
  if (signature === state.lastPoolSignature) return;
  state.lastPoolSignature = signature;

  if (!list.length) {
    poolBody.innerHTML =
      '<tr><td colspan="7">还没有 OAuth token。先启动“转 Key”，或把含 access_token / refresh_token 的 JSON 放进 tokens 目录，然后点“刷新号池”。</td></tr>';
    return;
  }

  const existing = new Map();
  poolBody.querySelectorAll("tr[data-id]").forEach((row) => {
    existing.set(row.dataset.id, row);
  });
  const fragment = document.createDocumentFragment();

  list.forEach((token) => {
    const id = String(token.id || token.email || "");
    const [status, badge] = tokenStatus(token);
    const html = `
      <td>${escapeHtml(token.email || token.id)}</td>
      <td><span class="badge ${badge}">${status}</span></td>
      <td>${token.free_units_remaining}/${token.free_units_total}</td>
      <td>${token.request_count} / 成功 ${token.success_count}</td>
      <td>${token.prompt_tokens || 0} + ${token.completion_tokens || 0}</td>
      <td>${escapeHtml(token.expires_at || "-")}</td>
      <td>${escapeHtml(token.last_error || "")}</td>
    `;
    let row = existing.get(id);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.id = id;
      row.classList.add("row-flash");
    } else if (row.dataset.signature !== html) {
      row.classList.remove("row-flash");
      void row.offsetWidth;
      row.classList.add("row-flash");
    }
    if (row.dataset.signature !== html) {
      row.innerHTML = html;
      row.dataset.signature = html;
    }
    fragment.appendChild(row);
  });

  poolBody.replaceChildren(fragment);
}

function processLabel(proc, external) {
  if (proc?.running) return `运行中 PID ${proc.pid || "-"}`;
  if (external && external.length) return `外部运行 PID ${external[0].pid || "-"}`;
  return "未运行";
}

function renderPipeline(pipeline) {
  const data = pipeline || { enabled: false };
  const signature = JSON.stringify({
    enabled: data.enabled,
    register: {
      running: data.register?.running,
      pid: data.register?.pid,
      accounts: data.register?.accounts_total,
      sessions: data.register?.sessions_total,
      rate: data.register?.rate_hint,
      out: (data.register?.recent_stdout || []).slice(-5),
      err: (data.register?.recent_stderr || []).slice(-3),
      external: data.register?.external || [],
    },
    auth: {
      running: data.auth?.running,
      pid: data.auth?.pid,
      oauth: data.auth?.oauth_files,
      pending: data.auth?.pending_convert_estimate,
      inventory: data.auth?.inventory,
      out: (data.auth?.recent_stdout || []).slice(-5),
      err: (data.auth?.recent_stderr || []).slice(-3),
      external: data.auth?.external || [],
    },
    bridge: data.bridge || {},
  });
  if (signature === state.lastPipelineSignature) return;
  state.lastPipelineSignature = signature;

  const bridge = data.bridge || {};
  setText("pipeAccounts", bridge.accounts_total ?? data.register?.accounts_total ?? 0);
  setText("pipeSessions", bridge.sessions_total ?? data.register?.sessions_total ?? 0);
  setText("pipeOAuth", bridge.oauth_files ?? data.auth?.oauth_files ?? 0);
  setText("pipePending", bridge.pending_convert_estimate ?? data.auth?.pending_convert_estimate ?? 0);

  const regExternal = data.register?.external || [];
  const authExternal = data.auth?.external || [];
  setText("registerState", processLabel(data.register, regExternal));
  setText("authState", processLabel(data.auth, authExternal));

  const rate = data.register?.rate_hint ? `当前速率 ${data.register.rate_hint}` : "速率未知";
  setText(
    "registerNote",
    data.enabled
      ? `注册产出 accounts / auth-sessions。${rate}。号池不直接读这个文件。`
      : "便携 EXE 内不内置注册机控制。请在源码目录启动 Grok Tool，或手动运行 start-register-turbo-windows.ps1。",
  );
  const inv = data.auth?.inventory || {};
  setText(
    "authNote",
    data.enabled
      ? `转 Key 会写入 authenticated/*.json。库存 available=${inv.available || 0} claiming=${inv.claiming || 0} claimed=${inv.claimed || 0}`
      : "便携 EXE 只管理 tokens 目录中的 OAuth JSON。",
  );

  const regLines = []
    .concat(data.register?.recent_stdout || [])
    .concat((data.register?.recent_stderr || []).map((line) => `[err] ${line}`));
  const authLines = []
    .concat(data.auth?.recent_stdout || [])
    .concat((data.auth?.recent_stderr || []).map((line) => `[err] ${line}`));
  setText("registerLog", regLines.slice(-18).join("\n") || "暂无日志");
  setText("authLog", authLines.slice(-18).join("\n") || "暂无日志");

  setText(
    "pipelineSnippet",
    [
      `enabled: ${Boolean(data.enabled)}`,
      `register: ${processLabel(data.register, regExternal)}`,
      `auth: ${processLabel(data.auth, authExternal)}`,
      `accounts: ${bridge.accounts_total ?? 0}`,
      `sessions: ${bridge.sessions_total ?? 0}`,
      `oauth keys: ${bridge.oauth_files ?? 0}`,
      `pending convert ~= ${bridge.pending_convert_estimate ?? 0}`,
      "",
      bridge.note || "注册 -> 转 Key -> 号池 Master Key",
    ].join("\n"),
  );
  setText(
    "pipelineBridgeNote",
    `账号 ${bridge.accounts_total ?? 0} · OAuth ${bridge.oauth_files ?? 0} · 待转 ${bridge.pending_convert_estimate ?? 0}`,
  );
}

function render(bootstrap) {
  state.bootstrap = bootstrap;
  const signature = bootstrapSignature(bootstrap);
  if (signature === state.lastBootstrapSignature) {
    // still refresh pipeline logs even if balance unchanged
    renderPipeline(bootstrap.pipeline || {});
    return;
  }
  state.lastBootstrapSignature = signature;

  const balance = bootstrap.balance || {};
  const usable = Number(balance.accounts_usable_now || 0);
  const successTotal = Number(balance.success_total || 0);
  const failedTotal = Number(balance.failed_total || 0);

  setText("statRemaining", balance.free_units_remaining ?? "-");
  setText("statUsable", usable);
  setText("statTotal", balance.accounts_total ?? "-");
  setText("statRequests", balance.requests_total ?? "-");
  setText("baseUrl", bootstrap.base_url || "-");
  setText("masterKey", bootstrap.master_key || "-");
  setText("masterKey2", bootstrap.master_key || "-");
  setText("masterHint", balance.master_key_hint || "-");
  setText("connBase", bootstrap.base_url || "-");
  setText("connKey", bootstrap.master_key || "-");
  setText("versionLabel", bootstrap.version || "v0.2.0");
  setText(
    "balanceNote",
    `${balance.note || "余额是本地请求预算，不是官方美元余额。"} 成功 ${successTotal}，失败 ${failedTotal}。`,
  );
  setText(
    "poolMeta",
    `${balance.accounts_total || 0} 个账号 · ${usable} 个当前可用 · 本地预算 ${balance.free_units_remaining || 0}`,
  );

  const gateway = byId("gatewayPill");
  if (gateway) {
    gateway.textContent = usable > 0 ? `在线 · ${usable} 可用` : "在线 · 无可用号";
    gateway.classList.remove("bad");
    gateway.classList.toggle("warn", usable <= 0);
  }

  renderPool(balance.tokens || []);
  renderPipeline(bootstrap.pipeline || {});

  const clientConfig = {
    base_url: bootstrap.base_url,
    api_key: bootstrap.master_key,
    headers: { Authorization: `Bearer ${bootstrap.master_key}` },
  };
  setText("clientSnippet", JSON.stringify(clientConfig, null, 2));
  setText(
    "quickSnippet",
    `Codex / 任意 OpenAI 兼容客户端\nBase URL = ${bootstrap.base_url}\nAPI Key  = ${bootstrap.master_key}\n\n本地号池状态\nGET ${bootstrap.endpoints?.balance || "-"}\nAuthorization: Bearer ${bootstrap.master_key}`,
  );
  setText(
    "codexSnippet",
    `# Codex / Chat client\nBASE_URL=${bootstrap.base_url}\nAPI_KEY=${bootstrap.master_key}\n\n# curl smoke\ncurl ${bootstrap.base_url}/models -H "Authorization: Bearer ${bootstrap.master_key}"`,
  );
  setText(
    "keyhubSnippet",
    JSON.stringify(bootstrap.connection?.keyhub_provider || {}, null, 2),
  );
  setText(
    "logs",
    (bootstrap.logs || [])
      .map((entry) => `[${entry.ts}] ${entry.level}: ${entry.message}`)
      .join("\n") || "暂无日志",
  );
}

async function refresh({ silent = false } = {}) {
  if (state.inFlight) return state.inFlight;
  if (!silent) setBusy(true);
  state.inFlight = (async () => {
    try {
      const data = await api("/api/bootstrap");
      render(data);
      return data;
    } catch (error) {
      const gateway = byId("gatewayPill");
      if (gateway) {
        gateway.textContent = "离线";
        gateway.classList.add("bad");
      }
      if (!silent) setText("balanceNote", String(error.message || error));
      throw error;
    } finally {
      state.inFlight = null;
      if (!silent) setBusy(false);
    }
  })();
  return state.inFlight;
}

async function withActionLock(action) {
  if (state.actionInFlight) {
    toast("已有操作正在执行");
    return null;
  }
  state.actionInFlight = true;
  setBusy(true);
  try {
    return await action();
  } finally {
    state.actionInFlight = false;
    setBusy(false);
  }
}

async function performAction(path, message, options = {}) {
  try {
    return await withActionLock(async () => {
      const data = await api(path, {
        method: "POST",
        body: "{}",
        timeoutMs: options.timeoutMs || 60000,
      });
      state.lastBootstrapSignature = "";
      state.lastPoolSignature = "";
      state.lastPipelineSignature = "";
      await refresh({ silent: true });
      toast(options.formatMessage ? options.formatMessage(data) : message);
      return data;
    });
  } catch (error) {
    toast(String(error.message || error));
    return null;
  }
}

function refreshResultMessage(data) {
  const results = Array.isArray(data?.results) ? data.results : [];
  if (!results.length) return "没有可刷新的 Token";
  const succeeded = results.filter((item) => item.ok).length;
  const failed = results.length - succeeded;
  if (!succeeded) {
    const reason = results.find((item) => item.error)?.error || "未知错误";
    throw new Error(`Token 刷新全部失败：${reason}`);
  }
  return failed
    ? `Token 刷新：成功 ${succeeded}，失败 ${failed}`
    : `Token 刷新成功：${succeeded}`;
}

function pipelineActionMessage(data, fallback) {
  const result = data?.result;
  if (!result) return fallback;
  if (result.ok === false) throw new Error(result.error || fallback);
  if (result.message) return result.message;
  if (result.already_running) return `${fallback}（已在运行）`;
  if (result.already_stopped) return `${fallback}（本来就没运行）`;
  if (result.register || result.auth) return `${fallback} 完成`;
  return fallback;
}

function wire() {
  document.querySelectorAll(".sidebar nav button").forEach((button) => {
    button.addEventListener("click", () => setActivePage(button.dataset.page));
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.copy;
      if (key === "masterKey") return copyText(state.bootstrap?.master_key || "");
      if (key === "baseUrl" || key === "connBase") {
        return copyText(state.bootstrap?.base_url || "");
      }
      if (key === "connKey") return copyText(state.bootstrap?.master_key || "");
      return copyText(byId(key)?.textContent || "");
    });
  });

  byId("btnCopyKey").onclick = () => copyText(state.bootstrap?.master_key || "");
  byId("btnReload").onclick = () => performAction("/api/reload", "号池已刷新");
  byId("btnRefreshTokens").onclick = () =>
    performAction("/api/refresh", "Token 刷新完成", {
      timeoutMs: 180000,
      formatMessage: refreshResultMessage,
    });
  byId("btnResetDepleted").onclick = () =>
    performAction("/api/reset-depleted", "已重置耗尽标记");
byId("btnPurgeDead").onclick = () => {
if (
!confirm(
"确认清理死号？会删除 depleted / spending-limit / refresh失败 / 过期且不可刷新 的账号及其 json 文件。",
)
)
return;
performAction("/api/purge-dead", "已清理死号", {
body: JSON.stringify({ delete_files: true }),
});
};


  byId("btnRefreshLogs").onclick = async () => {
    try {
      const data = await api("/api/logs?limit=100");
      setText(
        "logs",
        (data.logs || [])
          .map((entry) => `[${entry.ts}] ${entry.level}: ${entry.message}`)
          .join("\n") || "暂无日志",
      );
    } catch (error) {
      toast(String(error.message || error));
    }
  };

  const rotateKey = async () => {
    if (!confirm("确认轮换 Master Key？旧 Key 会立刻失效。")) return;
    try {
      await withActionLock(async () => {
        const data = await api("/api/rotate-key", {
          method: "POST",
          body: "{}",
        });
        state.lastBootstrapSignature = "";
        await refresh({ silent: true });
        await copyText(data.master_key);
        toast("Master Key 已更换并复制");
      });
    } catch (error) {
      toast(String(error.message || error));
    }
  };
  byId("btnRotateKey").onclick = rotateKey;
  byId("btnRotateKey2").onclick = rotateKey;

  byId("btnExportKeyhub").onclick = async () => {
    try {
      await withActionLock(async () => {
        const data = await api("/api/export/keyhub");
        await copyText(JSON.stringify(data, null, 2));
        setActivePage("connection");
        toast("KeyHub Provider 参数已复制");
      });
    } catch (error) {
      toast(String(error.message || error));
    }
  };
  byId("btnOpenCodexHint").onclick = () => setActivePage("connection");
  byId("btnOpenPipeline") && (byId("btnOpenPipeline").onclick = () => setActivePage("pipeline"));

  const bindPipeline = (id, path, message) => {
    const el = byId(id);
    if (!el) return;
    el.onclick = () =>
      performAction(path, message, {
        timeoutMs: 120000,
        formatMessage: (data) => pipelineActionMessage(data, message),
      });
  };
  bindPipeline("btnStartRegister", "/api/pipeline/start-register", "注册机已启动");
  bindPipeline("btnStopRegister", "/api/pipeline/stop-register", "注册机已停止");
  bindPipeline("btnStartAuth", "/api/pipeline/start-auth", "转 Key 已启动");
  bindPipeline("btnStopAuth", "/api/pipeline/stop-auth", "转 Key 已停止");
  bindPipeline("btnStartPipeline", "/api/pipeline/start-all", "流水线已启动");
  bindPipeline("btnStopPipeline", "/api/pipeline/stop-all", "流水线已停止");
  byId("btnReloadAfterAuth") &&
    (byId("btnReloadAfterAuth").onclick = () => performAction("/api/reload", "号池已同步"));
  byId("btnRefreshPipeline") &&
    (byId("btnRefreshPipeline").onclick = () => refresh().then(() => toast("状态已刷新")).catch((e) => toast(String(e.message || e))));

  const scheduleRefresh = () => {
    if (state.refreshTimer) window.clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(
      () => refresh({ silent: true }).catch(() => {}),
      document.hidden ? 30000 : 8000,
    );
  };
  document.addEventListener("visibilitychange", () => {
    scheduleRefresh();
    if (!document.hidden) refresh({ silent: true }).catch(() => {});
  });
  scheduleRefresh();
}

wire();
refresh().catch((error) => {
  setText("balanceNote", String(error));
  toast(String(error));
});
