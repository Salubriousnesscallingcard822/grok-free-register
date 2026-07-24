// Cloudflare Email Worker -> POST webhook for grok-free-register EMAIL_MODE=custom
// Domain: example.com
//
// CRITICAL: WEBHOOK_URL MUST be a hostname (not raw IP).
// Use cloudflared tunnel: https://mailhook.example.com/webhook
import PostalMime from "postal-mime";

function trim(s, max = 20000) {
  if (!s) return "";
  return s.length > max ? s.slice(0, max) + "\n...[truncated]" : s;
}

function asList(v) {
  if (!v) return [];
  if (Array.isArray(v)) return v.map(String);
  return [String(v)];
}

export default {
  async email(message, env, ctx) {
    if (!env.WEBHOOK_URL) {
      console.error("WEBHOOK_URL missing (wrangler secret put WEBHOOK_URL)");
      message.setReject("webhook not configured");
      return;
    }

    let parsed;
    try {
      parsed = await PostalMime.parse(message.raw);
    } catch (e) {
      console.error("parse fail", String(e));
      parsed = { subject: "", text: "", html: "" };
    }

    const payload = {
      from: message.from,
      to: message.to,
      to_list: asList(message.to),
      subject: parsed.subject || message.headers.get("subject") || "",
      text: trim(parsed.text || ""),
      html: trim(parsed.html || ""),
      ts: Date.now(),
    };

    const headers = { "content-type": "application/json" };
    if (env.WEBHOOK_TOKEN) headers["x-webhook-token"] = env.WEBHOOK_TOKEN;

    try {
      const res = await fetch(env.WEBHOOK_URL, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      console.log(`webhook delivery -> ${res.status}`);
      if (!res.ok) {
        console.error(`webhook delivery failed: ${res.status}`);
      }
    } catch (e) {
      console.error(`webhook fetch error: ${String(e)}`);
    }
  },
};
