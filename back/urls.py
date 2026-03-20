"""
URL configuration for back project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from alomind import views as alomind_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/patient/register/', alomind_views.patient_register, name='patient-register'),
    path('api/caregiver/register/', alomind_views.caregiver_register, name='caregiver-register'),
    path('api/patient/login/', alomind_views.patient_login, name='patient-login'),
    path('api/caregiver/login/', alomind_views.caregiver_login, name='caregiver-login'),
    path('api/emergency-contacts/', alomind_views.emergency_contacts, name='emergency-contacts'),
    path('api/emergency-contacts/<int:contact_id>/', alomind_views.emergency_contacts, name='emergency-contact-detail'),
    path('api/gallery/', alomind_views.gallery, name='gallery'),
    path('api/gallery/<int:item_id>/', alomind_views.gallery, name='gallery-detail'),
    # path('api/tasks/stream/', alomind_views.task_notifications, name='task-notifications'),
    path('api/chat/', alomind_views.chatbot, name='chatbot'),
    path('api/history/', alomind_views.history, name='history'),
    path('api/history/<int:item_id>/', alomind_views.history, name='history-detail'),
    path('api/tasks/', alomind_views.tasks, name='tasks'),
    path('api/tasks/<int:item_id>/', alomind_views.tasks, name='tasks-detail'),
    path('api/scheduled-med/', alomind_views.scheduled_med, name='scheduled-med'),
    path('api/scheduled-med/<int:item_id>/', alomind_views.scheduled_med, name='scheduled-med-detail'),
    path('api/timed-med/', alomind_views.timed_med, name='timed-med'),
    path('api/timed-med/<int:item_id>/', alomind_views.timed_med, name='timed-med-detail'),
    path('api/notifications/', alomind_views.notifications, name='notifications'),
    path('api/patient-profile/', alomind_views.patient_profile, name='patient-profile'),
    path('api/caregiver-profile/', alomind_views.caregiver_profile, name='caregiver-profile'),
    path('api/medicine-timing/',alomind_views.med_timing,name='medicine-timing'),
    path('api/save-fcm/', alomind_views.save_fcm, name='save-fcm'),
    path('api/fall-alert/', alomind_views.fall_alert, name='fall-alert'),
    path('api/music/', alomind_views.music, name='music'),
    path('api/music/<int:item_id>/', alomind_views.music, name='music-detail'),
]

# serve media during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

