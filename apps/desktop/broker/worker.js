/**
 * aos-connect-broker — Cloudflare Worker
 *
 * Holds ONE Composio project key (env.COMPOSIO_API_KEY) and lends it out as a
 * narrow, per-machine capability. Client apps never see the key; they present
 * an invite token plus a machine id and get back only session ids, redirect
 * URLs, and connection status.
 *
 * Isolation model: every (invite token, machine id) pair deterministically
 * derives one opaque Composio `user_id`. Composio scopes connected accounts by
 * user_id, so machine A can neither see nor revoke machine B's connections,
 * and revoking one machine is a single connected_accounts delete.
 *
 * The same invite also gates software updates: /v1/updater/latest.json fetches
 * the release manifest from the static origin and hands it back only to a valid
 * invite, so the origin can be unlisted and the invite becomes the one thing
 * that decides who may install and who may update.
 *
 * Bindings (wrangler.toml):
 *   COMPOSIO_API_KEY  secret   the ak_… project key
 *   ADMIN_TOKEN       secret   operator credential for the /v1/admin endpoints
 *   INVITES           KV       key = invite token, value = {"status":"active","label":"…"}
 *   SESSIONS          KV       key = "session:<user_id>", value = session id
 *                              key = "catalog:v1", value = cached toolkit catalog
 *                              key = "updater:latest", value = cached release manifest
 *                              key = "rl:<hash>:<minute>", value = request counter
 *   MACHINES          KV       key = "machine:<user_id>", value = activation record
 *                              key = "handle:<handle>",   value = owning user_id
 *
 * No dependencies. ES module format.
 */

const COMPOSIO_ORIGIN = "https://backend.composio.dev";
const API_V31 = `${COMPOSIO_ORIGIN}/api/v3.1`;
const API_V3 = `${COMPOSIO_ORIGIN}/api/v3`;

/** Where release manifests are published. Only this worker reads it directly. */
const UPDATER_ORIGIN = "https://aos.hish.am/updater/latest.json";
const UPDATER_TTL_MS = 60_000;
const UPDATER_KV_KEY = "updater:latest";

const RATE_LIMIT_PER_MIN = 60;
const CATALOG_TTL_S = 600; // 10 minutes
const SESSION_TTL_S = 60 * 60 * 24 * 90; // 90 days
const SESSION_REFRESH_AFTER_MS = 60 * 60 * 24 * 1000; // rewrite the TTL at most daily
const RATE_KEY_TTL_S = 120; // KV minimum is 60

const TIMEOUT_READ_MS = 15_000;
const TIMEOUT_WRITE_MS = 30_000;

const MAX_BODY_BYTES = 4096;
const MAX_TOOLKITS_PER_STATUS = 40;

// ── errors ──────────────────────────────────────────────────────────────

/** An error whose message is safe and plain enough to show a user. */
class BrokerError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extraHeaders,
    },
  });
}

// ── small utilities ─────────────────────────────────────────────────────

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Deterministic, opaque per-machine identity. Same token + same machine always
 * yields the same Composio user, so a reinstall of the app recovers the user's
 * existing connections without us storing any mapping — and the value carries
 * no email, hostname, or serial number.
 */
async function deriveUserId(inviteToken, machineId) {
  return `aos_${(await sha256Hex(`${inviteToken}:${machineId}`)).slice(0, 32)}`;
}

/** Constant-ish shape checks. Rejects anything that could distort a KV key. */
function validToken(value) {
  return typeof value === "string" && /^[\x21-\x7e]{8,256}$/.test(value);
}

function validMachineId(value) {
  return typeof value === "string" && /^[A-Za-z0-9._:-]{4,128}$/.test(value);
}

function validSlug(value) {
  return typeof value === "string" && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value.toLowerCase());
}

/** A handle is a person's public name on the network — short, lowercase, stable. */
function validHandle(value) {
  return typeof value === "string" && /^[a-z0-9][a-z0-9_-]{1,31}$/.test(value);
}

/**
 * Length-independent equality for credentials. `===` on strings can leak the
 * length of the shared secret through timing; comparing digests cannot.
 */
