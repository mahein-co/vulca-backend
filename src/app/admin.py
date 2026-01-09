from django.contrib import admin

# Register your models here.
from django.contrib import admin
from app import models

@admin.register(models.CustomUser)
class AppAdmin(admin.ModelAdmin):
    model = models.CustomUser
    list_display = ['username', 'email','role','is_verified','is_active','profile_picture','name']
    search_fields = ['username', 'email','role','is_verified','is_active','profile_picture','name']

class OtpTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "otp_code")


admin.site.register(models.OtpToken, OtpTokenAdmin)
