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
        self.project_id = project_id
        try:
            self.project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            self.project = None
    
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
    
    # ========== TRÉSORERIE ==========
    def get_tresorerie(self, date_fin=None):
        """
        Calcule la trésorerie (comptes 51x + 53x)
        PCG 2005: 51 = Banque, 53 = Caisse
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='51') | Q(numero_compte__startswith='53')
        )
        
        if date_fin:
            filters &= Q(date__lte=date_fin)
        
        # Récupérer depuis la Balance
        balances = Balance.objects.filter(filters).aggregate(
            solde_debit_total=Sum('solde_debit'),
            solde_credit_total=Sum('solde_credit')
        )
        
        tresorerie = (balances['solde_debit_total'] or Decimal('0.00')) - \
                     (balances['solde_credit_total'] or Decimal('0.00'))
        
        return {
            "montant": float(tresorerie),
            "date": date_fin.strftime('%Y-%m-%d') if date_fin else "aujourd'hui",
            "comptes": "51x (Banque) + 53x (Caisse)"
        }
    
    # ========== BILAN ==========
    def get_bilan_summary(self, date_bilan=None):
        """
        Résumé du bilan (Actif vs Passif)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        if date_bilan:
            filters &= Q(date=date_bilan)
        
        # Actif
        actif = Bilan.objects.filter(
            filters, type_bilan='ACTIF'
        ).aggregate(total=Sum('montant_ar'))
        
        # Passif
        passif = Bilan.objects.filter(
            filters, type_bilan='PASSIF'
        ).aggregate(total=Sum('montant_ar'))
        
        return {
            "actif": float(actif['total'] or Decimal('0.00')),
            "passif": float(passif['total'] or Decimal('0.00')),
            "equilibre": float((actif['total'] or Decimal('0.00')) - (passif['total'] or Decimal('0.00'))),
            "date": date_bilan.strftime('%Y-%m-%d') if date_bilan else "dernière date"
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