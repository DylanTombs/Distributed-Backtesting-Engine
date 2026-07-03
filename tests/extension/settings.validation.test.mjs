/**
 * settings.validation.test.mjs — unit tests for the settings-page URL
 * validation (extension/settings/validation.js, P1-5).
 *
 * Run with:  node --test tests/extension/settings.validation.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { validateBaseUrl } = require(
  path.join(HERE, "..", "..", "extension", "settings", "validation.js"),
);

test("accepts https remote URLs", () => {
  const r = validateBaseUrl("https://api.tradingtransformer.com");
  assert.equal(r.ok, true);
  assert.equal(r.url, "https://api.tradingtransformer.com");
});

test("accepts plain http for localhost and 127.0.0.1", () => {
  assert.equal(validateBaseUrl("http://localhost:8502").ok, true);
  assert.equal(validateBaseUrl("http://127.0.0.1:8502").ok, true);
});

test("rejects plain http for remote hosts (would leak the API key)", () => {
  const r = validateBaseUrl("http://api.example.com");
  assert.equal(r.ok, false);
  assert.match(r.error, /https/);
});

test("rejects empty and whitespace-only input", () => {
  assert.equal(validateBaseUrl("").ok, false);
  assert.equal(validateBaseUrl("   ").ok, false);
  assert.equal(validateBaseUrl(null).ok, false);
  assert.equal(validateBaseUrl(undefined).ok, false);
});

test("rejects non-http(s) schemes", () => {
  assert.equal(validateBaseUrl("javascript:alert(1)").ok, false);
  assert.equal(validateBaseUrl("file:///etc/passwd").ok, false);
  assert.equal(validateBaseUrl("ftp://example.com").ok, false);
});

test("rejects unparseable input", () => {
  assert.equal(validateBaseUrl("not a url").ok, false);
  assert.equal(validateBaseUrl("http//missing.colon").ok, false);
});

test("strips trailing slashes so paths never double up", () => {
  assert.equal(validateBaseUrl("https://api.example.com/").url, "https://api.example.com");
  assert.equal(validateBaseUrl("http://localhost:8502///").url, "http://localhost:8502");
});

test("trims surrounding whitespace", () => {
  const r = validateBaseUrl("  https://api.example.com  ");
  assert.equal(r.ok, true);
  assert.equal(r.url, "https://api.example.com");
});