async function secretEquals(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) return false;
  const [da, db] = await Promise.all([sha256Hex(a), sha256Hex(b)]);
  let diff = 0;
  for (let i = 0; i < da.length; i++) diff |= da.charCodeAt(i) ^ db.charCodeAt(i);
  return diff === 0;
}

function randomHex(bytes) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function readJsonBody(request) {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) throw new BrokerError(413, "Request body is too large.");
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) throw new BrokerError(413, "Request body is too large.");
  if (!raw.trim()) return {};
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("not an object");
    }
    return parsed;
  } catch {
    throw new BrokerError(400, "Request body must be a JSON object.");
  }
}

// ── Composio transport ──────────────────────────────────────────────────

/**
 * Every outbound call to Composio goes through here, so the api key is applied
 * in exactly one place and can never be attached to a client-controlled URL.
 */
async function composio(env, method, url, { body, timeout } = {}) {
  if (!url.startsWith(`${COMPOSIO_ORIGIN}/`)) {
    // Defensive: a bug that let a client shape the URL must not leak the key.
    throw new BrokerError(500, "Internal routing error.");
  }
  const headers = { "x-api-key": env.COMPOSIO_API_KEY };
  if (body !== undefined) headers["content-type"] = "application/json";

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout ?? TIMEOUT_READ_MS),
    });
  } catch (e) {
    const timedOut = e?.name === "TimeoutError" || e?.name === "AbortError";
    throw new BrokerError(504, timedOut
      ? "The connection service took too long to answer. Try again in a moment."
      : "Could not reach the connection service.");
  }
  return res;
}

/** Pull the most human sentence out of a Composio error body. */
async function composioMessage(res, fallback) {
  const raw = await res.text().catch(() => "");
  try {
    const body = JSON.parse(raw);
    const message = body?.message ?? body?.error?.message ?? body?.error;
    if (typeof message === "string" && message.trim()) return message.trim().slice(0, 300);
  } catch {
    /* not JSON */
  }
  return fallback;
}

/** Read a successful JSON response, or raise a client-safe error. */
async function composioJson(res, fallback) {
  if (res.status === 401 || res.status === 403) {
    // Our key, not theirs — never blame the user for this one.
    throw new BrokerError(502, "The connection service rejected this server's credentials. The operator has been given a bad or expired key.");
  }
  if (res.status === 429) {
    throw new BrokerError(429, "The connection service is rate limiting us. Try again shortly.");
  }
  if (!res.ok) {
    throw new BrokerError(502, await composioMessage(res, `The connection service returned an error (HTTP ${res.status}).`));
  }
  try {
    return await res.json();
  } catch {
    throw new BrokerError(502, "The connection service sent an unreadable reply.");
  }
}

// ── auth + rate limiting ────────────────────────────────────────────────

async function authenticate(request, env) {
  const inviteToken = request.headers.get("x-invite-token") ?? "";
  const machineId = request.headers.get("x-machine-id") ?? "";

  if (!inviteToken || !machineId) {
    throw new BrokerError(401, "This app is not set up to use the connection service yet.");
  }
  if (!validToken(inviteToken) || !validMachineId(machineId)) {
    throw new BrokerError(401, "That invite token or machine id is not in a valid form.");
  }

  let record;
  try {
    record = await env.INVITES.get(inviteToken, "json");
  } catch {
    throw new BrokerError(503, "Could not check the invite right now. Try again in a moment.");
  }
  if (!record) {
    throw new BrokerError(401, "This invite is not recognised. Ask for a new one.");
  }
  if (record.status !== "active") {
    throw new BrokerError(403, "This invite has been turned off. Ask for a new one.");
  }

  const tokenHash = await sha256Hex(inviteToken);
  await enforceRateLimit(env, tokenHash);

  return {
    inviteToken,
    machineId,
    label: typeof record.label === "string" ? record.label : "",
    userId: await deriveUserId(inviteToken, machineId),
  };
}

