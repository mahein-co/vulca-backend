from rest_framework import serializers
from compta.serializers import JournalSerializer
from ocr.models import FileSource, FormSource


class FileSourceSerializer(serializers.ModelSerializer):
    journals = JournalSerializer(many=True, read_only=True)

    class Meta:
        model = FileSource
        fields = '__all__'
        read_only_fields = ['id', 'file_name', 'uploaded_at', 'uploaded_by']
        extra_kwargs = {
            'project': {'required': True},   # rendre project obligatoire en entrée
        }

    def validate(self, attrs):
        # s'assurer que project est fourni explicitement (sécurité supplémentaire)
        if self.instance is None and 'project' not in attrs:
            raise serializers.ValidationError({"project": "Le champ project est requis."})
        return super().validate(attrs)


class FormSourceSerializer(serializers.ModelSerializer):

    class Meta:
        model = FormSource
        fields = '__all__'
        read_only_fields = ["id", "created_at", "uploaded_by"]
        extra_kwargs = {
            'project': {'required': True},
        }

    def validate(self, attrs):
        if self.instance is None and 'project' not in attrs:
            raise serializers.ValidationError({"project": "Le champ project est requis."})
        return super().validate(attrs)


# --- SÉRIALISEURS TECHNIQUES POUR SWAGGER/OPENAPI ---

class OcrExtractResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    type_document = serializers.CharField()
    ocr_brut = serializers.CharField()
    extracted_json = serializers.JSONField()

class ExcelAnalyzeResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    file_name = serializers.CharField()
    total_rows = serializers.IntegerField()
    extraction_method = serializers.CharField()
    sheets = serializers.JSONField()

class ExcelValidateRequestSerializer(serializers.Serializer):
    unmapped_rows = serializers.ListField(child=serializers.DictField())

class PieceListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
    pieces = serializers.ListField(child=serializers.DictField())
