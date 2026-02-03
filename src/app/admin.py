from django.contrib import admin

# Register your models here.
from django.contrib import admin
from app import models

from django.contrib.auth.admin import UserAdmin
from app.models import CustomUser, OtpToken

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['username', 'email', 'role', 'is_verified', 'is_active', 'profile_picture', 'name']
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('role', 'is_verified', 'profile_picture', 'name')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('role', 'is_verified', 'profile_picture', 'name')}),
    )

class OtpTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "otp_code")


admin.site.register(OtpToken, OtpTokenAdmin)
