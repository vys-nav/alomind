import json
import random
from openai import OpenAI
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from rest_framework_simplejwt.tokens import RefreshToken
from django.db import IntegrityError, transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
import base64
import uuid
from django.core.files.base import ContentFile
import time
from django.utils import timezone
from datetime import datetime, timedelta
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.http import HttpResponse
from .ai_history_summarize import extract_and_summarize
from firebase_admin import messaging
from .models import Patient, Caregiver, EmergencyContact, Gallery, Tasks,Music, ScheduledMed,TimedMed, History, FCM
client = OpenAI(
	base_url="https://models.inference.ai.azure.com",
	api_key=settings.GITHUB_API_KEY)

def _json_error(message, status=400):
	return JsonResponse({'message': message}, status=status)


def _resolve_patient(user):
	"""Return a Patient instance for the given user.

	- If the user is a patient, return their profile.
	- If the user is a caregiver, look up the linked patient via
	  ``caregiver_profile.patient_email`` and return that Patient object.
	- If neither or the linked patient doesn't exist, return ``None``.
	"""
	# direct patient
	if hasattr(user, 'patient_profile'):
		return user.patient_profile

	# caregiver linking
	if hasattr(user, 'caregiver_profile'):
		try:
			caregiver = user.caregiver_profile
			return Patient.objects.get(email=caregiver.patient_email)
		except Patient.DoesNotExist:
			return None

	return None


@csrf_exempt
def patient_register(request):
	if request.method != 'POST':
		return _json_error('Method not allowed', status=405)

	try:
		data = json.loads(request.body.decode('utf-8'))
	except Exception:
		return _json_error('Invalid JSON')

	name = data.get('name', '').strip()
	age = data.get('age', '').strip()
	gender = data.get('gender', '').strip()
	phone = data.get('phone', '').strip()
	email = data.get('email', '').strip().lower()
	password = data.get('password', '')

	if not all([name, email, password]):
		return _json_error('Missing required fields (name, email, password)')

	if User.objects.filter(email__iexact=email).exists():
		return _json_error('A user with that email already exists', status=400)

	try:
		with transaction.atomic():
			user = User.objects.create_user(username=email, email=email, first_name=name)
			user.set_password(password)
			user.save()

			Patient.objects.create(user=user, email=email, name=name, age=age, gender=gender, phone=phone)

		return JsonResponse({'message': 'Patient created successfully'}, status=201)
	except IntegrityError:
		return _json_error('Failed to create user due to integrity error', status=500)
	except Exception as e:
		return _json_error(str(e), status=500)


@csrf_exempt
def caregiver_register(request):
	if request.method != 'POST':
		return _json_error('Method not allowed', status=405)

	try:
		data = json.loads(request.body.decode('utf-8'))
	except Exception:
		return _json_error('Invalid JSON')

	name = data.get('name', '').strip()
	patient_email = data.get('patient_email', '').strip().lower()
	age = data.get('age', '').strip()
	gender = data.get('gender', '').strip()
	phone = data.get('phone', '').strip()
	email = data.get('email', '').strip().lower()
	password = data.get('password', '')

	if not all([name, patient_email, email, password]):
		return _json_error('Missing required fields (name, patient_email, email, password)')

	if Caregiver.objects.filter(patient_email__iexact=patient_email).exists():
		return _json_error('This patient already has a caregiver', status=400)

	if User.objects.filter(email__iexact=email).exists():
		return _json_error('A user with that email already exists', status=400)

	try:
		with transaction.atomic():
			user = User.objects.create_user(username=email, email=email, first_name=name)
			user.set_password(password)
			user.save()

			Caregiver.objects.create(
				user=user,
				email=email,
				name=name,
				patient_email=patient_email,
				age=age,
				gender=gender,
				phone=phone,
			)

		return JsonResponse({'message': 'Caregiver created successfully'}, status=201)
	except IntegrityError:
		return _json_error('Failed to create user due to integrity error', status=500)
	except Exception as e:
		return _json_error(str(e), status=500)


@csrf_exempt
def patient_login(request):
	if request.method != 'POST':
		return _json_error('Method not allowed', status=405)

	try:
		data = json.loads(request.body.decode('utf-8'))
	except Exception:
		return _json_error('Invalid JSON')

	email = data.get('email', '').strip().lower()
	password = data.get('password', '')

	if not email or not password:
		return _json_error('Missing email or password', status=400)

	user = authenticate(request, username=email, password=password)
	if user is None:
		return _json_error('Invalid credentials', status=401)

	# ensure user has a Patient profile
	try:
		_ = user.patient_profile
	except Exception:
		return _json_error('User is not a patient', status=403)
	
	if user:
		login(request, user)

	# create JWT tokens (refresh and access) for the authenticated patient
	try:
		refresh = RefreshToken.for_user(user)
		access_token = str(refresh.access_token)
		refresh_token = str(refresh)
	except Exception:
		# fallback: return login success without tokens if token creation fails
		return JsonResponse({'message': 'Login successful', 'email': user.email, 'name': user.first_name})

	return JsonResponse({
		'message': 'Login successful',
		'email': user.email,
		'name': user.first_name,
		'access': access_token,
		'refresh': refresh_token,
	})


