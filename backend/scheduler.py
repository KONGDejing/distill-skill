"""
Daily scheduler for script generation.
Uses Celery Beat for periodic tasks.
"""
from celery import Celery
from celery.schedules import crontab
from config import settings

# Parse daily generation time
hour, minute = settings.DAILY_GENERATION_TIME.split(":")
hour, minute = int(hour), int(minute)

beat_schedule = {
    "generate-daily-scripts": {
        "task": "generate_scripts_for_all_active",
        "schedule": crontab(hour=hour, minute=minute),
    },
}


def get_beat_schedule():
    return beat_schedule
