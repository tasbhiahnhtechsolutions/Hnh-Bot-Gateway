// Helpers shared by the outbound specs: browser profiles with the built extension, the fake
// WhatsApp, and a tiny client for the vendor API (what a vendor's backend would call).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium, expect } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE_PAGE = fs.readFileSync(path.join(here, 'fake-page.html'), 'utf8');
const FAKE_SCRIPT = fs.readFileSync(path.join(here, 'fake-whatsapp.js'), 'utf8');

export const GATEWAY = 'http://localhost:8000';
export const vendors = () => JSON.parse(process.env.E2E_VENDORS);
export const vendorNamed = (name) => vendors().find((v) => v.name === name);

const opened = [];

export const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function closeAll() {
  for (const { context, userDataDir } of opened.splice(0)) {
    await context.close().catch(() => {});
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
}

export async function launchProfile(label) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), `wa-e2e-${label}-`));
  const dist = process.env.E2E_DIST;
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'chromium', // the new headless mode, which (unlike headless-shell) runs extensions
    headless: true,
    args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
  });
  opened.push({ context, userDataDir });

  await context.route('https://web.whatsapp.com/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === '/__fake/fake-whatsapp.js') {
      return route.fulfill({ contentType: 'text/javascript', body: FAKE_SCRIPT });
    }
    return route.fulfill({ contentType: 'text/html', body: FAKE_PAGE });
  });

  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const extensionId = new URL(worker.url()).host;
  return { context, extensionId, label };
}

export async function openPopup(profile) {
  const page = await profile.context.newPage();
  await page.goto(`chrome-extension://${profile.extensionId}/popup.html`);
  return page;
}

/** Saves the key through the popup and keeps the popup page as `profile.popup` (it is also a handle to ask the background things). */
export async function connectVendor(profile, vendor) {
  const popup = await openPopup(profile);
  await popup.fill('#apiKeyInput', vendor.key);
  await popup.click('#saveBtn');
  await expect(popup.locator('#connectedName')).toHaveText(vendor.name);
  profile.popup = popup;
  profile.vendor = vendor;
  return popup;
}

export async function openWhatsApp(profile, config = {}) {
  const page = await profile.context.newPage();
  if (process.env.E2E_DEBUG) {
    page.on('console', (message) => console.log(`[${profile.label}]`, message.text().slice(0, 300)));
    page.on('pageerror', (error) => console.log(`[${profile.label}] ERROR`, error.message));
  }
  await page.goto(`https://web.whatsapp.com/?me=${profile.vendor.number}`);
  await page.waitForFunction(() => window.__fake?.hooked === true);
  if (Object.keys(config).length) await configureFake(page, config);
  profile.wa = page;
  return page;
}

export const configureFake = (page, patch) => page.evaluate((p) => window.__fake.configure(p), patch);
export const sentMessages = (page) => page.evaluate(() => window.__fake.sent());
export const sentTexts = async (page) => (await sentMessages(page)).map((m) => m.text);

/** What the extension itself says (the same thing the popup shows). */
export const extensionStatus = (profile) =>
  profile.popup.evaluate(() => chrome.runtime.sendMessage({ type: 'GET_STATUS' }));

export async function waitUntilReady(profile) {
  await expect
    .poll(async () => {
      const s = await extensionStatus(profile);
      return `${s.realtime.status}/${s.waState}`;
    }, { timeout: 40_000 })
    .toBe('connected/connected');
}

// --- the vendor's side: what their own backend would call -----------------------------

export async function sendMessage(vendor, { number, message, type = 'otp', idempotencyKey } = {}) {
  const response = await fetch(`${GATEWAY}/v1/message-to-customer`, {
    method: 'POST',
    headers: {
      'X-API-Key': vendor.key,
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey ?? `e2e-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    },
    body: JSON.stringify({ number, message, type }),
  });
  const body = await response.json();
  expect([200, 202], JSON.stringify(body)).toContain(response.status);
  return body;
}

export async function jobStatus(vendor, jobId) {
  const response = await fetch(`${GATEWAY}/v1/message-to-customer/${jobId}`, { headers: { 'X-API-Key': vendor.key } });
  return response.json();
}

export const waitForStatus = (vendor, jobId, wanted, timeout = 60_000) =>
  expect
    .poll(async () => (await jobStatus(vendor, jobId)).status, { timeout, intervals: [500, 1000, 2000] })
    .toBe(wanted);

export const waitForFinal = async (vendor, jobId, timeout = 60_000) => {
  let last;
  await expect
    .poll(async () => {
      last = await jobStatus(vendor, jobId);
      return ['queued', 'sending'].includes(last.status) ? 'pending' : 'final';
    }, { timeout, intervals: [500, 1000, 2000] })
    .toBe('final');
  return last;
};
