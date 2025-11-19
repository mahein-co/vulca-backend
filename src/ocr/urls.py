from django.urls import path
from ocr import views


urlpatterns = [
    path('files/', views.file_source_list_create, name='source-files'),
    path('files/extract', views.extract_content_file_view, name='files-extract'),
]