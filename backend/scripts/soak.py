"""Soak test against a RUNNING stack (``make up``): many vendors, many jobs, test keys only.

Test keys use the virtual plugin, so no WhatsApp message is ever sent. It checks that every job
reaches a final state exactly once and prints how long that took.

    make soak VENDORS=20 JOBS=50
"""

import argparse
import asyncio
import statistics
import time
import uuid

import httpx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import SecretBox
from app.db.session import create_engine, create_session_factory
from app.models.outbound import OutboundJob
from app.schemas.vendor import VendorCreate
from app.services.vendors import create_vendor

FINAL = ("sent", "failed", "expired", "cancelled", "unknown")


async def main(base_url: str, vendors: int, jobs_per_vendor: int, timeout_seconds: float) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    box = SecretBox(settings.secret_encryption_key.get_secret_value())
    keys: list[str] = []
    async with factory() as db:
        for index in range(vendors):
            created = await create_vendor(
                db,
                VendorCreate(
                    business_name=f"Soak {uuid.uuid4().hex[:6]}",
                    whatsapp_number=str(923100000000 + index),
                    email="soak@example.com",
                    bot_base_url="https://example.invalid",
                ),
                box=box,
                allow_private_bot_urls=settings.allow_private_bot_urls,
            )
            keys.append(created.secrets.test_key)
        await db.commit()

    started = time.monotonic()
    ids: list[str] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:

        async def one(key: str, n: int) -> None:
            number = f"0300{1_000_000 + n}"
            response = await client.post(
                "/v1/message-to-customer",
                headers={"X-API-Key": key},
                json={"number": number, "message": "soak", "type": "transactional"},
            )
            response.raise_for_status()
            ids.append(response.json()["jobId"])

        await asyncio.gather(*(one(key, n) for key in keys for n in range(jobs_per_vendor)))
    accepted_in = time.monotonic() - started

    total = len(ids)
    deadline = time.monotonic() + timeout_seconds
    open_jobs = total
    while time.monotonic() < deadline:
        async with factory() as db:
            open_jobs = (
                await db.scalar(
                    select(func.count())
                    .select_from(OutboundJob)
                    .where(OutboundJob.id.in_(ids), OutboundJob.status.notin_(FINAL))
                )
            ) or 0
        if open_jobs == 0:
            break
        await asyncio.sleep(2)
    finished_in = time.monotonic() - started

    async with factory() as db:
        rows = (
            await db.execute(
                select(
                    OutboundJob.status,
                    OutboundJob.attempts,
                    OutboundJob.created_at,
                    OutboundJob.finished_at,
                ).where(OutboundJob.id.in_(ids))
            )
        ).all()
    await engine.dispose()

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
    latencies = [
        (r.finished_at - r.created_at).total_seconds() for r in rows if r.finished_at is not None
    ]
    print(f"jobs accepted: {total} in {accepted_in:.1f}s; all finished after {finished_in:.1f}s")
    print(f"statuses: {by_status}")
    if latencies:
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        print(f"latency  median {statistics.median(latencies):.1f}s  p95 {p95:.1f}s")
    problems = []
    if open_jobs:
        problems.append(f"{open_jobs} jobs still open after {timeout_seconds:.0f}s")
    if any(r.attempts > 1 for r in rows if r.status == "sent"):
        problems.append("some jobs needed a second attempt")
    if len(rows) != total:
        problems.append("jobs are missing from the database")
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--vendors", type=int, default=10)
    parser.add_argument("--jobs", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.base_url, args.vendors, args.jobs, args.timeout)))