@csrf_exempt
def caregiver_login(request):
	if request.method != 'POST':
		return _json_error('Method not allowed', status=405)

	try:
		data = json.loads(request.body.decode('utf-8'))
	except Exception:
		return _json_error('Invalid JSON')

	email = data.get('email', '').strip().lower()
	password = data.get('password', '')

	if not email or not password:
		return _json_error('Missing email or password', status=400)

	user = authenticate(request, username=email, password=password)
	if user is None:
		return _json_error('Invalid credentials', status=401)

	# ensure user has a Caregiver profile
	try:
		_ = user.caregiver_profile
	except Exception:
		return _json_error('User is not a caregiver', status=403)
	
	if user:
		login(request, user)

	# create JWT tokens (refresh and access) for the authenticated caregiver
	try:
		refresh = RefreshToken.for_user(user)
		access_token = str(refresh.access_token)
		refresh_token = str(refresh)
	except Exception:
		# fallback: return login success without tokens if token creation fails
		return JsonResponse({'message': 'Login successful', 'email': user.email, 'name': user.first_name})

	return JsonResponse({
		'message': 'Login successful',
		'email': user.email,
		'name': user.first_name,
		'access': access_token,
		'refresh': refresh_token,
	})


#-------------------------------------------------------------------------------------------------#
#-------------------------------------------------------------------------------------------------#
#-------------------------------------------------------------------------------------------------#


