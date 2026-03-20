import logging
import os
import threading
import time
import json
from datetime import date, datetime, timedelta

from django.db import close_old_connections
from django.utils import timezone

from .models import FCM, Patient, ScheduledMed, Tasks, TimedMed

try:
    from firebase_admin import messaging
except Exception:
    messaging = None

logger = logging.getLogger(__name__)

_poll_interval_seconds = int(os.getenv("ALARM_POLL_INTERVAL_SECONDS", "1"))
_started = False
_start_lock = threading.Lock()
_triggered_today = {"alarm": {}, "medication": {}, "task": {}}

MEDICATION_SLOTS = {
    "morning": {
        "patient_field": "morning_med",
        "scheduled_flag": "isMorning",
        "label": "Morning medicine time",
    },
    "afternoon": {
        "patient_field": "afternoon_med",
        "scheduled_flag": "isAfternoon",
        "label": "Afternoon medicine time",
    },
    "evening": {
        "patient_field": "evening_med",
        "scheduled_flag": "isEvening",
        "label": "Evening medicine time",
    },
    "night": {
        "patient_field": "night_med",
        "scheduled_flag": "isNight",
        "label": "Night medicine time",
    },
}


def start_alarm_scheduler():
    """Start a daemon thread that checks patient alarms every few seconds."""
    global _started

    with _start_lock:
        if _started:
            return
        _started = True

    thread = threading.Thread(target=_run_scheduler, daemon=True, name="patient-alarm-scheduler")
    thread.start()
    logger.info("Patient alarm scheduler started (poll interval: %ss)", _poll_interval_seconds)
    print(f"[scheduler] Patient alarm scheduler started. Poll interval: {_poll_interval_seconds}s")


def _run_scheduler():
    while True:
        try:
            _check_due_reminders()
        except Exception:
            logger.exception("Unexpected error in patient alarm scheduler")
        time.sleep(_poll_interval_seconds)


def _check_due_reminders():
    now = timezone.now()
    today = now.date()

    close_old_connections()
    _cleanup_trigger_cache(today)
    _check_due_alarms(now, today)
    _check_due_medications(now, today)
    _check_due_timed_medications(now, today)
    _check_due_tasks(now, today)


def _check_due_alarms(now, today):
    alarm_trigger_cache = _triggered_today["alarm"]

    due_patients = Patient.objects.filter(
        alarm__isnull=False,
        alarm__hour=now.hour,
        alarm__minute=now.minute,
    ).values("id", "user_id", "name", "email", "alarm")

    for patient in due_patients:
        patient_id = patient["id"]
        alarm_time = patient.get("alarm")
        alarm_time_key = alarm_time.strftime("%H:%M:%S") if alarm_time else "unknown"
        cache_key = f"{patient_id}:{alarm_time_key}"

        # De-duplicate only the same patient's same alarm-time for the same day.
        # This keeps alarms recurring daily and also allows a changed alarm time
        # to trigger again on the same day.
        if alarm_trigger_cache.get(cache_key) == today:
            continue

        alarm_trigger_cache[cache_key] = today
        _send_alarm_trigger(patient=patient, triggered_at=now)


def _send_alarm_trigger(patient, triggered_at):
    if messaging is None:
        print("[scheduler] Alarm trigger skipped: firebase_admin.messaging unavailable")
        return

    try:
        token = FCM.objects.get(user_id=patient["user_id"]).token
    except FCM.DoesNotExist:
        print(
            f"[scheduler] Alarm trigger skipped: no FCM token for patient_id={patient['id']} "
            f"user_id={patient['user_id']}"
        )
        return

    alarm_time = patient["alarm"].strftime("%H:%M:%S") if patient.get("alarm") else ""
    message = messaging.Message(
        token=token,
        data={
            "type": "alarm_trigger",
            "title": "Good Morning",
            "body": "Alarm time reached",
            "full_screen": "true",
            "action": "full_screen_alarm",
            "priority": "max",
            "patient_id": str(patient["id"]),
            "patient_name": str(patient.get("name") or ""),
            "patient_email": str(patient.get("email") or ""),
            "alarm_time": alarm_time,
            "timestamp": triggered_at.isoformat(),
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                sound="default",
                channel_id="alarm_channel",
            ),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default"),
            ),
        ),
    )

    try:
        response = messaging.send(message)
        print(
            f"[scheduler] Alarm FCM sent to patient_id={patient['id']} user_id={patient['user_id']} "
            f"at {alarm_time} response={response}"
        )
    except Exception as exc:
        logger.exception(
            "Alarm FCM send failed for patient_id=%s user_id=%s: %s",
            patient["id"],
            patient["user_id"],
            exc,
        )


