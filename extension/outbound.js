// Outbound engine: takes a job from the gateway, makes sure it is sent at most once, and reports
// the result so that it is never lost (plan sections 8.7 and 8.8).
//
// No chrome.* and no network in here. Everything it needs is passed in, so the same file runs in
// the service worker (importScripts) and in the Node unit tests.
//
// The three rules that keep an OTP from going out twice:
//  1. Tell the server "started" BEFORE touching WhatsApp, and only send when it said yes.
//  2. Write the result to the outbox (chrome.storage) BEFORE telling the server; remove it only
//     when the server confirmed. After a reconnect the rest is sent again.
//  3. Remember every job for 7 days. A job we already started is never sent again.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WAOutbound = api;
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const MEMORY_MS = 7 * 24 * 60 * 60 * 1000;
  const SERVER_REPLY_TIMEOUT_MS = 10000;
  const TAB_SILENCE_MS = 90000; // the page said nothing for this long: stop waiting, never retry
  const RESUME_WAIT_MS = 60000; // after /send?phone= was opened, how long the reload may take
  const JOB_ID = /^job_[0-9a-f]{16}$/;
  const KEYS = ['outbound_memory', 'outbound_outbox', 'outbound_current'];

  const ERROR_WA_NOT_READY = 'WA_NOT_READY';
  const ERROR_SEND_FAILED = 'SEND_FAILED';
  const KNOWN_ERRORS = new Set([ERROR_WA_NOT_READY, ERROR_SEND_FAILED, 'NOT_ON_WHATSAPP']);

  function validJob(job) {
    return Boolean(
      job &&
        typeof job === 'object' &&
        JOB_ID.test(job.jobId || '') &&
        Number.isInteger(job.attempt) &&
        job.attempt >= 1 &&
        /^\d{8,15}$/.test(String(job.to || '')) &&
        typeof job.message === 'string' &&
        job.message.length > 0 &&
        job.message.length <= 4096,
    );
  }

  function createOutboundEngine(deps) {
    const {
      storage,
      emit, // (event, payload) => Promise<reply>  (rejects when there is no answer)
      getWaState, // () => Promise<string>
      runInTab, // (command) => Promise  (rejects when no WhatsApp tab can take it)
      openSendUrl, // (digits) => Promise  (navigates the WhatsApp tab to /send?phone=)
      now = Date.now,
      log = () => {},
      setTimer = setTimeout,
      clearTimer = clearTimeout,
    } = deps;

    let loaded = null;
    let memory = {};
    let outbox = [];
    let current = null;
    let finishCurrent = null; // resolves the wait of the job that is running
    let silenceTimer = null;
    let queued = 0;
    let chain = Promise.resolve();
    let lastResult = null;

    // -- persistence -------------------------------------------------------------------

    async function persist() {
      await storage.set({ outbound_memory: memory, outbound_outbox: outbox, outbound_current: current });
    }

    function ensureLoaded() {
      if (!loaded) {
        loaded = (async () => {
          const raw = await storage.get(KEYS);
          memory = raw.outbound_memory && typeof raw.outbound_memory === 'object' ? raw.outbound_memory : {};
          outbox = Array.isArray(raw.outbound_outbox) ? raw.outbound_outbox : [];
          current = raw.outbound_current || null;
          const cutoff = now() - MEMORY_MS;
          for (const [jobId, entry] of Object.entries(memory)) {
            if (!entry || entry.at < cutoff) delete memory[jobId];
          }
          if (current) await recoverAfterRestart();
        })();
      }
      return loaded;
    }

    // The service worker was stopped while a job was running. Only a job that is waiting for the
    // page to reload (/send?phone=) may go on; every other stage might already have sent.
    async function recoverAfterRestart() {
      if (current.stage === 'chat_opening' && !current.resumed) {
        armSilence(RESUME_WAIT_MS);
        return;
      }
      await markUnknown('service worker restarted while the job was running');
    }

    // -- helpers -----------------------------------------------------------------------

    function withTimeout(promise, ms) {
      return new Promise((resolve, reject) => {
        const timer = setTimer(() => reject(new Error('timeout')), ms);
        promise.then(
          (value) => {
            clearTimer(timer);
            resolve(value);
          },
          (err) => {
            clearTimer(timer);
            reject(err);
          },
        );
      });
    }

    function armSilence(ms) {
      clearTimer(silenceTimer);
      silenceTimer = setTimer(() => {
        markUnknown('no news from the page').catch(() => {});
      }, ms);
    }

    async function endCurrent() {
      clearTimer(silenceTimer);
      silenceTimer = null;
      current = null;
      await persist();
      if (finishCurrent) {
        const done = finishCurrent;
        finishCurrent = null;
        done();
      }
    }

    // The job may have been sent but we cannot prove it. No result is reported: the gateway
    // marks it "unknown" after its lease and never retries it.
    async function markUnknown(reason) {
      if (!current) return;
      log('outbound_unknown', { jobId: current.jobId, reason });
      const entry = memory[current.jobId];
      if (entry) {
        entry.stage = 'unknown';
        entry.at = now();
      }
      await endCurrent();
    }

    // -- results -----------------------------------------------------------------------

    async function deliverResult(result) {
      let reply;
      try {
        reply = await withTimeout(emit('job_result', result), SERVER_REPLY_TIMEOUT_MS);
      } catch (_) {
        return false; // offline or no answer: it stays in the outbox
      }
      if (reply && (reply.ok === true || reply.error === 'UNKNOWN_JOB' || reply.error === 'INVALID_PAYLOAD')) {
        outbox = outbox.filter((r) => !(r.jobId === result.jobId && r.attempt === result.attempt));
        await persist();
        return true;
      }
      return false;
    }

    async function report(job, fields) {
      const result = { jobId: job.jobId, attempt: job.attempt, status: fields.status };
      if (fields.waAck != null) result.waAck = fields.waAck;
      if (fields.errorCode) result.errorCode = KNOWN_ERRORS.has(fields.errorCode) ? fields.errorCode : ERROR_SEND_FAILED;
      outbox = outbox.filter((r) => !(r.jobId === result.jobId && r.attempt === result.attempt));
      outbox.push(result); // 1. write it down ...
      await persist();
      lastResult = { jobId: result.jobId, status: result.status, errorCode: result.errorCode || null, at: now() };
      await deliverResult(result); // 2. ... then tell the server
    }

    async function flushOutbox() {
      await ensureLoaded();
      for (const result of [...outbox]) {
        if (!(await deliverResult(result))) break; // still offline: try again at the next connect
      }
    }

    // -- one job -----------------------------------------------------------------------

    async function replay(job, entry) {
      if (entry.status === 'sent' || (entry.status === 'failed' && entry.attempt === job.attempt)) {
        // We already know how this ended: tell the server again, never send again.
        await report(job, { status: entry.status, waAck: entry.waAck, errorCode: entry.errorCode });
        return;
      }
      if (entry.status === 'failed' && job.attempt > entry.attempt) {
        // A normal retry of a message that failed (nothing went out): start over.
        delete memory[job.jobId];
        await persist();
        await processJob(job);
        return;
      }
      // Started earlier, no result known: it may have been sent. Say "started" so the gateway
      // stops re-handing it out, and leave it to become "unknown". Never send again.
      log('outbound_duplicate_blocked', { jobId: job.jobId });
      try {
        await withTimeout(emit('job_ack', { jobId: job.jobId, attempt: job.attempt, stage: 'started' }), SERVER_REPLY_TIMEOUT_MS);
      } catch (_) {
        // offline: the gateway will hand it out again later, and we will block it again
      }
    }

    async function processJob(job) {
      if (!validJob(job)) {
        log('outbound_job_invalid', {});
        return;
      }
      const entry = memory[job.jobId];
      if (entry) {
        await replay(job, entry);
        return;
      }
      if (job.expiresAt && Date.parse(job.expiresAt) <= now()) {
        await report(job, { status: 'expired' }); // nothing was sent
        return;
      }
      const waState = await getWaState().catch(() => 'not_loaded');
      if (waState !== 'connected') {
        await report(job, { status: 'failed', errorCode: ERROR_WA_NOT_READY }); // nothing was sent
        return;
      }

      // Rule 1: the gateway must agree before anything is sent.
      let reply;
      try {
        reply = await withTimeout(emit('job_ack', { jobId: job.jobId, attempt: job.attempt, stage: 'started' }), SERVER_REPLY_TIMEOUT_MS);
      } catch (_) {
        return; // no answer: do not send; the gateway takes the job back and hands it out again
      }
      if (!reply || reply.ok !== true || (reply.outcome !== 'applied' && reply.outcome !== 'duplicate')) return;

      memory[job.jobId] = { attempt: job.attempt, stage: 'started', at: now() };
      current = { jobId: job.jobId, attempt: job.attempt, to: job.to, message: job.message, stage: 'started', resumed: false };
      await persist();
      const finished = new Promise((resolve) => {
        finishCurrent = resolve;
      });
      armSilence(TAB_SILENCE_MS);
      try {
        await runInTab({ type: 'SEND', job: { jobId: job.jobId, attempt: job.attempt, to: job.to, message: job.message }, resume: false });
      } catch (_) {
        await finishWith({ status: 'failed', errorCode: ERROR_SEND_FAILED }); // no WhatsApp tab: nothing went out
      }
      await finished;
    }

    async function finishWith(fields) {
      if (!current) return;
      const job = { jobId: current.jobId, attempt: current.attempt };
      const entry = memory[job.jobId] || { attempt: job.attempt };
      entry.attempt = job.attempt;
      entry.status = fields.status;
      entry.waAck = fields.waAck ?? null;
      entry.errorCode = fields.errorCode || null;
      entry.at = now();
      memory[job.jobId] = entry;
      await report(job, fields);
      await endCurrent();
    }

    // -- public --------------------------------------------------------------------------

    function handleJob(job) {
      queued += 1;
      const run = chain.then(async () => {
        try {
          await ensureLoaded();
          await processJob(job);
        } catch (err) {
          log('outbound_job_error', { error: err && err.name });
        } finally {
          queued -= 1;
        }
      });
      chain = run;
      return run;
    }

    // News from the page (injected.js, through content.js and background.js).
    async function onTabEvent(event) {
      await ensureLoaded();
      if (!current || !event || event.jobId !== current.jobId || event.attempt !== current.attempt) return;
      armSilence(TAB_SILENCE_MS);
      if (event.event === 'sending') {
        current.stage = 'sending'; // from here on a reload means "unknown", not "resume"
        if (memory[current.jobId]) memory[current.jobId].stage = 'sending';
        await persist();
      } else if (event.event === 'url_fallback') {
        current.stage = 'chat_opening';
        if (memory[current.jobId]) memory[current.jobId].stage = 'chat_opening';
        await persist();
        armSilence(RESUME_WAIT_MS);
        try {
          await openSendUrl(current.to);
        } catch (_) {
          await finishWith({ status: 'failed', errorCode: ERROR_SEND_FAILED });
        }
      } else if (event.event === 'result') {
        if (event.status === 'sent') await finishWith({ status: 'sent', waAck: Number.isInteger(event.waAck) ? event.waAck : 1 });
        else await finishWith({ status: 'failed', errorCode: event.errorCode });
      } else if (event.event === 'no_ack') {
        await markUnknown('no WhatsApp acknowledgement');
      }
    }

    // A WhatsApp tab finished loading (content.js says so).
    async function onTabReady() {
      await ensureLoaded();
      if (!current) return;
      if (current.stage === 'chat_opening' && !current.resumed) {
        current.resumed = true; // a job may resume once, never twice
        await persist();
        armSilence(TAB_SILENCE_MS);
        try {
          await runInTab({ type: 'SEND', job: { jobId: current.jobId, attempt: current.attempt, to: current.to, message: current.message }, resume: true });
        } catch (_) {
          await finishWith({ status: 'failed', errorCode: ERROR_SEND_FAILED });
        }
      } else if (current.stage === 'started' || current.stage === 'sending' || current.resumed) {
        await markUnknown('the page reloaded while the job was running');
      }
    }

    async function status() {
      await ensureLoaded();
      return {
        queueLength: queued,
        sendingJobId: current ? current.jobId : null,
        outboxLength: outbox.length,
        lastResult,
      };
    }

    // Synchronous view for the heartbeat (what is waiting, what is being sent right now).
    function snapshot() {
      return { queueLength: queued, sendingJobId: current ? current.jobId : null };
    }

    return { handleJob, onTabEvent, onTabReady, flushOutbox, status, snapshot, ensureLoaded };
  }

  return { createOutboundEngine, validJob, MEMORY_MS, SERVER_REPLY_TIMEOUT_MS, TAB_SILENCE_MS, RESUME_WAIT_MS };
});
