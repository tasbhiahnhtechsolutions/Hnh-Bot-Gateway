// Phase 2 end to end: a vendor's backend calls the gateway, the gateway talks to the extension
// over a real WebSocket, the extension sends through a FAKE WhatsApp Web.
//
// What this proves: our wiring (gateway <-> extension <-> page), ordering, at-most-once, reload
// handling, reconnects. What it does NOT prove: that the real WhatsApp Web behaves like the fake
// (unsaved numbers, connection state, ack levels). That is the human Spike on a real account.
import { expect, test } from '@playwright/test';

import {
  closeAll, configureFake, connectVendor, extensionStatus, jobStatus, launchProfile, openWhatsApp,
  pause, sendMessage, sentMessages, sentTexts, vendorNamed, waitForFinal, waitForStatus, waitUntilReady,
} from './support.mjs';

const CUSTOMER = (n) => `92300555${String(n).padStart(4, '0')}`;

test.afterAll(closeAll);

test.describe.serial('one vendor, one profile, a fake WhatsApp', () => {
  let profile;
  let vendor;

  test.beforeAll(async () => {
    vendor = vendorNamed('demo-canned');
    profile = await launchProfile('outbound');
    await connectVendor(profile, vendor);
    await openWhatsApp(profile);
    await waitUntilReady(profile);
  });

  test('the popup shows the live line, WhatsApp and an idle queue', async () => {
    await profile.popup.reload();
    await expect(profile.popup.locator('#lineText')).toHaveText('Connected');
    await expect(profile.popup.locator('#waText')).toHaveText('Connected');
    await expect(profile.popup.locator('#queueText')).toHaveText('Idle');
  });

  test('a message to a number that already has a chat is sent once and reported sent', async () => {
    await configureFake(profile.wa, { contacts: [CUSTOMER(1)], registered: [CUSTOMER(1)], ackLevel: 1 });
    const job = await sendMessage(vendor, { number: CUSTOMER(1), message: 'Aap ka OTP 111111 hai.' });
    expect(job.success).toBe(true);
    await waitForStatus(vendor, job.jobId, 'sent');
    expect(await sentMessages(profile.wa)).toMatchObject([{ chatId: `${CUSTOMER(1)}@c.us`, text: 'Aap ka OTP 111111 hai.' }]);
    expect((await jobStatus(vendor, job.jobId)).sentAt).not.toBeNull();
  });

  test('a number that is not on WhatsApp fails with NOT_ON_WHATSAPP and nothing is sent', async () => {
    const before = (await sentMessages(profile.wa)).length;
    const job = await sendMessage(vendor, { number: CUSTOMER(2), message: 'hello' });
    const final = await waitForFinal(vendor, job.jobId);
    expect(final).toMatchObject({ status: 'failed', errorCode: 'NOT_ON_WHATSAPP' });
    expect((await sentMessages(profile.wa)).length).toBe(before);
  });

  test('UNSAVED number, primary way (create the chat internally): sent', async () => {
    await configureFake(profile.wa, { registered: [CUSTOMER(3)], primaryCreate: true });
    const job = await sendMessage(vendor, { number: CUSTOMER(3), message: 'primary way' });
    await waitForStatus(vendor, job.jobId, 'sent');
    expect(await sentTexts(profile.wa)).toContain('primary way');
  });

  test('UNSAVED number, fallback (/send?phone= reloads the page): sent exactly once', async () => {
    await configureFake(profile.wa, { registered: [CUSTOMER(4)], primaryCreate: false });
    const loadsBefore = await profile.wa.evaluate(() => window.__fake.loads);
    const job = await sendMessage(vendor, { number: CUSTOMER(4), message: 'fallback way' });
    await waitForStatus(vendor, job.jobId, 'sent');
    expect(await profile.wa.evaluate(() => window.__fake.loads)).toBeGreaterThan(loadsBefore); // the page really reloaded
    expect((await sentTexts(profile.wa)).filter((t) => t === 'fallback way')).toHaveLength(1);
  });

  test('a number WhatsApp calls invalid on the fallback page fails with NOT_ON_WHATSAPP', async () => {
    // This build has no "does it exist" function, so only the page itself can tell.
    await configureFake(profile.wa, { existsAvailable: false, registered: [], primaryCreate: false });
    const before = (await sentMessages(profile.wa)).length;
    const job = await sendMessage(vendor, { number: CUSTOMER(5), message: 'to nobody' });
    const final = await waitForFinal(vendor, job.jobId);
    expect(final).toMatchObject({ status: 'failed', errorCode: 'NOT_ON_WHATSAPP' });
    expect((await sentMessages(profile.wa)).length).toBe(before);
    await configureFake(profile.wa, { existsAvailable: true });
  });

  test('a send that WhatsApp rejects fails (OTP is not retried by typing into the page)', async () => {
    await configureFake(profile.wa, { contacts: [CUSTOMER(6)], registered: [CUSTOMER(6)], failSends: true });
    const before = (await sentMessages(profile.wa)).length;
    const job = await sendMessage(vendor, { number: CUSTOMER(6), message: 'will fail' });
    const final = await waitForFinal(vendor, job.jobId);
    expect(final).toMatchObject({ status: 'failed', errorCode: 'SEND_FAILED' });
    expect((await sentMessages(profile.wa)).length).toBe(before);
    await configureFake(profile.wa, { failSends: false });
  });

  test('no ack from WhatsApp: the message is NOT reported sent and NOT sent again', async () => {
    test.setTimeout(240_000);
    await configureFake(profile.wa, { contacts: [CUSTOMER(7)], registered: [CUSTOMER(7)], ackLevel: 0 });
    const job = await sendMessage(vendor, { number: CUSTOMER(7), message: 'no ack please' });
    await expect.poll(async () => (await sentTexts(profile.wa)).filter((t) => t === 'no ack please').length).toBe(1);
    await pause(25_000); // the page waits 15 s for an ack, then gives up without a result
    expect((await jobStatus(vendor, job.jobId)).status).not.toBe('sent');
    expect((await sentTexts(profile.wa)).filter((t) => t === 'no ack please')).toHaveLength(1);
    // The plugin never reports a result, so the server holds this vendor's line until the 120 s
    // lease runs out and marks the job "unknown". Wait for that so the next test starts clean.
    await waitForStatus(vendor, job.jobId, 'unknown', 180_000);
    expect((await sentTexts(profile.wa)).filter((t) => t === 'no ack please')).toHaveLength(1);
    await configureFake(profile.wa, { ackLevel: 1 });
  });

  test('WhatsApp offline: nothing is sent; when it is back, the same job goes out', async () => {
    await configureFake(profile.wa, { contacts: [CUSTOMER(8)], registered: [CUSTOMER(8)], connState: 'TIMEOUT' });
    await expect.poll(async () => (await extensionStatus(profile)).waState, { timeout: 20_000 }).toBe('offline');
    const job = await sendMessage(vendor, { number: CUSTOMER(8), message: 'after the outage' });
    await pause(4000);
    expect(await sentTexts(profile.wa)).not.toContain('after the outage');
    await configureFake(profile.wa, { connState: 'CONNECTED' });
    await waitForStatus(vendor, job.jobId, 'sent', 90_000);
    expect((await sentTexts(profile.wa)).filter((t) => t === 'after the outage')).toHaveLength(1);
  });

  test('messages of one vendor go out one at a time, in the order they were accepted', async () => {
    const numbers = [11, 12, 13].map(CUSTOMER);
    await configureFake(profile.wa, { contacts: numbers, registered: numbers });
    const ids = [];
    for (const [index, number] of numbers.entries()) {
      ids.push((await sendMessage(vendor, { number, message: `batch ${index + 1}`, type: 'transactional' })).jobId);
    }
    for (const id of ids) await waitForStatus(vendor, id, 'sent', 90_000);
    const batch = (await sentMessages(profile.wa)).filter((m) => m.text.startsWith('batch '));
    expect(batch.map((m) => m.text)).toEqual(['batch 1', 'batch 2', 'batch 3']);
    expect(batch[1].at - batch[0].at).toBeGreaterThanOrEqual(900); // the pause between messages
  });

  test('closing the browser tab of WhatsApp: the status shows it and the job waits', async () => {
    await profile.wa.close();
    await expect.poll(async () => (await extensionStatus(profile)).waState, { timeout: 40_000 }).toBe('not_loaded');
    const job = await sendMessage(vendor, { number: CUSTOMER(9), message: 'tab was closed' });
    await pause(3000);
    expect((await jobStatus(vendor, job.jobId)).status).not.toBe('sent');
    // reopening WhatsApp: the job goes out
    await openWhatsApp(profile, { contacts: [CUSTOMER(9)], registered: [CUSTOMER(9)] });
    await waitForStatus(vendor, job.jobId, 'sent', 90_000);
  });
});

