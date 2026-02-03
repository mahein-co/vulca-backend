
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.authtoken.views import obtain_auth_token
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('app.urls')), # Authentication & Users matched at root (e.g. /users/...)
    path('api/', include('ocr.urls')),
    path('api/', include('compta.urls')),
    path('api/', include('chatbot.urls')),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    path('api-token-auth/', obtain_auth_token, name='api_token_auth'),
]

# urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
