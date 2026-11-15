# Photon demo replay notes

1. Files added: `imessage/src/photon_demo_replay.mjs`, `imessage/src/photon_demo_replay.test.mjs`, `config/photon_demo_replies.json`, `backend/routers/photon_demo.py` (read-only `POST /imessage/demo-color`: Claude names the top's color; one include line in `backend/main.py`), this file. The module is `.mjs` because the agent's code is ES modules (package type is commonjs).
2. Hook in `imessage/src/handle-message.mjs` (two added lines, nothing else changed):
   `+import { demoReply } from "./photon_demo_replay.mjs";`
   `+  const demo = await demoReply({ senderId: msg.sentBy?.id ?? phone, text, hasImage: attachmentIds.length > 0, getImage: () => downloadAttachment(attachmentIds[0]) }); if (demo) return send(demo);`
3. On: start the agent with `PHOTON_DEMO_REPLAY=1 PHOTON_DEMO_SEQUENCE=1 node src/poll.mjs` (from `imessage/`). `scripts/demo_up.sh` starts only the backend, so the flags go on the agent's command. REPLAY=1 enables match entries; SEQUENCE=1 also enables step-only entries.
4. Off: start the agent without the flags (default). Then no image is downloaded twice and every reply comes from the live path.
5. Script: a black top gets the Shein reply, a blue top gets the halter-top reply (both fire in any order, no caption needed), and the next text after the blue reply gets "ha, it happens". Reset: text any message containing the word "reset" to restart that sender's sequence (the message itself gets a live reply). Restarting the agent also resets every sequence (state is in memory).
6. Delay: delay_ms plus up to 15% jitter before sending; the agent has no typing indicator or wait of its own. The color check (1.2 to 2.3 s measured) counts toward the delay. Measured end to end with the demo photos: black 7018 ms, blue 4108 ms, text 3406 ms.
7. Logs: one `[demo replay] image sha256 <hash> color <color> from <8-char sender hash>` line per image and `[demo replay] fired <id>`. No phone numbers or sender ids.
8. Closet check: no closet item in any account (including demo@secondlook.test) is a blue halter top, so reply top2_closet_duplicate refers to an item that is not in the demo closet.
9. Tests: `node --test imessage/src/photon_demo_replay.test.mjs` (10 pass, mocked clock, no network).
10. Disclosure: Photon replies in this demo are scripted for reliability. The chemical figures come from Greenpeace's published tests, not from the app analyzing the photo.