/**
 * Per-token throttle. Prefers the platform rate-limiting binding when one is
 * configured, because that costs no KV writes — the KV fallback spends one
 * write per request, which is the single biggest driver of KV write volume on
 * this worker (see OPERATIONS.md, "Limits").
 *
 * Either way this is abuse dampening, not a quota: KV is eventually consistent,
 * so a burst spread across colos can exceed the ceiling. A KV hiccup never
 * fails the request.
 */
async function enforceRateLimit(env, tokenHash) {
  if (env.RATE_LIMITER?.limit) {
    const { success } = await env.RATE_LIMITER.limit({ key: tokenHash.slice(0, 32) })
      .catch(() => ({ success: true }));
    if (!success) {
      throw new BrokerError(429, "Too many requests from this install. Wait a minute and try again.");
    }
    return;
  }

  const bucket = Math.floor(Date.now() / 60_000);
  const key = `rl:${tokenHash.slice(0, 16)}:${bucket}`;
  try {
    const current = Number((await env.SESSIONS.get(key)) ?? "0");
    if (current >= RATE_LIMIT_PER_MIN) {
      throw new BrokerError(429, "Too many requests from this install. Wait a minute and try again.");
    }
    await env.SESSIONS.put(key, String(current + 1), { expirationTtl: RATE_KEY_TTL_S });
  } catch (e) {
    if (e instanceof BrokerError) throw e;
    /* KV unavailable — let the request through rather than hard-failing. */
  }
}

// ── session lifecycle ───────────────────────────────────────────────────

const sessionKey = (userId) => `session:${userId}`;

async function fetchSession(env, sessionId) {
  const res = await composio(env, "GET", `${API_V31}/tool_router/session/${encodeURIComponent(sessionId)}`);
  if (res.status === 404) {
    await res.body?.cancel();
    return null;
  }
  const body = await composioJson(res, "Could not read the connection session.");
  return body?.session_id ? body : null;
}

async function createSession(env, userId) {
  const res = await composio(env, "POST", `${API_V31}/tool_router/session`, {
    body: { user_id: userId },
    timeout: TIMEOUT_WRITE_MS,
  });
  const body = await composioJson(res, "Could not start a connection session.");
  if (!body?.session_id) throw new BrokerError(502, "The connection service created an incomplete session.");
  await env.SESSIONS.put(sessionKey(userId), body.session_id, {
    expirationTtl: SESSION_TTL_S,
    metadata: { wrote: Date.now() },
  });
  return body.session_id;
}

/**
 * Session id for a user. `validate` costs one extra Composio round trip and is
 * used only on the handshake endpoint; the other endpoints trust the cache and
 * rely on retryWithSession() to heal a stale id.
 */
async function ensureSession(env, userId, { validate = false } = {}) {
  const { value: cached, metadata } = await env.SESSIONS.getWithMetadata(sessionKey(userId));
  if (cached) {
    if (!validate) return cached;
    const live = await fetchSession(env, cached);
    if (live) {
      // Refresh the TTL so an actively used session never ages out of KV — but
      // at most once a day, since every rewrite is a billable KV write.
      const age = Date.now() - Number(metadata?.wrote ?? 0);
      if (age > SESSION_REFRESH_AFTER_MS) {
        await env.SESSIONS.put(sessionKey(userId), cached, {
          expirationTtl: SESSION_TTL_S,
          metadata: { wrote: Date.now() },
        }).catch(() => { /* stale TTL only costs a session recreate later */ });
      }
      return cached;
    }
    await env.SESSIONS.delete(sessionKey(userId));
  }
  return createSession(env, userId);
}

/**
 * Run a session-scoped call. If Composio says the session is gone, drop the
 * cached id, make a fresh session, and try exactly once more.
 */
async function retryWithSession(env, userId, run) {
  const sessionId = await ensureSession(env, userId);
  const first = await run(sessionId);
  if (first.status !== 404) return first;

  await first.body?.cancel();
  await env.SESSIONS.delete(sessionKey(userId));
  const replacement = await createSession(env, userId);
  return run(replacement);
}

// ── endpoint: POST /v1/session ──────────────────────────────────────────

