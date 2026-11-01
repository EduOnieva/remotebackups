// node shot.mjs <url> <width> <height> <scale> <mobile 0|1> <out.png> [js to run before the shot]
import { writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
const [url, w, h, scale, mobile, out, prep] = process.argv.slice(2);
const port = 9400 + Math.floor(Math.random() * 100);
const chrome = spawn("google-chrome", ["--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", `--remote-debugging-port=${port}`, "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 1500));
const { webSocketDebuggerUrl } = await (await fetch(`http://localhost:${port}/json/version`)).json();
const ws = new WebSocket(webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d.result); pending.delete(d.id); } };
const send = (method, params = {}, sessionId) => new Promise((r) => { ws.send(JSON.stringify({ id: ++id, method, params, sessionId })); pending.set(id, r); });
const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Emulation.setDeviceMetricsOverride", { width: +w, height: +h, deviceScaleFactor: +scale, mobile: mobile === "1" }, sessionId);
await send("Page.navigate", { url }, sessionId);
await new Promise((r) => setTimeout(r, 2500));
if (prep) { await send("Runtime.evaluate", { expression: prep, awaitPromise: true }, sessionId); await new Promise((r) => setTimeout(r, 1200)); }
const { contentSize } = await send("Page.getLayoutMetrics", {}, sessionId);
const height = Math.ceil(contentSize.height);
await send("Emulation.setDeviceMetricsOverride", { width: +w, height, deviceScaleFactor: +scale, mobile: mobile === "1" }, sessionId);
await new Promise((r) => setTimeout(r, 400));
const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }, sessionId);
writeFileSync(out, Buffer.from(data, "base64"));
console.log(out, `${w}x${height} @${scale}x`);
ws.close(); chrome.kill();
