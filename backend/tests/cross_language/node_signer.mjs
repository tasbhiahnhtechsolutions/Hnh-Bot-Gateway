// A signer written in Node, to prove the gateway's Python verifier agrees with it.
// usage: node node_signer.mjs <secret> <timestamp> <body-file>   -> prints "v1=<hex>"
import crypto from 'node:crypto';
import fs from 'node:fs';

const [secret, ts, bodyFile] = process.argv.slice(2);
const body = fs.readFileSync(bodyFile);
const digest = crypto.createHmac('sha256', secret).update(`${ts}.`).update(body).digest('hex');
process.stdout.write(`v1=${digest}`);
