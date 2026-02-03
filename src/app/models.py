from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import random


def generate_numeric_otp():
    return ''.join(random.choices('0123456789', k=6))

class CustomUser(AbstractUser):
    # Ajoute des champs personnalisés si besoin
    ROLE_CHOICES = (
        ("expert_comptable", "Expert Comptable"),
        ("admin", "Administrateur"),
        ("assistant", "Assistant"),
    )
     
    profile_picture = models.ImageField(upload_to="profiles/", blank=True, null=True)
    email = models.EmailField(unique=True, blank=False, null=False, )
    name = models.CharField(max_length=150, blank=True, null=True)
    username = models.CharField(max_length=150, unique=True)  # username obligatoire
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="expert_comptable")
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Username_field: email field for authentication
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]


    def __str__(self):
        return self.email
    

class OtpToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="otps",
        null=True,
        blank=True
    )
    otp_code = models.CharField(max_length=6, default=generate_numeric_otp)
    otp_created_at = models.DateTimeField(auto_now_add=True)
    otp_expires_at = models.DateTimeField(blank=True, null=True)
    is_used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        """
        Définir automatiquement otp_expires_at à 5 minutes après la création
        si non défini explicitement.
        """
        if not self.otp_expires_at:
            self.otp_expires_at = timezone.now() + timedelta(minutes=5)
        super().save(*args, **kwargs)

    def is_valid(self):
        """
        Vérifie si le OTP est encore valide (non utilisé et pas expiré)
        """
        return not self.is_used and self.otp_expires_at > timezone.now()

    def mark_as_used(self):
        """Marquer le OTP comme utilisé"""
        self.is_used = True
        self.save()

    def __str__(self):
        return f"OTP {self.otp_code} for {self.user.email}"

    