async function handleSession(env, auth) {
  const sessionId = await ensureSession(env, auth.userId, { validate: true });
  return json({ session_id: sessionId });
}

// ── endpoint: POST /v1/link ─────────────────────────────────────────────

async function handleLink(request, env, auth) {
  const body = await readJsonBody(request);
  const toolkit = String(body.toolkit ?? "").trim().toLowerCase();
  if (!validSlug(toolkit)) throw new BrokerError(400, "Name the service you want to connect.");

  const res = await retryWithSession(env, auth.userId, (sessionId) =>
    composio(env, "POST", `${API_V31}/tool_router/session/${encodeURIComponent(sessionId)}/link`, {
      body: { toolkit },
      timeout: TIMEOUT_WRITE_MS,
    }));

  const payload = await composioJson(res, `Could not create a sign-in link for ${toolkit}.`);
  const redirect = payload?.redirect_url;
  if (typeof redirect !== "string" || !/^https:\/\//.test(redirect)) {
    throw new BrokerError(502, `The connection service did not return a sign-in link for ${toolkit}.`);
  }
  return json({ redirect_url: redirect });
}

// ── endpoint: GET /v1/status ────────────────────────────────────────────

function normalizeState(item, account) {
  const fromSession = item?.connected_account?.status;
  if (typeof fromSession === "string" && fromSession) return fromSession;
  if (item?.is_no_auth === true) return "ACTIVE";
  if (typeof account?.status === "string" && account.status) return account.status;
  return "not_connected";
}

/**
 * Session toolkits list an account only once it is usable. Reading the user's
 * connected accounts as well lets the app tell "waiting in the browser" apart
 * from "failed" — additive, so a failure here degrades rather than breaks.
 */
async function connectedAccounts(env, userId) {
  try {
    const params = new URLSearchParams({ limit: "50", user_ids: userId });
    const res = await composio(env, "GET", `${API_V31}/connected_accounts?${params}`);
    if (!res.ok) {
      await res.body?.cancel();
      return new Map();
    }
    const body = await res.json();
    const items = Array.isArray(body?.items) ? body.items : [];
    const bySlug = new Map();
    for (const account of items) {
      const slug = account?.toolkit?.slug?.toLowerCase();
      if (!slug) continue;
      const current = bySlug.get(slug);
      const isActive = /^active$/i.test(account?.status ?? "");
      const newer = (account?.updated_at ?? "") > (current?.updated_at ?? "");
      if (!current || isActive || (!/^active$/i.test(current?.status ?? "") && newer)) {
        bySlug.set(slug, account);
      }
    }
    return bySlug;
  } catch {
    return new Map();
  }
}

async function handleStatus(url, env, auth) {
  const requested = (url.searchParams.get("toolkits") ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);

  if (!requested.length) throw new BrokerError(400, "List the services you want the status of.");
  if (requested.length > MAX_TOOLKITS_PER_STATUS) {
    throw new BrokerError(400, `Ask about at most ${MAX_TOOLKITS_PER_STATUS} services at a time.`);
  }
  const slugs = [...new Set(requested)];
  if (!slugs.every(validSlug)) throw new BrokerError(400, "One of those service names is not valid.");

  const params = new URLSearchParams({ limit: "50", toolkits: slugs.join(",") });
  const [res, accounts] = await Promise.all([
    retryWithSession(env, auth.userId, (sessionId) =>
      composio(env, "GET", `${API_V31}/tool_router/session/${encodeURIComponent(sessionId)}/toolkits?${params}`)),
    connectedAccounts(env, auth.userId),
  ]);

  const body = await composioJson(res, "Could not read connection status.");
  const items = Array.isArray(body?.items) ? body.items : [];
  const bySlug = new Map(items.filter((i) => i?.slug).map((i) => [String(i.slug).toLowerCase(), i]));

  const out = {};
  for (const slug of slugs) {
    const state = normalizeState(bySlug.get(slug), accounts.get(slug));
    out[slug] = {
      connected: bySlug.get(slug)?.is_no_auth === true || /^active$/i.test(state),
      pending: /^(initiated|initializing|pending)$/i.test(state),
      status: state,
    };
  }
  return json(out);
}

