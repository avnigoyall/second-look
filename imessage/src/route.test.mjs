// Run from imessage/: npm test  (or node --test src/route.test.mjs; no install needed)
import assert from "node:assert/strict";
import { test } from "node:test";
import { replySenderId, routeMessage } from "./route.mjs";

test("attachments are photos, whatever the caption", () => {
  assert.deepEqual(routeMessage("yes", true), { kind: "photo" });
  assert.deepEqual(routeMessage("", true), { kind: "photo" });
});

test("empty text is ignored", () => {
  assert.deepEqual(routeMessage("", false), { kind: "ignore" });
  assert.deepEqual(routeMessage("   ", false), { kind: "ignore" });
  assert.deepEqual(routeMessage(undefined, false), { kind: "ignore" });
});

test("an email links the phone", () => {
  assert.deepEqual(routeMessage(" me@example.com ", false), { kind: "link" });
});

test("short yes/no answers the ownership question", () => {
  for (const t of ["yes", "Yes!", "yeah", "yep I do", "I own it", "y"]) assert.deepEqual(routeMessage(t, false), { kind: "own", owns: true }, t);
  for (const t of ["no", "Nope", "nah", "I don't", "no, I don't own it"]) assert.deepEqual(routeMessage(t, false), { kind: "own", owns: false }, t);
});

test("everything else is a question for /imessage/ask", () => {
  for (const t of ["what jeans do I have?", "how much have I saved", "hi", "help", "no, what about jeans?",
    "yes but how much did I save on that skirt last week", "nothing", "yesterday's verdict?", "noted, thanks"]) {
    assert.deepEqual(routeMessage(t, false), { kind: "ask" }, t);
  }
});

test("replies come from the line that received the message", () => {
  const msg = { id: "pho_msg_1", receivedBy: { id: "pho_usr_line_b", handle: "+15550199999" }, space: { id: "pho_spc_1" } };
  assert.equal(replySenderId(msg), "pho_usr_line_b");
});

test("without receivedBy the caller falls back to the agent handle", () => {
  for (const msg of [{}, { receivedBy: null }, { receivedBy: {} }, { receivedBy: { id: "" } }, null, undefined]) {
    assert.equal(replySenderId(msg), null, JSON.stringify(msg));
  }
});
