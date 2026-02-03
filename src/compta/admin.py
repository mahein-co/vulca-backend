from django.contrib import admin
from compta import models

# Register your models here.

@admin.register(models.Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ['libelle', 'numero_piece', 'numero_compte', 'debit_ar', 'credit_ar', 'type_journal', 'project',"date", "created_at"]
    search_fields = ['id']


@admin.register(models.GrandLivre)
class GrandLivreAdmin(admin.ModelAdmin):
    list_display = ['numero_compte', 'date', 'libelle', 'debit', 'credit', 'solde', 'project','created_at']
    search_fields = ['numero_compte', 'libelle']
    list_filter = ['date']

@admin.register(models.Balance)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ['numero_compte', 'libelle', 'total_debit', 'total_credit', 'date','project','created_at']
    search_fields = ['numero_compte', 'libelle']
    list_filter = ['date']

@admin.register(models.Bilan)
class BilanAdmin(admin.ModelAdmin):
    list_display = ['libelle','numero_compte','type_bilan','categorie','date','project', "created_at"]
    search_fields = ['id']

@admin.register(models.CompteResultat)
class CompteResultatAdmin(admin.ModelAdmin):
    list_display = ['libelle','numero_compte','nature','date','project', "created_at"]
    search_fields = ['id']
@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_by', 'is_active', 'created_at', 'updated_at']
    search_fields = ['name']
@admin.register(models.ProjectAccess)
class ProjectAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'project', 'status', 'requested_at', 'approved_at', 'approved_by']
    search_fields = ['user__email', 'project__name']
    list_filter = ['status']
