// Environment. Secrets come from the repo-root .env (shared with backend/), with the shell as a fallback.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const rootEnv = fileURLToPath(new URL("../../.env", import.meta.url));
if (existsSync(rootEnv)) process.loadEnvFile(rootEnv); // does not override variables already set in the shell

export const env = {
  PHOTON_PROJECT_ID: process.env.PHOTON_PROJECT_ID,
  PHOTON_API_KEY: process.env.PHOTON_API_KEY,
  PHOTON_PLATFORM: process.env.PHOTON_PLATFORM ?? "imessage",
  PHOTON_AGENT_HANDLE: process.env.PHOTON_AGENT_HANDLE,
  SECOND_LOOK_API: (process.env.SECOND_LOOK_API ?? "http://localhost:8000").replace(/\/$/, ""),
  IMESSAGE_AGENT_SECRET: process.env.IMESSAGE_AGENT_SECRET,
};

export function requireEnv(...names) {
  const missing = names.filter((n) => !env[n]);
  if (missing.length) {
    console.error(`Missing env vars: ${missing.join(", ")}. Put them in the repo-root .env (see imessage/.env.example).`);
    process.exit(1);
  }
}
