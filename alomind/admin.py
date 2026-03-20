from django.contrib import admin
from .models import Patient, Caregiver,Gallery, History, EmergencyContact, TimedMed, ScheduledMed, Tasks

# Register your models here.

admin.site.register(Patient)
admin.site.register(Caregiver)
admin.site.register(Gallery)
admin.site.register(History)
admin.site.register(EmergencyContact)
admin.site.register(TimedMed)
admin.site.register(ScheduledMed)
admin.site.register(Tasks)