@csrf_exempt
@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def emergency_contacts(request, contact_id=None):
	print("USER:", request.user)
	print("AUTH:", request.user.is_authenticated)
	print(request.headers)
	print(request.body)
	print(request.user)
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)

	if request.method == 'GET':
		contacts = EmergencyContact.objects.filter(user=patient).values( 'id', 'name', 'relation', 'phone')
		return JsonResponse({
			'message': 'Emergency contacts retrieved successfully',
			'contacts': list(contacts)
		}, status=200)

	elif request.method == 'POST':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		name = data.get('name', '').strip()
		relation = data.get('relation', '').strip()
		phone = data.get('phone', '').strip()

		if not all([name, relation, phone]):
			return _json_error('Missing required fields (name, relation, phone)')

		try:
			contact = EmergencyContact.objects.create(
				user=patient,
				name=name,
				relation=relation,
				phone=phone
			)
			return JsonResponse({
				'message': 'Emergency contact created successfully',
				'contact': {
					'name': contact.name,
					'relation': contact.relation,
					'phone': contact.phone
				}
			}, status=201)
		except Exception as e:
			return _json_error(str(e), status=500)
		
	elif request.method == 'PUT':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		emergency_id = data.get('id')
		if not emergency_id:
			return _json_error('Missing required field (id)', status=400)

		try:
			contact = EmergencyContact.objects.get(id=emergency_id, user=patient)
		except EmergencyContact.DoesNotExist:
			return _json_error('Contact not found', status=404)

		name = data.get('name', '').strip()
		relation = data.get('relation', '').strip()
		phone = data.get('phone', '').strip()
		if name:
			contact.name = name
		if relation:
			contact.relation = relation
		if phone:
			contact.phone = phone
		contact.save()

		return JsonResponse({
			'message': 'Emergency contact updated successfully',
			'contact': {
				'id': contact.id,
				'name': contact.name,
				'relation': contact.relation,
				'phone': contact.phone
			}
		}, status=200)
	
	elif request.method == 'DELETE':
		if contact_id is None:
			contact_id = request.GET.get('id')
			if not contact_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					contact_id = data.get('id')
				except Exception:
					contact_id = None

		if not contact_id:
			return _json_error('Missing contact item id', status=400)

		try:
			item = EmergencyContact.objects.get(id=contact_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'Contact item deleted successfully'}, status=200)
		except EmergencyContact.DoesNotExist:
			return _json_error('Contact item not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)
		
	else:
		return _json_error('Method not allowed', status=405)




@csrf_exempt
@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def gallery(request, item_id=None):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)

	if request.method == 'GET':
		items = Gallery.objects.filter(user=patient).values('id', 'name', 'relation', 'image')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'name': it['name'],
				'relation': it['relation'],
				'image': f"media/{it['image']}", 
			})
			
		return JsonResponse({
			'message': 'Gallery items retrieved successfully',
			'gallery': results
		}, status=200)

	elif request.method == 'POST':
		# debug info to help track down bad requests
		print("gallery POST content_type:", request.content_type)
		print("POST data:", request.POST.dict())
		print("FILES:", request.FILES.keys())

		# handle either multipart or JSON/base64
		if request.content_type.startswith('multipart'):
			name = request.POST.get('name', '').strip()
			relation = request.POST.get('relation', '').strip()
			image_file = request.FILES.get('image')
		else:
			try:
				data = json.loads(request.body.decode('utf-8'))
			except Exception:
				return _json_error('Invalid JSON')
			name = data.get('name', '').strip()
			relation = data.get('relation', '').strip()
			image_file = None
			img_b64 = data.get('image', '')
			if img_b64:
				if ',' in img_b64:
					_, img_b64 = img_b64.split(',', 1)
				try:
					decoded = base64.b64decode(img_b64)
					ext = 'jpg'
					image_file = ContentFile(decoded, name=f"{uuid.uuid4()}.{ext}")
				except Exception:
					image_file = None

		if not all([name, relation, image_file]):
			return _json_error('Missing required fields (name, relation, image)')

		try:
			item = Gallery.objects.create(
				user=patient,
				name=name,
				relation=relation,
				image=image_file
			)
			return JsonResponse({
				'message': 'Gallery item created successfully',
				'item': {
					'id': item.id,
					'name': item.name,
					'relation': item.relation,
					'image': f"media/{item.image}",
				}
			}, status=201)
		except Exception as e:
			return _json_error(str(e), status=500)
	
	elif request.method == 'DELETE':
		# delete using path parameter first, then fallback to query/body
		if item_id is None:
			item_id = request.GET.get('id')
			if not item_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					item_id = data.get('id')
				except Exception:
					item_id = None

		if not item_id:
			return _json_error('Missing gallery item id', status=400)

		try:
			item = Gallery.objects.get(id=item_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'Gallery item deleted successfully'}, status=200)
		except Gallery.DoesNotExist:
			return _json_error('Gallery item not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)
	else:
		return _json_error('Method not allowed', status=405)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def chatbot(request):
	message = request.data.get("message")

	if not message:
		return _json_error("Message required", status=400)

	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error("Patient profile not found", status=403)

	histories = History.objects.filter(user=patient).order_by("-date", "-id")
	medical_notes = []
	for history_item in histories:
		if history_item.ai_summary:
			medical_notes.append(history_item.ai_summary)
		elif history_item.description:
			medical_notes.append(history_item.description)

	medical_context = "\n\n".join(medical_notes[:10]) if medical_notes else "No medical history available."
	is_caregiver = hasattr(request.user, "caregiver_profile")

	if is_caregiver:
		system_prompt = f"""
You are a caregiving assistant supporting a caregiver for a person living with memory challenges.

Patient Medical Records:
{medical_context}

Keep responses practical, calm, and caregiver-friendly.
You may reference the patient's history when useful.
Do not diagnose.
Encourage professional medical consultation if necessary.
"""
	else:
		system_prompt = f"""
You are a warm, encouraging companion speaking directly to the user.

Background notes to quietly keep in mind:
{medical_context}

Do not call the user a patient.
Do not frame them as ill, impaired, or incapable.
Speak naturally, warmly, and with encouragement.
Help with motivation, routine, reassurance, and gentle conversation.
If health topics come up, be supportive without diagnosing, and suggest professional help when appropriate.
"""

	response = client.chat.completions.create(
		model="gpt-4o-mini",
		messages=[
			{
				"role": "system",
				"content": system_prompt
			},
			{
				"role": "user",
				"content": message
			}
		]
	)

	reply = response.choices[0].message.content

	return JsonResponse({"reply": reply})


@csrf_exempt
@api_view(['GET','POST','DELETE'])
@permission_classes([IsAuthenticated])
def history(request, item_id=None):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		items = History.objects.filter(user=patient).values('id', 'title', 'description', 'document', 'date')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'title': it['title'],
				'description': it['description'],
				'date': it['date'].isoformat() if it.get('date') else None,
				'document': f"media/{it['document']}" if it['document'] else None,
			})
			
		return JsonResponse({
			'message': 'Medical history retrieved successfully',
			'history': results
		}, status=200)
	
	elif request.method == 'POST':
		title = request.POST.get('title', '').strip()
		description = request.POST.get('description', '').strip()
		date_str = request.POST.get('date', '').strip()
		file = request.FILES.get('document')
		print("history POST data:", request.POST.dict())
		print("FILES:", request.FILES.keys())
		print(f"title: {title}, description: {description}, date: {date_str}, file: {file}")
		if not title or not description or not file:
			return _json_error('Missing required fields (title, description, document)', status=400)
		date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
		
		medical= History.objects.create(
			user=patient,
			title=title,
			description=description,
			document=file,
			date=date
			)
		summary=extract_and_summarize(medical.document.path)
		medical.ai_summary=summary
		medical.save()
		return JsonResponse({'message': 'Medical history created successfully'}, status=201)
	
	elif request.method == 'DELETE':
		if item_id is None:
			item_id = request.GET.get('id')
			if not item_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					item_id = data.get('id')
				except Exception:
					item_id = None

		if not item_id:
			return _json_error('Missing history item id', status=400)

		try:
			item = History.objects.get(id=item_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'History item deleted successfully'}, status=200)
		except History.DoesNotExist:
			return _json_error('History item not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)

	else:
		return _json_error('Method not allowed', status=405)


@csrf_exempt
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def notifications(request):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		now = timezone.now()
		today = timezone.localdate(now) if timezone.is_aware(now) else now.date()
		window_end = now + timedelta(minutes=30)
		stress_relief_quotes = [
			"Take one slow breath. You only need to handle the next small step.",
			"Progress is still progress, even when today feels gentle and slow.",
			"Your mind can pause for a moment. Rest is part of healing too.",
			"One calm moment can change the shape of the whole day.",
			"You are doing enough right now. Let this moment be lighter.",
		]

		tasks_qs = Tasks.objects.filter(user=patient, isDone=False, date=today).values('id', 'title', 'time')
		results = []
		for t in tasks_qs:
			results.append({
				'title': t['title'],
				'time': t['time'].isoformat() if t.get('time') else None,
			})

		medication_results = []
		slot_definitions = [
			('morning', patient.morning_med, {'isMorning': True}),
			('afternoon', patient.afternoon_med, {'isAfternoon': True}),
			('evening', patient.evening_med, {'isEvening': True}),
			('night', patient.night_med, {'isNight': True}),
		]

		for slot_name, slot_time, scheduled_filter in slot_definitions:
			if not slot_time:
				continue

			slot_dt = datetime.combine(today, slot_time)
			if slot_dt < now or slot_dt > window_end:
				continue

			medicines = list(
				ScheduledMed.objects.filter(user=patient, **scheduled_filter).values(
					'id', 'name', 'description', 'dosage', 'food'
				)
			)
			if not medicines:
				continue

			for medicine in medicines:
				medication_results.append({
					'title': f"Take {medicine['name']}",
					'time': slot_time.isoformat(),
				})

		quote_item = {
			'title': 'Quote of the day',
			'time': random.choice(stress_relief_quotes),
		}
		results = [quote_item] + results + medication_results

		return JsonResponse({
			'message': 'Notifications retrieved successfully',
			'notifications': results
		}, status=200)
	

@csrf_exempt
@api_view(['GET','POST','PUT','DELETE'])
@permission_classes([IsAuthenticated])
def tasks(request,item_id=None):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		is_caregiver = hasattr(request.user, 'caregiver_profile')
		if is_caregiver:
			items = Tasks.objects.filter(user=patient).values('id', 'title', 'description', 'date', 'time', 'image','isDone')
			results = []
			for it in items:
				results.append({
					'id': it['id'],
					'title': it['title'],
					'description': it['description'],
					'date': it['date'].isoformat() if it.get('date') else None,
					'time': it['time'].isoformat() if it.get('time') else None,
					'image': it['image'],
					'isDone': it['isDone']
				})
		else:
			items = Tasks.objects.filter(user=patient, isDone=False).values('id', 'title', 'description', 'date', 'time', 'image')
			results = []
			for it in items:
				results.append({
					'id': it['id'],
					'title': it['title'],
					'description': it['description'],
					'date': it['date'].isoformat() if it.get('date') else None,
					'time': it['time'].isoformat() if it.get('time') else None,
					'image': it['image'],
				})
			
		return JsonResponse({
			'message': 'Tasks retrieved successfully',
			'tasks': results
		}, status=200)
	elif request.method == 'POST':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')
		print(data)
		title = data.get('title', '').strip()
		description = data.get('description', '').strip()
		date_str = data.get('date', '').strip()
		time_str = data.get('time', '').strip()
		image = data.get('image', '').strip()

		if not title or not date_str or not time_str:
			return _json_error('Missing required fields (title, date, time)', status=400)

		try:
			date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
			time_val = timezone.datetime.strptime(time_str, '%H:%M:%S').time()
		except ValueError:
			return _json_error('Invalid date or time format', status=400)

		task = Tasks.objects.create(
			user=patient,
			title=title,
			description=description,
			date=date,
			time=time_val,
			image=image
		)
		return JsonResponse({'message': 'Task created successfully'}, status=201)
	elif request.method == 'PUT':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		task_id = data.get('id')
		if not task_id:
			return _json_error('Missing required field (id)', status=400)

		try:
			task = Tasks.objects.get(id=task_id, user=patient)
		except Tasks.DoesNotExist:
			return _json_error('Task not found', status=404)

		title = data.get('title', '').strip()
		description = data.get('description', '').strip()
		date_str = data.get('date', '').strip()
		time_str = data.get('time', '').strip()
		image = data.get('image', '').strip()

		if title:
			task.title = title
		if description:
			task.description = description
		if date_str:
			try:
				task.date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
			except ValueError:
				return _json_error('Invalid date format', status=400)
		if time_str:
			try:
				task.time = timezone.datetime.strptime(time_str, '%H:%M:%S').time()
			except ValueError:
				return _json_error('Invalid time format', status=400)
		if image:  # Allow setting image to empty string
			task.image = image
		task.isDone = False
		task.save()
		return JsonResponse({'message': 'Task updated successfully'}, status=200)
	elif request.method == 'DELETE':
		if item_id is None:
			item_id = request.GET.get('id')
			if not item_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					item_id = data.get('id')
				except Exception:
					item_id = None

		if not item_id:
			return _json_error('Missing task item id', status=400)

		try:
			item = Tasks.objects.get(id=item_id, user=patient)
			if hasattr(request.user, 'caregiver_profile'):
				item.delete()
			else:
				item.isDone=True
				item.save()
			return JsonResponse({'message': 'Task deleted successfully'}, status=200)
		except Tasks.DoesNotExist:
			return _json_error('Task not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)

	else:
		return _json_error('Method not allowed', status=405)

@csrf_exempt
@api_view(['GET','POST','DELETE','PUT'])
@permission_classes([IsAuthenticated])
def scheduled_med(request, item_id=None):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		items = ScheduledMed.objects.filter(user=patient).values('id', 'name', 'description', 'dosage','food', 'isMorning', 'isAfternoon', 'isEvening', 'isNight')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'name': it['name'],
				'description': it['description'],
				'dosage': it['dosage'],
				'food': it['food'],
				'isMorning': it['isMorning'],
				'isAfternoon': it['isAfternoon'],
				'isEvening': it['isEvening'],
				'isNight': it['isNight'],
			})
			
		return JsonResponse({
			'message': 'Scheduled medicines retrieved successfully',
			'scheduled_med': results
		}, status=200)
	elif request.method == 'POST':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		name = data.get('name', '').strip()
		description = data.get('description', '').strip()
		dosage = data.get('dosage', '').strip()
		food = data.get('food', False)
		isMorning = data.get('isMorning', False)
		isAfternoon = data.get('isAfternoon', False)
		isEvening = data.get('isEvening', False)
		isNight = data.get('isNight', False)

		if not name or not dosage:
			return _json_error('Missing required fields (name, dosage)', status=400)

		scheduled_med = ScheduledMed.objects.create(
			user=patient,
			name=name,
			description=description,
			dosage=dosage,
			food=food,
			isMorning=isMorning,
			isAfternoon=isAfternoon,
			isEvening=isEvening,
			isNight=isNight
		)
		return JsonResponse({'message': 'Scheduled medicine created successfully'}, status=201)
	
	elif request.method == 'PUT':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		med_id = data.get('id')
		if not med_id:
			return _json_error('Missing required field (id)', status=400)

		try:
			med = ScheduledMed.objects.get(id=med_id, user=patient)
		except ScheduledMed.DoesNotExist:
			return _json_error('Scheduled medicine not found', status=404)

		name = data.get('name', '').strip()
		description = data.get('description', '').strip()
		dosage = data.get('dosage', '').strip()
		food = data.get('food')
		isMorning = data.get('isMorning')
		isAfternoon = data.get('isAfternoon')
		isEvening = data.get('isEvening')
		isNight = data.get('isNight')

		if name:
			med.name = name
		if description:
			med.description = description
		if dosage:
			med.dosage = dosage
		if food is not None:
			med.food = food
		if isMorning is not None:
			med.isMorning = isMorning
		if isAfternoon is not None:
			med.isAfternoon = isAfternoon
		if isEvening is not None:
			med.isEvening = isEvening
		if isNight is not None:
			med.isNight = isNight

		med.save()
		return JsonResponse({'message': 'Scheduled medicine updated successfully'}, status=200)
	elif request.method == 'DELETE':
		if item_id is None:
			item_id = request.GET.get('id')
			if not item_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					item_id = data.get('id')
				except Exception:
					item_id = None

		if not item_id:
			return _json_error('Missing scheduled medicine item id', status=400)

		try:
			item = ScheduledMed.objects.get(id=item_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'Scheduled medicine item deleted successfully'}, status=200)
		except ScheduledMed.DoesNotExist:
			return _json_error('Scheduled medicine item not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)
	else:
		return _json_error('Method not allowed', status=405)
	

@csrf_exempt
@api_view(['GET','POST','DELETE','PUT'])
@permission_classes([IsAuthenticated])
def timed_med(request, item_id=None):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		items = TimedMed.objects.filter(user=patient).values('id', 'name', 'description', 'dosage', 'time_gap', 'start_time', 'end_time')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'name': it['name'],
				'description': it['description'],
				'dosage': it['dosage'],
				'time_gap': it['time_gap'],
				'start_time': it['start_time'].isoformat() if it.get('start_time') else None,
				'end_time': it['end_time'].isoformat() if it.get('end_time') else None,
			})
			
		return JsonResponse({
			'message': 'Timed medicines retrieved successfully',
			'timed_med': results
		}, status=200)
	elif request.method == 'POST':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		name = data.get('name', '').strip()
		description = data.get('description', '').strip()
		dosage = data.get('dosage', '').strip()
		time_str = data.get('time_gap', '').strip()
		start_time_str = data.get('start_time', '').strip()
		end_time_str = data.get('end_time', '').strip()

		if not name or not dosage or not time_str:
			return _json_error('Missing required fields (name, dosage, time)', status=400)

		try:
			start_time=timezone.datetime.strptime(start_time_str, '%H:%M:%S').time()
			end_time=timezone.datetime.strptime(end_time_str, '%H:%M:%S').time()
		except ValueError:
			return _json_error('Invalid time format', status=400)

		timed_med = TimedMed.objects.create(
			user=patient,
			name=name,
			description=description,
			dosage=dosage,
			time_gap=time_str,
			start_time=start_time,
			end_time=end_time
		)
		return JsonResponse({'message': 'Timed medicine created successfully'}, status=201)
	
	elif request.method == 'PUT':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')

		med_id = data.get('id')
		if not med_id:
			return _json_error('Missing required field (id)', status=400)
		
		try:
			med = TimedMed.objects.get(id=med_id, user=patient)
		except TimedMed.DoesNotExist:
			return _json_error('Timed medicine not found', status=404)

		name = data.get('name', '').strip()
		description = data.get('description', '').strip()
		dosage = data.get('dosage', '').strip()
		time_gap = data.get('time_gap', '').strip()
		start_time_str = data.get('start_time', '').strip()
		end_time_str = data.get('end_time', '').strip()

		if name:
			med.name = name
		if description:
			med.description = description
		if dosage:
			med.dosage = dosage
		if time_gap:
			med.time_gap = time_gap
		if start_time_str:
			try:
				start_time=timezone.datetime.strptime(start_time_str, '%H:%M:%S').time()
				med.start_time=start_time
			except ValueError:
				return _json_error('Invalid start time format', status=400)
		
		if end_time_str:
			try:
				end_time=timezone.datetime.strptime(end_time_str, '%H:%M:%S').time()
				med.end_time=end_time
			except ValueError:
				return _json_error('Invalid end time format', status=400)

		med.save()
		return JsonResponse({'message': 'Timed medicine updated successfully'}, status=200)
	elif request.method == 'DELETE':
		if item_id is None:
			item_id = request.GET.get('id')
			if not item_id:
				try:
					data = json.loads(request.body.decode('utf-8'))
					item_id = data.get('id')
				except Exception:
					item_id = None

		if not item_id:
			return _json_error('Missing timed medicine item id', status=400)

		try:
			item = TimedMed.objects.get(id=item_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'Timed medicine item deleted successfully'}, status=200)
		except TimedMed.DoesNotExist:
			return _json_error('Timed medicine item not found', status=404)
		except Exception as e:
			return _json_error(str(e), status=500)
	else:
		return _json_error('Method not allowed', status=405)


