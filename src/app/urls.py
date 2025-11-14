from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='user')
router.register(r'projects', views.ProjectViewSet, basename='project')
router.register(r'tasks', views.TaskViewSet, basename='task')

urlpatterns = [
    # Authentication
    path('auth/signup/', views.SignupAPI.as_view(), name='signup'),
    path('auth/login/', views.LoginAPI.as_view(), name='login'),
    path('auth/logout/', views.LogoutAPI.as_view(), name='logout'),
    path('auth/me/', views.MeAPI.as_view(), name='me'),
    path('auth/check-approval/', views.CheckApprovalAPI.as_view(), name='check_approval'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Page d'attente
    path('pending/', views.pending_approval_view, name='pending_approval'),
    
    # REST API
    path('api/', include(router.urls)),
]