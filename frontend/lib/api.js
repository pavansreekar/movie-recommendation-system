const DEFAULT_TIMEOUT_MS = 15000;

// In-memory GET cache shared by every page, so navigating back and forth is instant.
// Any write (POST/PUT/DELETE) clears it so user-specific data never goes stale.
const DEFAULT_CACHE_TTL_MS = 60 * 1000;
const LONG_CACHE_TTL_MS = 5 * 60 * 1000;
const LONG_CACHE_PREFIXES = [
  "/api/dashboard/ott-releases",
  "/api/dashboard/today-pick",
  "/api/search/trending",
  "/api/person/",
];
const responseCache = new Map(); // path -> { at, ttl, data }
const inflight = new Map(); // path -> Promise

function ttlFor(path) {
  return LONG_CACHE_PREFIXES.some((prefix) => path.startsWith(prefix))
    ? LONG_CACHE_TTL_MS
    : DEFAULT_CACHE_TTL_MS;
}

export function peekCache(path) {
  const hit = responseCache.get(path);
  return hit && Date.now() - hit.at < hit.ttl ? hit.data : undefined;
}

export function clearApiCache() {
  responseCache.clear();
  inflight.clear();
}

export function apiRequest(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET") {
    // Chatting doesn't change anything the cached pages show.
    if (!path.startsWith("/api/chat")) clearApiCache();
    return rawRequest(path, options);
  }
  if (options.signal) return rawRequest(path, options);

  const cached = peekCache(path);
  if (cached !== undefined) return Promise.resolve(cached);
  if (inflight.has(path)) return inflight.get(path);

  const promise = rawRequest(path, options)
    .then((data) => {
      if (inflight.get(path) === promise) {
        responseCache.set(path, { at: Date.now(), ttl: ttlFor(path), data });
      }
      return data;
    })
    .finally(() => {
      if (inflight.get(path) === promise) inflight.delete(path);
    });
  inflight.set(path, promise);
  return promise;
}

// For page data that matters on first load. Right after the server wakes up or restarts a
// request can be slow or fail once, so it is retried a few times before giving up.
export async function requestWithRetry(path, attempts = 3) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await apiRequest(path, { timeoutMs: 45000 });
    } catch (error) {
      if (attempt >= attempts) throw error;
      await new Promise((resolve) => setTimeout(resolve, 3000 * attempt));
    }
  }
}

async function rawRequest(path, options = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal, headers, ...restOptions } = options;
  const controller = signal ? null : new AbortController();
  const timeoutId = controller
    ? setTimeout(() => controller.abort(new Error("Request timed out.")), timeoutMs)
    : null;

  try {
    const response = await fetch(path, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(headers || {}),
      },
      signal: signal || controller.signal,
      ...restOptions,
    });

    const isJson = response.headers.get("content-type")?.includes("application/json");
    const payload = isJson ? await response.json() : null;

    if (!response.ok) {
      // Without a JSON error the reply came from the hosting layer, not the app: 502–504
      // mean the server is starting up, restarting or overloaded.
      const fallback = response.status >= 502 && response.status <= 504
        ? "The server is busy or starting up. Please try again in a few seconds."
        : "Request failed.";
      const error = new Error(payload?.error || payload?.message || fallback);
      error.status = response.status;
      throw error;
    }

    return payload;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("Request timed out. Please try again.");
    }
    throw error;
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}