test.describe.serial('the connection and the service worker', () => {
  test('a second computer with the same key is refused, the first keeps working', async () => {
    const vendor = vendorNamed('demo-echo');
    const first = await launchProfile('first');
    await connectVendor(first, vendor);
    await openWhatsApp(first);
    await waitUntilReady(first);

    const second = await launchProfile('second');
    await connectVendor(second, vendor);
    await openWhatsApp(second);
    await expect.poll(async () => (await extensionStatus(second)).realtime.status, { timeout: 30_000 }).toBe('busy');
    await second.popup.reload();
    await expect(second.popup.locator('#lineText')).toHaveText('Another computer is using this key');

    // the first one is unaffected
    expect((await extensionStatus(first)).realtime.status).toBe('connected');
    await configureFake(first.wa, { contacts: [CUSTOMER(20)], registered: [CUSTOMER(20)] });
    const job = await sendMessage(vendor, { number: CUSTOMER(20), message: 'first computer only' });
    await waitForStatus(vendor, job.jobId, 'sent');
    expect(await sentTexts(second.wa)).toEqual([]);
  });

  test('the internet drops and comes back: the line reconnects and a waiting job is delivered once', async () => {
    const vendor = vendorNamed('demo-mockbot');
    const profile = await launchProfile('offline');
    await connectVendor(profile, vendor);
    await openWhatsApp(profile, { contacts: [CUSTOMER(30)], registered: [CUSTOMER(30)] });
    await waitUntilReady(profile);

    await profile.context.setOffline(true);
    // UNVERIFIED: whether setOffline reaches the extension's service worker. If it does not,
    // the line stays connected and this test cannot test anything: say so, do not fake it.
    const dropped = await expect
      .poll(async () => (await extensionStatus(profile)).realtime.status, { timeout: 45_000 })
      .not.toBe('connected')
      .then(() => true, () => false);
    test.skip(!dropped, 'context.setOffline does not cut the extension service worker in this Chromium: use the manual checklist (docs/MANUAL_CHECKLIST.md, offline case)');

    const job = await sendMessage(vendor, { number: CUSTOMER(30), message: 'sent while the plugin was offline' });
    await pause(2000);
    expect(await sentTexts(profile.wa)).toEqual([]);
    await profile.context.setOffline(false);
    await waitForStatus(vendor, job.jobId, 'sent', 150_000);
    expect(await sentTexts(profile.wa)).toEqual(['sent while the plugin was offline']);
  });

  test('the service worker is killed: the next alarm wakes it and the line comes back', async () => {
    test.setTimeout(240_000);
    const vendor = vendorNamed('e2e-vendor-4');
    const profile = await launchProfile('swkill');
    await connectVendor(profile, vendor);
    await openWhatsApp(profile, { contacts: [CUSTOMER(40)], registered: [CUSTOMER(40)] });
    await waitUntilReady(profile);

    // Stop every service worker of this browser through DevTools.
    const cdp = await profile.context.newCDPSession(profile.wa);
    await cdp.send('ServiceWorker.enable');
    await cdp.send('ServiceWorker.stopAllWorkers');
    await pause(1000);

    const job = await sendMessage(vendor, { number: CUSTOMER(40), message: 'after the worker died' });
    // The 30 s keep-alive alarm starts the worker again; the gateway re-offers the job.
    await waitForStatus(vendor, job.jobId, 'sent', 180_000);
    expect((await sentTexts(profile.wa)).filter((t) => t === 'after the worker died')).toHaveLength(1);
  });
});

