// Phase 1 smoke test (plan sections 14.9 and 8.9):
//   real Chromium + the built extension + a fake web.whatsapp.com + the real gateway (docker).
// Two browser profiles, each with its own key and its own fake WhatsApp number. Routing is by key,
// so a customer message must only ever be answered through the profile it arrived in.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium, expect, test } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE_PAGE = fs.readFileSync(path.join(here, 'fake-page.html'), 'utf8');
const FAKE_SCRIPT = fs.readFileSync(path.join(here, 'fake-whatsapp.js'), 'utf8');

const CANNED = { business: 'demo-canned', me: '923009990001' };
const ECHO = { business: 'demo-echo', me: '923009990002' };
const BURST_QUIET_MS = 3500; // BURST_WAIT_MS in injected.js
const SILENCE_MS = BURST_QUIET_MS + 3000; // long enough to be sure nothing is coming

const openContexts = [];

async function launchProfile(label) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), `wa-e2e-${label}-`));
  const dist = process.env.E2E_DIST;
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'chromium', // the new headless mode, which (unlike headless-shell) runs extensions
    headless: true,
    args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
  });
  openContexts.push({ context, userDataDir });

  await context.route('https://web.whatsapp.com/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === '/__fake/fake-whatsapp.js') {
      return route.fulfill({ contentType: 'text/javascript', body: FAKE_SCRIPT });
    }
    return route.fulfill({ contentType: 'text/html', body: FAKE_PAGE });
  });

  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const extensionId = new URL(worker.url()).host;
  return { context, extensionId };
}

async function openPopup(profile) {
  const page = await profile.context.newPage();
  await page.goto(`chrome-extension://${profile.extensionId}/popup.html`);
  return page;
}

async function saveKey(profile, key) {
  const popup = await openPopup(profile);
  await popup.fill('#apiKeyInput', key);
  await popup.click('#saveBtn');
  return popup;
}

async function openWhatsApp(profile, me) {
  const page = await profile.context.newPage();
  if (process.env.E2E_DEBUG) {
    page.on('console', (message) => console.log(`[page ${me}]`, message.text().slice(0, 300)));
    page.on('pageerror', (error) => console.log(`[page ${me}] ERROR`, error.message));
  }
  const autoReplyOn = new Promise((resolve) => {
    page.on('console', (message) => {
      if (message.text().includes('Auto-reply is ON')) resolve();
    });
  });
  await page.goto(`https://web.whatsapp.com/?me=${me}`);
  await page.waitForFunction(() => window.__fake?.hooked === true);
  await autoReplyOn;
  return page;
}

const sentTexts = (page) => page.evaluate(() => window.__fake.sent().map((m) => m.text));
const deliver = (page, message) => page.evaluate((m) => window.__fake.deliverIncoming(m), message);
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function requestsPerVendor() {
  const sql =
    "select v.business_name, count(*) from request_logs r join vendors v on v.id = r.vendor_id " +
    "where r.kind = 'ai_reply' group by 1 order by 1";
  const out = execFileSync(
    'docker',
    ['compose', 'exec', '-T', 'postgres', 'psql', '-U', 'gateway', '-d', 'gateway', '-At', '-c', sql],
    { cwd: process.env.E2E_PROJECT_DIR, encoding: 'utf8' },
  );
  return Object.fromEntries(
    out
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        const [name, count] = line.split('|');
        return [name, Number(count)];
      }),
  );
}

