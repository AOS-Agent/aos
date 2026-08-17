import worker from "/tmp/agents-work2/broker/worker.js";

// ── mock KV ──────────────────────────────────────────────────────────
function mkKV(seed = {}) {
  const store = new Map(Object.entries(seed));
  return {
    _store: store,
    async get(k, type) {
      const e = store.get(k);
      if (e === undefined) return null;
      return type === "json" ? JSON.parse(e) : e;
    },
    async getWithMetadata(k) {
      const e = store.get(k);
      return { value: e === undefined ? null : e, metadata: store.get(`__meta__${k}`) ?? null };
    },
    async put(k, v, opts) {
      store.set(k, v);
      if (opts?.metadata) store.set(`__meta__${k}`, opts.metadata);
    },
    async delete(k) { store.delete(k); },
  };
}

let calls = [];
let routes = {};
globalThis.fetch = async (url, init) => {
  calls.push({ url, method: init?.method, key: init?.headers?.["x-api-key"], body: init?.body });
  for (const [pattern, fn] of Object.entries(routes)) {
    if (url.includes(pattern)) return fn(url, init);
  }
  return new Response("not stubbed", { status: 500 });
};
const J = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

const TOKEN = "itk_" + "a".repeat(40);
const MACHINE = "3f1c8a20-0000-4000-8000-abcdefabcdef";
const H = { "x-invite-token": TOKEN, "x-machine-id": MACHINE };

function mkEnv(invites = { [TOKEN]: JSON.stringify({ status: "active", label: "test" }) }, sessions = {}) {
  return { COMPOSIO_API_KEY: "ak_secret_never_leak", INVITES: mkKV(invites), SESSIONS: mkKV(sessions) };
}

let pass = 0, fail = 0;
async function t(name, fn) {
  calls = [];
  try { await fn(); console.log(`  ok   ${name}`); pass++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); fail++; }
}
function eq(a, b, what = "") {
  const A = JSON.stringify(a), B = JSON.stringify(b);
  if (A !== B) throw new Error(`${what} expected ${B}, got ${A}`);
}
const call = (path, init = {}) => worker.fetch(new Request(`https://b.example.com${path}`, init), init.__env);

async function run(path, env, init = {}) {
  const res = await worker.fetch(new Request(`https://b.example.com${path}`, init), env);
  let body; try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

console.log("\nauth");
await t("health needs no auth", async () => {
  const r = await run("/health", mkEnv());
  eq(r.status, 200); eq(r.body.ok, true);
});
await t("missing headers -> 401", async () => {
  const r = await run("/v1/session", mkEnv(), { method: "POST" });
  eq(r.status, 401);
  if (!r.body.error) throw new Error("no error field");
});
await t("unknown token -> 401", async () => {
  const r = await run("/v1/session", mkEnv({}), { method: "POST", headers: H });
  eq(r.status, 401);
});
await t("revoked token -> 403", async () => {
  const env = mkEnv({ [TOKEN]: JSON.stringify({ status: "revoked", label: "x" }) });
  const r = await run("/v1/session", env, { method: "POST", headers: H });
  eq(r.status, 403);
});
await t("malformed machine id -> 401", async () => {
  const r = await run("/v1/session", mkEnv(), { method: "POST", headers: { ...H, "x-machine-id": "a b/../c" } });
  eq(r.status, 401);
});
await t("missing secret -> 503", async () => {
  const env = mkEnv(); env.COMPOSIO_API_KEY = "";
  const r = await run("/v1/session", env, { method: "POST", headers: H });
  eq(r.status, 503);
});

console.log("\nsession");
await t("creates session, caches, derives aos_ user id", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "sess_1", mcp: { url: "x" } }) };
  const env = mkEnv();
  const r = await run("/v1/session", env, { method: "POST", headers: H });
  eq(r.status, 200); eq(r.body, { session_id: "sess_1" });
  const created = JSON.parse(calls.find(c => c.method === "POST").body);
  if (!/^aos_[0-9a-f]{32}$/.test(created.user_id)) throw new Error("bad user_id " + created.user_id);
  const cached = [...env.SESSIONS._store.keys()].find(k => k.startsWith("session:"));
  if (!cached) throw new Error("session not cached");
});
await t("deterministic user id across calls", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "sess_1" }) };
  const ids = [];
  for (let i = 0; i < 2; i++) {
    await run("/v1/session", mkEnv(), { method: "POST", headers: H });
    ids.push(JSON.parse(calls.filter(c => c.method === "POST").pop().body).user_id);
  }
  if (ids[0] !== ids[1]) throw new Error("not deterministic");
});
await t("different machine -> different user id", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "s" }) };
  await run("/v1/session", mkEnv(), { method: "POST", headers: H });
  const a = JSON.parse(calls.filter(c => c.method === "POST").pop().body).user_id;
  calls = [];
  await run("/v1/session", mkEnv(), { method: "POST", headers: { ...H, "x-machine-id": "other-machine-id" } });
  const b = JSON.parse(calls.filter(c => c.method === "POST").pop().body).user_id;
  if (a === b) throw new Error("collision");
});
const expectedKey = await (async () => {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${TOKEN}:${MACHINE}`));
  const hex = [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
  return `session:aos_${hex}`;
})();

await t("reuses valid cached session (no POST)", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "sess_cached" }) };
  const env = mkEnv();
  await env.SESSIONS.put(expectedKey, "sess_cached", { metadata: { wrote: Date.now() } });
  calls = [];
  const r = await run("/v1/session", env, { method: "POST", headers: H });
  eq(r.body, { session_id: "sess_cached" });
  if (calls.some(c => c.method === "POST")) throw new Error("recreated instead of reusing");
  if (calls.length !== 1) throw new Error("expected exactly one validating GET, got " + calls.length);
});

await t("cache key matches derived user id", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "s1" }) };
  const env = mkEnv();
  await run("/v1/session", env, { method: "POST", headers: H });
  if (!env.SESSIONS._store.has(expectedKey)) {
    throw new Error("cached under " + [...env.SESSIONS._store.keys()].join(","));
  }
});

await t("non-session endpoints skip the validating GET", async () => {
  routes = {
    "/toolkits?": () => J({ items: [{ slug: "gmail", connected_account: { status: "ACTIVE" } }] }),
    "/connected_accounts?": () => J({ items: [] }),
  };
  const env = mkEnv();
  await env.SESSIONS.put(expectedKey, "sess_cached", { metadata: { wrote: Date.now() } });
  calls = [];
  const r = await run("/v1/status?toolkits=gmail", env, { headers: H });
  eq(r.status, 200);
  if (calls.some(c => /\/tool_router\/session\/[^/]+$/.test(String(c.url)))) {
    throw new Error("wasted a validating GET on a cached session");
  }
});
await t("recreates on 404 cached session", async () => {
  let created = 0;
  routes = {
    "/tool_router/session/": () => new Response("", { status: 404 }),
    "/tool_router/session": (u, i) => { if (i.method === "POST") { created++; return J({ session_id: "sess_new" }); } return new Response("", { status: 404 }); },
  };
  const env = mkEnv();
  // derive the right key by running once with a stub that creates
  const uidKey = "session:" + "aos_" + [...(await (async () => {
    const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${TOKEN}:${MACHINE}`));
    return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
  })())].join("");
  await env.SESSIONS.put(uidKey, "sess_stale", { metadata: { wrote: Date.now() } });
  const r = await run("/v1/session", env, { method: "POST", headers: H });
  eq(r.body, { session_id: "sess_new" });
  if (created !== 1) throw new Error("expected 1 create, got " + created);
});

