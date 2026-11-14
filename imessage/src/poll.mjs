// Local dev loop: polls Photon for new received messages and handles each one.
// Run: node src/poll.mjs   (Ctrl+C to stop)
import { requireEnv } from "./config.mjs";
import { handleMessage } from "./handle-message.mjs";
import { getAgent, listReceived, reply } from "./photon.mjs";

requireEnv(
  "PHOTON_PROJECT_ID", "PHOTON_API_KEY", "PHOTON_AGENT_HANDLE",
  "GEMINI_API_KEY", "MONGODB_URI",
);

const INTERVAL_MS = 3000;
let since = new Date().toISOString(); // only messages that arrive after startup

const agent = await getAgent();
console.log(`Second Look listening on ${agent.handle} (polling every ${INTERVAL_MS / 1000}s). Text it a photo.`);

for (;;) {
  try {
    const messages = await listReceived(since);
    for (const msg of messages) {
      try {
        await handleMessage(msg);
      } catch (err) {
        console.error(`[${msg.id}] failed:`, err.message ?? err);
        await reply(msg.space.id, "Sorry, I hit a snag looking at that. Try sending it again in a minute.").catch(() => {});
      }
      if (msg.createdAt > since) since = msg.createdAt;
    }
  } catch (err) {
    console.error("poll error:", err.message ?? err);
  }
  await new Promise((r) => setTimeout(r, INTERVAL_MS));
}