test.describe('five browsers, five vendors, one gateway', () => {
  test('every message arrives at its own vendor\'s WhatsApp and nowhere else', async () => {
    test.setTimeout(240_000);
    const all = JSON.parse(process.env.E2E_VENDORS);
    expect(all).toHaveLength(5);

    // Earlier tests left their browsers open. Three of these vendors are still connected there,
    // and the gateway (rightly) refuses a second computer for the same key.
    await closeAll();

    const profiles = [];
    for (const vendor of all) {
      const profile = await launchProfile(vendor.name);
      await connectVendor(profile, vendor);
      await openWhatsApp(profile, { contacts: [CUSTOMER(50)], registered: [CUSTOMER(50)] });
      profiles.push(profile);
    }
    for (const profile of profiles) await waitUntilReady(profile);

    // Every vendor writes to the SAME customer number with its own text, all at the same moment.
    const jobs = await Promise.all(
      profiles.map(async (p) => ({
        profile: p,
        text: `from ${p.vendor.name}`,
        job: await sendMessage(p.vendor, { number: CUSTOMER(50), message: `from ${p.vendor.name}` }),
      })),
    );
    for (const { profile, job } of jobs) await waitForStatus(profile.vendor, job.jobId, 'sent', 120_000);

    for (const { profile, text } of jobs) {
      expect(await sentTexts(profile.wa), profile.vendor.name).toEqual([text]);
    }
  });
});