console.log("\nlink");
await t("returns redirect_url only", async () => {
  routes = {
    "/tool_router/session/": (u, i) => i.method === "POST"
      ? J({ redirect_url: "https://auth.composio.dev/x", secret_stuff: "nope" })
      : J({ session_id: "s1" }),
    "/tool_router/session": () => J({ session_id: "s1" }),
  };
  const r = await run("/v1/link", mkEnv(), { method: "POST", headers: { ...H, "content-type": "application/json" }, body: JSON.stringify({ toolkit: "gmail" }) });
  eq(r.status, 200); eq(r.body, { redirect_url: "https://auth.composio.dev/x" });
});
await t("rejects bad toolkit slug", async () => {
  const r = await run("/v1/link", mkEnv(), { method: "POST", headers: H, body: JSON.stringify({ toolkit: "../../admin" }) });
  eq(r.status, 400);
});
await t("rejects non-https redirect", async () => {
  routes = { "/link": () => J({ redirect_url: "javascript:alert(1)" }), "/tool_router/session": () => J({ session_id: "s1" }) };
  const r = await run("/v1/link", mkEnv(), { method: "POST", headers: H, body: JSON.stringify({ toolkit: "gmail" }) });
  eq(r.status, 502);
});

console.log("\nstatus");
await t("maps connected/pending per slug", async () => {
  routes = {
    "/toolkits?": () => J({ items: [{ slug: "gmail", connected_account: { status: "ACTIVE", id: "ca_1" } }] }),
    "/connected_accounts?": () => J({ items: [{ toolkit: { slug: "slack" }, status: "INITIATED", updated_at: "2026-01-01" }] }),
    "/tool_router/session": () => J({ session_id: "s1" }),
  };
  const r = await run("/v1/status?toolkits=gmail,slack,notion", mkEnv(), { headers: H });
  eq(r.status, 200);
  eq(r.body.gmail.connected, true);
  eq(r.body.slack.pending, true);
  eq(r.body.notion, { connected: false, pending: false, status: "not_connected" });
});
await t("no toolkits param -> 400", async () => {
  const r = await run("/v1/status", mkEnv(), { headers: H });
  eq(r.status, 400);
});
await t("too many toolkits -> 400", async () => {
  const many = Array.from({ length: 50 }, (_, i) => `tk${i}`).join(",");
  const r = await run(`/v1/status?toolkits=${many}`, mkEnv(), { headers: H });
  eq(r.status, 400);
});
await t("survives connected_accounts failure", async () => {
  routes = {
    "/toolkits?": () => J({ items: [{ slug: "gmail", is_no_auth: false, connected_account: { status: "ACTIVE" } }] }),
    "/connected_accounts?": () => new Response("boom", { status: 500 }),
    "/tool_router/session": () => J({ session_id: "s1" }),
  };
  const r = await run("/v1/status?toolkits=gmail", mkEnv(), { headers: H });
  eq(r.status, 200); eq(r.body.gmail.connected, true);
});

