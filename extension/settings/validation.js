/**
 * validation.js — base-URL validation for the settings page (P1-5).
 *
 * Loaded by settings.html before settings.js; also evaluated directly by the
 * Node test harness, so it must stay dependency-free and side-effect-free.
 */

/**
 * Validate and normalise a user-supplied base URL.
 *
 * Rules:
 *  - must be non-empty and parse as a URL
 *  - scheme must be http or https
 *  - plaintext http is allowed only for localhost/127.0.0.1 — a remote
 *    http endpoint would leak the API key on the wire
 *  - trailing slashes are stripped so `${base}/api/...` never doubles up
 *
 * Returns { ok: true, url } or { ok: false, error }.
 */
function validateBaseUrl(raw) {
  const trimmed = (raw ?? "").trim();
  if (!trimmed) {
    return { ok: false, error: "URL must not be empty." };
  }

  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch (_) {
    return { ok: false, error: "Not a valid URL." };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, error: "URL must use http:// or https://." };
  }

  const isLocal = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  if (parsed.protocol === "http:" && !isLocal) {
    return {
      ok: false,
      error: "Remote servers require https:// — plain http would expose your API key.",
    };
  }

  return { ok: true, url: trimmed.replace(/\/+$/, "") };
}

// Node test harness support; ignored inside the extension page.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { validateBaseUrl };
}
