
import os
import django
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vulca_backend.settings')
django.setup()

from app.models import CustomUser

try:
    email = "manambina316@gmail.com"
    user = CustomUser.objects.get(email=email)
    user.is_verified = True
    user.is_active = True
    user.save()
    print(f"SUCCESS: User {email} is now VERIFIED and ACTIVE.")
except CustomUser.DoesNotExist:
    print(f"ERROR: User {email} not found.")
except Exception as e:
    print(f"ERROR: {e}")