test.afterAll(async () => {
  for (const { context, userDataDir } of openContexts) {
    await context.close().catch(() => {});
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
});

test.describe.serial('two profiles, two vendors, one gateway', () => {
  let a;
  let b;
  let waA;
  let waB;
  let popupA;
  let baseline; // requests the gateway already logged before this run (a stack may be reused)

  test('each popup connects with its own key and shows its own business', async () => {
    a = await launchProfile('a');
    b = await launchProfile('b');
    baseline = requestsPerVendor();
    popupA = await saveKey(a, process.env.E2E_CANNED_KEY);
    const popupB = await saveKey(b, process.env.E2E_ECHO_KEY);

    await expect(popupA.locator('#connectedName')).toHaveText(CANNED.business);
    await expect(popupB.locator('#connectedName')).toHaveText(ECHO.business);
    await expect(popupA.locator('#connectedNumber')).toContainText('9990001');
    await expect(popupB.locator('#connectedNumber')).toContainText('9990002');
    await expect(popupA.locator('#backendText')).toContainText('Online');
    await expect(popupA.locator('#numberWarning')).toBeHidden();
    // the key is never shown again, only a hint
    await expect(popupA.locator('#keyHint')).toContainText('Saved key');
    expect(await popupA.locator('body').innerText()).not.toContain(process.env.E2E_CANNED_KEY);
  });

  test('a customer message in profile A is answered by vendor A only', async () => {
    waA = await openWhatsApp(a, CANNED.me);
    waB = await openWhatsApp(b, ECHO.me);

    await deliver(waA, { from: '923001112222', text: 'kya price hai?' });

    await expect.poll(() => sentTexts(waA)).toEqual(['Price 85,000 se shuru hota hai.']);
    const [{ chatId }] = await waA.evaluate(() => window.__fake.sent());
    expect(chatId).toBe('923001112222@c.us'); // answered in the same chat
    await pause(1500);
    expect(await sentTexts(waB)).toEqual([]); // nothing leaked into the other profile
  });

  test('a customer message in profile B is answered by vendor B (echo) only', async () => {
    await deliver(waB, { from: '923004445555', text: 'hello from the customer' });

    await expect
      .poll(() => sentTexts(waB))
      .toEqual(['Aap ne likha: hello from the customer']);
    await pause(1500);
    expect(await sentTexts(waA)).toEqual(['Price 85,000 se shuru hota hai.']);
  });

  test('a burst of quick messages produces one request carrying all of them, and one answer', async () => {
    const before = await sentTexts(waB);
    await deliver(waB, { from: '923007778888', text: 'pehla message' });
    await pause(400);
    await deliver(waB, { from: '923007778888', text: 'doosra message' });

    // The echo vendor repeats what it received, so the answer proves both messages were in
    // the same request (and none was lost).
    await expect
      .poll(() => sentTexts(waB))
      .toEqual([...before, 'Aap ne likha: pehla message / doosra message']);
    await pause(SILENCE_MS);
    expect(await sentTexts(waB)).toEqual([...before, 'Aap ne likha: pehla message / doosra message']);
  });

  test('group messages are never answered', async () => {
    const before = (await sentTexts(waA)).length;
    await deliver(waA, { from: '923001119999', text: 'price in the group?', group: true });
    await pause(SILENCE_MS);
    expect((await sentTexts(waA)).length).toBe(before);
  });

  test('switching auto-reply off stops answers, switching it on resumes them', async () => {
    await popupA.reload();
    await popupA.locator('.slider').click(); // toggle off
    await expect(popupA.locator('#autoReplyToggle')).not.toBeChecked();
    // the control message travels background -> content -> page
    await pause(1000);

    const before = (await sentTexts(waA)).length;
    await deliver(waA, { from: '923002223333', text: 'price while off' });
    await pause(SILENCE_MS);
    expect((await sentTexts(waA)).length).toBe(before);

    await popupA.locator('.slider').click(); // back on
    await expect(popupA.locator('#autoReplyToggle')).toBeChecked();
    await pause(1000);
    await deliver(waA, { from: '923002223333', text: 'price after on' });
    await expect.poll(async () => (await sentTexts(waA)).length).toBe(before + 1);
  });

  test('the gateway saw exactly the requests that were sent, per vendor', async () => {
    // A: the price question and "price after on" = 2. B: the echo question and the burst = 2.
    // The group message and the message sent while auto-reply was off never reached the gateway.
    const now = requestsPerVendor();
    expect({
      'demo-canned': now['demo-canned'] - (baseline['demo-canned'] ?? 0),
      'demo-echo': now['demo-echo'] - (baseline['demo-echo'] ?? 0),
    }).toEqual({ 'demo-canned': 2, 'demo-echo': 2 });
    expect(Object.keys(now).sort()).toEqual(['demo-canned', 'demo-echo']); // no other vendor
  });
});

test('a wrong key is refused in the popup and nothing is ever sent', async () => {
  const profile = await launchProfile('wrong');
  const popup = await saveKey(profile, 'wab_live_abcd1234_notarealsecretnotarealsecret00');
  await expect(popup.locator('#keyMessage')).toBeVisible();
  await expect(popup.locator('#keyMessage')).toHaveClass(/bad/);
  await expect(popup.locator('#connectedSection')).toBeHidden();

  const wa = await profile.context.newPage();
  await wa.goto(`https://web.whatsapp.com/?me=923009990001`);
  await wa.waitForFunction(() => window.__fake?.hooked === true);
  await deliver(wa, { from: '923001112222', text: 'price?' });
  await pause(SILENCE_MS);
  expect(await sentTexts(wa)).toEqual([]);
});
