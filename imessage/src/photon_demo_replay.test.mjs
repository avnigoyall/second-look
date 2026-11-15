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
const img = (senderId, imageColor, text = "") => ({ senderId, text, hasImage: true, imageSha256: "abc", imageColor });
const txt = (senderId, text) => ({ senderId, text, hasImage: false, imageSha256: null, imageColor: null });
const id = (e) => e?.id ?? null;

test("config replies are byte-for-byte the given text", () => {
  assert.deepEqual(entries.map((e) => e.reply), [REPLY_1, REPLY_2, REPLY_3]);
});

test("black top -> Shein reply, blue top -> closet reply, then a text -> thanks", () => {
  const { r } = make();
  const first = r.decide(img("A", "black"));
  assert.equal(id(first), "top1_fast_fashion");
  assert.equal(first.reply, REPLY_1);
  const second = r.decide(img("A", "blue"));
  assert.equal(id(second), "top2_closet_duplicate");
  assert.equal(second.reply, REPLY_2);
  const third = r.decide(txt("A", "oh thanks I forgot about that one"));
  assert.equal(id(third), "top2_thanks");
  assert.equal(third.reply, REPLY_3);
  assert.equal(r.decide(txt("A", "anything else")), null); // sequence done: live path
});

test("a second sender has an independent sequence", () => {
  const { r } = make();
  r.decide(img("A", "black"));
  r.decide(img("A", "blue"));
  assert.equal(r.decide(txt("B", "hello")), null); // B is at step 1: no text entry there
  assert.equal(id(r.decide(txt("A", "thanks"))), "top2_thanks");
});

test("color entries fire in any order, and after the sequence ends", () => {
  const { r } = make();
  assert.equal(id(r.decide(img("A", "blue"))), "top2_closet_duplicate"); // blue first
  assert.equal(id(r.decide(img("A", "black"))), "top1_fast_fashion"); // the step does not go back
  assert.equal(id(r.decide(txt("A", "ok"))), "top2_thanks");
  assert.equal(id(r.decide(img("A", "black"))), "top1_fast_fashion"); // still matches after the end
});

test("other colors, unknown color and wrong message kinds fall through", () => {
  const { r } = make();
  assert.equal(r.decide(img("A", "red")), null);
  assert.equal(r.decide(img("A", null)), null); // color check failed
  assert.equal(r.decide(txt("A", "What do you think about this top?")), null); // text at step 1
  r.decide(img("A", "black"));
  r.decide(img("A", "blue"));
  assert.equal(r.decide(img("A", "green")), null); // image at the text step
  assert.equal(id(r.decide(txt("A", "oh thanks I forgot about that one"))), "top2_thanks");
});

test("reset restarts the sequence and falls through", () => {
  const { r } = make();
  r.decide(img("A", "black"));
  r.decide(img("A", "blue"));
  assert.equal(r.decide(txt("A", "RESET please")), null);
  assert.equal(r.decide(txt("A", "thanks")), null); // back at step 1, so the thanks text does not fire yet
});

test("sequence flag off: color entries fire, the step-only thanks does not", () => {
  const { r } = make({ sequence: false });
  assert.equal(id(r.decide(img("A", "black"))), "top1_fast_fashion");
  assert.equal(id(r.decide(img("A", "blue"))), "top2_closet_duplicate");
  assert.equal(r.decide(txt("A", "thanks")), null);
});

test("both flags off changes nothing", async () => {
  const { r } = make({ replay: false, sequence: false });
  assert.equal(r.decide(img("A", "black")), null);
  delete process.env.PHOTON_DEMO_REPLAY;
  let downloaded = false;
  const out = await demoReply({ senderId: "A", text: "", hasImage: true,
    getImage: async () => { downloaded = true; return { buffer: Buffer.from("x") }; },
    colorOf: async () => { throw new Error("must not be called"); } });
  assert.equal(out, null);
  assert.equal(downloaded, false); // the live path runs exactly as before
});

test("the reply waits delay_ms, minus time already spent (mocked clock)", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const flush = () => new Promise((res) => setImmediate(res));
  try {
    const { r } = make({ random: () => 1 }); // full 15% jitter: 7000 * 1.15 = 8050
    const entry = r.decide(img("A", "black"));
    let done = false;
    r.wait(entry).then(() => { done = true; });
    mock.timers.tick(6999);
    await flush();
    assert.equal(done, false);
    mock.timers.tick(1051); // 6999 + 1051 = 8050
    await flush();
    assert.equal(done, true);

    let done2 = false; // the color check already took 3000 ms: wait only the rest
    r.wait(entry, 3000).then(() => { done2 = true; });
    mock.timers.tick(5049);
    await flush();
    assert.equal(done2, false);
    mock.timers.tick(1);
    await flush();
    assert.equal(done2, true);
  } finally {
    mock.timers.reset();
  }
});

test("logs carry no phone numbers or raw sender ids", () => {
  const { r, logs } = make();
  const phone = "+15105550123";
  r.decide(img(phone, "black"));
  r.decide(txt(phone, "reset"));
  assert.ok(logs.length >= 2);
  assert.ok(logs.every((l) => !l.includes(phone) && !l.includes("5105550123")));
  assert.ok(logs.some((l) => l === "[demo replay] fired top1_fast_fashion"));
  assert.ok(logs.some((l) => l.includes(senderHash(phone))));
});
