from rest_framework import serializers

from chatbot.models import ChatMessage, DocumentPage, Document, MessageHistory


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        #fields = ['id', 'user', 'user_input', 'ai_response', 'timestamp', 'updated_at']
        fields = ['id', 'user_input', 'ai_response', 'timestamp', 'updated_at']
        read_only_fields = ['id', 'timestamp', 'updated_at']


class MessageHistorySerializer(serializers.ModelSerializer):
    chat_messages = ChatMessageSerializer(many=True, read_only=True)
    class Meta:
        model = MessageHistory
        #fields = "__all__"
        fields = ['id', 'title', 'project', 'created_at', 'updated_at', 'chat_messages'] 
        read_only_fields = ['id', 'created_at', 'updated_at']




class DocumentPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentPage
        fields = "__all__"
        read_only_fields = ["id", ]



class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = "__all__"
        read_only_fields = ["id", "created_at"]
