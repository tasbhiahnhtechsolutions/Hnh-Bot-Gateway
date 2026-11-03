// The outbound engine: at-most-once sending and results that are never lost (plan 8.7, 8.8).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { createOutboundEngine, TAB_SILENCE_MS, RESUME_WAIT_MS, MEMORY_MS } = require('../outbound.js');

const JOB = { jobId: 'job_0123456789abcdef', attempt: 1, to: '923001234567', message: 'Aap ka OTP 482913 hai.', type: 'otp' };
const settle = () => new Promise((resolve) => setImmediate(resolve));
async function settleAll() {
  for (let i = 0; i < 8; i += 1) await settle();
}

function harness({ storage = {}, waState = 'connected' } = {}) {
  const store = storage;
  const clock = { now: Date.UTC(2026, 10, 2, 10, 0, 0) };
  const timers = [];
  const calls = { emits: [], tab: [], urls: [] };
  const behaviour = {
    waState,
    ackReply: { ok: true, outcome: 'applied' },
    resultReply: { ok: true, outcome: 'applied' },
    offline: false,
    noTab: false,
  };

  const make = () =>
    createOutboundEngine({
      storage: {
        get: async (keys) => Object.fromEntries([].concat(keys).filter((k) => k in store).map((k) => [k, JSON.parse(JSON.stringify(store[k]))])),
        set: async (items) => {
          for (const [k, v] of Object.entries(items)) store[k] = JSON.parse(JSON.stringify(v));
        },
      },
      emit: async (event, payload) => {
        calls.emits.push({ event, payload: JSON.parse(JSON.stringify(payload)) });
        if (behaviour.offline) throw new Error('not connected');
        return event === 'job_ack' ? behaviour.ackReply : behaviour.resultReply;
      },
      getWaState: async () => behaviour.waState,
      runInTab: async (command) => {
        if (behaviour.noTab) throw new Error('no tab');
        calls.tab.push(JSON.parse(JSON.stringify(command)));
      },
      openSendUrl: async (digits) => {
        calls.urls.push(digits);
      },
      now: () => clock.now,
      setTimer: (fn, ms) => {
        const timer = { fn, ms, cleared: false };
        timers.push(timer);
        return timer;
      },
      clearTimer: (timer) => {
        if (timer) timer.cleared = true;
      },
    });

  const fire = async (ms) => {
    const timer = timers.filter((t) => !t.cleared && t.ms === ms).pop();
    assert.ok(timer, `no pending timer of ${ms} ms`);
    timer.cleared = true;
    timer.fn();
    await settleAll();
  };

  return { engine: make(), make, store, calls, behaviour, clock, fire };
}

const emitted = (h, event) => h.calls.emits.filter((e) => e.event === event).map((e) => e.payload);

async function runToSent(h, job = JOB, waAck = 1) {
  const done = h.engine.handleJob(job);
  await settleAll();
  await h.engine.onTabEvent({ jobId: job.jobId, attempt: job.attempt, event: 'sending' });
  await h.engine.onTabEvent({ jobId: job.jobId, attempt: job.attempt, event: 'result', status: 'sent', waAck });
  await done;
}

test('a job is acknowledged as started first, then sent, then the result is reported', async () => {
  const h = harness();
  await runToSent(h);
  assert.deepEqual(h.calls.emits.map((e) => e.event), ['job_ack', 'job_result']);
  assert.deepEqual(emitted(h, 'job_ack')[0], { jobId: JOB.jobId, attempt: 1, stage: 'started' });
  assert.deepEqual(emitted(h, 'job_result')[0], { jobId: JOB.jobId, attempt: 1, status: 'sent', waAck: 1 });
  assert.equal(h.calls.tab.length, 1);
  assert.deepEqual(h.calls.tab[0], { type: 'SEND', job: { jobId: JOB.jobId, attempt: 1, to: JOB.to, message: JOB.message }, resume: false });
  assert.deepEqual(h.store.outbound_outbox, [], 'the server confirmed: nothing is left to resend');
  assert.equal(h.store.outbound_memory[JOB.jobId].status, 'sent');
});

test('nothing is sent when the gateway does not confirm "started"', async () => {
  for (const reply of [{ ok: false, error: 'UNKNOWN_JOB' }, { ok: true, outcome: 'ignored' }, undefined]) {
    const h = harness();
    h.behaviour.ackReply = reply;
    await h.engine.handleJob(JOB);
    assert.equal(h.calls.tab.length, 0, `sent although the answer was ${JSON.stringify(reply)}`);
    assert.equal(h.store.outbound_memory?.[JOB.jobId], undefined);
  }
});

test('nothing is sent when the line is down while acknowledging', async () => {
  const h = harness();
  h.behaviour.offline = true;
  await h.engine.handleJob(JOB);
  assert.equal(h.calls.tab.length, 0);
});

