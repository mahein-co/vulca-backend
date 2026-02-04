from django.urls import path
from chatbot import views

urlpatterns = [
    path('messages/', views.generate_response, name='messages'),
    path('histories/', views.get_message_histories, name='histories'),
    path('new-chat/', views.save_new_history_and_new_chat, name='new-chat'),
    path('histories/<int:id>/', views.message_history_details, name='history'),
    path('histories/<int:id>/rename/', views.rename_history, name='rename_history'),
]
