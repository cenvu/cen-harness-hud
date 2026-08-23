import crypto from "node:crypto";
import net from "node:net";

/**
 * cen-harness-hud — DeepSeek PAYG Balance Extension for Pi
 *
 * Canonical Source: cen-harness-hud/integrations/pi/deepseek-balance.ts
 *
 * Feeds live account balance into:
 * 1. Pi native footer status widget (`ctx.ui.setStatus`)
 * 2. Herdr sidebar agent card via Unix socket (`pane.report_metadata`)
 *
 * Features:
 * - Queries official DeepSeek balance API (https://api.deepseek.com/user/balance)
 * - Credential-aware 45-second in-memory TTL cache (cache bound to key fingerprint)
 * - Safe SHA-256 key fingerprinting (zero credential exposure)
 * - Finite number validation (prevents NaN / Infinity on malformed API responses)
 * - Epoch-based monotonic Herdr sequence numbering (safe across process restarts in same pane)
 * - Explicit null token patching for reliable token clearance
 * - Graceful failure handling without crashing or blocking the agent
 */

const STATUS_KEY = "deepseek-balance";
const DEEPSEEK_BALANCE_API_URL = "https://api.deepseek.com/user/balance";
const CACHE_TTL_MS = 45_000; // 45 seconds TTL
const HERDR_SOURCE = "cen-hud:pi-balance";

interface BalanceInfo {
  currency?: string;
  total_balance?: string | number;
  granted_balance?: string | number;
  topped_up_balance?: string | number;
}

interface DeepSeekBalanceResponse {
  is_available?: boolean;
  balance_infos?: BalanceInfo[];
}

interface CacheEntry {
  fingerprint: string;
  statusText: string;
  timestamp: number;
}

let cache: CacheEntry | null = null;
let inFlightRequest: { fingerprint: string; promise: Promise<string | undefined> } | null = null;
let lastHerdrMetadataSeq = 0;

function nextHerdrMetadataSeq(): number {
  const now = Date.now();
  lastHerdrMetadataSeq = Math.max(now, lastHerdrMetadataSeq + 1);
  return lastHerdrMetadataSeq;
}

function computeFingerprint(key: string): string {
  if (!key || key.length < 8) return "";
  try {
    const hash = crypto.createHash("sha256").update(key).digest("hex");
    return `#${hash.slice(0, 4).toUpperCase()}`;
  } catch {
    return "";
  }
}

function formatCurrency(curr?: string): string {
  if (!curr) return "¥";
  if (curr.toUpperCase() === "CNY") return "¥";
  if (curr.toUpperCase() === "USD") return "$";
  return `${curr} `;
}

function parseBalanceResponse(data: DeepSeekBalanceResponse, fingerprint: string): string | undefined {
  if (!data || !Array.isArray(data.balance_infos) || data.balance_infos.length === 0) {
    return undefined;
  }

  const primary = data.balance_infos[0];
  if (!primary || primary.total_balance === undefined || primary.total_balance === null) {
    return undefined;
  }

  const rawTotal = typeof primary.total_balance === "number"
    ? primary.total_balance
    : parseFloat(String(primary.total_balance));

  if (!Number.isFinite(rawTotal) || Number.isNaN(rawTotal)) {
    return undefined;
  }

  const symbol = formatCurrency(primary.currency);
  const total = rawTotal.toFixed(2);
  const tag = fingerprint ? `DS${fingerprint}` : "DeepSeek";

  // Compact footer representation
  return `${tag} · BAL ${symbol}${total}`;
}

async function resolveApiKey(ctx: any): Promise<string | undefined> {
  // 1. Check process environment
  if (process.env.DEEPSEEK_API_KEY && process.env.DEEPSEEK_API_KEY.trim()) {
    return process.env.DEEPSEEK_API_KEY.trim();
  }

  // 2. Check Pi ModelRegistry via ctx
  try {
    if (ctx?.modelRegistry?.getApiKeyForProvider) {
      const key = await ctx.modelRegistry.getApiKeyForProvider("deepseek");
      if (typeof key === "string" && key.trim()) {
        return key.trim();
      }
    }
  } catch {
    // ModelRegistry lookup failed or not present
  }

  try {
    if (ctx?.modelRegistry?.getApiKeyAndHeaders && ctx?.model) {
      const auth = await ctx.modelRegistry.getApiKeyAndHeaders(ctx.model);
      if (auth?.ok && typeof auth.apiKey === "string" && auth.apiKey.trim()) {
        return auth.apiKey.trim();
      }
    }
  } catch {
    // Fallback failed
  }

  return undefined;
}