// ── endpoint: GET /v1/toolkits ──────────────────────────────────────────

const CATALOG_KEY = "catalog:v1";

async function handleToolkits(env) {
  const cached = await env.SESSIONS.get(CATALOG_KEY, "json").catch(() => null);
  if (cached?.cards?.length) return json({ cards: cached.cards, source: "api" });

  const res = await composio(env, "GET", `${API_V3}/toolkits?limit=500&sort_by=usage`);
  const body = await composioJson(res, "Could not load the list of available services.");
  const items = Array.isArray(body?.items) ? body.items : Array.isArray(body?.data) ? body.data : [];

  // Pass through presentation fields only. Nothing about auth schemes, tool
  // inventories, or project internals crosses this boundary.
  const cards = items
    .map((t) => ({
      slug: String(t?.slug ?? t?.key ?? t?.name ?? "").toLowerCase(),
      label: String(t?.name ?? t?.slug ?? ""),
      blurb: String(t?.meta?.description ?? t?.description ?? "").slice(0, 90),
      logo: typeof t?.meta?.logo === "string" ? t.meta.logo
        : typeof t?.logo === "string" ? t.logo
        : null,
    }))
    .filter((c) => c.slug);

  if (!cards.length) throw new BrokerError(502, "The connection service returned an empty service list.");
  await env.SESSIONS.put(CATALOG_KEY, JSON.stringify({ cards }), { expirationTtl: CATALOG_TTL_S })
    .catch(() => { /* cache miss next time is fine */ });

  return json({ cards, source: "api" });
}

// ── endpoint: DELETE /v1/connection/{slug} ──────────────────────────────

async function handleDisconnect(slugRaw, env, auth) {
  const slug = slugRaw.toLowerCase();
  if (!validSlug(slug)) throw new BrokerError(400, "That is not a valid service name.");

  const params = new URLSearchParams({ limit: "50", toolkits: slug });
  const listRes = await retryWithSession(env, auth.userId, (sessionId) =>
    composio(env, "GET", `${API_V31}/tool_router/session/${encodeURIComponent(sessionId)}/toolkits?${params}`));
  const list = await composioJson(listRes, "Could not look up that connection.");

  const items = Array.isArray(list?.items) ? list.items : [];
  const accountId = items
    .find((i) => String(i?.slug ?? "").toLowerCase() === slug)
    ?.connected_account?.id;

  // Nothing connected is a successful disconnect, not an error — the caller
  // asked for a state that already holds.
  if (!accountId) return json({ removed: 0 });

  const delRes = await composio(
    env,
    "DELETE",
    `${API_V31}/connected_accounts/${encodeURIComponent(accountId)}?revoke_on_delete=true`,
    { timeout: TIMEOUT_WRITE_MS },
  );
  if (delRes.status === 404) {
    await delRes.body?.cancel();
    return json({ removed: 0 });
  }
  if (!delRes.ok) {
    throw new BrokerError(502, await composioMessage(delRes, `Could not disconnect ${slug}.`));
  }
  await delRes.body?.cancel();
  return json({ removed: 1 });
}

// ── endpoint: GET /v1/updater/latest.json ───────────────────────────────

/**
 * In-isolate manifest cache. Cloudflare keeps an isolate warm across requests
 * on the same colo, so most update checks are answered without touching KV or
 * the origin at all. `stale` is kept past its expiry on purpose: if the origin
 * is down, an old manifest is a far better answer than an error, because a
 * failed check is indistinguishable to the app from "you are up to date".
 */
let updaterMemo = { manifest: null, expires: 0 };

async function readUpdaterOrigin() {
  let res;
  try {
    res = await fetch(UPDATER_ORIGIN, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_READ_MS),
    });
  } catch {
    return null;
  }
  if (!res.ok) {
    await res.body?.cancel();
    return null;
  }
  try {
    const manifest = await res.json();
    return manifest && typeof manifest === "object" ? manifest : null;
  } catch {
    return null;
  }
}