def _check_due_medications(now, today):
    medication_trigger_cache = _triggered_today["medication"]

    for slot_key, slot_config in MEDICATION_SLOTS.items():
        patient_field = slot_config["patient_field"]
        scheduled_flag = slot_config["scheduled_flag"]

        due_patients = Patient.objects.filter(
            **{
                f"{patient_field}__isnull": False,
                f"{patient_field}__hour": now.hour,
                f"{patient_field}__minute": now.minute,
            }
        ).values("id", "user_id", "name", "email", patient_field)

        for patient in due_patients:
            reminder_time = patient.get(patient_field)
            reminder_time_key = reminder_time.strftime("%H:%M:%S") if reminder_time else "unknown"
            cache_key = f"{patient['id']}:{slot_key}:{reminder_time_key}"
            if medication_trigger_cache.get(cache_key) == today:
                continue

            medicines = list(
                ScheduledMed.objects.filter(
                    user_id=patient["id"],
                    **{scheduled_flag: True},
                ).values("id", "name", "description", "dosage", "food")
            )

            if not medicines:
                continue

            medication_trigger_cache[cache_key] = today
            _send_medication_trigger(
                patient=patient,
                medicines=medicines,
                slot_key=slot_key,
                slot_config=slot_config,
                triggered_at=now,
            )


def _send_medication_trigger(patient, medicines, slot_key, slot_config, triggered_at):
    if messaging is None:
        print("[scheduler] Medication trigger skipped: firebase_admin.messaging unavailable")
        return

    reminder_time = patient.get(slot_config["patient_field"])
    try:
        token = FCM.objects.get(user_id=patient["user_id"]).token
    except FCM.DoesNotExist:
        print(
            f"[scheduler] Medication trigger skipped: no FCM token for patient_id={patient['id']} "
            f"user_id={patient['user_id']} slot={slot_key}"
        )
        return

    reminder_time_text = reminder_time.strftime("%H:%M:%S") if reminder_time else ""
    message = messaging.Message(
        token=token,
        data={
            "type": "medication_reminder",
            "title": slot_config["label"],
            "body": f"{len(medicines)} medicine(s) scheduled for {slot_key}",
            "slot": slot_key,
            "patient_id": str(patient["id"]),
            "patient_name": str(patient.get("name") or ""),
            "patient_email": str(patient.get("email") or ""),
            "reminder_time": reminder_time_text,
            "timestamp": triggered_at.isoformat(),
            "medicines": json.dumps(medicines),
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                sound="default",
                channel_id="medication_reminder_channel",
            ),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default"),
            ),
        ),
    )

    try:
        response = messaging.send(message)
        print(
            f"[scheduler] Medication reminder FCM sent to patient_id={patient['id']} "
            f"user_id={patient['user_id']} slot={slot_key} medicines={len(medicines)} "
            f"time={reminder_time_text} response={response}"
        )
    except Exception as exc:
        logger.exception(
            "Medication reminder FCM send failed for patient_id=%s user_id=%s slot=%s: %s",
            patient["id"],
            patient["user_id"],
            slot_key,
            exc,
        )


def _check_due_timed_medications(now, today):
    medication_trigger_cache = _triggered_today["medication"]
    due_by_patient = {}

    timed_medicines = TimedMed.objects.select_related("user").values(
        "id",
        "user_id",
        "name",
        "description",
        "dosage",
        "time_gap",
        "start_time",
        "end_time",
    )

    for medicine in timed_medicines:
        occurrence_dt = _get_due_timed_occurrence(now, medicine)
        if occurrence_dt is None:
            continue

        cache_key = (
            f"timed:{medicine['id']}:{occurrence_dt.strftime('%Y-%m-%dT%H:%M')}"
        )
        if medication_trigger_cache.get(cache_key) == today:
            continue

        medication_trigger_cache[cache_key] = today
        patient_bucket = due_by_patient.setdefault(
            medicine["user_id"],
            {
                "patient": {
                    "id": medicine["user_id"],
                },
                "medicines": [],
                "occurrence_dt": occurrence_dt,
            },
        )
        if occurrence_dt < patient_bucket["occurrence_dt"]:
            patient_bucket["occurrence_dt"] = occurrence_dt
        patient_bucket["medicines"].append(
            {
                "id": medicine["id"],
                "name": medicine["name"],
                "description": medicine["description"],
                "dosage": medicine["dosage"],
                "time_gap": medicine["time_gap"],
                "start_time": medicine["start_time"].strftime("%H:%M:%S")
                if medicine.get("start_time")
                else "",
                "end_time": medicine["end_time"].strftime("%H:%M:%S")
                if medicine.get("end_time")
                else "",
            }
        )

    if not due_by_patient:
        return

    patient_map = {
        patient["id"]: patient
        for patient in Patient.objects.filter(id__in=due_by_patient.keys()).values(
            "id", "user_id", "name", "email"
        )
    }

    for patient_id, payload in due_by_patient.items():
        patient = patient_map.get(patient_id)
        if not patient:
            continue

        _send_timed_medication_trigger(
            patient=patient,
            medicines=payload["medicines"],
            triggered_at=now,
            occurrence_dt=payload["occurrence_dt"],
        )