console.log("\ntoolkits");
await t("passes through only slug/name/desc/logo", async () => {
  routes = { "/api/v3/toolkits": () => J({ items: [{ slug: "GMAIL", name: "Gmail", meta: { description: "d", logo: "l" }, auth_config: "SECRET" }] }) };
  const env = mkEnv();
  const r = await run("/v1/toolkits", env, { headers: H });
  eq(r.body.cards, [{ slug: "gmail", label: "Gmail", blurb: "d", logo: "l" }]);
  if (JSON.stringify(r.body).includes("SECRET")) throw new Error("leaked field");
});
await t("serves from cache on second call", async () => {
  routes = { "/api/v3/toolkits": () => J({ items: [{ slug: "gmail", name: "Gmail" }] }) };
  const env = mkEnv();
  await run("/v1/toolkits", env, { headers: H });
  calls = [];
  const r = await run("/v1/toolkits", env, { headers: H });
  eq(r.body.cards.length, 1);
  if (calls.some(c => String(c.url).includes("toolkits"))) throw new Error("did not use cache");
});

console.log("\ndisconnect");
await t("finds account and deletes with revoke", async () => {
  let deleted = null;
  routes = {
    "/toolkits?": () => J({ items: [{ slug: "gmail", connected_account: { id: "ca_9" } }] }),
    "/connected_accounts/": (u, i) => { deleted = u; return J({ ok: true }); },
    "/tool_router/session": () => J({ session_id: "s1" }),
  };
  const r = await run("/v1/connection/gmail", mkEnv(), { method: "DELETE", headers: H });
  eq(r.body, { removed: 1 });
  if (!deleted.includes("ca_9") || !deleted.includes("revoke_on_delete=true")) throw new Error("bad delete url " + deleted);
});
await t("nothing connected -> removed 0", async () => {
  routes = { "/toolkits?": () => J({ items: [] }), "/tool_router/session": () => J({ session_id: "s1" }) };
  const r = await run("/v1/connection/gmail", mkEnv(), { method: "DELETE", headers: H });
  eq(r.body, { removed: 0 });
});

console.log("\nrouting + leakage");
await t("unknown path -> 404", async () => {
  const r = await run("/v1/whatever", mkEnv(), { headers: H });
  eq(r.status, 404);
});
await t("path traversal to composio blocked", async () => {
  const r = await run("/v1/../../api/v3/projects", mkEnv(), { headers: H });
  if (r.status === 200) throw new Error("reachable!");
});
await t("wrong method -> 405", async () => {
  const r = await run("/v1/session", mkEnv(), { method: "GET", headers: H });
  eq(r.status, 405);
});
await t("composio 401 never blames user / never echoes key", async () => {
  routes = { "/tool_router/session": () => J({ message: "invalid api key ak_secret_never_leak" }, 401) };
  const r = await run("/v1/session", mkEnv(), { method: "POST", headers: H });
  eq(r.status, 502);
  if (r.body.error.includes("ak_")) throw new Error("key echoed!");
});
await t("api key sent to composio, only to composio", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "s1" }) };
  await run("/v1/session", mkEnv(), { method: "POST", headers: H });
  if (!calls.every(c => String(c.url).startsWith("https://backend.composio.dev/"))) throw new Error("off-host call");
  if (!calls.every(c => c.key === "ak_secret_never_leak")) throw new Error("missing key");
});
await t("OPTIONS preflight", async () => {
  const r = await run("/v1/session", mkEnv(), { method: "OPTIONS" });
  eq(r.status, 204);
});

console.log("\nrate limit");
await t("kv fallback throttles at 60/min", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "s1" }) };
  const env = mkEnv();
  let last;
  for (let i = 0; i < 62; i++) last = await run("/v1/session", env, { method: "POST", headers: H });
  eq(last.status, 429);
});
await t("rate limiter binding preferred, no kv writes", async () => {
  routes = { "/tool_router/session": () => J({ session_id: "s1" }) };
  const env = mkEnv();
  env.RATE_LIMITER = { limit: async () => ({ success: true }) };
  await run("/v1/session", env, { method: "POST", headers: H });
  if ([...env.SESSIONS._store.keys()].some(k => k.startsWith("rl:"))) throw new Error("kv counter written anyway");
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
