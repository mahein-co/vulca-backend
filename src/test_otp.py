
import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vulca_backend.settings')
django.setup()

from app.models import OtpToken, CustomUser
from django.utils import timezone

try:
    # Use existing user or create dummy
    user = CustomUser.objects.first()
    if not user:
        print("No user found, skipping")
    else:
        otp = OtpToken.objects.create(
            user=user,
            otp_expires_at=timezone.now()
        )
        print(f"Generated OTP Code: '{otp.otp_code}'")
        print(f"Length: {len(otp.otp_code)}")
except Exception as e:
    print(f"Error: {e}")
