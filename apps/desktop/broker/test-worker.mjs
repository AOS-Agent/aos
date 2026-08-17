import worker from "./worker.js";

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

const ADMIN = "adm_" + "b".repeat(48);
const AH = { "x-admin-token": ADMIN };

function mkEnv(invites = { [TOKEN]: JSON.stringify({ status: "active", label: "test" }) }, sessions = {}, machines = {}) {
  return {
    COMPOSIO_API_KEY: "ak_secret_never_leak",
    ADMIN_TOKEN: ADMIN,
    INVITES: mkKV(invites),
    SESSIONS: mkKV(sessions),
    MACHINES: mkKV(machines),
  };
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

// The manifest cache lives in module scope, so these tests drive a fake clock
// rather than pretending each call starts from a cold isolate.
const realNow = Date.now;
let clock = realNow();
Date.now = () => clock;
const advance = (ms) => { clock += ms; };

const MANIFEST = {
  version: "0.9.0",
  notes: "test build",
  pub_date: "2026-08-17T00:00:00Z",
  platforms: { "darwin-aarch64": { signature: "sig", url: "https://aos.hish.am/updater/x.tar.gz" } },
};

console.log("\nupdater");
await t("requires an invite like every other endpoint", async () => {
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const r = await run("/v1/updater/latest.json", mkEnv());
  eq(r.status, 401);
  if (calls.length) throw new Error("hit the origin before authenticating");
});
await t("unknown invite -> 401, origin untouched", async () => {
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const r = await run("/v1/updater/latest.json", mkEnv({}), { headers: H });
  eq(r.status, 401);
  if (calls.length) throw new Error("hit the origin for a stranger");
});
await t("revoked invite -> 403 (an update is a privilege, not a right)", async () => {
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const env = mkEnv({ [TOKEN]: JSON.stringify({ status: "revoked", label: "x" }) });
  const r = await run("/v1/updater/latest.json", env, { headers: H });
  eq(r.status, 403);
});
await t("fetches the manifest from the origin and caches it in KV", async () => {
  advance(120_000);
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const env = mkEnv();
  const r = await run("/v1/updater/latest.json", env, { headers: H });
  eq(r.status, 200); eq(r.body, MANIFEST);
  if (!calls.some((c) => String(c.url).includes("aos.hish.am"))) throw new Error("never asked the origin");
  const cached = await env.SESSIONS.get("updater:latest", "json");
  if (!cached?.manifest) throw new Error("not cached in KV");
});
await t("second call inside the TTL never re-reads the origin", async () => {
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const r = await run("/v1/updater/latest.json", mkEnv(), { headers: H });
  eq(r.body, MANIFEST);
  if (calls.some((c) => String(c.url).includes("aos.hish.am"))) throw new Error("cache did nothing");
});
await t("a cold isolate warms from KV instead of the origin", async () => {
  advance(120_000);
  const fresh = await import("./worker.js?cold=kv");
  routes = { "aos.hish.am": () => J({ version: "should-not-be-read" }) };
  const env = mkEnv({}, { "updater:latest": JSON.stringify({ manifest: MANIFEST, expires: Date.now() + 60_000 }) });
  env.INVITES = mkKV({ [TOKEN]: JSON.stringify({ status: "active", label: "test" }) });
  const res = await fresh.default.fetch(new Request("https://b.example.com/v1/updater/latest.json", { headers: H }), env);
  eq(await res.json(), MANIFEST);
  if (calls.some((c) => String(c.url).includes("aos.hish.am"))) throw new Error("ignored the KV cache");
});
await t("origin down -> serves the last good manifest rather than failing", async () => {
  advance(120_000);
  routes = { "aos.hish.am": () => new Response("down", { status: 503 }) };
  const env = mkEnv({}, { "updater:latest": JSON.stringify({ manifest: MANIFEST, expires: Date.now() - 1 }) });
  env.INVITES = mkKV({ [TOKEN]: JSON.stringify({ status: "active", label: "test" }) });
  const r = await run("/v1/updater/latest.json", env, { headers: H });
  eq(r.status, 200); eq(r.body, MANIFEST);
});
await t("origin down with nothing cached anywhere -> 502, not a fake manifest", async () => {
  const cold = await import("./worker.js?cold=502");
  routes = { "aos.hish.am": () => new Response("down", { status: 503 }) };
  const res = await cold.default.fetch(new Request("https://b.example.com/v1/updater/latest.json", { headers: H }), mkEnv());
  eq(res.status, 502);
});
await t("works with no Composio key configured", async () => {
  advance(120_000);
  routes = { "aos.hish.am": () => J(MANIFEST) };
  const env = mkEnv(); env.COMPOSIO_API_KEY = "";
  const r = await run("/v1/updater/latest.json", env, { headers: H });
  eq(r.status, 200); eq(r.body, MANIFEST);
});
await t("wrong method -> 405", async () => {
  const r = await run("/v1/updater/latest.json", mkEnv(), { method: "POST", headers: H });
  eq(r.status, 405);
});

console.log("\nactivate + me");
await t("claims a handle and records the machine", async () => {
  const env = mkEnv();
  const r = await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  eq(r.status, 200); eq(r.body.ok, true); eq(r.body.handle, "hadi");
  const uid = [...env.MACHINES._store.keys()].find((k) => k.startsWith("machine:"));
  if (!uid) throw new Error("no machine record");
  const rec = JSON.parse(env.MACHINES._store.get(uid));
  eq(rec.machine_id, MACHINE); eq(rec.handle, "hadi");
  if (!rec.activated_at) throw new Error("no activated_at");
  eq(env.MACHINES._store.get("handle:hadi"), uid.slice("machine:".length));
});
await t("handles are normalised to lowercase", async () => {
  const env = mkEnv();
  const r = await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "HaDi" }) });
  eq(r.body.handle, "hadi");
});
await t("a taken handle is refused with 409", async () => {
  const env = mkEnv();
  await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  const other = { ...H, "x-machine-id": "some-other-machine-id" };
  const r = await run("/v1/activate", env, { method: "POST", headers: other, body: JSON.stringify({ handle: "hadi" }) });
  eq(r.status, 409);
});
await t("re-activating the same machine with the same handle is a no-op", async () => {
  const env = mkEnv();
  const first = await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  const again = await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  eq(again.status, 200); eq(again.body.activated_at, first.body.activated_at);
});
await t("renaming releases the old handle", async () => {
  const env = mkEnv();
  await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hisham" }) });
  if (env.MACHINES._store.has("handle:hadi")) throw new Error("old handle still reserved");
  eq(JSON.parse(env.MACHINES._store.get([...env.MACHINES._store.keys()].find((k) => k.startsWith("machine:")))).handle, "hisham");
});
await t("rejects malformed handles", async () => {
  for (const bad of ["a", "", "has space", "../etc", "x".repeat(40), "-leading"]) {
    const r = await run("/v1/activate", mkEnv(), { method: "POST", headers: H, body: JSON.stringify({ handle: bad }) });
    if (r.status !== 400) throw new Error(`accepted ${JSON.stringify(bad)} (${r.status})`);
  }
});
await t("activate needs an invite", async () => {
  const r = await run("/v1/activate", mkEnv(), { method: "POST", body: JSON.stringify({ handle: "hadi" }) });
  eq(r.status, 401);
});
await t("me reports not activated before activation", async () => {
  const r = await run("/v1/me", mkEnv(), { headers: H });
  eq(r.status, 200); eq(r.body, { activated: false });
});
await t("me returns this machine's record", async () => {
  const env = mkEnv();
  await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  const r = await run("/v1/me", env, { headers: H });
  eq(r.body.activated, true); eq(r.body.handle, "hadi"); eq(r.body.machine_id, MACHINE);
});
await t("me is scoped per machine, not per invite", async () => {
  const env = mkEnv();
  await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  const r = await run("/v1/me", env, { headers: { ...H, "x-machine-id": "some-other-machine-id" } });
  eq(r.body, { activated: false });
});
await t("activation degrades cleanly when MACHINES is unbound", async () => {
  const env = mkEnv(); delete env.MACHINES;
  const r = await run("/v1/activate", env, { method: "POST", headers: H, body: JSON.stringify({ handle: "hadi" }) });
  eq(r.status, 503);
});

