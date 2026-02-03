from rest_framework import serializers
from .models import Balance, Bilan, GrandLivre, Journal, CompteResultat, Project, ProjectAccess


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'created_by', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class ProjectAccessSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source='user.email', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)

    class Meta:
        model = ProjectAccess
        fields = ['id', 'user', 'user_name', 'user_email', 'project', 'project_name', 'status', 'requested_at', 'approved_at', 'approved_by']
        read_only_fields = ['id', 'user', 'project', 'requested_at', 'approved_at', 'approved_by']

    def get_user_name(self, obj):
        return obj.user.name or obj.user.username or obj.user.email


class ProjectListSerializer(serializers.ModelSerializer):
    """Serializer pour lister les projets avec statut d'accès pour l'utilisateur courant"""
    access_status = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'created_at', 'access_status']

    def get_access_status(self, obj):
        user = self.context['request'].user
        
        # Check if user has admin role
        if user.role == 'admin':
            return 'admin'
        
        # Check if user created this project
        if obj.created_by == user:
            return 'admin'
            
        # Check ProjectAccess for regular users
        try:
            access = ProjectAccess.objects.get(user=user, project=obj)
            return access.status  # 'approved', 'pending', or 'rejected'
        except ProjectAccess.DoesNotExist:
            return None  # No access requested yet


class JournalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Journal
        fields = [
            'id',
            'project',
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
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']

class BilanSerializer(serializers.ModelSerializer):
    categorie = serializers.CharField()

    class Meta:
        model = Bilan
        fields = "__all__"
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']

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
            'project',
            'journal_source',
            'numero_piece',
            'libelle',
            'debit',
            'credit',
            'solde_cumule',
        ]
        read_only_fields = ['project']

        
class CompteSerializer(serializers.Serializer):
    numero_compte = serializers.CharField()
    libelle = serializers.CharField()


class BalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Balance
        fields = "__all__"
        read_only_fields = ['project']


class CompteResultatSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompteResultat
        fields = "__all__"
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']

class ChiffreAffaireSerializer(serializers.Serializer):
    numero_compte = serializers.CharField()
    total_credit = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_debit = serializers.DecimalField(max_digits=15, decimal_places=2)
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)

class EbeSerializer(serializers.Serializer):
    chiffre_affaires = serializers.DecimalField(max_digits=15, decimal_places=2)
    subventions = serializers.DecimalField(max_digits=15, decimal_places=2)
    achats = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_externes = serializers.DecimalField(max_digits=15, decimal_places=2)
    impots_taxes = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_personnel = serializers.DecimalField(max_digits=15, decimal_places=2)
    ebe = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)

class ResultatNetSerializer(serializers.Serializer):
    produits = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_exploitation = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_financieres = serializers.DecimalField(max_digits=15, decimal_places=2)
    produits_financiers = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_exceptionnelles = serializers.DecimalField(max_digits=15, decimal_places=2)
    produits_exceptionnels = serializers.DecimalField(max_digits=15, decimal_places=2)
    impots_benefices = serializers.DecimalField(max_digits=15, decimal_places=2)
    resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2)
    previous_resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    variation_percentage = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    resultat_net_cumule = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)

class BfrSerializer(serializers.Serializer):
    stocks = serializers.DecimalField(max_digits=15, decimal_places=2)
    creances_clients = serializers.DecimalField(max_digits=15, decimal_places=2)
    autres_creances = serializers.DecimalField(max_digits=15, decimal_places=2)
    dettes_fournisseurs = serializers.DecimalField(max_digits=15, decimal_places=2)
    autres_dettes = serializers.DecimalField(max_digits=15, decimal_places=2)
    bfr = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)

class CafSerializer(serializers.Serializer):
    resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2)
    dotations_amort_provisions = serializers.DecimalField(max_digits=15, decimal_places=2)
    reprises_amort_provisions = serializers.DecimalField(max_digits=15, decimal_places=2)
    caf = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)

class LeverageSerializer(serializers.Serializer):
    total_endettement = serializers.DecimalField(max_digits=15, decimal_places=2)
    ebe = serializers.DecimalField(max_digits=15, decimal_places=2)
    leverage_brut = serializers.DecimalField(max_digits=10, decimal_places=2)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class AnnuiteCafSerializer(serializers.Serializer):
    annuite_emprunt = serializers.DecimalField(max_digits=15, decimal_places=2)
    caf = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio = serializers.DecimalField(max_digits=10, decimal_places=2)
    alerte = serializers.BooleanField()

