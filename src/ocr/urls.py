from django.urls import path
from ocr import views


urlpatterns = [
    path('files/', views.file_source_list_create, name='source-files'),
    path('pieces/', views.form_source_list_create, name='data-sources'),
    path('pieceslist/', views.all_pieces_list_view, name='all-pieces-list'),
    path('files/extract', views.extract_content_file_view, name='files-extract'),
    
    # Endpoints Excel
    path('excel/upload/', views.excel_upload_and_analyze_view, name='excel-upload'),
    path('excel/validate/', views.excel_validate_mapping_view, name='excel-validate'),
    path('excel/save/', views.excel_save_data_view, name='excel-save'),
]