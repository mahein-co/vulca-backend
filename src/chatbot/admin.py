from django.contrib import admin

# Register your models here.
from django.contrib import admin
from chatbot import models

@admin.register(models.MessageHistory)
class MessageHistoryAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_renamed_by_ai', 'user__username', 'user__email']
    search_fields = ['title', 'user__username', 'user__email']

@admin.register(models.ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['message_history__title', 'user__username']
    search_fields = ['user__username', 'user__email', 'user_input', 'ai_response']

@admin.register(models.Document)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['title', 'file_path']
    search_fields = ['title', 'file_path']

@admin.register(models.DocumentPage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['document__title', 'page_number']
    search_fields = ['document__title', 'page_number', 'content']
