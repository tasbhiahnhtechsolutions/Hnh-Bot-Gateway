// Vendor side in Node: the verification logic of ai-contract.md section 5.4 as a function.
// usage: node node_verifier.mjs <secret> <timestamp> <signature-header> <body-file> <now-seconds>
// exit 0 = accepted, 1 = rejected (what the vendor would answer with HTTP 401)
import crypto from 'node:crypto';
import fs from 'node:fs';

const [secret, tsArg, header, bodyFile, nowArg] = process.argv.slice(2);
const body = fs.readFileSync(bodyFile); // raw bytes, never parsed
const ts = Number(tsArg);

if (!Number.isFinite(ts) || Math.abs(Number(nowArg) - ts) > 300) process.exit(1);

const expected = crypto.createHmac('sha256', secret).update(`${ts}.`).update(body).digest('hex');
const given = header.split(',').map((s) => s.trim().replace('v1=', ''));
const ok = given.some(
  (g) => g.length === expected.length && crypto.timingSafeEqual(Buffer.from(g), Buffer.from(expected)),
);
process.exit(ok ? 0 : 1);
