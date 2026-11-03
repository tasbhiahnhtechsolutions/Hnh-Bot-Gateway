import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { BuildError, build, resolveGatewayOrigin } from '../build.mjs';

const outRoot = mkdtempSync(join(tmpdir(), 'wa-ext-build-'));
test.after(() => rmSync(outRoot, { recursive: true, force: true }));

const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'));
// Our own files (the bundled third-party socket.io client in vendor/ is not ours to scan).
const allText = (dir) =>
  readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => readFileSync(join(dir, entry.name), 'utf8'))
    .join('\n');

test('dev build points at localhost and allows only WhatsApp Web plus the gateway', () => {
  const { outDir, origin } = build({ env: 'dev', outRoot, envVars: {} });
  assert.equal(origin, 'http://localhost:8000');
  const manifest = readJson(join(outDir, 'manifest.json'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, ['https://web.whatsapp.com/*', 'http://localhost:8000/*']);
  assert.deepEqual(manifest.permissions, ['storage', 'alarms']);
  assert.match(manifest.name, /\(dev\)$/);
  assert.match(readFileSync(join(outDir, 'config.js'), 'utf8'), /"GATEWAY_BASE_URL": "http:\/\/localhost:8000"/);
});

test('prod build has the prod origin only and no trace of localhost', () => {
  const { outDir } = build({ env: 'prod', url: 'https://api.example.com', outRoot, envVars: {} });
  const manifest = readJson(join(outDir, 'manifest.json'));
  assert.deepEqual(manifest.host_permissions, ['https://web.whatsapp.com/*', 'https://api.example.com/*']);
  assert.equal(manifest.name, 'WhatsApp Auto-Reply Gateway');
  assert.equal(manifest.version_name, undefined);
  const text = allText(outDir);
  assert.doesNotMatch(text, /localhost|127\.0\.0\.1|0\.0\.0\.0/i);
  assert.match(readFileSync(join(outDir, 'config.js'), 'utf8'), /"env": "prod"/);
});

test('staging build reads its URL from GATEWAY_STAGING_URL', () => {
  const { origin, outDir } = build({ env: 'staging', outRoot, envVars: { GATEWAY_STAGING_URL: 'https://staging.example.com/' } });
  assert.equal(origin, 'https://staging.example.com');
  assert.match(readJson(join(outDir, 'manifest.json')).name, /\(staging\)$/);
});

test('the build output has no dashboard, logging or contact-sync leftovers', () => {
  const { outDir } = build({ env: 'prod', url: 'https://api.example.com', outRoot, envVars: {} });
  const text = allText(outDir);
  for (const forbidden of ['167.114.96.66', '/log-message', '/api/sync-contacts', '/api/poll-commands', '/api/command-result', 'syncOneOnOneContacts', 'WA_LOGGER_HISTORY_RESULT', 'WA_LOGGER_FETCH_HISTORY']) {
    assert.ok(!text.includes(forbidden), `${forbidden} is still in the build`);
  }
});

test('the build ships the bundled socket.io client and the outbound files', () => {
  const { outDir } = build({ env: 'prod', url: 'https://api.example.com', outRoot, envVars: {} });
  for (const file of ['outbound.js', 'realtime.js', 'vendor/socket.io.min.js', 'vendor/socket.io.LICENSE']) {
    assert.ok(readdirSync(join(outDir, ...file.split('/').slice(0, -1))).includes(file.split('/').pop()), file);
  }
  assert.match(readFileSync(join(outDir, 'vendor', 'socket.io.min.js'), 'utf8'), /Socket\.IO v4/);
});

test('the build refuses unsafe or missing gateway URLs', () => {
  assert.throws(() => resolveGatewayOrigin('prod', ''), BuildError);
  assert.throws(() => resolveGatewayOrigin('prod', 'http://api.example.com'), /https/);
  assert.throws(() => resolveGatewayOrigin('prod', 'https://localhost:8000'), /public host/);
  assert.throws(() => resolveGatewayOrigin('prod', 'https://192.168.1.5'), /public host/);
  assert.throws(() => resolveGatewayOrigin('staging', 'https://10.0.0.4'), /public host/);
  assert.throws(() => resolveGatewayOrigin('prod', 'https://api.example.com/v1'), /origin only/);
  assert.throws(() => resolveGatewayOrigin('prod', 'not a url'), /valid URL/);
  assert.throws(() => resolveGatewayOrigin('qa', 'https://api.example.com'), /--env/);
});
