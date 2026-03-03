from django.db.models import Q, Sum, Max, Case, When, DecimalField
from decimal import Decimal
from .models import Bilan, CompteResultat

def get_latest_bilan_sum(project_id, d_start, d_end, prefix_list=None, categorie=None, type_bilan=None, cumulative=False):
    """
    Calcule la somme des montants du Bilan en prenant uniquement le dernier enregistrement 
    disponible pour chaque compte dans la période donnée.
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

    # Récupérer la dernière date pour chaque compte
    latest_dates = Bilan.objects.filter(q).values('numero_compte').annotate(max_d=Max('date'))
    
    total = Decimal('0.00')
    for item in latest_dates:
        # On récupère le montant du dernier record pour ce compte spécifique
        last_rec = Bilan.objects.filter(
            project_id=project_id, 
            numero_compte=item['numero_compte'], 
            date=item['max_d']
        ).first()
        if last_rec: 
            total += last_rec.montant_ar
            
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
    Calcule le résultat net (Produits - Charges) pour la période.
    """
    filters = {"project_id": project_id, "date__range": [d_start, d_end]}
    
    # On prend la dernière date dispo pour éviter les doubles comptages si plusieurs imports
    latest_res_date = CompteResultat.objects.filter(**filters).aggregate(Max('date'))['date__max']
    
    if not latest_res_date:
        return Decimal("0.00")
        
    agg = CompteResultat.objects.filter(project_id=project_id, date=latest_res_date).aggregate(
        prod=Sum(Case(When(nature="PRODUIT", then="montant_ar"), default=0, output_field=DecimalField())),
        char=Sum(Case(When(nature="CHARGE", then="montant_ar"), default=0, output_field=DecimalField()))
    )
    return (agg["prod"] or Decimal("0.00")) - (agg["char"] or Decimal("0.00"))