async function fetchBalance(apiKey: string, fingerprint: string): Promise<string | undefined> {
  try {
    const res = await fetch(DEEPSEEK_BALANCE_API_URL, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      signal: AbortSignal.timeout(6000),
    });

    if (!res.ok) {
      return undefined;
    }

    const json = (await res.json()) as DeepSeekBalanceResponse;
    return parseBalanceResponse(json, fingerprint);
  } catch {
    return undefined;
  }
}

async function getOrRefreshBalance(ctx: any): Promise<string | undefined> {
  const apiKey = await resolveApiKey(ctx);
  if (!apiKey) {
    // No credential available; clear cache and return undefined
    cache = null;
    return undefined;
  }

  const fingerprint = computeFingerprint(apiKey);
  const now = Date.now();

  // Invalidate cache immediately if credential identity has changed
  if (cache && cache.fingerprint !== fingerprint) {
    cache = null;
  }

  // Return cached result if still valid for THIS specific credential
  if (cache && cache.fingerprint === fingerprint && now - cache.timestamp < CACHE_TTL_MS) {
    return cache.statusText;
  }

  // Deduplicate concurrent requests for the same credential
  if (inFlightRequest && inFlightRequest.fingerprint === fingerprint) {
    return inFlightRequest.promise;
  }

  const requestPromise = (async () => {
    try {
      const statusText = await fetchBalance(apiKey, fingerprint);
      if (statusText) {
        cache = { fingerprint, statusText, timestamp: Date.now() };
        return statusText;
      }

      // Return previous cached value ONLY if it matches the current credential
      if (cache && cache.fingerprint === fingerprint) {
        return cache.statusText;
      }

      return undefined;
    } catch {
      if (cache && cache.fingerprint === fingerprint) {
        return cache.statusText;
      }
      return undefined;
    } finally {
      if (inFlightRequest?.fingerprint === fingerprint) {
        inFlightRequest = null;
      }
    }
  })();

  inFlightRequest = { fingerprint, promise: requestPromise };
  return requestPromise;
}

/**
 * Report display-only balance metadata tokens to Herdr sidebar over Unix domain socket.
 * Uses epoch-based monotonic sequence numbering and explicit null patching for reliable clearance.
 */
function reportHerdrMetadata(tokenValue?: string): Promise<void> {
  const isHerdr = process.env.HERDR_ENV === "1";
  const socketPath = process.env.HERDR_SOCKET_PATH;
  const paneId = process.env.HERDR_PANE_ID;

  if (!isHerdr || !socketPath || !paneId) {
    return Promise.resolve();
  }

  const endpoint =
    process.platform === "win32" && socketPath ? `\\\\.\\pipe\\${socketPath}` : socketPath;

  const seq = nextHerdrMetadataSeq();
  const request = {
    id: `${HERDR_SOURCE}:${Date.now()}:${seq}`,
    method: "pane.report_metadata",
    params: {
      pane_id: paneId,
      source: HERDR_SOURCE,
      seq: seq,
      tokens: {
        cen_ds_balance: tokenValue !== undefined ? tokenValue : null,
      },
    },
  };

  return new Promise((resolve) => {
    let finished = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const done = () => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      socket.destroy();
      resolve();
    };

    const socket = net.createConnection(endpoint);
    socket.on("error", () => done());
    socket.on("connect", () => {
      socket.write(`${JSON.stringify(request)}\n`, () => done());
    });
    socket.on("data", () => done());
    socket.on("end", () => done());

    timer = setTimeout(() => done(), 600);
    timer.unref?.();
  });
}

export default function (pi: any) {
  async function updateStatus(ctx: any) {
    try {
      const isDeepSeek =
        ctx?.model?.provider === "deepseek" ||
        (typeof ctx?.model?.baseUrl === "string" && ctx.model.baseUrl.includes("deepseek.com"));

      if (!isDeepSeek) {
        ctx?.ui?.setStatus?.(STATUS_KEY, undefined);
        void reportHerdrMetadata(undefined);
        return;
      }

      const statusText = await getOrRefreshBalance(ctx);
      ctx?.ui?.setStatus?.(STATUS_KEY, statusText);
      void reportHerdrMetadata(statusText);
    } catch {
      // Never throw an uncaught error into Pi
    }
  }

  // Register lifecycle hooks
  pi.on("session_start", async (_event: any, ctx: any) => {
    await updateStatus(ctx);
  });

  pi.on("model_select", async (_event: any, ctx: any) => {
    await updateStatus(ctx);
  });

  pi.on("agent_settled", async (_event: any, ctx: any) => {
    await updateStatus(ctx);
  });

  pi.on("session_shutdown", async (_event: any, ctx: any) => {
    try {
      ctx?.ui?.setStatus?.(STATUS_KEY, undefined);
      void reportHerdrMetadata(undefined);
    } catch {
      // Ignore cleanup error on exit
    }
  });
}
