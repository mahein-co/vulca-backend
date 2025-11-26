from django.contrib import admin
from compta import models

# Register your models here.

@admin.register(models.Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ['libelle', 'numero_piece', 'numero_compte', 'debit_ar', 'credit_ar', 'type_journal', "date", "created_at"]
    search_fields = ['id']