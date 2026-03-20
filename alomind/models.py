from django.db import models
from django.contrib.auth.models import User


class Patient(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='patient_profile')
	email = models.EmailField(blank=True)
	name = models.CharField(max_length=255, blank=True)
	age = models.CharField(max_length=16, blank=True)
	gender = models.CharField(max_length=32, blank=True)
	phone = models.CharField(max_length=32, blank=True)
	profile_pic = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
	alarm = models.TimeField(null=True, blank=True)
	morning_med = models.TimeField(null=True, blank=True)
	afternoon_med = models.TimeField(null=True, blank=True)
	evening_med = models.TimeField(null=True, blank=True)
	night_med = models.TimeField(null=True, blank=True)

	def __str__(self):
		return f"Patient: {self.user.email}"


class Caregiver(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='caregiver_profile')
	email = models.EmailField(blank=True)
	name = models.CharField(max_length=255, blank=True)
	patient_email = models.EmailField(blank=True)
	age = models.CharField(max_length=16, blank=True)
	gender = models.CharField(max_length=32, blank=True)
	phone = models.CharField(max_length=32, blank=True)
	profile_pic = models.ImageField(upload_to='profile_pics/', null=True, blank=True)

	def __str__(self):
		return f"Caregiver: {self.user.email} (patient={self.patient_email})"
	

class Gallery(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='gallery')
	name = models.CharField(max_length=255)
	relation  = models.CharField(max_length=255)
	image = models.ImageField(upload_to='gallery_images/')

	def delete(self, *args, **kwargs):
		if self.image:
			self.image.delete(save=False)  # delete the file from storage
		super().delete(*args, **kwargs)  # delete the model instance

	def __str__(self):
		return f"Gallery Image: {self.name}"
	

class History(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='history')
	title = models.CharField(max_length=255)
	description = models.TextField(blank=True)
	date = models.DateField(null=True, blank=True)
	# make document optional so existing rows don't require a value
	document = models.FileField(upload_to='medical_history/', null=True, blank=True)

	ai_summary = models.TextField(blank=True)

	def delete(self, *args, **kwargs):
		if self.document:
			self.document.delete(save=False)  # delete the file from storage
		super().delete(*args, **kwargs)  # delete the model instance


	def __str__(self):
		return f"History: {self.title}"


class EmergencyContact(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='emergency_contacts')
	name = models.CharField(max_length=255)
	relation = models.CharField(max_length=255)
	phone = models.CharField(max_length=32)

	def __str__(self):
		return f"Emergency Contact: {self.name} ({self.phone})"


class Tasks(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='tasks')
	title = models.CharField(max_length=255)
	description = models.TextField(blank=True)
	image = models.CharField(blank=True, max_length=255,null=True)
	date = models.DateField(null=True, blank=True)
	time = models.TimeField(null=True, blank=True)
	isDone = models.BooleanField(default=False)

	def __str__(self):
		return f"Task: {self.title}"
	

class ScheduledMed(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='scheduled_med')
	name = models.CharField(max_length=255)
	description = models.TextField(blank=True)
	dosage = models.CharField(max_length=255)
	food=models.BooleanField(default=False)
	isMorning = models.BooleanField(default=False)
	isAfternoon = models.BooleanField(default=False)
	isEvening = models.BooleanField(default=False)
	isNight = models.BooleanField(default=False)

	def __str__(self):
		return f"Medicine: {self.name}"
	

class TimedMed(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='timed_med')
	name = models.CharField(max_length=255)
	description = models.TextField(blank=True)
	dosage = models.CharField(max_length=255)
	time_gap = models.CharField(max_length=255)
	start_time = models.TimeField()
	end_time = models.TimeField()

	
	def __str__(self):
		return f"Timed Medicine: {self.name} from {self.start_time} to {self.end_time}"
	

class Music(models.Model):
	user = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='music')
	title = models.CharField(max_length=255)
	file = models.FileField(upload_to='music/')

	def delete(self, *args, **kwargs):
		if self.file:
			self.file.delete(save=False)  # delete the file from storage
		super().delete(*args, **kwargs)  # delete the model instance

	def __str__(self):
		return f"Music: {self.title}"

class FCM(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='fcm_token')
	token = models.CharField(max_length=255)

