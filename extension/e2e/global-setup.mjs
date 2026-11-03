// Builds the dev extension and reads the keys of the two seeded demo vendors from the
// running docker stack (`make e2e` starts a fresh one). Keys go to the worker environment
// only; nothing is printed or written to disk.
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const extensionDir = path.resolve(here, '..');
const projectDir = path.resolve(extensionDir, '..');

export default async function globalSetup() {
  execFileSync('node', ['build.mjs', '--env', 'dev'], { cwd: extensionDir, stdio: 'ignore' });

  const gateway = (args) =>
    execFileSync('docker', ['compose', 'exec', '-T', 'api', 'gateway', ...args], {
      cwd: projectDir,
      encoding: 'utf8',
    });

  let out;
  try {
    out = gateway(['seed']);
  } catch (err) {
    throw new Error(
      'Could not run `gateway seed` in the docker stack. Start it with `make up`, or run `make e2e`.',
      { cause: err },
    );
  }

  const keys = {};
  let current = null;
  for (const line of out.split('\n')) {
    const created = line.match(/^Vendor created: (\S+)/);
    if (created) current = created[1];
    const live = line.match(/LIVE key:\s+(wab_live_\S+)/);
    if (live && current) keys[current] = live[1];
  }

  // Vendors left over from an earlier run: their keys are not shown again, so issue new ones.
  // The three demo vendors come from `seed`. Two more are made here for the 5-profile routing test.
  const EXTRA = [
    { name: 'e2e-vendor-4', number: '923009990004' },
    { name: 'e2e-vendor-5', number: '923009990005' },
  ];
  const vendorRow = (name) => {
    const list = gateway(['list-vendors']);
    return list.split('\n').find((line) => new RegExp(`^[0-9a-f-]{36}\\s+${name}\\s`).test(line));
  };
  for (const { name, number } of EXTRA) {
    if (vendorRow(name)) continue;
    const created = gateway([
      'create-vendor', '--name', name, '--number', number, '--email', `${name}@example.com`,
      '--bot-url', 'http://localhost:9000', '--inbound-mode', 'echo',
    ]);
    const key = created.match(/LIVE key:\s+(wab_live_\S+)/);
    if (key) keys[name] = key[1];
  }
  const allNames = ['demo-canned', 'demo-echo', 'demo-mockbot', ...EXTRA.map((v) => v.name)];
  for (const name of allNames.filter((n) => !keys[n])) {
    const row = vendorRow(name);
    if (!row) throw new Error(`Vendor ${name} not found after seeding.`);
    const rotated = gateway(['rotate-key', row.split(/\s+/)[0], '--kind', 'live']);
    const key = rotated.match(/(wab_live_\S+)/);
    if (!key) throw new Error(`Could not rotate the key of ${name}.`);
    keys[name] = key[1];
  }
  const numbers = {
    'demo-canned': '923009990001',
    'demo-echo': '923009990002',
    'demo-mockbot': '923009990003',
    ...Object.fromEntries(EXTRA.map((v) => [v.name, v.number])),
  };
  process.env.E2E_CANNED_KEY = keys['demo-canned'];
  process.env.E2E_ECHO_KEY = keys['demo-echo'];
  process.env.E2E_VENDORS = JSON.stringify(allNames.map((name) => ({ name, number: numbers[name], key: keys[name] })));
  process.env.E2E_DIST = path.join(extensionDir, 'dist', 'dev');
  process.env.E2E_PROJECT_DIR = projectDir;
}