test('WhatsApp not connected: failed WA_NOT_READY, no started, nothing sent, not remembered', async () => {
  const h = harness({ waState: 'logged_out' });
  await h.engine.handleJob(JOB);
  assert.deepEqual(h.calls.emits.map((e) => e.event), ['job_result']);
  assert.deepEqual(emitted(h, 'job_result')[0], { jobId: JOB.jobId, attempt: 1, status: 'failed', errorCode: 'WA_NOT_READY' });
  assert.equal(h.calls.tab.length, 0);
  assert.equal(h.store.outbound_memory[JOB.jobId], undefined, 'a later attempt may send');
});

test('a job that is already past its expiry is reported expired and not sent', async () => {
  const h = harness();
  await h.engine.handleJob({ ...JOB, expiresAt: new Date(h.clock.now - 1000).toISOString() });
  assert.deepEqual(emitted(h, 'job_result')[0], { jobId: JOB.jobId, attempt: 1, status: 'expired' });
  assert.equal(h.calls.tab.length, 0);
  assert.equal(emitted(h, 'job_ack').length, 0);
});

test('malformed jobs are ignored', async () => {
  const h = harness();
  for (const bad of [null, {}, { ...JOB, jobId: '../x' }, { ...JOB, to: 'abc' }, { ...JOB, message: '' }, { ...JOB, attempt: 0 }]) {
    await h.engine.handleJob(bad);
  }
  assert.equal(h.calls.emits.length, 0);
  assert.equal(h.calls.tab.length, 0);
});

test('the same job handed out again after it was sent is never sent twice', async () => {
  const h = harness();
  await runToSent(h);
  h.calls.emits.length = 0;
  await h.engine.handleJob({ ...JOB, attempt: 2 });
  assert.equal(h.calls.tab.length, 1, 'still only the first send');
  assert.deepEqual(h.calls.emits.map((e) => e.event), ['job_result']);
  assert.equal(emitted(h, 'job_result')[0].status, 'sent', 'it only repeats the old truth');
});

test('a job that was started but has no known result is never sent again', async () => {
  const h = harness();
  const first = h.engine.handleJob(JOB);
  await settleAll();
  await h.fire(TAB_SILENCE_MS); // the page never said anything
  await first;
  assert.equal(h.store.outbound_memory[JOB.jobId].stage, 'unknown');

  h.calls.emits.length = 0;
  await h.engine.handleJob({ ...JOB, attempt: 2 }); // the gateway handed it out again
  assert.equal(h.calls.tab.length, 1, 'no second send');
  assert.deepEqual(h.calls.emits.map((e) => e.event), ['job_ack'], 'it says "started" so the gateway stops, and no result');
});

test('a result that cannot be delivered waits in the outbox and goes out after reconnecting', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  h.behaviour.offline = true; // the internet drops right after the message was sent
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 });
  await done;
  assert.equal(h.store.outbound_outbox.length, 1);
  assert.deepEqual(h.store.outbound_outbox[0], { jobId: JOB.jobId, attempt: 1, status: 'sent', waAck: 1 });

  // the service worker is restarted: a new engine reads the same storage
  const restarted = h.make();
  h.behaviour.offline = false;
  h.calls.emits.length = 0;
  await restarted.flushOutbox();
  assert.deepEqual(emitted(h, 'job_result'), [{ jobId: JOB.jobId, attempt: 1, status: 'sent', waAck: 1 }]);
  assert.deepEqual(h.store.outbound_outbox, []);
});

test('a result the server did not confirm stays in the outbox', async () => {
  const h = harness();
  h.behaviour.resultReply = { ok: false, error: 'NOT_CONNECTED' };
  await runToSent(h);
  assert.equal(h.store.outbound_outbox.length, 1);
  h.behaviour.resultReply = { ok: true, outcome: 'duplicate' };
  await h.engine.flushOutbox();
  assert.deepEqual(h.store.outbound_outbox, []);
});

test('a failed send is reported, and a later attempt of the same job may try again', async () => {
  const h = harness();
  const first = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'failed', errorCode: 'SEND_FAILED' });
  await first;
  assert.deepEqual(emitted(h, 'job_result')[0], { jobId: JOB.jobId, attempt: 1, status: 'failed', errorCode: 'SEND_FAILED' });

  const second = h.engine.handleJob({ ...JOB, attempt: 2, type: 'transactional' });
  await settleAll();
  assert.equal(h.calls.tab.length, 2, 'nothing was sent the first time, so the retry is allowed');
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 2, event: 'result', status: 'sent', waAck: 1 });
  await second;
});

test('NOT_ON_WHATSAPP is final: the same attempt is only re-reported', async () => {
  const h = harness();
  const first = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'failed', errorCode: 'NOT_ON_WHATSAPP' });
  await first;
  h.calls.emits.length = 0;
  await h.engine.handleJob(JOB);
  assert.equal(h.calls.tab.length, 1);
  assert.equal(emitted(h, 'job_result')[0].errorCode, 'NOT_ON_WHATSAPP');
});

test('unknown error codes are reported as SEND_FAILED', async () => {
  const h = harness();
  const first = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'failed', errorCode: 'WEIRD <script>' });
  await first;
  assert.equal(emitted(h, 'job_result')[0].errorCode, 'SEND_FAILED');
});

