from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenObtainPairView

from app import views



urlpatterns = [
    path("users/", view=views.get_users, name="users"),
    path("users/admin-count/", view=views.get_admin_count, name="admin-count"),
    path("users/create/", view=views.create_user_by_admin, name="create-user-by-admin"),
    path("users/profile/", view=views.UserProfileView.as_view(), name="users-profile"),
    path("users/register/", view=views.register_user, name="users-register"),
    path("users/<int:pk>/update/", view=views.update_user, name="user-update"),
    path("users/<int:pk>/delete/", view=views.delete_user, name="user-delete"),
    
    path('users/login/', views.MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('users/token/refresh/', views.CookieTokenRefreshView.as_view(), name='token_refresh'),
    
    path("users/verify-otp/", views.verify_otp, name="verify_otp"),
    path("users/resend-otp/", views.ResendOtpAPIView.as_view(), name="resend-otp"),

    path("password-reset/request/", views.request_password_reset, name="request-password-reset"),
    path("password-reset/verify/", views.verify_reset_otp, name="verify-reset-otp"),
    path("password-reset/confirm/", views.reset_password, name="reset-password"),
    path("users/change-password/", views.change_password, name="change-password"),
]