class DetteLmtCafSerializer(serializers.Serializer):
    dette_lmt = serializers.DecimalField(max_digits=15, decimal_places=2)
    caf = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio = serializers.DecimalField(max_digits=10, decimal_places=2)
    alerte = serializers.BooleanField()

class MargeNetteSerializer(serializers.Serializer):
    resultat_net = serializers.DecimalField(max_digits=15, decimal_places=2)
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    marge_nette = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class ChargeEbeSerializer(serializers.Serializer):
    charge_financiere = serializers.DecimalField(max_digits=15, decimal_places=2)
    ebe = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio = serializers.DecimalField(max_digits=5, decimal_places=2)
    alerte = serializers.BooleanField()

class ChargeCaSerializer(serializers.Serializer):
    charge_financiere = serializers.DecimalField(max_digits=15, decimal_places=2)
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio = serializers.DecimalField(max_digits=5, decimal_places=2)
    alerte = serializers.BooleanField()

class MargeEndettementSerializer(serializers.Serializer):
    dette_cmlt = serializers.DecimalField(max_digits=15, decimal_places=2)
    fonds_propres = serializers.DecimalField(max_digits=15, decimal_places=2)
    ratio = serializers.DecimalField(max_digits=5, decimal_places=2)
    alerte = serializers.BooleanField()

class CurrentRatioSerializer(serializers.Serializer):
    actifs_courants = serializers.DecimalField(max_digits=15, decimal_places=2)
    passifs_courants = serializers.DecimalField(max_digits=15, decimal_places=2)
    current_ratio = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class QuickRatioSerializer(serializers.Serializer):
    actifs_courants = serializers.DecimalField(max_digits=15, decimal_places=2)
    stocks = serializers.DecimalField(max_digits=15, decimal_places=2)
    passifs_courants = serializers.DecimalField(max_digits=15, decimal_places=2)
    quick_ratio = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class GearingSerializer(serializers.Serializer):
    dettes_financieres = serializers.DecimalField(max_digits=15, decimal_places=2)
    fonds_propres = serializers.DecimalField(max_digits=15, decimal_places=2)
    gearing = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class RotationStockSerializer(serializers.Serializer):
    cout_ventes = serializers.DecimalField(max_digits=15, decimal_places=2)
    stocks = serializers.DecimalField(max_digits=15, decimal_places=2)
    rotation_stock = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    duree_stock_jours = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class MargeOperationnelleSerializer(serializers.Serializer):
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    charges_exploitation = serializers.DecimalField(max_digits=15, decimal_places=2)
    resultat_operationnel = serializers.DecimalField(max_digits=15, decimal_places=2)
    marge_operationnelle = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class RepartitionResultatSerializer(serializers.Serializer):
    label = serializers.CharField()
    montant = serializers.DecimalField(max_digits=15, decimal_places=2)
    pourcentage = serializers.DecimalField(max_digits=5, decimal_places=2)

class TVASerializer(serializers.Serializer):
    tva_collectee = serializers.DecimalField(max_digits=15, decimal_places=2)
    tva_deductible = serializers.DecimalField(max_digits=15, decimal_places=2)
    tva_nette = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation_collectee = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation_deductible = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    variation_nette = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class MargeBruteSerializer(serializers.Serializer):
    ventes = serializers.DecimalField(max_digits=15, decimal_places=2)
    achats = serializers.DecimalField(max_digits=15, decimal_places=2)
    marge_brute = serializers.DecimalField(max_digits=15, decimal_places=2)
    variation = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)

class DelaisClientsSerializer(serializers.Serializer):
    creances_clients = serializers.DecimalField(max_digits=15, decimal_places=2)
    chiffre_affaire = serializers.DecimalField(max_digits=15, decimal_places=2)
    delais_jours = serializers.DecimalField(max_digits=10, decimal_places=1, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=1, required=False, allow_null=True)

class DelaisFournisseursSerializer(serializers.Serializer):
    dettes_fournisseurs = serializers.DecimalField(max_digits=15, decimal_places=2)
    achats = serializers.DecimalField(max_digits=15, decimal_places=2)
    delais_jours = serializers.DecimalField(max_digits=10, decimal_places=1, required=False, allow_null=True)
    variation = serializers.DecimalField(max_digits=10, decimal_places=1, required=False, allow_null=True)
