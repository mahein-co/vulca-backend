
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('app.urls')),
    path('api/', include('ocr.urls')),
    path('api/', include('compta.urls')),
    # re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

# urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
