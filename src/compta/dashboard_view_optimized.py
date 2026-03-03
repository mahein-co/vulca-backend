
from decimal import Decimal
from datetime import datetime, timedelta

from django.db.models import Sum, Q, Max
import traceback
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from compta.models import GrandLivre, Bilan, CompteResultat
from compta.kpi_utils import get_latest_bilan_sum, get_cr_sum, get_resultat_net

from compta.permissions import HasProjectAccess
from rest_framework.permissions import IsAuthenticated

@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def dashboard_indicators_view(request):
    """
    ENDPOINT OPTIMISÉ POUR DASHBOARD (V2)
    Calcule TOUS les indicateurs (16 KPIs) + Variations en un minimum de requêtes.
    """
    # 1. RÉCUPÉRATION DU PROJET (Priorité: Header > Param > Request)
    header_project_id = request.headers.get('X-Project-ID')
    param_project_id = request.query_params.get('project_id')
    project_id = header_project_id or param_project_id or getattr(request, 'project_id', None)
    
    # Secure project_id type
    try:
        project_id = int(project_id) if project_id else None
    except (ValueError, TypeError):
        project_id = None

    date_start_str = request.GET.get("date_start")
    date_end_str = request.GET.get("date_end")

    # --- HELPERS: DATA FETCHING ---
    def calculate_all_kpis(d_start, d_end):
        if not d_start or not d_end:
            return {}
            
        def get_sum_cr(prefix_list):
            return get_cr_sum(project_id, d_start, d_end, prefix_list=prefix_list)

        def get_sum_bilan(prefix_list, type_bilan=None, cumulative=False):
            return get_latest_bilan_sum(project_id, d_start, d_end, prefix_list=prefix_list, type_bilan=type_bilan, cumulative=cumulative)

        from compta.models import Balance
        def get_total_balance_live(d_start, d_end):
            # Utiliser le même filtre que BalanceModal (date_range) pour avoir des valeurs cohérentes.
            # BalanceModal filtre avec date__range=[date_start, date_end]
            qs = Balance.objects.filter(project_id=project_id, date__range=[d_start, d_end])
            # Agréger le total débit (= total débit de la balance générale)
            res = qs.aggregate(
                total_debit=Sum("total_debit"),
                total_credit=Sum("total_credit")
            )
            debit = res["total_debit"] or Decimal("0.00")
            return debit

        try:
            # 1. CALCULS DES MASSES
            ca = get_sum_cr(["70"])
            total_produits = get_sum_cr(["7"])
            
            achats = get_sum_cr(["60"])
            charges_externes = get_sum_cr(["61", "62"])
            impots = get_sum_cr(["63"])
            personnel = get_sum_cr(["64"])
            charges_fi = get_sum_cr(["66"])
            dotations = get_sum_cr(["68"])
            total_charges = get_sum_cr(["6"])
            charges_exploit = achats + charges_externes + impots + personnel + dotations 
            
            ebe = (get_sum_cr(["70", "71", "72", "73", "74"])) - (achats + charges_externes + impots + personnel)
            resultat_net = total_produits - total_charges
            resultat_exploit = ebe - dotations + get_sum_cr(["78"])
            
            reprises = get_sum_cr(["78"])
            caf = resultat_net + dotations - reprises
            
            # BILAN
            stocks = get_sum_bilan(["3"], type_bilan="ACTIF")
            creances_clients = get_sum_bilan(["411"], type_bilan="ACTIF")
            tresorerie_actif = get_sum_bilan(["5"], type_bilan="ACTIF")
            actifs_courants = stocks + creances_clients + tresorerie_actif 
            
            # ⚡ [LIVE FIX] : Utiliser Balance pour le total_balance du dashboard (même logique que BalanceModal)
            total_balance_live = get_total_balance_live(d_start, d_end)
            if total_balance_live > 0:
                total_actif = total_balance_live
            else:
                total_actif = get_sum_bilan([""], type_bilan="ACTIF")
            
            capitaux_propres = get_sum_bilan(["10", "11", "12"], type_bilan="PASSIF") + resultat_net
            dettes_fi = get_sum_bilan(["16"], type_bilan="PASSIF") + get_sum_bilan(["512"], type_bilan="PASSIF")
            dettes_fournisseurs = get_sum_bilan(["401"], type_bilan="PASSIF")
            passifs_courants = dettes_fournisseurs + get_sum_bilan(["42", "43", "44"], type_bilan="PASSIF") + get_sum_bilan(["512"], type_bilan="PASSIF")
            
            bfr = stocks + creances_clients - dettes_fournisseurs
            
            # 2. CALCULS DES RATIOS
            marge_brute = ca - achats 
            marge_nette = (resultat_net / ca * 100) if ca != 0 else 0
            marge_op = (resultat_exploit / ca * 100) if ca != 0 else 0
            
            roe = (resultat_net / capitaux_propres * 100) if capitaux_propres != 0 else 0
            roa = (resultat_net / total_actif * 100) if total_actif != 0 else 0
            
            current_ratio = (actifs_courants / passifs_courants) if passifs_courants != 0 else 0
            quick_ratio = ((actifs_courants - stocks) / passifs_courants) if passifs_courants != 0 else 0
            gearing = (dettes_fi / capitaux_propres) if capitaux_propres != 0 else 0
            
            rotation_stock = (get_sum_cr(["607"]) / stocks) if stocks != 0 else 0
            duree_stock = (360 / rotation_stock) if rotation_stock != 0 else 0
            leverage_brut = (dettes_fi / ebe) if ebe != 0 else 0

            # Ratios exports spécifiques
            annuite_caf = (dotations / caf) if caf != 0 else 0
            fi_ebe = (charges_fi / ebe) if ebe != 0 else 0
            fi_ca = (charges_fi / ca) if ca != 0 else 0

            return {
                "ca": ca, "ebe": ebe, "resultat_net": resultat_net, "caf": caf, "bfr": bfr,
                "marge_brute": marge_brute, "marge_nette": marge_nette, "marge_operationnelle": marge_op,
                "tresorerie": get_sum_bilan(["5"], type_bilan="ACTIF") - get_sum_bilan(["512"], type_bilan="PASSIF"),
                "roe": roe, "roa": roa, "current_ratio": current_ratio, "quick_ratio": quick_ratio,
                "gearing": gearing, "rotation_stock": rotation_stock, "duree_stock_jours": duree_stock,
                "leverage_brut": leverage_brut, 
                "annuite_caf": annuite_caf, "fi_ebe": fi_ebe, "fi_ca": fi_ca,
                
                "actifs_courants": actifs_courants, "passifs_courants": passifs_courants, "stocks": stocks,
                "fonds_propres": capitaux_propres, "total_actif": total_actif, "dettes_financieres": dettes_fi,
                "cout_ventes": get_sum_cr(["607"]), "chiffre_affaire": ca, "charges_exploitation": charges_exploit,
                "resultat_operationnel": resultat_exploit
            }
        except Exception as e:
            print(f"[ERROR] calculate_all_kpis failed: {e}")
            traceback.print_exc()
            return {}

    # EXECUTION
    current = calculate_all_kpis(date_start_str, date_end_str)
    
    previous = {}
    if date_start_str and date_end_str:
        try:
            d_start = datetime.strptime(date_start_str, '%Y-%m-%d').date()
            d_end = datetime.strptime(date_end_str, '%Y-%m-%d').date()
            delta = (d_end - d_start).days
            
            # Simple fallback for previous period without relativedelta
            if delta >= 360:
                # Approximately one year back
                try:
                    p_start = d_start.replace(year=d_start.year - 1)
                    p_end = d_end.replace(year=d_end.year - 1)
                except ValueError: # Feb 29th case
                    p_start = d_start.replace(year=d_start.year - 1, day=28)
                    p_end = d_end.replace(year=d_end.year - 1, day=28)
            else:
                p_start = d_start - timedelta(days=delta + 1)
                p_end = d_start - timedelta(days=1)
                
            previous = calculate_all_kpis(p_start.strftime('%Y-%m-%d'), p_end.strftime('%Y-%m-%d'))
        except Exception:
            pass

    def get_v(key, is_p=False):
        try:
            c, p = Decimal(str(current.get(key, 0))), Decimal(str(previous.get(key, 0)))
            if is_p: return float(c - p)
            if p == 0: return None
            return float((c - p) / abs(p) * 100)
        except: return None

    try:
        data = {
            "ca": float(current.get("ca", 0)),
            "ebe": float(current.get("ebe", 0)),
            "resultat_net": float(current.get("resultat_net", 0)),
            "caf": float(current.get("caf", 0)),
            "bfr": float(current.get("bfr", 0)),
            "leverage": float(current.get("leverage_brut", 0)),
            "total_balance": float(current.get("total_actif", 0)),

            "roe_data": { "roe": float(current.get("roe", 0)), "variation": get_v("roe", True), "resultat_net": float(current.get("resultat_net", 0)), "fonds_propres": float(current.get("fonds_propres", 0)) },
            "roa_data": { "roa": float(current.get("roa", 0)), "variation": get_v("roa", True), "resultat_net": float(current.get("resultat_net", 0)), "total_actif": float(current.get("total_actif", 0)) },
            "current_ratio_data": { "current_ratio": float(current.get("current_ratio", 0)), "variation": get_v("current_ratio", True), "actifs_courants": float(current.get("actifs_courants", 0)), "passifs_courants": float(current.get("passifs_courants", 0)) },
            "quick_ratio_data": { "quick_ratio": float(current.get("quick_ratio", 0)), "variation": get_v("quick_ratio", True), "actifs_courants": float(current.get("actifs_courants", 0)), "stocks": float(current.get("stocks", 0)), "passifs_courants": float(current.get("passifs_courants", 0)) },
            "gearing_data": { "gearing": float(current.get("gearing", 0)), "variation": get_v("gearing", True), "dettes_financieres": float(current.get("dettes_financieres", 0)), "fonds_propres": float(current.get("fonds_propres", 0)) },
            "rotation_stock_data": { "rotation_stock": float(current.get("rotation_stock", 0)), "variation": get_v("rotation_stock", True), "duree_stock_jours": float(current.get("duree_stock_jours", 0)), "cout_ventes": float(current.get("cout_ventes", 0)), "stocks": float(current.get("stocks", 0)) },
            "marge_operationnelle_data": { "marge_operationnelle": float(current.get("marge_operationnelle", 0)), "variation": get_v("marge_operationnelle", True), "chiffre_affaire": float(current.get("chiffre_affaire", 0)), "charges_exploitation": float(current.get("charges_exploitation", 0)), "resultat_operationnel": float(current.get("resultat_operationnel", 0)) },

            "variations": { "ca": get_v("ca"), "caf": get_v("caf"), "ebe": get_v("ebe"), "leverage": get_v("leverage_brut", True), "bfr": get_v("bfr"), "marge_brute": get_v("marge_brute"), "marge_nette": get_v("marge_nette", True), "tresorerie": get_v("tresorerie") },
            
            "ratios": {
                "annuite_caf": { "value": float(current.get("annuite_caf", 0)), "alerte": float(current.get("annuite_caf", 0)) > 0.5 },
                "leverage": { "value": float(current.get("leverage_brut", 0)), "alerte": float(current.get("leverage_brut", 0)) > 3.5 },
                "leverage_brut": { "value": float(current.get("leverage_brut", 0)), "alerte": float(current.get("leverage_brut", 0)) > 3.5 },
                "dette_caf": { "value": float(current.get("leverage_brut", 0)), "alerte": float(current.get("leverage_brut", 0)) > 3.5 },
                "marge_nette": { "value": float(current.get("marge_nette", 0)), "alerte": float(current.get("marge_nette", 0)) < 10 },
                "fi_ebe": { "value": float(current.get("fi_ebe", 0)), "alerte": float(current.get("fi_ebe", 0)) > 0.3 },
                "fi_ca": { "value": float(current.get("fi_ca", 0)), "alerte": float(current.get("fi_ca", 0)) > 0.05 },
                "gearing": { "value": float(current.get("gearing", 0)), "alerte": float(current.get("gearing", 0)) > 1.3 }
            },
            "marge_brute": float(current.get("marge_brute", 0)),
            "marge_nette": float(current.get("marge_nette", 0)),
            "tresorerie": float(current.get("tresorerie", 0))
        }
        return Response(data)
    except Exception as e:
        return Response({"error": str(e)}, status=500)
