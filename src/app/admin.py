from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Project, Task, ProjectAssignment


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'username', 'name', 'role', 'is_approved', 'is_active', 'created_at']
    list_filter = ['role', 'is_approved', 'is_active', 'created_at']
    search_fields = ['email', 'username', 'name']
    ordering = ['-created_at']
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Informations personnelles', {'fields': ('username', 'name', 'role')}),
        ('Approbation', {'fields': ('is_approved', 'approved_by', 'approved_at')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('Dates', {'fields': ('created_at', 'updated_at')}),
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'username', 'name', 'role', 'password1', 'password2'),
        }),
    )


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_by', 'is_active', 'created_at']  # ← CHANGÉ 'owner' en 'created_by'
    list_filter = ['is_active', 'created_at']
    search_fields = ['title', 'description']
    ordering = ['-created_at']
    
    fieldsets = (
        (None, {'fields': ('title', 'description')}),
        ('Gestion', {'fields': ('created_by', 'is_active')}),
        ('Dates', {'fields': ('created_at', 'updated_at')}),
    )
    
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'project', 'assigned_by', 'can_edit', 'can_delete', 'assigned_at']
    list_filter = ['can_edit', 'can_delete', 'assigned_at']
    search_fields = ['user__username', 'user__email', 'project__title']
    ordering = ['-assigned_at']
    
    fieldsets = (
        (None, {'fields': ('user', 'project')}),
        ('Permissions', {'fields': ('can_edit', 'can_delete')}),
        ('Informations', {'fields': ('assigned_by', 'assigned_at')}),
    )
    
    readonly_fields = ['assigned_at']


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'status', 'priority', 'assigned_to', 'due_date', 'created_at']
    list_filter = ['status', 'priority', 'created_at']
    search_fields = ['title', 'description']
    ordering = ['-created_at']
    
    fieldsets = (
        (None, {'fields': ('title', 'description', 'project')}),
        ('Statut', {'fields': ('status', 'priority')}),
        ('Assignation', {'fields': ('assigned_to', 'due_date')}),
        ('Dates', {'fields': ('created_at', 'updated_at')}),
    )
    
    readonly_fields = ['created_at', 'updated_at']