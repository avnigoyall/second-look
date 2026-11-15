// friendReply never breaks the existing reply. Run: node --test src/friend.test.mjs
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

let server, mode = "ok";
before(async () => {
  server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      if (mode === "500") { res.writeHead(500); return res.end("{}"); }
      if (mode === "slow") return setTimeout(() => res.end("{}"), 5_000).unref();
      const sent = JSON.parse(body);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ reply: `friend: ${sent.verdict.verdict}`, used_fallback: false }));
    });
  });
  await new Promise((r) => server.listen(0, r));
  process.env.SECOND_LOOK_API = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

const call = async () => {
  const { friendReply } = await import("./friend.mjs");
  return friendReply({ phone: "+15555550100", text: null, hasImage: true, verdict: "good_buy", verdictId: "v1", existing: "existing" });
};

test("returns the friend reply when the backend answers", async () => {
  mode = "ok";
  assert.equal(await call(), "friend: good_buy");
});

test("returns the existing reply on a backend error", async () => {
  mode = "500";
  assert.equal(await call(), "existing");
});

test("returns the existing reply after the 3.5 s timeout", async () => {
  mode = "slow";
  const t = Date.now();
  assert.equal(await call(), "existing");
  assert.ok(Date.now() - t < 4_500);
});
