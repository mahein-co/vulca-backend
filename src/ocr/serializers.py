from rest_framework import serializers
from compta.serializers import JournalSerializer
from ocr.models import FileSource, FormSource


class FileSourceSerializer(serializers.ModelSerializer):
    journals = JournalSerializer(many=True, read_only=True)
    
    class Meta:
        model = FileSource
        fields = '__all__'
        read_only_fields = ['id', 'file_name', 'uploaded_at']

        


class FormSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormSource
        fields = '__all__'
        read_only_fields = ["id", "created_at"]