async function handleUpdater(env) {
  const now = Date.now();
  if (updaterMemo.manifest && updaterMemo.expires > now) {
    return json(updaterMemo.manifest);
  }

  const cached = await env.SESSIONS?.get(UPDATER_KV_KEY, "json").catch(() => null);
  if (cached?.manifest && Number(cached.expires) > now) {
    updaterMemo = { manifest: cached.manifest, expires: Number(cached.expires) };
    return json(cached.manifest);
  }

  const fresh = await readUpdaterOrigin();
  if (fresh) {
    updaterMemo = { manifest: fresh, expires: now + UPDATER_TTL_MS };
    await env.SESSIONS?.put(
      UPDATER_KV_KEY,
      JSON.stringify({ manifest: fresh, expires: updaterMemo.expires }),
      { expirationTtl: 300 },
    ).catch(() => { /* a cache miss next time only costs one origin fetch */ });
    return json(fresh);
  }

  // Origin unreachable — serve whatever we last saw rather than nothing.
  const stale = updaterMemo.manifest ?? cached?.manifest ?? null;
  if (stale) return json(stale);
  throw new BrokerError(502, "Could not read the update manifest right now. Try again shortly.");
}

// ── endpoint: POST /v1/activate, GET /v1/me ─────────────────────────────

const machineKey = (userId) => `machine:${userId}`;
const handleKey = (handle) => `handle:${handle}`;

function requireMachines(env) {
  if (!env.MACHINES) throw new BrokerError(503, "Activation is not available yet.");
  return env.MACHINES;
}

async function handleActivate(request, env, auth) {
  const machines = requireMachines(env);
  const body = await readJsonBody(request);
  const handle = String(body.handle ?? "").trim().toLowerCase();
  if (!validHandle(handle)) {
    throw new BrokerError(400, "Pick a handle of 2–32 letters, numbers, dashes or underscores.");
  }

  const existing = await machines.get(machineKey(auth.userId), "json").catch(() => null);
  if (existing?.handle === handle) {
    // Re-activating the same machine with the same handle is a no-op, so a
    // reinstall or a retried request never locks the user out of their own name.
    return json({ ok: true, handle, activated_at: existing.activated_at });
  }

  const owner = await machines.get(handleKey(handle));
  if (owner && owner !== auth.userId) {
    throw new BrokerError(409, "That handle is taken. Pick another one.");
  }

  const record = {
    machine_id: auth.machineId,
    handle,
    activated_at: new Date().toISOString(),
    label: auth.label,
  };
  // Claim the name first: if the second write fails we are left with a reserved
  // handle owned by this user, which the branch above heals on the next attempt.
  await machines.put(handleKey(handle), auth.userId);
  await machines.put(machineKey(auth.userId), JSON.stringify(record));

  if (existing?.handle && existing.handle !== handle) {
    await machines.delete(handleKey(existing.handle)).catch(() => { /* stale reservation */ });
  }

  return json({ ok: true, handle, activated_at: record.activated_at });
}

async function handleMe(env, auth) {
  const machines = requireMachines(env);
  const record = await machines.get(machineKey(auth.userId), "json").catch(() => null);
  if (!record) return json({ activated: false });
  return json({
    activated: true,
    handle: record.handle,
    machine_id: record.machine_id,
    activated_at: record.activated_at,
  });
}

// ── endpoints: /v1/admin/invites ────────────────────────────────────────

async function requireAdmin(request, env) {
  if (!env.ADMIN_TOKEN) throw new BrokerError(503, "Admin access is not configured.");

  // Throttled before the comparison, and on a bucket shared by the whole admin
  // surface rather than by presented credential — otherwise an attacker escapes
  // the limit simply by varying the guess.
  await enforceRateLimit(env, "admin");

  const presented = request.headers.get("x-admin-token") ?? "";
  if (!(await secretEquals(presented, env.ADMIN_TOKEN))) {
    throw new BrokerError(401, "Not authorised.");
  }
}

