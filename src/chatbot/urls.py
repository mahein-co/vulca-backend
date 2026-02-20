from django.urls import path
from chatbot import views
from chatbot.filtered_data_view import get_filtered_accounting_data

print("[DEBUG] chatbot/urls.py loaded")

urlpatterns = [
    path('messages/', views.generate_response, name='generate-response'),
    path('histories/', views.get_message_histories, name='get-message-histories'),
    path('histories/<int:id>/', views.message_history_details, name='message-history-details'),
    path('histories/<int:id>/rename/', views.rename_history, name='rename-history'),
    path('save-new-history-and-new-chat/', views.save_new_history_and_new_chat, name='save-new-history-and-new-chat'),
    path('filtered-data/', get_filtered_accounting_data, name='filtered-accounting-data'),  # NOUVEAU
]
