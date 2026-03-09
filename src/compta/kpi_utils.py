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

    # 1. Identifier la dernière date connue pour chaque numéro de compte
    latest_dates = Bilan.objects.filter(q).values('numero_compte').annotate(max_d=Max('date'))
    
    total = Decimal('0.00')
    for item in latest_dates:
        # 2. Sommer TOUS les montants pour ce compte à CETTE date précise
        # (Indispensable si un bilan est importé en plusieurs lignes ou fichiers)
        day_sum = Bilan.objects.filter(
            project_id=project_id, 
            numero_compte=item['numero_compte'], 
            date=item['max_d']
        ).aggregate(s=Sum('montant_ar'))['s'] or Decimal('0.00')
        total += day_sum
            
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
    
    # 2. Récupération de la base CP (Classe 1 hors résultat 12) via le helper unifié
    base_cp = get_latest_bilan_sum(
        project_id, d_start, d_end, 
        prefix_list=["10", "11"], 
        categorie="CAPITAUX_PROPRES", 
        cumulative=True
    )
    
    return base_cp + rn

def get_chiffre_affaire(project_id, d_start, d_end):
    """Calcul du CA (Ventes 70)"""
    return get_cr_sum(project_id, d_start, d_end, prefix_list=["70"], nature="PRODUIT")

def get_ebe(project_id, d_start, d_end):
    """Calcul de l'EBE (Excédent Brut d'Exploitation)"""
    # Formule : (70+71+72+74) - (60+61+62+63+64)
    produits = get_cr_sum(project_id, d_start, d_end, prefix_list=["70", "71", "72", "74"], nature="PRODUIT")
    charges = get_cr_sum(project_id, d_start, d_end, prefix_list=["60", "61", "62", "63", "64"], nature="CHARGE")
    return produits - charges