async function handleMintInvite(request, env) {
  const body = await readJsonBody(request);
  const label = String(body.label ?? "").trim().slice(0, 120);
  if (!label) throw new BrokerError(400, "Give the invite a label so it can be recognised later.");

  const token = `aos_inv_${randomHex(24)}`;
  const record = { status: "active", label, created_at: new Date().toISOString() };
  await env.INVITES.put(token, JSON.stringify(record));
  return json({ token, label, created_at: record.created_at }, 201);
}

async function handleRevokeInvite(token, env) {
  if (!validToken(token)) throw new BrokerError(400, "That is not a valid invite token.");
  const record = await env.INVITES.get(token, "json");
  if (!record) throw new BrokerError(404, "No such invite.");

  // Revoked rather than deleted: authenticate() turns a revoked record into a
  // clear "this invite was turned off", where a missing one reads as a typo.
  const updated = { ...record, status: "revoked", revoked_at: new Date().toISOString() };
  await env.INVITES.put(token, JSON.stringify(updated));
  return json({ ok: true, label: updated.label ?? "", status: "revoked" });
}

// ── router ──────────────────────────────────────────────────────────────

const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
  "access-control-allow-headers": "content-type, x-invite-token, x-machine-id, x-admin-token",
  "access-control-max-age": "86400",
};

/**
 * Exact-match routing only. There is deliberately no catch-all proxy: a path
 * this function does not name cannot reach Composio.
 */
async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });

  // Unauthenticated liveness probe. Touches neither the key nor Composio.
  if (path === "/health" && method === "GET") {
    return json({ ok: true, service: "aos-connect-broker" });
  }

  // Admin lane. Guarded by its own credential and deliberately reachable
  // without an invite — this is how the first invite ever comes into being.
  const adminInvite = /^\/v1\/admin\/invites(?:\/([^/]+))?$/.exec(path);
  if (adminInvite) {
    await requireAdmin(request, env);
    const token = adminInvite[1];
    if (!token && method === "POST") return handleMintInvite(request, env);
    if (token && method === "DELETE") return handleRevokeInvite(decodeURIComponent(token), env);
    throw new BrokerError(405, "That request method is not allowed here.");
  }

  const auth = await authenticate(request, env);

  // Invite-gated, Composio-independent. These answer even when no Composio key
  // is configured, because updates and activation are not connector features.
  if (path === "/v1/updater/latest.json" && method === "GET") return handleUpdater(env);
  if (path === "/v1/activate" && method === "POST") return handleActivate(request, env, auth);
  if (path === "/v1/me" && method === "GET") return handleMe(env, auth);

  if (!env.COMPOSIO_API_KEY) {
    throw new BrokerError(503, "The connection service is not configured yet.");
  }

  if (path === "/v1/session" && method === "POST") return handleSession(env, auth);
  if (path === "/v1/link" && method === "POST") return handleLink(request, env, auth);
  if (path === "/v1/status" && method === "GET") return handleStatus(url, env, auth);
  if (path === "/v1/toolkits" && method === "GET") return handleToolkits(env);

  const disconnect = /^\/v1\/connection\/([^/]+)$/.exec(path);
  if (disconnect && method === "DELETE") {
    return handleDisconnect(decodeURIComponent(disconnect[1]), env, auth);
  }
  const KNOWN_PATHS = [
    "/v1/session", "/v1/link", "/v1/status", "/v1/toolkits",
    "/v1/updater/latest.json", "/v1/activate", "/v1/me",
  ];
  if (disconnect || KNOWN_PATHS.includes(path)) {
    throw new BrokerError(405, "That request method is not allowed here.");
  }

  throw new BrokerError(404, "No such endpoint.");
}

export default {
  async fetch(request, env) {
    let response;
    try {
      response = await route(request, env);
    } catch (e) {
      if (e instanceof BrokerError) {
        response = json({ error: e.message }, e.status);
      } else {
        // Never surface an internal stack or a URL that might carry the key.
        console.error("unhandled", e?.stack ?? String(e));
        response = json({ error: "Something went wrong on the connection service." }, 500);
      }
    }
    const headers = new Headers(response.headers);
    for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
    return new Response(response.body, { status: response.status, headers });
  },
};
