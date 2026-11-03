#!/usr/bin/env node
// Builds the unpacked extension for one environment into dist/<env>/.
//
//   node build.mjs --env dev                       -> http://localhost:8000
//   node build.mjs --env staging --url https://staging-api.example.com
//   node build.mjs --env prod --url https://api.example.com
//
// staging/prod take the gateway origin from --url or from the environment
// variable GATEWAY_STAGING_URL / GATEWAY_PROD_URL. It must be https and a public
// host. The generated manifest only allows WhatsApp Web and that one origin, so
// a prod build has no trace of localhost. Node is used for this script only.

import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SOURCE_DIR = dirname(fileURLToPath(import.meta.url));
const ENVS = ['dev', 'staging', 'prod'];
const DEV_DEFAULT_URL = 'http://localhost:8000';
const URL_ENV_VARS = { staging: 'GATEWAY_STAGING_URL', prod: 'GATEWAY_PROD_URL' };
const COPIED_FILES = [
  'background.js',
  'content.js',
  'injected.js',
  'gateway-client.js',
  'outbound.js',
  'realtime.js',
  'popup.html',
  'popup.js',
  'vendor/socket.io.min.js',
  'vendor/socket.io.LICENSE',
];
const LOCAL_MARKERS = /localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]/i;

export class BuildError extends Error {}

function isPrivateHost(hostname) {
  const host = hostname.toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal')) return true;
  if (host.startsWith('[')) return true; // IPv6 literals are not accepted for staging/prod
  const parts = host.split('.').map(Number);
  if (parts.length === 4 && parts.every((n) => Number.isInteger(n) && n >= 0 && n <= 255)) {
    const [a, b] = parts;
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }
  return !host.includes('.');
}

export function resolveGatewayOrigin(env, rawUrl) {
  if (!ENVS.includes(env)) throw new BuildError(`--env must be one of ${ENVS.join(', ')}`);
  const value = rawUrl || (env === 'dev' ? DEV_DEFAULT_URL : '');
  if (!value) {
    throw new BuildError(`${env} build needs the gateway URL: --url https://... or ${URL_ENV_VARS[env]}`);
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new BuildError(`not a valid URL: ${value}`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new BuildError('the gateway URL must start with http:// or https://');
  if ((parsed.pathname !== '/' && parsed.pathname !== '') || parsed.search || parsed.hash || parsed.username) {
    throw new BuildError('give the origin only (scheme, host, port), without path, query or credentials');
  }
  if (env !== 'dev') {
    if (parsed.protocol !== 'https:') throw new BuildError(`${env} builds require https`);
    if (isPrivateHost(parsed.hostname)) throw new BuildError(`${env} builds must point to a public host, not ${parsed.hostname}`);
  }
  return parsed.origin;
}

export function buildManifest(base, env, origin) {
  const manifest = structuredClone(base);
  manifest.host_permissions = [...new Set([...base.host_permissions, `${origin}/*`])];
  if (env !== 'prod') {
    manifest.name = `${base.name} (${env})`;
    manifest.version_name = `${base.version}-${env}`;
  }
  return manifest;
}

function listFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? listFiles(join(dir, entry.name)) : [join(dir, entry.name)],
  );
}

export function build({ env, url, outRoot = join(SOURCE_DIR, 'dist'), sourceDir = SOURCE_DIR, envVars = process.env }) {
  const origin = resolveGatewayOrigin(env, url || envVars[URL_ENV_VARS[env]]);
  const outDir = resolve(outRoot, env);
  const base = JSON.parse(readFileSync(join(sourceDir, 'manifest.base.json'), 'utf8'));

  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });
  for (const file of COPIED_FILES) {
    mkdirSync(dirname(join(outDir, file)), { recursive: true });
    cpSync(join(sourceDir, file), join(outDir, file));
  }

  const config = `self.WA_GATEWAY_CONFIG = ${JSON.stringify({ env, GATEWAY_BASE_URL: origin }, null, 2)};\n`;
  writeFileSync(join(outDir, 'config.js'), config);
  writeFileSync(join(outDir, 'manifest.json'), `${JSON.stringify(buildManifest(base, env, origin), null, 2)}\n`);

  if (env !== 'dev') {
    // vendor/ is third-party code (it mentions localhost in its own URL parsing): not ours to police.
    const ours = listFiles(outDir).filter((file) => !file.startsWith(join(outDir, 'vendor')));
    const offenders = ours.filter((file) => LOCAL_MARKERS.test(readFileSync(file, 'utf8')));
    if (offenders.length > 0) {
      rmSync(outDir, { recursive: true, force: true });
      throw new BuildError(`${env} build must not mention localhost, found in: ${offenders.join(', ')}`);
    }
  }
  return { env, origin, outDir };
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--env' || flag === '--url') {
      const value = argv[i + 1];
      if (!value || value.startsWith('--')) throw new BuildError(`${flag} needs a value`);
      args[flag.slice(2)] = value;
      i += 1;
    } else {
      throw new BuildError(`unknown argument: ${flag}`);
    }
  }
  if (!args.env) throw new BuildError('usage: node build.mjs --env dev|staging|prod [--url https://gateway-origin]');
  return args;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { env, origin, outDir } = build(parseArgs(process.argv.slice(2)));
    console.log(`Built ${env} extension for ${origin}\n  -> ${outDir}`);
    if (!existsSync(join(outDir, 'manifest.json'))) throw new BuildError('manifest.json was not written');
  } catch (err) {
    if (err instanceof BuildError) {
      console.error(`build error: ${err.message}`);
      process.exit(1);
    }
    throw err;
  }
}
