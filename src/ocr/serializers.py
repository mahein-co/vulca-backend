from rest_framework import serializers

from ocr.models import FileSource


class FileSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileSource
        fields = '__all__'
        read_only_fields = ['id', 'file_name', 'uploaded_at']

        