def _get_due_timed_occurrence(now, medicine):
    start_time = medicine.get("start_time")
    end_time = medicine.get("end_time")
    interval_delta = _parse_interval_delta(medicine.get("time_gap"))
    if not start_time or not end_time or interval_delta is None:
        return None

    current_dt = now.replace(microsecond=0)
    start_dt = datetime.combine(now.date(), start_time)
    end_dt = datetime.combine(now.date(), end_time)

    # Support windows that pass midnight, e.g. 22:00 to 06:00.
    if end_dt < start_dt:
        if current_dt < start_dt:
            start_dt -= timedelta(days=1)
        else:
            end_dt += timedelta(days=1)

    if current_dt < start_dt or current_dt > end_dt:
        return None

    elapsed_seconds = int((current_dt - start_dt).total_seconds())
    interval_seconds = int(interval_delta.total_seconds())
    if interval_seconds <= 0:
        return None

    occurrence_count = elapsed_seconds // interval_seconds
    occurrence_dt = start_dt + (interval_delta * occurrence_count)

    # Fire if the current scheduler tick is within one poll interval of the due occurrence.
    if occurrence_dt > current_dt:
        return None

    tolerance_seconds = max(_poll_interval_seconds, 1)
    if (current_dt - occurrence_dt).total_seconds() >= tolerance_seconds:
        return None

    if occurrence_dt > end_dt:
        return None

    return occurrence_dt


def _parse_interval_delta(raw_value):
    if raw_value in (None, ""):
        return None

    try:
        interval_hours = float(str(raw_value).strip())
    except (TypeError, ValueError):
        return None

    if interval_hours <= 0:
        return None

    interval_seconds = int(interval_hours * 3600)
    if interval_seconds <= 0:
        return None

    return timedelta(seconds=interval_seconds)


def _send_timed_medication_trigger(patient, medicines, triggered_at, occurrence_dt):
    if messaging is None:
        print("[scheduler] Timed medication trigger skipped: firebase_admin.messaging unavailable")
        return

    try:
        token = FCM.objects.get(user_id=patient["user_id"]).token
    except FCM.DoesNotExist:
        print(
            f"[scheduler] Timed medication trigger skipped: no FCM token for patient_id={patient['id']} "
            f"user_id={patient['user_id']}"
        )
        return

    occurrence_time = occurrence_dt.strftime("%H:%M:%S")
    message = messaging.Message(
        token=token,
        data={
            "type": "timed_medication_reminder",
            "title": "Timed medicine reminder",
            "body": f"{len(medicines)} timed medicine(s) due now",
            "patient_id": str(patient["id"]),
            "patient_name": str(patient.get("name") or ""),
            "patient_email": str(patient.get("email") or ""),
            "occurrence_time": occurrence_time,
            "timestamp": triggered_at.isoformat(),
            "medicines": json.dumps(medicines),
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                sound="default",
                channel_id="medication_reminder_channel",
            ),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default"),
            ),
        ),
    )

    try:
        response = messaging.send(message)
        print(
            f"[scheduler] Timed medication reminder FCM sent to patient_id={patient['id']} "
            f"user_id={patient['user_id']} medicines={len(medicines)} "
            f"occurrence_time={occurrence_time} response={response}"
        )
    except Exception as exc:
        logger.exception(
            "Timed medication reminder FCM send failed for patient_id=%s user_id=%s: %s",
            patient["id"],
            patient["user_id"],
            exc,
        )