test('no WhatsApp tab: the job fails with SEND_FAILED and nothing went out', async () => {
  const h = harness();
  h.behaviour.noTab = true;
  await h.engine.handleJob(JOB);
  assert.deepEqual(emitted(h, 'job_result')[0], { jobId: JOB.jobId, attempt: 1, status: 'failed', errorCode: 'SEND_FAILED' });
});

test('events for another job or another attempt are ignored', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: 'job_ffffffffffffffff', attempt: 1, event: 'result', status: 'sent' });
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 2, event: 'result', status: 'sent' });
  assert.equal(emitted(h, 'job_result').length, 0);
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 2 });
  await done;
  assert.equal(emitted(h, 'job_result')[0].waAck, 2);
});

test('no WhatsApp acknowledgement: no result is reported (it becomes unknown), and no retry', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'sending' });
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'no_ack' });
  await done;
  assert.equal(emitted(h, 'job_result').length, 0);
  assert.equal(h.store.outbound_memory[JOB.jobId].stage, 'unknown');
});

test('jobs are sent one at a time', async () => {
  const h = harness();
  const other = { ...JOB, jobId: 'job_aaaaaaaaaaaaaaaa', to: '923009998888' };
  const a = h.engine.handleJob(JOB);
  const b = h.engine.handleJob(other);
  await settleAll();
  assert.equal(h.calls.tab.length, 1, 'the second waits for the first');
  assert.deepEqual(h.engine.snapshot(), { queueLength: 2, sendingJobId: JOB.jobId });
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 });
  await a;
  await settleAll();
  assert.equal(h.calls.tab.length, 2);
  await h.engine.onTabEvent({ jobId: other.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 });
  await b;
  assert.deepEqual(h.engine.snapshot(), { queueLength: 0, sendingJobId: null });
});

// --- the /send?phone= fallback and reloads ---------------------------------------------

test('url fallback: the page is opened once, the job resumes once, and a second reload means unknown', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'url_fallback' });
  assert.deepEqual(h.calls.urls, [JOB.to]);
  assert.equal(h.store.outbound_current.stage, 'chat_opening');

  await h.engine.onTabReady(); // the page reloaded
  assert.equal(h.calls.tab.length, 2);
  assert.equal(h.calls.tab[1].resume, true);

  await h.engine.onTabReady(); // reloaded AGAIN: it might have sent in between
  await done;
  assert.equal(h.calls.tab.length, 2, 'never resumed twice');
  assert.equal(emitted(h, 'job_result').length, 0);
  assert.equal(h.store.outbound_memory[JOB.jobId].stage, 'unknown');
});

test('url fallback then success: the resumed send reports sent', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'url_fallback' });
  await h.engine.onTabReady();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'sending' });
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 });
  await done;
  assert.equal(emitted(h, 'job_result')[0].status, 'sent');
});

test('a reload while sending (not while opening the chat) is unknown, never resumed', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'sending' });
  await h.engine.onTabReady();
  await done;
  assert.equal(h.calls.tab.length, 1);
  assert.equal(emitted(h, 'job_result').length, 0);
});

test('the page never reloads after the url fallback: unknown after the wait, no result, no resend', async () => {
  const h = harness();
  const done = h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'url_fallback' });
  await h.fire(RESUME_WAIT_MS);
  await done;
  assert.equal(h.calls.tab.length, 1);
  assert.equal(h.store.outbound_memory[JOB.jobId].stage, 'unknown');
});

test('service worker restart while sending: unknown, nothing sent again', async () => {
  const h = harness();
  h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'sending' });
  const restarted = h.make(); // the old worker is gone; storage survives
  await restarted.ensureLoaded();
  assert.equal(h.store.outbound_current, null);
  assert.equal(h.store.outbound_memory[JOB.jobId].stage, 'unknown');
  h.calls.emits.length = 0;
  await restarted.handleJob({ ...JOB, attempt: 2 });
  assert.equal(h.calls.tab.length, 1);
});

test('service worker restart while the page is opening the chat: still resumes once', async () => {
  const h = harness();
  h.engine.handleJob(JOB);
  await settleAll();
  await h.engine.onTabEvent({ jobId: JOB.jobId, attempt: 1, event: 'url_fallback' });
  const restarted = h.make();
  await restarted.ensureLoaded();
  await restarted.onTabReady();
  assert.equal(h.calls.tab.length, 2);
  assert.equal(h.calls.tab[1].resume, true);
});

test('jobs are remembered for seven days and then forgotten', async () => {
  const h = harness();
  await runToSent(h);
  h.clock.now += MEMORY_MS - 1000;
  assert.ok((await h.make().status()).outboxLength === 0);
  assert.ok(h.store.outbound_memory[JOB.jobId]);
  h.clock.now += 2000;
  const fresh = h.make();
  await fresh.ensureLoaded();
  fresh.handleJob({ ...JOB, attempt: 3 }); // forgotten: treated as a brand new job
  await settleAll();
  assert.equal(h.calls.tab.length, 2);
});

test('the message text is never kept in the memory of past jobs', async () => {
  const h = harness();
  await runToSent(h);
  assert.ok(!JSON.stringify(h.store.outbound_memory).includes('482913'));
  assert.ok(!JSON.stringify(h.store.outbound_outbox).includes('482913'));
});
