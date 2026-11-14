import { randomUUID } from "node:crypto";
import { Photon } from "@photon-ai/api";
import { env } from "./config.mjs";
import { sniffMime } from "./image.mjs";

export const photon = new Photon({ headers: { Authorization: `Bearer ${env.PHOTON_API_KEY}` } });
const path = () => ({ id: env.PHOTON_PROJECT_ID });

let agentPromise;
/** The agent line's Photon user (cached). */
export function getAgent() {
  agentPromise ??= photon.projects.createOrGetUser({
    path: path(),
    body: { handle: env.PHOTON_AGENT_HANDLE, platform: env.PHOTON_PLATFORM },
  });
  return agentPromise;
}

/** Send a plain-text reply from the agent into a space. */
export async function reply(spaceId, text) {
  const agent = await getAgent();
  return photon.projects.sendMessage({
    path: path(),
    headers: { "idempotency-key": randomUUID() },
    body: { spaceId, senderId: agent.id, content: { type: "text", format: "plain", text } },
  });
}

/** Download an attachment. Returns { buffer, mime }. */
export async function downloadAttachment(attachmentId) {
  const p = { id: env.PHOTON_PROJECT_ID, attachmentId };
  const [meta, blob] = await Promise.all([
    photon.projects.getAttachment({ path: p }),
    photon.projects.downloadAttachment({ path: p }),
  ]);
  const buffer = Buffer.from(await blob.arrayBuffer());
  return { buffer, mime: sniffMime(buffer) ?? (meta.contentType || blob.type) };
}

/** Received messages created at or after `since` (ISO string), oldest first. */
export async function listReceived(since) {
  const items = [];
  let pageToken;
  do {
    const page = await photon.projects.listMessages({
      path: path(),
      query: { direction: "received", platform: env.PHOTON_PLATFORM, createdSince: since, order: "asc", pageSize: 100, pageToken },
    });
    items.push(...page.items);
    pageToken = page.nextPageToken;
  } while (pageToken);
  return items;
}

/** Split message content into { text, attachmentIds } (handles grouped text+photo messages). */
export function parseContent(content) {
  const parts = content?.type === "group" ? content.parts : [content];
  const text = parts.filter((p) => p?.type === "text").map((p) => p.text).join("\n").trim();
  const attachmentIds = parts.filter((p) => p?.type === "attachment").map((p) => p.attachmentId);
  return { text, attachmentIds };
}
