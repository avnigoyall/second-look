// Reads and validates environment variables. Import `env` and call `requireEnv` for the vars a script needs.
export const env = {
  PHOTON_PROJECT_ID: process.env.PHOTON_PROJECT_ID,
  PHOTON_API_KEY: process.env.PHOTON_API_KEY,
  PHOTON_PLATFORM: process.env.PHOTON_PLATFORM ?? "imessage",
  PHOTON_AGENT_HANDLE: process.env.PHOTON_AGENT_HANDLE,
  GEMINI_API_KEY: process.env.GEMINI_API_KEY,
  EMBED_MODEL: process.env.EMBED_MODEL ?? "gemini-embedding-2",
  EMBED_DIMS: Number(process.env.EMBED_DIMS ?? 1536), // must match the Atlas vector indexes (scripts/setup-mongo.mjs)
  ANTHROPIC_MODEL: process.env.SECOND_LOOK_MODEL ?? "claude-opus-5-5",
  MONGODB_URI: process.env.MONGODB_URI,
  MONGODB_DB: process.env.MONGODB_DB ?? "second_look",
};

export function requireEnv(...names) {
  const missing = names.filter((n) => !env[n]);
  if (missing.length) {
    console.error(`Missing env vars: ${missing.join(", ")}. Add them to ~/.bash_profile, then: source ~/.bash_profile`);
    process.exit(1);
  }
}
