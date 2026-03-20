from channels.generic.websocket import AsyncWebsocketConsumer
import json
from urllib.parse import parse_qs
import asyncio

from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken, TokenError

from .models import Caregiver, EmergencyContact, FCM

try:
    from firebase_admin import messaging
except Exception:
    messaging = None

try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

class TestConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        print("Websocket Connected")
        await self.accept()
        await self.send(text_data=json.dumps({
            "message": "Connected successfully!"
        }))

    async def receive(self, text_data):
        data = json.loads(text_data)

        await self.send(text_data=json.dumps({
            "message": f"You said: {data['message']}"
        }))

    async def disconnect(self, close_code):
        print("Disconnected")


class SOSConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = await self._get_user_from_query_token()
        if not self.user:
            await self.close(code=4001)
            return

        self.user_group = f"sos_user_{self.user.id}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    "type": "connection_ack",
                    "message": "SOS websocket connected",
                    "user_id": self.user.id,
                }
            )
        )

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid JSON"}))
            return

        event_type = data.get("type")

        if event_type == "sos_alert":
            await self._handle_sos_alert(data)
            return

        await self.send(text_data=json.dumps({"type": "error", "message": "Unsupported event type"}))

    async def _handle_sos_alert(self, data):
        patient = await self._get_patient_profile()
        if not patient:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Only patients can trigger SOS alerts"}
                )
            )
            return

        payload = {
            "type": "sos_alert",
            "patient": {
                "id": patient.id,
                "name": patient.name,
                "email": patient.email,
                "phone": patient.phone,
            },
            "message": data.get("message", "Emergency alert triggered"),
            "latitude":data.get("latitude"),
            "longitude":data.get("longitude"),
            "location_link":data.get("location_link"),
            "timestamp": timezone.now().isoformat(),
        }

        caregiver = await self._get_caregiver_for_patient(patient.email)
        caregiver_delivered = 0
        fcm_result = {"sent": 0, "error": None}
        if caregiver:
            await self.channel_layer.group_send(
                f"sos_user_{caregiver.user_id}",
                {
                    "type": "sos_alert_event",
                    "payload": payload,
                },
            )
            caregiver_delivered = 1
            fcm_result = await self._send_fcm_to_caregiver(caregiver.user_id, payload)

        sms_result = await self._send_sms_to_emergency_contacts(
            patient=patient,
            message=payload["message"],
            location_link=payload.get("location_link"),
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "sos_ack",
                    "message": "SOS alert processed",
                    "caregiver_delivered": caregiver_delivered,
                    "sms_sent": sms_result["sent"],
                    "sms_failed": sms_result["failed"],
                    "sms_skipped": sms_result["skipped"],
                    "sms_error": sms_result.get("error"),
                    "fcm_sent": fcm_result["sent"],
                    "fcm_error": fcm_result.get("error"),
                }
            )
        )

    async def sos_alert_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def alarm_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def _get_user_from_query_token(self):
        query_params = parse_qs(self.scope.get("query_string", b"").decode())
        token = query_params.get("token", [None])[0]
        if not token:
            return None

        try:
            access_token = AccessToken(token)
            user_id = access_token.get("user_id")
            if not user_id:
                return None
        except TokenError:
            return None

        return await self._get_user_by_id(user_id)

    @database_sync_to_async
    def _get_user_by_id(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_patient_profile(self):
        try:
            return self.user.patient_profile
        except Exception:
            return None

    @database_sync_to_async
    def _get_caregiver_for_patient(self, patient_email):
        return Caregiver.objects.filter(patient_email__iexact=patient_email).only("user_id").first()

    @database_sync_to_async
    def _get_emergency_contacts(self, patient):
        return list(
            EmergencyContact.objects.filter(user=patient).values_list("name", "phone")
        )

    @database_sync_to_async
    def _get_fcm_token_for_user_id(self, user_id):
        if not user_id:
            return None
        try:
            return FCM.objects.get(user_id=user_id).token
        except FCM.DoesNotExist:
            return None

    async def _send_sms_to_emergency_contacts(self, patient, message, location_link=None):
        contacts = await self._get_emergency_contacts(patient)
        if not contacts:
            return {"sent": 0, "failed": 0, "skipped": 0, "error": "No emergency contacts found"}

        account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
        auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", None)
        from_number = getattr(settings, "TWILIO_FROM_NUMBER", None)

        if not account_sid or not auth_token or not from_number:
            return {
                "sent": 0,
                "failed": 0,
                "skipped": len(contacts),
                "error": "Twilio config missing (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER)",
            }

        if TwilioClient is None:
            return {
                "sent": 0,
                "failed": 0,
                "skipped": len(contacts),
                "error": "twilio package not installed",
            }

        sms_body = (
            f"SOS Alert from Alomind.\n"
            f"Patient: {patient.name or patient.email}\n"
            f"Phone: {patient.phone or 'N/A'}\n"
            f"Message: {message}\n"
            f"Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        if location_link:
            sms_body += f"\nLocation: {location_link}"

        sent = 0
        failed = 0
        client = TwilioClient(account_sid, auth_token)

        for _name, phone in contacts:
            try:
                await asyncio.to_thread(
                    client.messages.create,
                    to=phone,
                    from_=from_number,
                    body=sms_body,
                )
                sent += 1
            except Exception:
                failed += 1

        return {"sent": sent, "failed": failed, "skipped": 0}

    async def _send_fcm_to_caregiver(self, caregiver_user_id, payload):
        if messaging is None:
            return {"sent": 0, "error": "firebase_admin package not installed"}

        token = await self._get_fcm_token_for_user_id(caregiver_user_id)
        if not token:
            return {"sent": 0, "error": "Caregiver FCM token not found"}

        patient_data = payload.get("patient", {}) or {}
        message = messaging.Message(
            token=token,
            data={
                "type": "sos_alert",
                "title": "SOS Alert",
                "body": payload.get("message", "Emergency alert triggered"),
                "patient_id": str(patient_data.get("id", "")),
                "patient_name": str(patient_data.get("name", "")),
                "patient_email": str(patient_data.get("email", "")),
                "patient_phone": str(patient_data.get("phone", "")),
                "latitude": str(payload.get("latitude") or ""),
                "longitude": str(payload.get("longitude") or ""),
                "location_link": str(payload.get("location_link") or ""),
                "timestamp": str(payload.get("timestamp") or ""),
            },
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
        )

        try:
            await asyncio.to_thread(messaging.send, message)
            return {"sent": 1, "error": None}
        except Exception as exc:
            return {"sent": 0, "error": str(exc)}


class LocationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = await self._get_user_from_query_token()
        if not self.user:
            await self.close(code=4001)
            return

        self.user_group = f"location_user_{self.user.id}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    "type": "connection_ack",
                    "message": "Location websocket connected",
                    "user_id": self.user.id,
                }
            )
        )

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"type": "error", "message": "Invalid JSON"}))
            return

        event_type = data.get("type")

        if event_type == "start_location_tracking":
            await self._handle_start_location_tracking()
            return

        if event_type == "stop_location_tracking":
            await self._handle_stop_location_tracking()
            return

        if event_type == "location_update":
            await self._handle_location_update(data)
            return

        await self.send(text_data=json.dumps({"type": "error", "message": "Unsupported event type"}))

    async def location_tracking_request(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def location_update_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def _handle_start_location_tracking(self):
        caregiver = await self._get_caregiver_profile()
        if not caregiver:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Only caregivers can start location tracking"}
                )
            )
            return

        patient = await self._get_patient_by_email(caregiver.patient_email)
        if not patient:
            await self.send(
                text_data=json.dumps({"type": "error", "message": "Linked patient not found"})
            )
            return

        await self.channel_layer.group_send(
            f"location_user_{patient.user_id}",
            {
                "type": "location_tracking_request",
                "payload": {
                    "type": "location_tracking_request",
                    "action": "start",
                    "message": "Caregiver opened live location page",
                    "timestamp": timezone.now().isoformat(),
                    "caregiver": {
                        "id": caregiver.id,
                        "name": caregiver.name,
                        "email": caregiver.email,
                    },
                },
            },
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "location_tracking_ack",
                    "action": "start",
                    "message": "Live location request sent to patient",
                    "patient": {
                        "id": patient.id,
                        "name": patient.name,
                        "email": patient.email,
                    },
                }
            )
        )

    async def _handle_stop_location_tracking(self):
        caregiver = await self._get_caregiver_profile()
        if not caregiver:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Only caregivers can stop location tracking"}
                )
            )
            return

        patient = await self._get_patient_by_email(caregiver.patient_email)
        if not patient:
            await self.send(
                text_data=json.dumps({"type": "error", "message": "Linked patient not found"})
            )
            return

        await self.channel_layer.group_send(
            f"location_user_{patient.user_id}",
            {
                "type": "location_tracking_request",
                "payload": {
                    "type": "location_tracking_request",
                    "action": "stop",
                    "message": "Caregiver closed live location page",
                    "timestamp": timezone.now().isoformat(),
                },
            },
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "location_tracking_ack",
                    "action": "stop",
                    "message": "Stop location request sent to patient",
                }
            )
        )

    async def _handle_location_update(self, data):
        patient = await self._get_patient_profile()
        if not patient:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Only patients can send location updates"}
                )
            )
            return

        caregiver = await self._get_caregiver_for_patient(patient.email)
        if not caregiver:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "No caregiver linked to this patient"}
                )
            )
            return

        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")
        speed = data.get("speed")
        heading = data.get("heading")

        if latitude is None or longitude is None:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "latitude and longitude are required"}
                )
            )
            return

        try:
            latitude = float(latitude)
            longitude = float(longitude)
            accuracy = float(accuracy) if accuracy is not None else None
            speed = float(speed) if speed is not None else None
            heading = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Invalid location payload"}
                )
            )
            return

        payload = {
            "type": "location_update",
            "timestamp": timezone.now().isoformat(),
            "patient": {
                "id": patient.id,
                "name": patient.name,
                "email": patient.email,
            },
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "speed": speed,
                "heading": heading,
            },
        }

        await self.channel_layer.group_send(
            f"location_user_{caregiver.user_id}",
            {
                "type": "location_update_event",
                "payload": payload,
            },
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "location_update_ack",
                    "message": "Location forwarded to caregiver",
                    "caregiver_user_id": caregiver.user_id,
                }
            )
        )

    async def _get_user_from_query_token(self):
        query_params = parse_qs(self.scope.get("query_string", b"").decode())
        token = query_params.get("token", [None])[0]
        if not token:
            return None

        try:
            access_token = AccessToken(token)
            user_id = access_token.get("user_id")
            if not user_id:
                return None
        except TokenError:
            return None

        return await self._get_user_by_id(user_id)

    @database_sync_to_async
    def _get_user_by_id(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_patient_profile(self):
        try:
            return self.user.patient_profile
        except Exception:
            return None

    @database_sync_to_async
    def _get_caregiver_profile(self):
        try:
            return self.user.caregiver_profile
        except Exception:
            return None

    @database_sync_to_async
    def _get_caregiver_for_patient(self, patient_email):
        return Caregiver.objects.filter(patient_email__iexact=patient_email).only("user_id").first()

    @database_sync_to_async
    def _get_patient_by_email(self, patient_email):
        if not patient_email:
            return None

        try:
            return User.objects.select_related("patient_profile").get(
                patient_profile__email__iexact=patient_email
            ).patient_profile
        except User.DoesNotExist:
            return None


# class ReminderConsumer(AsyncWebsocketConsumer):
#     async def connect(self):
#         self.user = await self._get_user_from_query_token()
#         if not self.user:
#             await self.close(code=4001)
#             return

#         self.user_group = f"reminder_user_{self.user.id}"
#         await self.channel_layer.group_add(self.user_group, self.channel_name)
#         await self.accept()
#         print(f"[reminder-ws] Connected user_id={self.user.id} group={self.user_group}")
#         await self.send(
#             text_data=json.dumps(
#                 {
#                     "type": "connection_ack",
#                     "message": "Reminder websocket connected",
#                     "user_id": self.user.id,
#                 }
#             )
#         )

#     async def disconnect(self, close_code):
#         if hasattr(self, "user_group"):
#             await self.channel_layer.group_discard(self.user_group, self.channel_name)
#             print(f"[reminder-ws] Disconnected user_id={self.user.id} group={self.user_group}")

#     async def receive(self, text_data):
#         await self.send(
#             text_data=json.dumps(
#                 {"type": "error", "message": "Reminder websocket is server-push only"}
#             )
#         )

#     async def alarm_event(self, event):
#         print(f"[reminder-ws] Delivering alarm event to user_id={self.user.id}")
#         await self.send(text_data=json.dumps(event["payload"]))

#     async def medication_reminder_event(self, event):
#         print(f"[reminder-ws] Delivering medication reminder to user_id={self.user.id}")
#         await self.send(text_data=json.dumps(event["payload"]))

#     async def _get_user_from_query_token(self):
#         query_params = parse_qs(self.scope.get("query_string", b"").decode())
#         token = query_params.get("token", [None])[0]
#         if not token:
#             return None

#         try:
#             access_token = AccessToken(token)
#             user_id = access_token.get("user_id")
#             if not user_id:
#                 return None
#         except TokenError:
#             return None

#         return await self._get_user_by_id(user_id)

#     @database_sync_to_async
#     def _get_user_by_id(self, user_id):
#         try:
#             return User.objects.get(id=user_id)
#         except User.DoesNotExist:
#             return None
