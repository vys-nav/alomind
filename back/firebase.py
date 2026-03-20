import os

import firebase_admin
from firebase_admin import credentials

# Some environments inject local/broken proxy settings (e.g. 127.0.0.1:9),
# which prevents Firebase Admin from reaching Google token endpoints.
# Default: bypass proxy for Firebase unless explicitly opted in.
if os.getenv("FIREBASE_USE_SYSTEM_PROXY", "0") != "1":
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    no_proxy = os.getenv("NO_PROXY", "")
    google_hosts = "oauth2.googleapis.com,fcm.googleapis.com,www.googleapis.com"
    os.environ["NO_PROXY"] = f"{no_proxy},{google_hosts}".strip(",")

cred = credentials.Certificate("alomind-26-firebase-adminsdk-fbsvc-b4aa18774a.json")
firebase_admin.initialize_app(cred)