@csrf_exempt
@api_view(['POST','PUT','GET'])
@permission_classes([IsAuthenticated])
def med_timing(request):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	# resolve patient whether request.user is patient or caregiver
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	if request.method == 'GET':
		items = Patient.objects.filter(id=patient.id).values('id', 'morning_med', 'afternoon_med', 'evening_med', 'night_med')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'morning_med': it['morning_med'],
				'afternoon_med': it['afternoon_med'],
				'evening_med': it['evening_med'],
				'night_med': it['night_med'],
			})
		return JsonResponse({'med_timing': results}, status=200)
	elif request.method == 'PUT' or request.method == 'POST':
		try:
			data = json.loads(request.body.decode('utf-8'))
		except Exception:
			return _json_error('Invalid JSON')
		try:
			timing = Patient.objects.get(id=patient.id)
		except Patient.DoesNotExist:
			return _json_error('Patient record not found', status=404)
		
		morning_med = data.get('morning')
		afternoon_med = data.get('noon')
		evening_med = data.get('evening')
		night_med = data.get('night')
		print(f"Parsed times - morning: {morning_med}, afternoon: {afternoon_med}, evening: {evening_med}, night: {night_med}")

		if morning_med:
			try:
				morning=timezone.datetime.strptime(morning_med, '%H:%M:%S').time()
				timing.morning_med=morning
			except ValueError:
				return _json_error('Invalid start time format', status=400)

		if afternoon_med:
			try:
				afternoon=timezone.datetime.strptime(afternoon_med, '%H:%M:%S').time()
				timing.afternoon_med=afternoon
			except ValueError:
				return _json_error('Invalid start time format', status=400)

		if evening_med:
			try:
				evening=timezone.datetime.strptime(evening_med, '%H:%M:%S').time()
				timing.evening_med=evening
			except ValueError:
				return _json_error('Invalid start time format', status=400)

		if night_med:
			try:
				night=timezone.datetime.strptime(night_med, '%H:%M:%S').time()
				timing.night_med=night
			except ValueError:
				return _json_error('Invalid start time format', status=400)

		timing.save()
		return JsonResponse({'message': 'Medicine timing updated successfully'}, status=200)
	else:
		return _json_error('Method not allowed', status=405)



