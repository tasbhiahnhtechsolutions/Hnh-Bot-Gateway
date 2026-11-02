"""The Celery application (plan section 8.3).

Start a worker:  celery -A app.worker.celery_app worker -Q dispatch,maintenance,webhooks -l INFO
Start the beat:  celery -A app.worker.celery_app beat -l INFO
"""

from app.worker.factory import make_celery

celery_app = make_celery()
