from django.apps import AppConfig
import os
import sys


class AlomindConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'alomind'

    def ready(self):
        import back.firebase  # Ensure Firebase is initialized when the app is ready
        if len(sys.argv) > 1 and sys.argv[0].endswith("manage.py") and sys.argv[1] != "runserver":
            return

        # Django runserver starts a parent + child process; run scheduler only in child.
        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        if os.environ.get("DISABLE_ALARM_SCHEDULER") == "1":
            return

        from .alarm_scheduler import start_alarm_scheduler

        start_alarm_scheduler()