@csrf_exempt
@api_view(['PUT','POST','GET','DELETE'])
@permission_classes([IsAuthenticated])
def patient_profile(request):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)

	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)
	
	if request.method == 'GET':
		items = Patient.objects.filter(id=patient.id).values('id', 'name', 'email', 'age', 'gender', 'phone', 'profile_pic','alarm')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'name': it['name'],
				'email': it['email'],
				'age': it['age'],
				'gender': it['gender'],
				'phone': it['phone'],
				'wake_up_time': it['alarm'].isoformat() if it.get('alarm') else None,
				'profile_pic': f"media/{it['profile_pic']}" if it['profile_pic'] else None,
			})
		return JsonResponse({'patient_profile': results}, status=200)
	elif request.method == 'POST':
		if request.content_type.startswith('multipart'):
			name = request.POST.get('name', '').strip()
			age = request.POST.get('age')
			phone = request.POST.get('phone','').strip()
			alarm = request.POST.get('wake_up_time', '').strip()
			image_file = request.FILES.get('image')
		else:
			try:
				data = json.loads(request.body.decode('utf-8'))
			except Exception:
				return _json_error('Invalid JSON')
			name = data.get('name', '').strip()
			age = data.get('age')
			phone = data.get('phone','').strip()
			alarm = data.get('wake_up_time', '').strip()
			image_file = None
			img_b64 = data.get('image', '')
			if img_b64:
				if ',' in img_b64:
					_, img_b64 = img_b64.split(',', 1)
				try:
					decoded = base64.b64decode(img_b64)
					ext = 'jpg'
					image_file = ContentFile(decoded, name=f"{uuid.uuid4()}.{ext}")
				except Exception:
					image_file = None
		try:
			profile = Patient.objects.get(id=patient.id)
		except Patient.DoesNotExist:
			return _json_error('Patient profile not found', status=404)
		if name:
			profile.name = name
		if age:
			profile.age = age
		if phone:
			profile.phone = phone
		if alarm:
			try:
				alarm_time=timezone.datetime.strptime(alarm, '%H:%M:%S').time()
				profile.alarm=alarm_time
			except ValueError:
				return _json_error('Invalid alarm time format', status=400)
		if image_file:
			if profile.profile_pic:
				profile.profile_pic.delete(save=False)
			profile.profile_pic = image_file
		profile.save()
		return JsonResponse({'message': 'Patient profile updated successfully'}, status=200)

	

