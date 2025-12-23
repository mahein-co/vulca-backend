from django.contrib import admin
from ocr import models

@admin.register(models.FileSource)
class FileSourceAdmin(admin.ModelAdmin):
    list_display = ['file_name','file','description','ref_file','piece_type','hash_ocr']
    search_fields = ['file_name', 'is_ocr_processed']   


@admin.register(models.FormSource)
class FormSourceAdmin(admin.ModelAdmin):
    list_display = [ 'description','ref_file','piece_type','created_at']
    search_fields = ['piece_type', 'description']  