console.log("\nadmin invites");
await t("mint without a credential -> 401", async () => {
  const r = await run("/v1/admin/invites", mkEnv(), { method: "POST", body: JSON.stringify({ label: "x" }) });
  eq(r.status, 401);
});
await t("an invite token is not an admin token", async () => {
  const r = await run("/v1/admin/invites", mkEnv(), { method: "POST", headers: { "x-admin-token": TOKEN }, body: JSON.stringify({ label: "x" }) });
  eq(r.status, 401);
});
await t("wrong admin token -> 401, nothing minted", async () => {
  const env = mkEnv();
  const before = env.INVITES._store.size;
  const r = await run("/v1/admin/invites", env, { method: "POST", headers: { "x-admin-token": "adm_" + "c".repeat(48) }, body: JSON.stringify({ label: "x" }) });
  eq(r.status, 401); eq(env.INVITES._store.size, before);
});
await t("no ADMIN_TOKEN configured -> 503, never open", async () => {
  const env = mkEnv(); env.ADMIN_TOKEN = "";
  const r = await run("/v1/admin/invites", env, { method: "POST", headers: AH, body: JSON.stringify({ label: "x" }) });
  eq(r.status, 503);
});
await t("mints aos_inv_ token, stored active, usable immediately", async () => {
  const env = mkEnv({});
  const r = await run("/v1/admin/invites", env, { method: "POST", headers: AH, body: JSON.stringify({ label: "operator" }) });
  eq(r.status, 201); eq(r.body.label, "operator");
  if (!/^aos_inv_[0-9a-f]{48}$/.test(r.body.token)) throw new Error("bad token shape " + r.body.token);
  eq(JSON.parse(env.INVITES._store.get(r.body.token)).status, "active");
  const me = await run("/v1/me", env, { headers: { "x-invite-token": r.body.token, "x-machine-id": MACHINE } });
  eq(me.status, 200);
});
await t("two mints never collide", async () => {
  const env = mkEnv({});
  const a = await run("/v1/admin/invites", env, { method: "POST", headers: AH, body: JSON.stringify({ label: "a" }) });
  const b = await run("/v1/admin/invites", env, { method: "POST", headers: AH, body: JSON.stringify({ label: "b" }) });
  if (a.body.token === b.body.token) throw new Error("collision");
});
await t("mint without a label -> 400", async () => {
  const r = await run("/v1/admin/invites", mkEnv(), { method: "POST", headers: AH, body: JSON.stringify({}) });
  eq(r.status, 400);
});
await t("revoke turns the invite off everywhere", async () => {
  const env = mkEnv();
  const r = await run(`/v1/admin/invites/${TOKEN}`, env, { method: "DELETE", headers: AH });
  eq(r.status, 200); eq(r.body.status, "revoked");
  eq(JSON.parse(env.INVITES._store.get(TOKEN)).status, "revoked");
  const after = await run("/v1/updater/latest.json", env, { headers: H });
  eq(after.status, 403);
});
await t("revoke keeps the label for the audit trail", async () => {
  const env = mkEnv();
  const r = await run(`/v1/admin/invites/${TOKEN}`, env, { method: "DELETE", headers: AH });
  eq(r.body.label, "test");
});
await t("revoking an unknown invite -> 404", async () => {
  const r = await run("/v1/admin/invites/aos_inv_deadbeef", mkEnv(), { method: "DELETE", headers: AH });
  eq(r.status, 404);
});
await t("revoke without a credential -> 401", async () => {
  const env = mkEnv();
  const r = await run(`/v1/admin/invites/${TOKEN}`, env, { method: "DELETE" });
  eq(r.status, 401);
  eq(JSON.parse(env.INVITES._store.get(TOKEN)).status, "active");
});
await t("guessing the admin token is throttled", async () => {
  const env = mkEnv();
  let last;
  for (let i = 0; i < 62; i++) {
    last = await run("/v1/admin/invites", env, { method: "POST", headers: { "x-admin-token": `guess-${i}` }, body: JSON.stringify({ label: "x" }) });
  }
  eq(last.status, 429);
});
await t("wrong method on the admin collection -> 405", async () => {
  const r = await run("/v1/admin/invites", mkEnv(), { method: "GET", headers: AH });
  eq(r.status, 405);
});

Date.now = realNow;

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
