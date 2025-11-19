from rest_framework import serializers
from .models import Journal

class JournalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Journal
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
