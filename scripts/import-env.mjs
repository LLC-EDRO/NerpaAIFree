// Optional, explicit import. Copies only LLM configuration, never database settings.
import { readFile, writeFile, chmod } from "node:fs/promises";
import { resolve } from "node:path";
import { parse } from "../backend/node_modules/dotenv/lib/main.js";
const source = process.argv[2];
if (!source)
  throw new Error(
    "Usage: node scripts/import-env.mjs /absolute/path/to/backend/.env",
  );
const env = parse(await readFile(source));
const keys = [
  "LLM_PROVIDER",
  "OPENAI_API_KEY",
  "OPENAI_MODEL_MAIN",
  "OPENAI_SEARCH_MODEL",
  "OPENAI_TIMEOUT_MS",
  "DEEPSEEK_API_KEY",
  "DEEPSEEK_BASE_URL",
  "DEEPSEEK_MODEL",
];
const dest = resolve(import.meta.dirname, "../backend/.env");
await writeFile(
  dest,
  keys
    .filter((k) => env[k])
    .map((k) => `${k}=${JSON.stringify(env[k])}`)
    .concat("PORT=4100", "PRESENTATION_SANDBOX_IMAGE=nerpa-test-pptx:1")
    .join("\n") + "\n",
  { mode: 0o600 },
);
await chmod(dest, 0o600);
console.log("LLM settings imported. Database settings were not copied.");
