
from decimal import Decimal
from datetime import datetime, timedelta

from django.db.models import Sum, Q, Max, Case, When, DecimalField
import traceback
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from compta.models import GrandLivre, Bilan, CompteResultat
from compta.kpi_utils import get_latest_bilan_sum, get_cr_sum, get_resultat_net, get_capitaux_propres, get_chiffre_affaire, get_ebe

from compta.permissions import HasProjectAccess
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter
from compta.serializers import DashboardIndicatorsResponseSerializer

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: DashboardIndicatorsResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def dashboard_indicators_view(request):
    """
    ENDPOINT OPTIMISÉ POUR DASHBOARD (V2)
    Calcule TOUS les indicateurs (16 KPIs) + Variations en un minimum de requêtes.
    """
    # 1. RÉCUPÉRATION DU PROJET
    project_id = getattr(request, 'project_id', None)
    if not project_id:
        return Response({"error": "Project non fourni."}, status=400)

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
            # 1. CALCULS DES MASSES UNIFIÉS (CR) - UNE SEULE REQUÊTE
            cr_agg = CompteResultat.objects.filter(project_id=project_id, date__range=[d_start, d_end]).aggregate(
                ca=Sum(Case(When(numero_compte__startswith="70", then="montant_ar"), default=0, output_field=DecimalField())),
                v_71_72_74=Sum(Case(When(Q(numero_compte__startswith="71") | Q(numero_compte__startswith="72") | Q(numero_compte__startswith="74"), then="montant_ar"), default=0, output_field=DecimalField())),
                total_produits=Sum(Case(When(numero_compte__startswith="7", then="montant_ar"), default=0, output_field=DecimalField())),
                achats=Sum(Case(When(numero_compte__startswith="60", then="montant_ar"), default=0, output_field=DecimalField())),
                vente_607=Sum(Case(When(numero_compte__startswith="607", then="montant_ar"), default=0, output_field=DecimalField())),
                charges_externes=Sum(Case(When(Q(numero_compte__startswith="61") | Q(numero_compte__startswith="62"), then="montant_ar"), default=0, output_field=DecimalField())),
                impots=Sum(Case(When(numero_compte__startswith="63", then="montant_ar"), default=0, output_field=DecimalField())),
                personnel=Sum(Case(When(numero_compte__startswith="64", then="montant_ar"), default=0, output_field=DecimalField())),
                charges_fi=Sum(Case(When(numero_compte__startswith="66", then="montant_ar"), default=0, output_field=DecimalField())),
                dotations=Sum(Case(When(numero_compte__startswith="68", then="montant_ar"), default=0, output_field=DecimalField())),
                reprises=Sum(Case(When(numero_compte__startswith="78", then="montant_ar"), default=0, output_field=DecimalField())),
                total_charges=Sum(Case(When(numero_compte__startswith="6", then="montant_ar"), default=0, output_field=DecimalField())),
            )
            
            ca = cr_agg['ca'] or Decimal("0.00")
            v_71_72_74 = cr_agg['v_71_72_74'] or Decimal("0.00")
            achats = cr_agg['achats'] or Decimal("0.00")
            charges_ext_imp_pers = (cr_agg['charges_externes'] or 0) + (cr_agg['impots'] or 0) + (cr_agg['personnel'] or 0)
            dotations = cr_agg['dotations'] or Decimal("0.00")
            reprises = cr_agg['reprises'] or Decimal("0.00")
            charges_fi = cr_agg['charges_fi'] or Decimal("0.00")
            total_produits = cr_agg['total_produits'] or Decimal("0.00")
            total_charges = cr_agg['total_charges'] or Decimal("0.00")
            
            ebe = (ca + v_71_72_74) - (achats + charges_ext_imp_pers)
            resultat_net = total_produits - total_charges
            resultat_exploit = ebe - dotations + reprises
            caf = resultat_net + dotations - reprises
            
            # 2. CALCULS BILAN - OPTIMISÉS (Une seule requête pour tout le Bilan de la période)
            def get_mass_bilan_data(start, end, cumulative=False):
                q = Q(project_id=project_id, date__lte=end)
                if not cumulative: q &= Q(date__gte=start)
                
                # Fetch only latest per account in the period
                rows = Bilan.objects.filter(q).order_by('numero_compte', '-date').distinct('numero_compte').values('numero_compte', 'montant_ar', 'type_bilan')
                data = {}
                for r in rows:
                    data[r['numero_compte']] = {'m': r['montant_ar'] or Decimal('0.00'), 't': r['type_bilan']}
                return data

            bilan_period = get_mass_bilan_data(d_start, d_end, cumulative=False)
            bilan_cumul = get_mass_bilan_data(d_start, d_end, cumulative=True)

            def sum_p(data, prefixes, t_bilan=None):
                return sum((v['m'] for k, v in data.items() if any(k.startswith(p) for p in prefixes) and (t_bilan is None or v['t'] == t_bilan)), Decimal('0.00'))

            stocks = sum_p(bilan_period, ["3"], "ACTIF")
            creances_clients = sum_p(bilan_period, ["411"], "ACTIF")
            tresorerie_actif = sum_p(bilan_period, ["5"], "ACTIF")
            actifs_courants = stocks + creances_clients + tresorerie_actif 
            
            total_balance_live = get_total_balance_live(d_start, d_end)
            total_actif = total_balance_live if total_balance_live > 0 else sum_p(bilan_period, [""], "ACTIF")
            
            # Capitaux Propres = CP cumulé + RN période
            # Capitaux Propres = CP cumulé (sans compte 12) + Résultat Net période
            # On exclut le compte 12 (Résultat) de la base car on l'ajoute manuellement pour avoir le RN "Live"
            base_cp = sum_p(bilan_cumul, ["10", "11"], "PASSIF")
            capitaux_propres = base_cp + resultat_net
            
            tresorerie_passif = sum_p(bilan_period, ["512", "519"], "PASSIF")
            dettes_fi = sum_p(bilan_period, ["16"], "PASSIF") + tresorerie_passif
            dettes_fournisseurs = sum_p(bilan_period, ["401"], "PASSIF")
            
            # Autres créances (42, 43, 44, 45, 46, 47)
            autres_creances = sum_p(bilan_period, ["42", "43", "44", "45", "46", "47"], "ACTIF")
            # Autres dettes d'exploitation (42, 43, 44, 45, 46, 47)
            autres_dettes = sum_p(bilan_period, ["42", "43", "44", "45", "46", "47"], "PASSIF")
            
            passifs_courants = dettes_fournisseurs + autres_dettes + tresorerie_passif
            passif_non_courant = sum_p(bilan_period, ["15", "17"], "PASSIF") + sum_p(bilan_period, ["16"], "PASSIF")
            
            # BFR élargi : (Stocks + Clients + Autres Créances) - (Fournisseurs + Autres Dettes)
            bfr = stocks + creances_clients + autres_creances - (dettes_fournisseurs + autres_dettes)
            
            # 3. CALCULS DES RATIOS ... (Reste inchangé)
            marge_brute = ca - achats 
            marge_nette = (resultat_net / ca * 100) if ca != 0 else 0
            marge_op = (resultat_exploit / ca * 100) if ca != 0 else 0
            
            roe = (resultat_net / capitaux_propres * 100) if capitaux_propres != 0 else 0
            roa = (resultat_net / total_actif * 100) if total_actif != 0 else 0
            
            current_ratio = (actifs_courants / passifs_courants) if passifs_courants != 0 else 0
            quick_ratio = ((actifs_courants - stocks) / passifs_courants) if passifs_courants != 0 else 0
            gearing = (dettes_fi / capitaux_propres) if capitaux_propres != 0 else 0
            
            cout_607 = cr_agg['vente_607'] or Decimal("0.00")
            rotation_stock = (cout_607 / stocks) if stocks != 0 else 0
            duree_stock = (360 / rotation_stock) if rotation_stock != 0 else 0
            leverage_brut = (dettes_fi / ebe) if ebe != 0 else 0
            annuite_caf = (dotations / caf) if caf != 0 else 0
            dette_caf = (dettes_fi / caf) if caf != 0 else 0
            fi_ebe = (charges_fi / ebe) if ebe != 0 else 0
            fi_ca = (charges_fi / ca) if ca != 0 else 0

            return {
                "ca": ca, "ebe": ebe, "resultat_net": resultat_net, "caf": caf, "bfr": bfr,
                "marge_brute": marge_brute, "marge_nette": marge_nette, "marge_operationnelle": marge_op,
                "tresorerie": tresorerie_actif - tresorerie_passif,
                "roe": roe, "roa": roa, "current_ratio": current_ratio, "quick_ratio": quick_ratio,
                "gearing": gearing, "rotation_stock": rotation_stock, "duree_stock_jours": duree_stock,
                "leverage_brut": leverage_brut, 
                "annuite_caf": annuite_caf, "dette_caf": dette_caf, "fi_ebe": fi_ebe, "fi_ca": fi_ca,
                
                "actifs_courants": actifs_courants, "passifs_courants": passifs_courants, "stocks": stocks,
                "fonds_propres": capitaux_propres, "total_actif": total_actif, "dettes_financieres": dettes_fi,
                "cout_ventes": cout_607, "chiffre_affaire": ca, "charges_exploitation": (achats + charges_ext_imp_pers + dotations),
                "resultat_operationnel": resultat_exploit, "passif_non_courant": passif_non_courant
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
            "total_passif": float(current.get("passifs_courants", 0) + current.get("passif_non_courant", 0) + current.get("fonds_propres", 0)),

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
                "dette_caf": { "value": float(current.get("dette_caf", 0)), "alerte": float(current.get("dette_caf", 0)) > 3.5 },
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
