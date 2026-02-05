from django.db.models import Sum, Q, F
from decimal import Decimal
from datetime import datetime, date
from compta.models import (
    Journal, GrandLivre, Balance, 
    CompteResultat, Bilan, Project
)

class AccountingQueryService:
    """Service pour interroger les données comptables d'un projet"""
    
    def __init__(self, project_id):
        try:
            self.project_id = int(project_id) if project_id else None
            self.project = Project.objects.get(id=self.project_id)
        except (Project.DoesNotExist, ValueError, TypeError):
            self.project = None
            self.project_id = None
    
    # ========== CHIFFRE D'AFFAIRES ==========
    def get_chiffre_affaires(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le chiffre d'affaires (comptes 70x - Produits d'exploitation)
        PCG 2005: 701-709 = Ventes et produits
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        # Déterminer les dates
        filters = Q(project_id=self.project_id) & Q(numero_compte__startswith='70')
        
        if annee:
            filters &= Q(date__year=annee)
        elif start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        
        # Récupérer depuis CompteResultat (plus rapide) ou calculer depuis Journal
        resultat = CompteResultat.objects.filter(
            filters, nature='PRODUIT'
        ).aggregate(
            total=Sum('montant_ar')
        )
        
        ca = resultat['total'] or Decimal('0.00')
        
        return {
            "montant": float(ca),
            "periode": self._format_periode(start_date, end_date, annee),
            "comptes": "70x (Ventes et produits)"
        }
    
    # ========== CHARGES ==========
    def get_charges(self, start_date=None, end_date=None, annee=None):
        """
        Calcule les charges totales (comptes 6xx)
        PCG 2005: 60-69 = Charges d'exploitation, financières, exceptionnelles
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id) & Q(numero_compte__startswith='6')
        
        if annee:
            filters &= Q(date__year=annee)
        elif start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        
        resultat = CompteResultat.objects.filter(
            filters, nature='CHARGE'
        ).aggregate(
            total=Sum('montant_ar')
        )
        
        charges = resultat['total'] or Decimal('0.00')
        
        return {
            "montant": float(charges),
            "periode": self._format_periode(start_date, end_date, annee),
            "comptes": "6xx (Charges)"
        }
    
    # ========== RÉSULTAT NET ==========
    def get_resultat_net(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le résultat net = Produits - Charges
        """
        ca = self.get_chiffre_affaires(start_date, end_date, annee)
        charges = self.get_charges(start_date, end_date, annee)
        
        if "error" in ca or "error" in charges:
            return {"error": "Impossible de calculer le résultat"}
        
        resultat = ca['montant'] - charges['montant']
        
        return {
            "montant": resultat,
            "produits": ca['montant'],
            "charges": charges['montant'],
            "periode": ca['periode']
        }
    
    # ========== EBE (Excédent Brut d'Exploitation) ==========
    def get_ebe(self, start_date=None, end_date=None, annee=None):
        """
        Calcule l'EBE (Excédent Brut d'Exploitation)
        PCG 2005: Valeur Ajoutée + Subventions - Impôts/Taxes - Charges Personnel
        Simplification: Produits Exploitation (70-74) - Charges Exploitation (60-64)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        # Produits d'exploitation (70 à 74)
        prod_filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='70') | 
            Q(numero_compte__startswith='71') |
            Q(numero_compte__startswith='72') |
            Q(numero_compte__startswith='74')
        )
        
        # Charges d'exploitation (60 à 64)
        char_filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='60') | 
            Q(numero_compte__startswith='61') |
            Q(numero_compte__startswith='62') |
            Q(numero_compte__startswith='63') |
            Q(numero_compte__startswith='64')
        )
        
        if annee:
            prod_filters &= Q(date__year=annee)
            char_filters &= Q(date__year=annee)
        elif start_date and end_date:
            prod_filters &= Q(date__gte=start_date, date__lte=end_date)
            char_filters &= Q(date__gte=start_date, date__lte=end_date)
            
        prod_res = CompteResultat.objects.filter(prod_filters, nature='PRODUIT').aggregate(total=Sum('montant_ar'))
        char_res = CompteResultat.objects.filter(char_filters, nature='CHARGE').aggregate(total=Sum('montant_ar'))
        
        prod_total = prod_res['total'] or Decimal('0.00')
        char_total = char_res['total'] or Decimal('0.00')
        ebe = float(prod_total - char_total)
        
        return {
            "montant": ebe,
            "produits_exploitation": float(prod_total),
            "charges_exploitation": float(char_total),
            "periode": self._format_periode(start_date, end_date, annee),
            "comptes": "70-74 vs 60-64"
        }
    
    # ========== ROE (Return on Equity) ==========
    def get_roe(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le ROE (Rentabilité des capitaux propres)
        Formule: Résultat Net / Capitaux Propres
        PCG 2005: Capitaux propres (comptes 10x à 14x)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        # 1. Résultat Net sur la période
        res_net_data = self.get_resultat_net(start_date, end_date, annee)
        res_net = res_net_data.get('montant', 0)
        
        # 2. Capitaux Propres (Bilan)
        # On prend la date de fin (soit passée soit calculée par l'année)
        date_bilan = end_date
        if annee and not date_bilan:
            date_bilan = date(annee, 12, 31)
        elif not date_bilan:
            date_bilan = date.today()
            
        cp_filters = Q(project_id=self.project_id) & Q(type_bilan='PASSIF') & Q(categorie='CAPITAUX_PROPRES')
        cp_filters &= Q(date__lte=date_bilan)
            
        # On prend le montant total des capitaux propres au dernier bilan connu avant ou à la date de fin
        latest_cp_date = Bilan.objects.filter(cp_filters).order_by('-date').values_list('date', flat=True).first()
        
        if latest_cp_date:
            cp_res = Bilan.objects.filter(
                project_id=self.project_id,
                date=latest_cp_date,
                categorie='CAPITAUX_PROPRES'
            ).aggregate(total=Sum('montant_ar'))
            cp_total = cp_res['total'] or Decimal('0.00')
        else:
            # Fallback sur les comptes 10-14
            cp_res = Balance.objects.filter(
                project_id=self.project_id, 
                numero_compte__regex=r'^1[0-4]',
                date__lte=date_bilan
            ).order_by('-date').values('numero_compte').annotate(latest_balance=F('solde_credit') - F('solde_debit')).aggregate(total=Sum('latest_balance'))
            cp_total = cp_res['total'] or Decimal('0.00')
            
        cp_total_float = float(cp_total)
        roe = (res_net / cp_total_float * 100) if cp_total_float != 0 else 0
        
        return {
            "valeur": roe,
            "resultat_net": res_net,
            "capitaux_propres": cp_total_float,
            "periode": res_net_data['periode']
        }

    # ========== MARGE BRUTE ==========
    def get_marge_brute(self, start_date=None, end_date=None, annee=None):
        """
        Calcule la Marge Brute
        Formule: Ventes (70) - Achats de marchandises (60)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        filters_70 = Q(project_id=self.project_id, numero_compte__startswith='70')
        filters_60 = Q(project_id=self.project_id, numero_compte__startswith='60')
        
        if annee:
            filters_70 &= Q(date__year=annee)
            filters_60 &= Q(date__year=annee)
        elif start_date and end_date:
            filters_70 &= Q(date__gte=start_date, date__lte=end_date)
            filters_60 &= Q(date__gte=start_date, date__lte=end_date)
            
        ventes = CompteResultat.objects.filter(filters_70, nature='PRODUIT').aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        achats = CompteResultat.objects.filter(filters_60, nature='CHARGE').aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        
        marge = float(ventes - achats)
        taux = (marge / float(ventes) * 100) if ventes != 0 else 0
        
        return {
            "montant": marge,
            "taux": taux,
            "ventes": float(ventes),
            "achats": float(achats),
            "periode": self._format_periode(start_date, end_date, annee)
        }

    # ========== BFR (Besoin en Fonds de Roulement) ==========
    def get_bfr(self, date_ref=None, annee=None):
        """
        Calcule le BFR = (Stocks + Créances clients) - Dettes fournisseurs
        Stocks (3), Créances (41), Dettes Fournisseurs (40)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        target_date = date_ref or (date(annee, 12, 31) if annee else date.today())
        
        # On cherche les soldes à la date cible
        def get_balance_sum(prefix):
            res = Balance.objects.filter(
                project_id=self.project_id,
                numero_compte__startswith=prefix,
                date__lte=target_date
            ).order_by('-date').values('numero_compte').annotate(
                solde=F('solde_debit') - F('solde_credit')
            ).aggregate(total=Sum('solde'))
            return res['total'] or Decimal('0.00')

        stocks = get_balance_sum('3')
        creances = get_balance_sum('41')
        dettes_fourn = -get_balance_sum('40') # Inversé car solde créditeur positif pour dettes
        
        bfr = float(stocks + creances - dettes_fourn)
        
        return {
            "montant": bfr,
            "stocks": float(stocks),
            "creances_clients": float(creances),
            "dettes_fournisseurs": float(dettes_fourn),
            "date": target_date.strftime('%d/%m/%Y')
        }

    # ========== ROA (Return on Assets) ==========
    def get_roa(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le ROA = Résultat Net / Total Actif
        """
        res_net_data = self.get_resultat_net(start_date, end_date, annee)
        res_net = res_net_data.get('montant', 0)
        
        bilan = self.get_bilan_summary(date_bilan=end_date, annee=annee)
        total_actif = bilan.get('actif', 0)
        
        roa = (res_net / total_actif * 100) if total_actif != 0 else 0
        
        return {
            "valeur": roa,
            "resultat_net": res_net,
            "total_actif": total_actif,
            "periode": res_net_data['periode']
        }

    # ========== Ratios de Structure / Liquidité ==========
    def get_ratios_structure(self, date_ref=None, annee=None):
        """
        Calcule Leverage et Current Ratio
        Leverage = Dettes / Capitaux Propres
        Current Ratio = Actif Courant / Passif Courant
        """
        target_date = date_ref or (date(annee, 12, 31) if annee else date.today())
        
        # Capitaux Propres
        cp_res = self.get_roe(end_date=target_date, annee=annee)
        cp = cp_res.get('capitaux_propres', 0)
        
        # Dettes (16, 17, 40, etc.) - Simplifié aux dettes financières (16)
        dettes_fin = Bilan.objects.filter(
            project_id=self.project_id,
            numero_compte__startswith='16',
            date__lte=target_date
        ).aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        dettes_fin_float = float(dettes_fin)
        
        # Leverage
        leverage = (dettes_fin_float / cp) if cp != 0 else 0
        
        # Current Ratio (Actif Courant: 3, 4, 5 / Passif Courant: 4)
        actif_courant = Bilan.objects.filter(
            project_id=self.project_id,
            type_bilan='ACTIF',
            categorie='ACTIF_COURANTS',
            date__lte=target_date
        ).aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        
        passif_courant = Bilan.objects.filter(
            project_id=self.project_id,
            type_bilan='PASSIF',
            categorie='PASSIFS_COURANTS',
            date__lte=target_date
        ).aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        
        ac_float = float(actif_courant)
        pc_float = float(passif_courant)
        current_ratio = (ac_float / pc_float) if pc_float != 0 else 0
        
        return {
            "leverage": leverage,
            "current_ratio": current_ratio,
            "capitaux_propres": cp,
            "dettes_financieres": dettes_fin_float,
            "actif_courant": ac_float,
            "passif_courant": pc_float,
            "date": target_date.strftime('%d/%m/%Y')
        }

    # ========== Marges de Profitabilité ==========
    def get_marges_profitabilite(self, start_date=None, end_date=None, annee=None):
        """
        Calcule Marge Nette et Marge Opérationnelle
        Marge Nette = Résultat Net / CA
        Marge Opérationnelle = EBE / CA
        """
        ca_data = self.get_chiffre_affaires(start_date, end_date, annee)
        ca = ca_data.get('montant', 0)
        
        res_net_data = self.get_resultat_net(start_date, end_date, annee)
        res_net = res_net_data.get('montant', 0)
        
        ebe_data = self.get_ebe(start_date, end_date, annee)
        ebe = ebe_data.get('montant', 0)
        
        marge_nette = (res_net / ca * 100) if ca != 0 else 0
        marge_ope = (ebe / ca * 100) if ca != 0 else 0
        
        return {
            "marge_nette": marge_nette,
            "marge_operationnelle": marge_ope,
            "ca": ca,
            "resultat_net": res_net,
            "ebe": ebe,
            "periode": ca_data['periode']
        }

    # ========== Rotation des Stocks ==========
    def get_rotation_stocks(self, annee=None):
        """
        Calcule la Rotation des stocks = Achats / Stock moyen (ici stock final)
        """
        if not annee:
            annee = date.today().year
            
        marge_brute = self.get_marge_brute(annee=annee)
        achats = marge_brute.get('achats', 0)
        
        bfr = self.get_bfr(annee=annee)
        stocks = bfr.get('stocks', 0)
        
        rotation = (achats / stocks) if stocks != 0 else 0
        # En jours : 365 / rotation
        jours = (365 / rotation) if rotation != 0 else 0
        
        return {
            "coefficient": rotation,
            "jours_stock": jours,
            "achats": achats,
            "stock_final": stocks,
            "annee": annee
        }
    
    # ========== TRÉSORERIE ==========
    def get_tresorerie(self, date_fin=None, annee=None):
        """
        Calcule la trésorerie (comptes 51x + 53x)
        PCG 2005: 51 = Banque, 53 = Caisse
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='51') | Q(numero_compte__startswith='53')
        )
        
        if annee:
            # Si année spécifiée, on prend la situation au 31/12 de cette année
            filters &= Q(date__year__lte=annee)
            final_date_str = f"31/12/{annee}"
        elif date_fin:
            filters &= Q(date__lte=date_fin)
            final_date_str = date_fin.strftime('%d/%m/%Y')
        else:
            final_date_str = "aujourd'hui"
        
        # Récupérer depuis la Balance
        balances = Balance.objects.filter(filters).aggregate(
            solde_debit_total=Sum('solde_debit'),
            solde_credit_total=Sum('solde_credit')
        )
        
        tresorerie = (balances['solde_debit_total'] or Decimal('0.00')) - \
                     (balances['solde_credit_total'] or Decimal('0.00'))
        
        return {
            "montant": float(tresorerie),
            "date": final_date_str,
            "comptes": "51x (Banque) + 53x (Caisse)"
        }
    
    # ========== BILAN ==========
    def get_bilan_summary(self, date_bilan=None, annee=None):
        """
        Résumé du bilan (Actif vs Passif)
        Prend la situation la plus récente disponible à ou avant la date demandée.
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        # Détermination de la date de référence
        target_date = date_bilan
        if annee:
            target_date = date(annee, 12, 31)
        elif not target_date:
            target_date = date.today()

        # Trouver la date la plus récente disponible dans le bilan <= target_date
        latest_date = Bilan.objects.filter(
            project_id=self.project_id,
            date__lte=target_date
        ).order_by('-date').values_list('date', flat=True).first()

        if not latest_date:
            return {
                "actif": 0.0, "passif": 0.0, "equilibre": 0.0,
                "date": f"aucune donnée avant le {target_date.strftime('%d/%m/%Y')}"
            }

        # Actif
        actif = Bilan.objects.filter(
            project_id=self.project_id,
            date=latest_date,
            type_bilan='ACTIF'
        ).aggregate(total=Sum('montant_ar'))
        
        # Passif
        passif = Bilan.objects.filter(
            project_id=self.project_id,
            date=latest_date,
            type_bilan='PASSIF'
        ).aggregate(total=Sum('montant_ar'))
        
        return {
            "actif": float(actif['total'] or Decimal('0.00')),
            "passif": float(passif['total'] or Decimal('0.00')),
            "equilibre": float((actif['total'] or Decimal('0.00')) - (passif['total'] or Decimal('0.00'))),
            "date": latest_date.strftime('%d/%m/%Y')
        }
    
    # ========== COMPARAISON PÉRIODES ==========
    def compare_periodes(self, annee1, annee2):
        """
        Compare deux années (CA, Charges, Résultat)
        """
        data_annee1 = {
            "ca": self.get_chiffre_affaires(annee=annee1),
            "charges": self.get_charges(annee=annee1),
            "resultat": self.get_resultat_net(annee=annee1)
        }
        
        data_annee2 = {
            "ca": self.get_chiffre_affaires(annee=annee2),
            "charges": self.get_charges(annee=annee2),
            "resultat": self.get_resultat_net(annee=annee2)
        }
        
        return {
            "annee_1": {
                "annee": annee1,
                "chiffre_affaires": data_annee1["ca"]["montant"],
                "charges": data_annee1["charges"]["montant"],
                "resultat": data_annee1["resultat"]["montant"]
            },
            "annee_2": {
                "annee": annee2,
                "chiffre_affaires": data_annee2["ca"]["montant"],
                "charges": data_annee2["charges"]["montant"],
                "resultat": data_annee2["resultat"]["montant"]
            },
            "evolution": {
                "ca": data_annee2["ca"]["montant"] - data_annee1["ca"]["montant"],
                "charges": data_annee2["charges"]["montant"] - data_annee1["charges"]["montant"],
                "resultat": data_annee2["resultat"]["montant"] - data_annee1["resultat"]["montant"]
            }
        }
    
    # ========== HELPERS ==========
    def _format_periode(self, start_date, end_date, annee):
        """Formate la période pour l'affichage"""
        if annee:
            return f"Année {annee}"
        elif start_date and end_date:
            return f"Du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}"
        else:
            return "Toute la période"