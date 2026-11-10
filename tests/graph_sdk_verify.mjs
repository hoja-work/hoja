// Verify a Hoja webhook request with the upstream contract's official SDK (independent check).
// usage: node graph_sdk_verify.mjs <dir containing node_modules/@linear/sdk> <secret> <signature> < body
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [dir, secret, signature] = process.argv.slice(2);
const mod = await import(pathToFileURL(join(dir, "node_modules/@linear/sdk/dist/webhooks/index.mjs")).href);
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const body = Buffer.concat(chunks);
const client = new mod.LinearWebhookClient(secret);
const parsed = client.parseData(body, signature);
console.log(JSON.stringify({ ok: true, type: parsed.type, action: parsed.action }));
