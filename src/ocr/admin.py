from django.contrib import admin
from ocr import models

@admin.register(models.FileSource)
class FileSourceAdmin(admin.ModelAdmin):
    list_display = ['file_name', 'is_ocr_processed', 'file']
    search_fields = ['file_name', 'is_ocr_processed']