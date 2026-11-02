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
  const missing = ['demo-canned', 'demo-echo'].filter((name) => !keys[name]);
  if (missing.length) {
    const list = gateway(['list-vendors']);
    for (const name of missing) {
      const row = list.split('\n').find((line) => new RegExp(`^[0-9a-f-]{36}\\s+${name}\\s`).test(line));
      if (!row) throw new Error(`Vendor ${name} not found after seeding.`);
      const rotated = gateway(['rotate-key', row.split(/\s+/)[0], '--kind', 'live']);
      const key = rotated.match(/(wab_live_\S+)/);
      if (!key) throw new Error(`Could not rotate the key of ${name}.`);
      keys[name] = key[1];
    }
  }
  process.env.E2E_CANNED_KEY = keys['demo-canned'];
  process.env.E2E_ECHO_KEY = keys['demo-echo'];
  process.env.E2E_DIST = path.join(extensionDir, 'dist', 'dev');
  process.env.E2E_PROJECT_DIR = projectDir;
}
