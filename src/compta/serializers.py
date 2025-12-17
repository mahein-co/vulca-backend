from rest_framework import serializers
from .models import Journal, Bilan,CompteResultat
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

class BilanSerializer(serializers.ModelSerializer):
    categorie = serializers.CharField()

    class Meta:
        model = Bilan
        fields = "__all__"
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_categorie(self, value):
        # Supprime espaces invisibles
        return value.strip()
    
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
        fields = "__all__"
        read_only_fields = ['id', 'created_at', 'updated_at']

class ChiffreAffaireSerializer(serializers.Serializer):
    chiffre_affaire = serializers.DecimalField(
        max_digits=15, decimal_places=2, read_only=True
    )

class EbeSerializer(serializers.Serializer):
    produits_exploitation = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    charges_exploitation = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    ebe = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )

class ResultatNetSerializer(serializers.Serializer):
    produits = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_exploitation = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_financieres = serializers.DecimalField(max_digits=15, decimal_places=2)
    produits_financiers = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_exceptionnelles = serializers.DecimalField(max_digits=15, decimal_places=2)
    produits_exceptionnels = serializers.DecimalField(max_digits=15, decimal_places=2)
    impots_benefices = serializers.DecimalField(max_digits=15, decimal_places=2)
    resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2)

class BfrSerializer(serializers.Serializer):
    actif_circulant = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    passif_circulant = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    bfr = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )

class CafSerializer(serializers.Serializer):
    resultat_net = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    dotations = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    reprises = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )
    caf = serializers.DecimalField(
        max_digits=15, decimal_places=2
    )

class LeverageSerializer(serializers.Serializer):
    total_endettement = serializers.DecimalField(max_digits=15, decimal_places=2)
    ebe = serializers.DecimalField(max_digits=15, decimal_places=2)
    leverage_brut = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class AnnuiteCafSerializer(serializers.Serializer):
    annuite_emprunt = serializers.DecimalField(max_digits=15, decimal_places=2)
    caf = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_annuite_caf = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class DetteLmtCafSerializer(serializers.Serializer):
    dette_lmt = serializers.DecimalField(max_digits=15, decimal_places=2)
    caf = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_dette_lmt_caf = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class MargeNetteSerializer(serializers.Serializer):
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_resultat_ca = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class ChargeEbeSerializer(serializers.Serializer):
    charge_financiere = serializers.DecimalField(max_digits=15, decimal_places=2)
    ebe = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_charge_ebe = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class ChargeCaSerializer(serializers.Serializer):
    charge_financiere = serializers.DecimalField(max_digits=15, decimal_places=2)
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_charge_ca = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class MargeEndettementSerializer(serializers.Serializer):
    dette_cmlt = serializers.DecimalField(max_digits=15, decimal_places=2)
    fonds_propres = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio_marge_endettement = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )

class RoeSerializer(serializers.Serializer):
    roe = serializers.DecimalField(max_digits=5, decimal_places=2)
    variation = serializers.DecimalField(max_digits=5, decimal_places=2)

class RoaSerializer(serializers.Serializer):
    roa = serializers.DecimalField(max_digits=5, decimal_places=2)
    variation = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)

class CurrentRatioSerializer(serializers.Serializer):
    current_ratio = serializers.DecimalField(max_digits=6, decimal_places=2)

class QuickRatioSerializer(serializers.Serializer):
    quick_ratio = serializers.DecimalField(max_digits=6, decimal_places=2)

class GearingSerializer(serializers.Serializer):
    gearing = serializers.DecimalField(max_digits=6, decimal_places=2)

class RotationStockSerializer(serializers.Serializer):
    rotation_stock = serializers.DecimalField(max_digits=6, decimal_places=2)
    duree_stock_jours = serializers.DecimalField(
        max_digits=6, decimal_places=0, required=False
    )

class MargeOperationnelleSerializer(serializers.Serializer):
    marge_operationnelle = serializers.DecimalField(max_digits=6, decimal_places=2)

class RepartitionResultatSerializer(serializers.Serializer):
    label = serializers.CharField()
    montant = serializers.DecimalField(max_digits=15, decimal_places=2)
    pourcentage = serializers.DecimalField(max_digits=5, decimal_places=2)

class EvolutionTresorerieSerializer(serializers.Serializer):
    periode = serializers.CharField()
    tresorerie = serializers.DecimalField(max_digits=15, decimal_places=2)

class TopCompteSerializer(serializers.Serializer):
    numero_compte = serializers.CharField()
    libelle = serializers.CharField()
    montant_total = serializers.DecimalField(max_digits=20, decimal_places=2)

class EvolutionChiffreAffaireSerializer(serializers.Serializer):
    periode = serializers.CharField()
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
