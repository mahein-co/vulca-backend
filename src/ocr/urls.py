from django.urls import path
from ocr import views


urlpatterns = [
    path('files/', views.FileSourceListCreateView.as_view(), name='source-files'),
]