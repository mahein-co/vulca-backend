from rest_framework import serializers

from ocr.models import SourceFile


class SourceFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceFile
        fields = ['id', 'file', 'file_name', 'uploaded_at']
        read_only_fields = ['id', 'file_name', 'uploaded_at']

        


