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


# --- SÉRIALISEURS TECHNIQUES POUR SWAGGER/OPENAPI ---

class ChatRequestSerializer(serializers.Serializer):
    user_input = serializers.CharField(required=True, help_text="Message de l'utilisateur")
    message_history = serializers.IntegerField(required=False, help_text="ID de l'historique de discussion")
    project_id = serializers.IntegerField(required=False, help_text="ID du projet")
    filtered_data = serializers.JSONField(required=False, help_text="Données filtrées optionnelles")

class ChatSourceSerializer(serializers.Serializer):
    title = serializers.CharField()
    path = serializers.CharField()

class ChatResponseSerializer(serializers.Serializer):
    conversation = ChatMessageSerializer()
    sources = ChatSourceSerializer(many=True)
    suggested_filter = serializers.JSONField(required=False, allow_null=True)

class FilteredDataResponseSerializer(serializers.Serializer):
    filter = serializers.JSONField()
    chiffre_affaires = serializers.JSONField()
    charges = serializers.JSONField()
    resultat_net = serializers.JSONField()
    tresorerie = serializers.JSONField()
    tresorerie = serializers.JSONField()
    bilan = serializers.JSONField()

class EmptySerializer(serializers.Serializer):
    pass
