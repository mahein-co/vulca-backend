
from decimal import Decimal
from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.db.models import Sum, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from compta.models import GrandLivre, Bilan, CompteResultat

@api_view(["GET"])
@permission_classes([AllowAny])
def dashboard_indicators_view(request):
    """
    ENDPOINT OPTIMISÉ POUR DASHBOARD (V2)
    Calcule TOUS les indicateurs (16 KPIs) + Variations en un minimum de requêtes.
    Remplace les 16 appels HTTP séparés du frontend pour une performance maximale.
    """
    
    date_start_str = request.GET.get("date_start")
    date_end_str = request.GET.get("date_end")

    # --- HELPER: CALCULATE_ALL_KPI_FOR_PERIOD ---
    def calculate_all_kpis(d_start, d_end):
        if not d_start or not d_end:
            return {}
            
        filters = {"date__range": [d_start, d_end]}
        
        # ⚡ OPTIMISATION: Récupérer les soldes par classe en 1 requête groupée (ou quelques requêtes clés)
        # Plutôt que 50 requêtes, on récupère les sommes des comptes clés
        
        # Helper interne pour requêtes groupées
        def get_sum(prefix_list, type_bilan=None, sens="balance"):
            q = Q()
            for p in prefix_list:
                q |= Q(numero_compte__startswith=p)
            
            qs = GrandLivre.objects.filter(q, **filters)
            agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
            d = agg["d"] or Decimal("0.00")
            c = agg["c"] or Decimal("0.00")
            
            if sens == "debit": return d
            if sens == "credit": return c
            if sens == "solde_debit": return d - c
            if sens == "solde_credit": return c - d
            return c - d # Default solde credit
            
        # 1. DONNÉES DE BASE (CACHE)
        # On calcule les blocs masse par masse pour éviter de refaire les queries
        
        # VENTES & PRODUITS
        ca = get_sum(["70"], sens="solde_credit")
        total_produits = get_sum(["7"], sens="solde_credit")
        subventions = get_sum(["74"], sens="solde_credit")
        
        # CHARGES
        achats = get_sum(["60"], sens="solde_debit")
        charges_externes = get_sum(["61", "62"], sens="solde_debit")
        impots = get_sum(["63"], sens="solde_debit")
        personnel = get_sum(["64"], sens="solde_debit")
        charges_fi = get_sum(["66"], sens="solde_debit")
        dotations = get_sum(["68"], sens="solde_debit")
        total_charges = get_sum(["6"], sens="solde_debit")
        charges_exploit = achats + charges_externes + impots + personnel + dotations # Approx
        
        # RESULTATS
        ebe = (get_sum(["70", "71", "72", "73", "74"], sens="solde_credit")) - (achats + charges_externes + impots + personnel)
        resultat_net = total_produits - total_charges
        resultat_exploit = ebe - dotations + get_sum(["78"], sens="solde_credit")
        
        reprises = get_sum(["78"], sens="solde_credit")
        caf = resultat_net + dotations - reprises
        
        # BILAN (ACTIF/PASSIF)
        # Actif Courant: 3 (Stocks) + 4 (Créances) + 5 (Trésorerie Actif)
        stocks = get_sum(["3"], sens="solde_debit")
        creances = get_sum(["4"], sens="solde_debit") - get_sum(["401", "408", "419", "4457"], sens="solde_debit") # Simplifié: tout 4 sauf fournisseurs
        # Pour être plus précis sur créances clients (411)
        creances_clients = get_sum(["411"], sens="solde_debit")
        
        tresorerie_actif = get_sum(["5"], sens="solde_debit")
        actifs_courants = stocks + creances_clients + tresorerie_actif # Approx 
        
        total_actif = get_sum(["2", "3", "4", "5"], sens="solde_debit")
        
        # Passif
        capitaux_propres = get_sum(["10", "11", "12"], sens="solde_credit") + resultat_net
        dettes_fi = get_sum(["16"], sens="solde_credit") + get_sum(["512"], sens="solde_credit") # 512 credit = découvert
        
        dettes_fournisseurs = get_sum(["401"], sens="solde_credit")
        passifs_courants = dettes_fournisseurs + get_sum(["42", "43", "44"], sens="solde_credit") + get_sum(["512"], sens="solde_credit") # + Découverts
        
        # BFR
        bfr = stocks + creances_clients - dettes_fournisseurs
        
        # KPIs
        marge_brute = ca - achats 
        marge_nette = (resultat_net / ca * 100) if ca != 0 else 0
        marge_op = (resultat_exploit / ca * 100) if ca != 0 else 0
        
        roe = (resultat_net / capitaux_propres * 100) if capitaux_propres != 0 else 0
        roa = (resultat_net / total_actif * 100) if total_actif != 0 else 0
        
        current_ratio = (actifs_courants / passifs_courants) if passifs_courants != 0 else 0
        quick_ratio = ((actifs_courants - stocks) / passifs_courants) if passifs_courants != 0 else 0
        gearing = (dettes_fi / capitaux_propres) if capitaux_propres != 0 else 0
        
        rotation_stock = (get_sum(["607"], sens="solde_debit") / stocks) if stocks != 0 else 0
        duree_stock = (360 / rotation_stock) if rotation_stock != 0 else 0
        
        leverage_brut = (dettes_fi / ebe) if ebe != 0 else 0
        
        return {
            "ca": ca,
            "ebe": ebe,
            "resultat_net": resultat_net,
            "caf": caf,
            "bfr": bfr,
            "marge_brute": marge_brute,
            "marge_nette": marge_nette,
            "marge_operationnelle": marge_op,
            "tresorerie": get_sum(["5"], sens="solde_debit") - get_sum(["512"], sens="solde_credit"), # Trésorerie nette
            "roe": roe,
            "roa": roa,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "gearing": gearing,
            "rotation_stock": rotation_stock,
            "duree_stock_jours": duree_stock,
            "leverage_brut": leverage_brut,
            
            "actifs_courants": actifs_courants,
            "passifs_courants": passifs_courants,
            "stocks": stocks,
            "fonds_propres": capitaux_propres,
            "total_actif": total_actif,
            "dettes_financieres": dettes_fi,
            "cout_ventes": get_sum(["607"], sens="solde_debit"),
            "chiffre_affaire": ca,
            "charges_exploitation": charges_exploit,
            "resultat_operationnel": resultat_exploit
        }

    # CALCUL PÉRIODE ACTUELLE
    current = calculate_all_kpis(date_start_str, date_end_str)
    
    # CALCUL PÉRIODE PRÉCÉDENTE (Variation)
    previous = {}
    if date_start_str and date_end_str:
        try:
            d_start = datetime.strptime(date_start_str, '%Y-%m-%d').date()
            d_end = datetime.strptime(date_end_str, '%Y-%m-%d').date()
            delta_days = (d_end - d_start).days
            
            # Détermination période précédente simple (Année N-1 par défaut pour comparaison pertinente)
            # Sinon glissant
            if delta_days >= 360:
                prev_start = d_start - relativedelta(years=1)
                prev_end = d_end - relativedelta(years=1)
            else:
                prev_start = d_start - relativedelta(days=delta_days + 1)
                prev_end = d_start - relativedelta(days=1)
                
            previous = calculate_all_kpis(prev_start.strftime('%Y-%m-%d'), prev_end.strftime('%Y-%m-%d'))
        except:
            pass

    # PACKAGING RESPONSE
    def get_var(key, is_percent=False):
        curr_val = current.get(key, 0)
        prev_val = previous.get(key, 0)
        
        # Si c'est déjà un ratio (%)
        # Variation absolue ou relative?
        # Pour les montants -> relative (%)
        # Pour les taux (%) -> absolue (points)
        
        if is_percent:
            # Variation en points
            return float(curr_val - prev_val)
        else:
            # Variation en %
            if prev_val == 0: return None
            return float((curr_val - prev_val) / abs(prev_val) * 100)

    response_data = {
        # INDICATEURS CLÉS (Pour setIndicators)
        "ca": float(current.get("ca", 0)),
        "ebe": float(current.get("ebe", 0)),
        "resultat_net": float(current.get("resultat_net", 0)),
        "caf": float(current.get("caf", 0)),
        "bfr": float(current.get("bfr", 0)),
        "leverage": float(current.get("leverage_brut", 0)),
        "total_balance": float(current.get("total_actif", 0)), # Approx
        
        # RATIOS DASHBOARD (Objets complets pour setXData)
        "roe_data": { 
            "roe": float(current.get("roe", 0)), 
            "variation": get_var("roe", True),
            "resultat_net": float(current.get("resultat_net", 0)), 
            "fonds_propres": float(current.get("fonds_propres", 0))
        },
        "roa_data": { 
            "roa": float(current.get("roa", 0)), 
            "variation": get_var("roa", True),
            "resultat_net": float(current.get("resultat_net", 0)), 
            "total_actif": float(current.get("total_actif", 0))
        },
        "current_ratio_data": { 
            "current_ratio": float(current.get("current_ratio", 0)), 
            "variation": get_var("current_ratio", True),
            "actifs_courants": float(current.get("actifs_courants", 0)), 
            "passifs_courants": float(current.get("passifs_courants", 0))
        },
        "quick_ratio_data": { 
            "quick_ratio": float(current.get("quick_ratio", 0)), 
            "variation": get_var("quick_ratio", True),
            "actifs_courants": float(current.get("actifs_courants", 0)), 
            "stocks": float(current.get("stocks", 0)),
            "passifs_courants": float(current.get("passifs_courants", 0))
        },
        "gearing_data": { 
            "gearing": float(current.get("gearing", 0)), 
            "variation": get_var("gearing", True),
            "dettes_financieres": float(current.get("dettes_financieres", 0)), 
            "fonds_propres": float(current.get("fonds_propres", 0))
        },
        "rotation_stock_data": { 
            "rotation_stock": float(current.get("rotation_stock", 0)), 
            "variation": get_var("rotation_stock", True),
            "duree_stock_jours": float(current.get("duree_stock_jours", 0)),
            "cout_ventes": float(current.get("cout_ventes", 0)),
            "stocks": float(current.get("stocks", 0))
        },
        "marge_operationnelle_data": { 
            "marge_operationnelle": float(current.get("marge_operationnelle", 0)), 
            "variation": get_var("marge_operationnelle", True),
            "chiffre_affaire": float(current.get("chiffre_affaire", 0)),
            "charges_exploitation": float(current.get("charges_exploitation", 0)),
            "resultat_operationnel": float(current.get("resultat_operationnel", 0))
        },
        
        # VARIATIONS EN VRAC
        "variations": {
            "ca": get_var("ca"),
            "caf": get_var("caf"),
            "ebe": get_var("ebe"),
            "leverage": get_var("leverage_brut", True),
            "bfr": get_var("bfr"),
            "marge_brute": get_var("marge_brute"),
            "marge_nette": get_var("marge_nette", True),
            "tresorerie": get_var("tresorerie")
        },
        
        # VALEURS BRUTES COMPLEMENTAIRES
        "marge_brute": float(current.get("marge_brute", 0)),
        "marge_nette": float(current.get("marge_nette", 0)),
        "tresorerie": float(current.get("tresorerie", 0))
    }

    return Response(response_data)