@csrf_exempt
@api_view(['GET','POST','DELETE','PUT'])
@permission_classes([IsAuthenticated])
def caregiver_profile(request):
	if not request.user.is_authenticated:
		return _json_error('User not authenticated', status=401)
	
	if request.method == 'GET':
		items = Caregiver.objects.filter(user=request.user).values('id', 'name', 'email','age','gender','phone','profile_pic')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'name': it['name'],
				'email': it['email'],
				'age': it['age'],
				'gender': it['gender'],
				'phone': it['phone'],
				'profile_pic': f"media/{it['profile_pic']}" if it['profile_pic'] else None,
			})
		return JsonResponse({'caregiver_profile': results}, status=200)
	elif request.method == 'POST' or request.method == 'PUT':
		if request.content_type.startswith('multipart'):
			name = request.POST.get('name', '').strip()
			age = request.POST.get('age')
			phone = request.POST.get('phone','').strip()
			image_file = request.FILES.get('image')
		else:
			try:
				data = json.loads(request.body.decode('utf-8'))
			except Exception:
				return _json_error('Invalid JSON')
			name = data.get('name', '').strip()
			age = data.get('age')
			phone = data.get('phone','').strip()
			image_file = None
			img_b64 = data.get('image', '')
			if img_b64:
				if ',' in img_b64:
					_, img_b64 = img_b64.split(',', 1)
				try:
					decoded = base64.b64decode(img_b64)
					ext = 'jpg'
					image_file = ContentFile(decoded, name=f"{uuid.uuid4()}.{ext}")
				except Exception:
					image_file = None
		try:
			profile = Caregiver.objects.get(user=request.user)
		except Caregiver.DoesNotExist:
			return _json_error('Caregiver profile not found', status=404)
		
		if name:
			profile.name = name
		if age:
			profile.age = age
		if phone:
			profile.phone = phone
		if image_file:
			if profile.profile_pic:
				profile.profile_pic.delete(save=False)
			profile.profile_pic = image_file
		profile.save()
		return JsonResponse({'message': 'Caregiver profile updated successfully'}, status=200)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_fcm(request):
	token = request.data.get('fcm_token')
	if not token:
		return _json_error('FCM token is required', status=400)
	
	FCM.objects.update_or_create(user=request.user, defaults={'token': token})
	return JsonResponse({'message': 'FCM token saved successfully'}, status=200)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fall_alert(request):
	patient = request.user
	if not patient:
		return _json_error('User is not a patient', status=403)

	try:
		caregiver = Caregiver.objects.select_related('user').get(patient_email__iexact=patient.email)
	except Caregiver.DoesNotExist:
		return _json_error('No caregiver linked to this patient', status=404)

	try:
		fcm = FCM.objects.get(user=caregiver.user)
	except FCM.DoesNotExist:
		return _json_error('Caregiver FCM token not found', status=403)

	title = request.data.get('title', 'Fall Alert')
	body = request.data.get(
		'body',
		f'{patient.first_name or patient.email} may have fallen. Please check immediately.',
	)
	location = request.data.get('location_link')

	message = messaging.Message(
    token=fcm.token,
    data={
        'type': 'fall_alert',
        'title': title,
        'body': body,
        'patient_id': str(patient.id),
        'patient_name': patient.first_name or '',
        'patient_email': patient.email or '',
        'location_link': location or '',
    },
    android=messaging.AndroidConfig(
        priority='high',
    ),
    apns=messaging.APNSConfig(
        headers={'apns-priority': '10'},
    ),
)
	try:
		response = messaging.send(message)
	except Exception as exc:
		return _json_error(f'Failed to send fall alert: {exc}', status=500)
	print("Fall alert sent successfully")
	print(response)
	return JsonResponse(
		{
			'message': 'Fall alert sent successfully',
			'patient': {
				'id': patient.id,
				'name': patient.first_name,
				'email': patient.email,
			},
			'caregiver': {
				'id': caregiver.id,
				'name': caregiver.name,
				'email': caregiver.email,
				'user_id': caregiver.user_id,
			},
			'fcm_token': fcm.token,
			'firebase_response': response,
		},
		status=200,
	)



