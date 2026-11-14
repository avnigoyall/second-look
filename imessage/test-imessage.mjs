// Sends a test iMessage from your Photon agent line to your phone.
// Run: node test-imessage.mjs ["optional message text"]
import { randomUUID } from "node:crypto";
import { Photon, ApiError } from "@photon-ai/api";

const {
  PHOTON_PROJECT_ID: projectId,
  PHOTON_API_KEY: apiKey,
  PHOTON_PLATFORM: platform = "imessage",
  PHOTON_TEST_SENDER: testerHandle,
  PHOTON_AGENT_HANDLE: agentHandle,
} = process.env;

const missing = Object.entries({
  PHOTON_PROJECT_ID: projectId,
  PHOTON_API_KEY: apiKey,
  PHOTON_TEST_SENDER: testerHandle,
  PHOTON_AGENT_HANDLE: agentHandle,
})
  .filter(([, v]) => !v)
  .map(([k]) => k);
if (missing.length) {
  console.error(`Missing env vars: ${missing.join(", ")}. Run: source ~/.bash_profile`);
  process.exit(1);
}

const photon = new Photon({ headers: { Authorization: `Bearer ${apiKey}` } });
const path = { id: projectId };
const text = process.argv[2] ?? `Hello from your Photon agent 👋 (${new Date().toLocaleTimeString()})`;

try {
  // 1. Resolve the agent line and your phone to Photon user IDs.
  const agent = await photon.projects.createOrGetUser({ path, body: { handle: agentHandle, platform } });
  const tester = await photon.projects.createOrGetUser({ path, body: { handle: testerHandle, platform } });
  console.log(`Agent  ${agentHandle} -> ${agent.id}`);
  console.log(`Tester ${testerHandle} -> ${tester.id}`);

  // 2. Find the direct conversation you started by texting the agent.
  const page = await photon.projects.listSpaces({
    path,
    query: { platform, participantId: tester.id, orderBy: "last_message_created_at" },
  });
  let space = page.items.find(
    (s) => s.participantIds?.length === 2 && s.participantIds.includes(agent.id),
  );
  if (!space) {
    space = await photon.projects.createOrGetSpace({
      path,
      headers: { "idempotency-key": `dm-${tester.id}-${agent.id}` },
      body: { platform, participantIds: [tester.id, agent.id] },
    });
  }
  console.log(`Space  ${space.id}`);

  // 3. Send the message.
  const msg = await photon.projects.sendMessage({
    path,
    headers: { "idempotency-key": randomUUID() },
    body: {
      spaceId: space.id,
      senderId: agent.id,
      content: { type: "text", format: "plain", text },
    },
  });
  console.log(`\nSent ✅  message ${msg.id}, status: ${msg.status}`);
  console.log(`Check your phone (${testerHandle}) for: "${text}"`);
} catch (err) {
  if (err instanceof ApiError) {
    console.error(`\nPhoton API error ${err.status} (${err.operationId}, request ${err.requestId}):`);
    console.error(JSON.stringify(err.body, null, 2));
  } else {
    console.error(err);
  }
  process.exit(1);
}
