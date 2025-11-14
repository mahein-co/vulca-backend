from rest_framework import serializers

from ocr.models import FileSource


class FileSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileSource
        fields = ['id', 'file', 'file_name', 'uploaded_at']
        read_only_fields = ['id', 'file_name', 'uploaded_at']

        


