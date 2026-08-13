#!/usr/bin/env node
/**
 * Writes frontend/env.js from environment variables at build time.
 *
 * Vercel runs this as the build command. Only values that are safe in a browser
 * belong here: the Supabase publishable/anon key (protected by Row Level
 * Security) and the public API base URL. The service-role key and the
 * encryption key must never appear in this file — they live only on the API
 * host.
 */

const fs = require("fs");
const path = require("path");

// Auth is done by the browser talking to Supabase directly, so these two are
// genuinely required for the app to be usable at all.
const REQUIRED = ["SUPABASE_URL", "SUPABASE_KEY"];

const env = {
  SUPABASE_URL: process.env.SUPABASE_URL || "",
  SUPABASE_KEY: process.env.SUPABASE_KEY || "",
  API_BASE_URL: (process.env.API_BASE_URL || "").replace(/\/$/, ""),
};

const missing = REQUIRED.filter((key) => !env[key]);
if (missing.length) {
  console.error(
    `\n[build] Missing required environment variable(s): ${missing.join(", ")}\n` +
      `        Set them in Vercel → Project → Settings → Environment Variables.\n`
  );
  process.exit(1);
}

// API_BASE_URL is optional so the frontend can be published before the API
// host exists. Without it only sign-in/registration work, because every other
// screen calls the FastAPI backend.
if (!env.API_BASE_URL) {
  console.warn(
    `\n[build] WARNING: API_BASE_URL is not set.\n` +
      `        Login and registration will work (they talk to Supabase directly),\n` +
      `        but dashboard, projects, generation, assets and export will fail\n` +
      `        until you deploy the backend and set API_BASE_URL to its URL.\n`
  );
}

if (/service_role/i.test(env.SUPABASE_KEY) || env.SUPABASE_KEY.length > 300) {
  console.error("\n[build] SUPABASE_KEY looks like a service-role key. Refusing to publish it to the browser.\n");
  process.exit(1);
}

const output = path.join(__dirname, "..", "frontend", "env.js");
fs.writeFileSync(
  output,
  `// Generated at build time by scripts/generate-frontend-env.js — do not edit.\n` +
    `window.__ENV__ = ${JSON.stringify(env, null, 2)};\n`,
  "utf8"
);

console.log(`[build] wrote ${output}`);
console.log(`[build]   SUPABASE_URL = ${env.SUPABASE_URL}`);
console.log(`[build]   API_BASE_URL = ${env.API_BASE_URL}`);
