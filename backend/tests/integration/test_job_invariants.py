"""Random event sequences against the real database (plan 14.4: state machine + OTP safety).

Whatever order things happen in (duplicates, stale attempts, lost acks, sweeper races, results
for jobs that were never sent), these must hold:
  1. a job only moves along ``ALLOWED_TRANSITIONS``;
  2. ``sent``, ``failed``, ``expired`` and ``cancelled`` never change again;
  3. a job whose "started" was accepted is never handed to the plugin again (no second OTP);
  4. at most one real job per vendor is in flight.
"""

import random

import pytest
from sqlalchemy import select

from app.models.outbound import OutboundJob
from app.services import sweepers
from app.services.job_results import Outcome
from app.services.job_rules import ALLOWED_TRANSITIONS, can_transition
from tests.integration.conftest import Api, MakeApi

FINAL = {"sent", "failed", "expired", "cancelled"}
IN_FLIGHT = {"dispatched", "sending"}


async def snapshot(api: Api) -> dict[str, tuple[str, int]]:
    async with api.app.state.session_factory() as db:
        rows = await db.execute(select(OutboundJob.id, OutboundJob.status, OutboundJob.attempts))
        return {r.id: (r.status, r.attempts) for r in rows.all()}


@pytest.mark.parametrize("seed", range(30))
async def test_random_sequences_never_break_the_rules(make_api: MakeApi, seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 (test data, not security)
    api = await make_api()
    ob = api.ob
    created = await api.vendor()
    await ob.online(created.vendor)
    key = created.secrets.live_key

    jobs: list[str] = []
    started_at_attempt: dict[str, int] = {}  # job -> attempt whose "started" was accepted
    before: dict[str, tuple[str, int]] = {}
    emitted_seen = 0

    for step in range(40):
        action = rng.choice(
            ["post", "post", "dispatch", "dispatch", "started", "result", "result", "time", "dup"]
        )
        if action == "post" and len(jobs) < 8:
            kind = rng.choice(["otp", "transactional", "marketing"])
            response = await ob.post(
                key,
                type=kind,
                idem=f"k{seed}-{step}" if kind == "otp" else None,
                ttl=rng.choice([None, 30, 120]),
                number=f"0300{rng.randrange(1_000_000, 9_999_999)}",
            )
            if response.status_code in (200, 202):
                jobs.append(response.json()["jobId"])
        elif action == "dispatch":
            await ob.dispatch(created.vendor)
        elif action == "time":
            ob.clock.advance(rng.choice([1, 5, 31, 125, 400]))
            async with api.app.state.session_factory() as db:
                await sweepers.sweep_leases(db, ob.rt, now=ob.clock.dt)
                await sweepers.expire_jobs(db, ob.rt, now=ob.clock.dt)
        elif jobs:
            job_id = rng.choice(jobs)
            attempt = rng.choice([0, 1, 1, 2, 3])
            if action == "started":
                applied = await ob.started(created.vendor, job_id, attempt)
                if applied.outcome is Outcome.APPLIED:
                    started_at_attempt[job_id] = attempt
            else:
                status = rng.choice(["sent", "failed", "failed", "expired"])
                code = rng.choice([None, "SEND_FAILED", "WA_NOT_READY", "NOT_ON_WHATSAPP", "X"])
                await ob.result(
                    created.vendor,
                    job_id,
                    attempt,
                    status,
                    wa_ack=1 if status == "sent" else None,
                    error_code=code if status == "failed" else None,
                )
                if rng.random() < 0.3:  # the same news again
                    await ob.result(
                        created.vendor, job_id, attempt, status, error_code=code or None
                    )

        after = await snapshot(api)
        for job_id, (status, _attempts) in after.items():
            old = before.get(job_id)
            if old is None or old[0] == status:
                continue
            assert can_transition(old[0], status) or (old[0], status) == ("queued", "dispatched"), (
                f"seed {seed} step {step}: {job_id} went {old[0]} -> {status}"
            )
        for job_id, (status, _a) in before.items():
            if status in FINAL:
                assert after[job_id][0] == status, f"seed {seed}: final job {job_id} changed"
        in_flight = [j for j, (s, _a) in after.items() if s in IN_FLIGHT]
        assert len(in_flight) <= 1, f"seed {seed}: {len(in_flight)} jobs in flight"
        before = after

        sent = ob.emitter.jobs_for(created.vendor.id)
        for payload in sent[emitted_seen:]:
            accepted = started_at_attempt.get(payload["jobId"])
            assert accepted is None or payload["attempt"] <= accepted, (
                f"seed {seed}: job {payload['jobId']} was handed out again after it had started"
            )
        emitted_seen = len(sent)

    assert all(status in ALLOWED_TRANSITIONS for status, _a in before.values())
