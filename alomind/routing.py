from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/test/?$", consumers.TestConsumer.as_asgi()),
    re_path(r"^ws/sos/?$", consumers.SOSConsumer.as_asgi()),
    re_path(r"^ws/location/?$", consumers.LocationConsumer.as_asgi()),
    # re_path(r"^ws/reminders/?$", consumers.ReminderConsumer.as_asgi()),
]
