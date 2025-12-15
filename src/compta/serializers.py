from rest_framework import serializers
from .models import Balance, Bilan, GrandLivre, Journal, CompteResultat

class JournalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Journal
        fields = [
            'id',
            'date',
            'numero_piece',
            'type_journal',
            'numero_compte',
            'libelle',
            'debit_ar',
            'credit_ar',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class GrandLivreSerializer(serializers.ModelSerializer):
    journal_source = serializers.CharField(source='journal.type_journal', read_only=True)
    solde_cumule = serializers.DecimalField(source='solde', max_digits=15, decimal_places=2, read_only=True)

    class Meta:
        model = GrandLivre
        fields = [
            'date',
            'journal_source',
            'numero_piece',
            'libelle',
            'debit',
            'credit',
            'solde_cumule',
        ]

        
class CompteSerializer(serializers.Serializer):
    numero_compte = serializers.CharField()
    libelle = serializers.CharField()


class BalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Balance
        fields = "__all__"

class BilanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bilan
        fields = '__all__'


class CompteResultatSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompteResultat
        fields = '__all__'