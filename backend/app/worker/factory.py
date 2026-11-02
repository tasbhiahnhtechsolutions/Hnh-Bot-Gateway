"""Builds the Celery application (kept apart from ``celery_app`` so importing it is free)."""

from celery import Celery

from app.core.config import Settings, get_settings
from app.services.scheduling import QUEUE_DISPATCH, QUEUE_MAINTENANCE, QUEUE_WEBHOOKS


def make_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("gateway", broker=settings.redis_url, include=["app.worker.tasks"])
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,  # the database status is enough (plan section 8.3)
        task_default_queue=QUEUE_DISPATCH,
        task_routes={
            "gateway.dispatch_next": {"queue": QUEUE_DISPATCH},
            "gateway.check_lease": {"queue": QUEUE_DISPATCH},
            "gateway.run_virtual": {"queue": QUEUE_DISPATCH},
            "gateway.deliver_webhook": {"queue": QUEUE_WEBHOOKS},
            "gateway.sweep_leases": {"queue": QUEUE_MAINTENANCE},
            "gateway.expire_jobs": {"queue": QUEUE_MAINTENANCE},
            "gateway.reconcile_idle_vendors": {"queue": QUEUE_MAINTENANCE},
            "gateway.purge_bodies": {"queue": QUEUE_MAINTENANCE},
            "gateway.due_webhooks": {"queue": QUEUE_MAINTENANCE},
        },
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        beat_schedule={
            "sweep-leases": {"task": "gateway.sweep_leases", "schedule": 30.0},
            "expire-jobs": {"task": "gateway.expire_jobs", "schedule": 30.0},
            "reconcile-idle-vendors": {"task": "gateway.reconcile_idle_vendors", "schedule": 30.0},
            "due-webhooks": {"task": "gateway.due_webhooks", "schedule": 60.0},
            "purge-bodies": {"task": "gateway.purge_bodies", "schedule": 3600.0},
        },
    )
    return app
