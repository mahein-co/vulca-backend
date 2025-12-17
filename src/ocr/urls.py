
from django.urls import path
from .views import file_source_list_create, extract_text_view 

urlpatterns = [
<<<<<<< Updated upstream
    path('files/', views.file_source_list_create, name='source-files'),
    path('pieces/', views.form_source_list_create, name='data-sources'),
    path('files/extract', views.extract_content_file_view, name='files-extract'),
=======
    path('files/', file_source_list_create, name='source-files'),
    path("extract-text/", extract_text_view, name="extract-text"),
>>>>>>> Stashed changes
]