@csrf_exempt
@api_view(['POST', 'GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def music(request, item_id=None):
	patient = _resolve_patient(request.user)
	if not patient:
		return _json_error('User is not a patient or linked caregiver', status=403)

	if request.method == 'POST':
		title = request.POST.get('title', '').strip()
		file = request.FILES.get('file')
		if not title or not file:
			return _json_error('Missing required fields (title, file)', status=400)

		item = Music.objects.create(user=patient, title=title, file=file)
		return JsonResponse(
			{
				'message': 'Music added successfully',
				'music': {
					'id': item.id,
					'title': item.title,
					'file': f"media/{item.file}",
				},
			},
			status=201,
		)

	elif request.method == 'GET':
		items = Music.objects.filter(user=patient).values('id', 'title', 'file')
		results = []
		for it in items:
			results.append({
				'id': it['id'],
				'title': it['title'],
				'file': f"media/{it['file']}" if it.get('file') else None,
			})
		return JsonResponse({'music': results}, status=200)

	elif request.method == 'DELETE':
		music_id = item_id
		if music_id is None:
			music_id = request.GET.get('id')
		if not music_id:
			try:
				data = json.loads(request.body.decode('utf-8'))
				music_id = data.get('id')
			except Exception:
				music_id = None
		if not music_id:
			return _json_error('Missing music id', status=400)
		try:
			item = Music.objects.get(id=music_id, user=patient)
			item.delete()
			return JsonResponse({'message': 'Music deleted successfully'}, status=200)
		except Music.DoesNotExist:
			return _json_error('Music item not found', status=404)
	else:
		return _json_error('Method not allowed', status=405)