def _check_due_tasks(now, today):
    task_trigger_cache = _triggered_today["task"]
    current_dt = now.replace(microsecond=0)

    tasks = Tasks.objects.filter(date__isnull=False, time__isnull=False).values(
        "id",
        "user_id",
        "title",
        "description",
        "image",
        "date",
        "time",
        "isDone",
    )

    tasks_to_mark_done = []
    tasks_to_delete = []

    for task in tasks:
        task_dt = datetime.combine(task["date"], task["time"])
        reminder_30_before = task_dt - timedelta(minutes=30)
        delete_after = task_dt + timedelta(days=1)
        mark_done_after = task_dt + timedelta(minutes=30)

        if not task["isDone"]:
            _maybe_send_task_notification(
                task=task,
                trigger_key="task_30m_before",
                trigger_at=reminder_30_before,
                current_dt=current_dt,
                cache=task_trigger_cache,
                today=today,
                stage="before_due",
                title="Upcoming Task",
                body=f"{task['title']} starts in 30 minutes",
            )
            _maybe_send_task_notification(
                task=task,
                trigger_key="task_due_now",
                trigger_at=task_dt,
                current_dt=current_dt,
                cache=task_trigger_cache,
                today=today,
                stage="due_now",
                title="Task Due Now",
                body=f"{task['title']} is due now",
            )

        if not task["isDone"] and current_dt >= mark_done_after:
            tasks_to_mark_done.append(task["id"])

        if current_dt >= delete_after:
            tasks_to_delete.append(task["id"])

    if tasks_to_mark_done:
        updated = Tasks.objects.filter(id__in=tasks_to_mark_done, isDone=False).update(isDone=True)
        if updated:
            print(f"[scheduler] Auto-marked {updated} task(s) as done")

    if tasks_to_delete:
        deleted, _ = Tasks.objects.filter(id__in=tasks_to_delete).delete()
        if deleted:
            print(f"[scheduler] Deleted {deleted} expired task(s)")


def _maybe_send_task_notification(
    task,
    trigger_key,
    trigger_at,
    current_dt,
    cache,
    today,
    stage,
    title,
    body,
):
    if trigger_at > current_dt:
        return

    if (current_dt - trigger_at).total_seconds() >= max(_poll_interval_seconds, 1):
        return

    cache_key = f"{trigger_key}:{task['id']}:{task['date'].isoformat()}T{task['time'].strftime('%H:%M:%S')}"
    if cache.get(cache_key) == today:
        return

    cache[cache_key] = today
    _send_task_trigger(
        task=task,
        triggered_at=current_dt,
        stage=stage,
        title=title,
        body=body,
    )


def _send_task_trigger(task, triggered_at, stage, title, body):
    if messaging is None:
        print("[scheduler] Task trigger skipped: firebase_admin.messaging unavailable")
        return

    try:
        token = FCM.objects.get(user_id=task["user_id"]).token
    except FCM.DoesNotExist:
        print(
            f"[scheduler] Task trigger skipped: no FCM token for task_id={task['id']} "
            f"user_id={task['user_id']}"
        )
        return

    task_time = task["time"].strftime("%H:%M:%S") if task.get("time") else ""
    message = messaging.Message(
        token=token,
        data={
            "type": "task_reminder",
            "stage": stage,
            "title": title,
            "body": body,
            "task_id": str(task["id"]),
            "task_title": str(task.get("title") or ""),
            "task_description": str(task.get("description") or ""),
            "task_image": str(task.get("image") or ""),
            "task_date": task["date"].isoformat() if task.get("date") else "",
            "task_time": task_time,
            "timestamp": triggered_at.isoformat(),
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                sound="default",
                channel_id="task_reminder_channel",
            ),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default"),
            ),
        ),
    )

    try:
        response = messaging.send(message)
        print(
            f"[scheduler] Task reminder FCM sent task_id={task['id']} user_id={task['user_id']} "
            f"stage={stage} response={response}"
        )
    except Exception as exc:
        logger.exception(
            "Task reminder FCM send failed for task_id=%s user_id=%s stage=%s: %s",
            task["id"],
            task["user_id"],
            stage,
            exc,
        )


def _cleanup_trigger_cache(today: date):
    for cache in _triggered_today.values():
        stale_ids = [cache_key for cache_key, d in cache.items() if d != today]
        for cache_key in stale_ids:
            cache.pop(cache_key, None)
