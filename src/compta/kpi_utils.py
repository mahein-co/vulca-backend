from django.db.models import Q, Sum, Max, Case, When, DecimalField
from decimal import Decimal
from .models import Bilan, CompteResultat

def get_latest_bilan_sum(project_id, d_start, d_end, prefix_list=None, categorie=None, type_bilan=None, cumulative=False):
    """
    Calcule la somme des montants du Bilan en prenant TOUS les enregistrements
    disponibles à la date la plus RÉCENTE pour chaque compte.
    """
    if cumulative:
        q = Q(project_id=project_id, date__lte=d_end)
    else:
        q = Q(project_id=project_id, date__range=[d_start, d_end])
    
    if prefix_list:
        pq = Q()
        for p in prefix_list: pq |= Q(numero_compte__startswith=p)
        q &= pq
    
    if categorie: q &= Q(categorie=categorie)
    if type_bilan: q &= Q(type_bilan=type_bilan)

    from django.db.models import Subquery, OuterRef

    # 1. Identifier la dernière date connue pour chaque numéro de compte via une sous-requête
    max_date_subquery = Bilan.objects.filter(
        project_id=project_id,
        numero_compte=OuterRef('numero_compte'),
        # On réapplique les mêmes filtres de base que dans 'q' pour la cohérence
        date__lte=d_end if cumulative else d_end,
    )
    if not cumulative:
        max_date_subquery = max_date_subquery.filter(date__gte=d_start)
    
    # On trie par date décroissante pour prendre la plus récente
    max_date_subquery = max_date_subquery.order_by('-date').values('date')[:1]

    # 2. Sommer TOUS les montants dont la date correspond à la date maximale pour ce compte
    total = Bilan.objects.filter(
        q,
        date=Subquery(max_date_subquery)
    ).aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
            
    return total

def get_cr_sum(project_id, d_start, d_end, prefix_list=None, nature=None):
    """
    Calcule la somme des montants du Compte de Résultat pour la période donnée.
    """
    q = Q(project_id=project_id, date__range=[d_start, d_end])
    if prefix_list:
        pq = Q()
        for p in prefix_list: pq |= Q(numero_compte__startswith=p)
        q &= pq
    if nature: q &= Q(nature=nature)

    total = CompteResultat.objects.filter(q).aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    return total

def get_resultat_net(project_id, d_start, d_end):
    """
    Calcule le résultat net (Produits Classe 7 - Charges Classe 6) pour la période.
    Indépendant des catégories et uniquement basé sur les classes comptables (Source de vérité).
    """
    filters = {"project_id": project_id, "date__range": [d_start, d_end]}
    
    # On filtre strictement : Classe 7 (Produits) - Classe 6 (Charges)
    agg = CompteResultat.objects.filter(**filters).aggregate(
        prod=Sum(Case(When(numero_compte__startswith="7", then="montant_ar"), default=0, output_field=DecimalField())),
        char=Sum(Case(When(numero_compte__startswith="6", then="montant_ar"), default=0, output_field=DecimalField()))
    )
    return (agg["prod"] or Decimal("0.00")) - (agg["char"] or Decimal("0.00"))

def get_capitaux_propres(project_id, d_start, d_end):
    """
    Calcule les Capitaux Propres totaux = Base (Bilan Cat=CAPITAUX_PROPRES) + Résultat Net (Calculé).
    Filtre de sécurité sur les préfixes 10 (Capital/Réserves) et 11 (Report à nouveau).
    """
    # 1. Calcul du Résultat Net sur la période
    rn = get_resultat_net(project_id, d_start, d_end)
    
    # 2. Récupération de la base CP via le helper unifié
    base_cp = get_latest_bilan_sum(
        project_id, d_start, d_end, 
        categorie="CAPITAUX_PROPRES", 
        cumulative=True
    )
    
    return base_cp + rn

def get_chiffre_affaire(project_id, d_start, d_end):
    """
    Calcul du CA (Ventes) : comptes 70, 701, 702, 703, 704, 705, 706, 707.
    On utilise startswith("70") pour couvrir 70, 701...707...70x.
    Pas de filtre sur nature car le numéro de compte est la source de vérité.
    """
    return get_cr_sum(project_id, d_start, d_end, prefix_list=["70"])


def get_ebe(project_id, d_start, d_end):
    """Calcul de l'EBE (Excédent Brut d'Exploitation)"""
    # Formule : (70+71+72+74) - (60+61+62+63+64)
    # Suppression du filtre sur nature car les numéros de comptes sont prioritaires
    produits = get_cr_sum(project_id, d_start, d_end, prefix_list=["70", "71", "72", "74"])
    charges = get_cr_sum(project_id, d_start, d_end, prefix_list=["60", "61", "62", "63", "64"])
    return produits - charges
