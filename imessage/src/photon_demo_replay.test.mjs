// Tests for the demo replay (photon_demo_replay.mjs). Mocked clock, no network.
// Run: node --test imessage/src/photon_demo_replay.test.mjs
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { createReplay, demoReply, loadEntries, senderHash } from "./photon_demo_replay.mjs";

const REPLY_1 = "hm, that top kind of looks like it's from Shein? I can't be sure from a pic, so check the tag. If it is, I'd be careful: Greenpeace tested 56 Shein items and about a third went over EU chemical limits, with stuff like phthalates and PFAS showing up in some. Maybe look at the fabric before you buy.";
const REPLY_2 = "this looks like the blue halter top you already have in your closet. want to just wear that one?";
const REPLY_3 = "ha, it happens. send me anything else you're unsure about.";

const entries = loadEntries();
const make = (over = {}) => {
  const logs = [];
  const r = createReplay({ entries, replay: true, sequence: true, log: (l) => logs.push(l), random: () => 0, ...over });
  return { r, logs };
};
const img = (senderId, text = "") => ({ senderId, text, hasImage: true, imageSha256: "abc" });
const txt = (senderId, text) => ({ senderId, text, hasImage: false, imageSha256: null });
const id = (e) => e?.id ?? null;

test("config replies are byte-for-byte the given text", () => {
  assert.deepEqual(entries.map((e) => e.reply), [REPLY_1, REPLY_2, REPLY_3]);
});

test("steps fire in order; a second sender has its own sequence", () => {
  const { r } = make();
  assert.equal(id(r.decide(img("A", "What do you think of this top?"))), "top1_fast_fashion");
  assert.equal(id(r.decide(img("B", "What do you think of this top?"))), "top1_fast_fashion");
  assert.equal(id(r.decide(img("A"))), "top2_closet_duplicate");
  assert.equal(id(r.decide(txt("A", "oh thanks I forgot about that one"))), "top2_thanks");
  assert.equal(r.decide(txt("A", "anything else")), null); // finished: live path
  assert.equal(id(r.decide(img("B"))), "top2_closet_duplicate"); // B is still at step 2
});

test("wrong kind of message falls through without advancing", () => {
  const { r } = make();
  r.decide(img("A", "what do you think of this top"));
  assert.equal(r.decide(txt("A", "hello")), null); // text at an image step
  assert.equal(id(r.decide(img("A"))), "top2_closet_duplicate"); // still step 2
  assert.equal(r.decide(img("A")), null); // image at the text step
  assert.equal(id(r.decide(txt("A", "forgot about that one"))), "top2_thanks");
});

test("reset restarts the sequence and falls through", () => {
  const { r } = make();
  r.decide(img("A", "what do you think of this top"));
  r.decide(img("A"));
  assert.equal(r.decide(txt("A", "RESET please")), null);
  assert.equal(r.decide(img("A")), null); // step 1 has no step-only entry
  assert.equal(id(r.decide(img("A", "What   do you THINK of this top?"))), "top1_fast_fashion");
});

test("entry 1 fires on an image with the question; the reply is the config text exactly", () => {
  const { r } = make();
  const e = r.decide(img("A", "What do you think of this top?"));
  assert.equal(e.reply, REPLY_1);
});

test("entry 3 fires on its text, out of order, with priority over step entries", () => {
  const { r } = make();
  const e = r.decide(txt("A", "oh thanks I forgot about that one"));
  assert.equal(id(e), "top2_thanks");
  assert.equal(e.reply, REPLY_3);
  assert.equal(r.decide(img("A")), null); // advanced past the last step
});

test("no match falls through", () => {
  const { r } = make();
  assert.equal(r.decide(txt("A", "is this jacket worth it?")), null);
  assert.equal(r.decide(img("A", "random caption")), null); // step 1 needs the question
});

test("sequence flag off: only match entries fire", () => {
  const { r } = make({ sequence: false });
  r.decide(img("A", "what do you think of this top"));
  assert.equal(r.decide(img("A")), null);
});

test("both flags off changes nothing", async () => {
  const { r } = make({ replay: false, sequence: false });
  assert.equal(r.decide(img("A", "what do you think of this top")), null);
  delete process.env.PHOTON_DEMO_REPLAY;
  let downloaded = false;
  const out = await demoReply({ senderId: "A", text: "what do you think of this top", hasImage: true,
    getImage: async () => { downloaded = true; return { buffer: Buffer.from("x") }; } });
  assert.equal(out, null);
  assert.equal(downloaded, false); // the live path runs exactly as before
});

test("the reply waits delay_ms (mocked clock)", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { r } = make({ random: () => 1 }); // full 15% jitter: 7000 * 1.15 = 8050
    const entry = r.decide(img("A", "what do you think of this top"));
    let done = false;
    r.wait(entry).then(() => { done = true; });
    mock.timers.tick(6999);
    await new Promise((res) => setImmediate(res));
    assert.equal(done, false);
    mock.timers.tick(1051); // 6999 + 1051 = 8050
    await new Promise((res) => setImmediate(res));
    assert.equal(done, true);
  } finally {
    mock.timers.reset();
  }
});

test("logs carry no phone numbers or raw sender ids", () => {
  const { r, logs } = make();
  const phone = "+15105550123";
  r.decide(img(phone, "what do you think of this top"));
  r.decide(txt(phone, "reset"));
  assert.ok(logs.length >= 2);
  assert.ok(logs.every((l) => !l.includes(phone) && !l.includes("5105550123")));
  assert.ok(logs.some((l) => l === "[demo replay] fired top1_fast_fashion"));
  assert.ok(logs.some((l) => l.includes(senderHash(phone))));
});
