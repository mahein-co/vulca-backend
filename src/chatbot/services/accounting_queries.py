from django.db.models import Sum, Q, F, Count, Avg, Max, Min
from django.db.models.functions import ExtractYear
from datetime import datetime, date
from decimal import Decimal
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
    
    # ========================================
    # MÉTHODES GÉNÉRIQUES POUR ACCÉDER À TOUT
    # ========================================
    
    def get_all_data(self, include_details=True):
        """
        Récupère TOUTES les données du projet
        Retourne un dictionnaire complet avec toutes les tables
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        return {
            "project": {
                "id": self.project.id,
                "name": self.project.name if hasattr(self.project, 'name') else None,
            },
            "journal": self.get_journal_data(include_details=include_details),
            "grand_livre": self.get_grand_livre_data(include_details=include_details),
            "balance": self.get_balance_data(include_details=include_details),
            "compte_resultat": self.get_compte_resultat_data(include_details=include_details),
            "bilan": self.get_bilan_data(include_details=include_details),
            "synthese": self.get_synthese_complete()
        }
    
    def get_journal_data(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Récupère toutes les écritures du journal
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
        
        queryset = Journal.objects.filter(filters).order_by('-date', 'numero_piece')
        
        # Statistiques
        stats = queryset.aggregate(
            total_debit=Sum('montant_debit'),
            total_credit=Sum('montant_credit'),
            nb_ecritures=Count('id')
        )
        
        response = {
            "total_debit": float(stats['total_debit'] or 0),
            "total_credit": float(stats['total_credit'] or 0),
            "nb_ecritures": stats['nb_ecritures'],
            "equilibre": float((stats['total_debit'] or 0) - (stats['total_credit'] or 0)),
            "periode": self._format_periode(start_date, end_date, annee)
        }
        
        if include_details:
            response['ecritures'] = [
                {
                    "id": j.id,
                    "date": j.date.strftime('%d/%m/%Y'),
                    "numero_piece": j.numero_piece,
                    "compte": j.numero_compte,
                    "libelle": j.libelle,
                    "debit": float(j.montant_debit or 0),
                    "credit": float(j.montant_credit or 0),
                    "lettrage": j.lettrage if hasattr(j, 'lettrage') else None,
                }
                for j in queryset
            ]
        
        return response
    
    def get_grand_livre_data(self, numero_compte=None, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Récupère les données du grand livre
        Peut filtrer par compte spécifique
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        
        if numero_compte:
            filters &= Q(numero_compte=numero_compte)
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
        
        queryset = GrandLivre.objects.filter(filters).order_by('numero_compte', '-date')
        
        # Grouper par compte
        comptes = queryset.values('numero_compte').distinct()
        
        response = {
            "nb_comptes": len(comptes),
            "periode": self._format_periode(start_date, end_date, annee),
        }
        
        if include_details:
            response['comptes'] = []
            for compte in comptes:
                compte_data = queryset.filter(numero_compte=compte['numero_compte'])
                compte_stats = compte_data.aggregate(
                    total_debit=Sum('montant_debit'),
                    total_credit=Sum('montant_credit'),
                    solde=Sum(F('montant_debit') - F('montant_credit'))
                )
                
                response['comptes'].append({
                    "numero_compte": compte['numero_compte'],
                    "total_debit": float(compte_stats['total_debit'] or 0),
                    "total_credit": float(compte_stats['total_credit'] or 0),
                    "solde": float(compte_stats['solde'] or 0),
                    "mouvements": [
                        {
                            "date": m.date.strftime('%d/%m/%Y'),
                            "libelle": m.libelle,
                            "debit": float(m.montant_debit or 0),
                            "credit": float(m.montant_credit or 0),
                        }
                        for m in compte_data
                    ]
                })
        
        return response
    
    def get_balance_data(self, numero_compte=None, nature=None, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Récupère toutes les données de la balance
        Peut filtrer par compte, nature (ACTIF/PASSIF)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        
        if numero_compte:
            if isinstance(numero_compte, list):
                filters &= Q(numero_compte__in=numero_compte)
            else:
                filters &= Q(numero_compte=numero_compte)
        
        if nature:
            filters &= Q(nature=nature)
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
        
        queryset = Balance.objects.filter(filters).order_by('numero_compte', '-date')
        
        # Statistiques globales
        stats = queryset.aggregate(
            total_debit=Sum('solde_debit'),
            total_credit=Sum('solde_credit'),
            nb_comptes=Count('numero_compte', distinct=True)
        )
        
        # Par nature
        stats_actif = queryset.filter(nature='ACTIF').aggregate(total=Sum(F('solde_debit') - F('solde_credit')))
        stats_passif = queryset.filter(nature='PASSIF').aggregate(total=Sum(F('solde_credit') - F('solde_debit')))
        
        response = {
            "total_debit": float(stats['total_debit'] or 0),
            "total_credit": float(stats['total_credit'] or 0),
            "total_actif": float(stats_actif['total'] or 0),
            "total_passif": float(stats_passif['total'] or 0),
            "equilibre": float((stats['total_debit'] or 0) - (stats['total_credit'] or 0)),
            "nb_comptes": stats['nb_comptes'],
            "periode": self._format_periode(start_date, end_date, annee)
        }
        
        if include_details:
            response['comptes'] = [
                {
                    "date": b.date.strftime('%d/%m/%Y'),
                    "numero_compte": b.numero_compte,
                    "libelle": b.libelle,
                    "debit": float(b.solde_debit or 0),
                    "credit": float(b.solde_credit or 0),
                    "solde": float((b.solde_debit or 0) - (b.solde_credit or 0)),
                    "nature": b.nature,
                }
                for b in queryset
            ]
        
        return response
    
    def get_compte_resultat_data(self, numero_compte=None, nature=None, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Récupère toutes les données du compte de résultat
        Peut filtrer par compte, nature (PRODUIT/CHARGE)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        
        if numero_compte:
            if isinstance(numero_compte, list):
                filters &= Q(numero_compte__in=numero_compte)
            else:
                filters &= Q(numero_compte__startswith=numero_compte)
        
        if nature:
            filters &= Q(nature=nature)
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
        
        queryset = CompteResultat.objects.filter(filters).order_by('numero_compte', '-date')
        
        # Statistiques
        stats_produits = queryset.filter(nature='PRODUIT').aggregate(total=Sum('montant_ar'))
        stats_charges = queryset.filter(nature='CHARGE').aggregate(total=Sum('montant_ar'))
        
        total_produits = stats_produits['total'] or Decimal('0.00')
        total_charges = stats_charges['total'] or Decimal('0.00')
        
        response = {
            "total_produits": float(total_produits),
            "total_charges": float(total_charges),
            "resultat_net": float(total_produits - total_charges),
            "nb_lignes": queryset.count(),
            "periode": self._format_periode(start_date, end_date, annee)
        }
        
        if include_details:
            response['lignes'] = [
                {
                    "date": cr.date.strftime('%d/%m/%Y'),
                    "numero_compte": cr.numero_compte,
                    "libelle": cr.libelle,
                    "montant": float(cr.montant_ar),
                    "nature": cr.nature,
                }
                for cr in queryset
            ]
        
        return response
    
    def get_bilan_data(self, type_bilan=None, categorie=None, date_bilan=None, annee=None, include_details=True):
        """
        Récupère toutes les données du bilan
        Peut filtrer par type (ACTIF/PASSIF), catégorie
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        # Déterminer la date de référence
        target_date = date_bilan or (date(annee, 12, 31) if annee else date.today())
        
        # Trouver la date la plus récente
        latest_date = Bilan.objects.filter(
            project_id=self.project_id,
            date__lte=target_date
        ).order_by('-date').values_list('date', flat=True).first()
        
        if not latest_date:
            return {
                "error": f"Aucune donnée de bilan avant le {target_date.strftime('%d/%m/%Y')}"
            }
        
        filters = Q(project_id=self.project_id, date=latest_date)
        
        if type_bilan:
            filters &= Q(type_bilan=type_bilan)
        
        if categorie:
            filters &= Q(categorie=categorie)
        
        queryset = Bilan.objects.filter(filters).order_by('type_bilan', 'categorie', 'numero_compte')
        
        # Statistiques
        stats_actif = queryset.filter(type_bilan='ACTIF').aggregate(total=Sum('montant_ar'))
        stats_passif = queryset.filter(type_bilan='PASSIF').aggregate(total=Sum('montant_ar'))
        
        response = {
            "date": latest_date.strftime('%d/%m/%Y'),
            "total_actif": float(stats_actif['total'] or 0),
            "total_passif": float(stats_passif['total'] or 0),
            "equilibre": float((stats_actif['total'] or 0) - (stats_passif['total'] or 0)),
            "nb_lignes": queryset.count()
        }
        
        if include_details:
            # Grouper par type et catégorie
            response['actif'] = {}
            response['passif'] = {}
            
            for ligne in queryset.filter(type_bilan='ACTIF'):
                cat = ligne.categorie
                if cat not in response['actif']:
                    response['actif'][cat] = []
                response['actif'][cat].append({
                    "numero_compte": ligne.numero_compte,
                    "libelle": ligne.libelle,
                    "montant": float(ligne.montant_ar)
                })
            
            for ligne in queryset.filter(type_bilan='PASSIF'):
                cat = ligne.categorie
                if cat not in response['passif']:
                    response['passif'][cat] = []
                response['passif'][cat].append({
                    "numero_compte": ligne.numero_compte,
                    "libelle": ligne.libelle,
                    "montant": float(ligne.montant_ar)
                })
        
        return response
    
    def search_in_all_tables(self, search_term, start_date=None, end_date=None, annee=None):
        """
        Recherche un terme dans TOUTES les tables
        Retourne les résultats groupés par table
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        date_filters = Q()
        if annee:
            date_filters = Q(date__year=annee)
        elif start_date and end_date:
            date_filters = Q(date__gte=start_date, date__lte=end_date)
        
        results = {
            "terme_recherche": search_term,
            "periode": self._format_periode(start_date, end_date, annee),
            "resultats": {}
        }
        
        # Recherche dans Journal
        journal_results = Journal.objects.filter(
            Q(project_id=self.project_id) & date_filters &
            (Q(libelle__icontains=search_term) | 
             Q(numero_compte__icontains=search_term) |
             Q(numero_piece__icontains=search_term))
        )
        if journal_results.exists():
            results['resultats']['journal'] = [
                {
                    "date": j.date.strftime('%d/%m/%Y'),
                    "compte": j.numero_compte,
                    "libelle": j.libelle,
                    "debit": float(j.montant_debit or 0),
                    "credit": float(j.montant_credit or 0)
                }
                for j in journal_results[:10]  # Limiter à 10
            ]
            results['resultats']['journal_count'] = journal_results.count()
        
        # Recherche dans Balance
        balance_results = Balance.objects.filter(
            Q(project_id=self.project_id) & date_filters &
            (Q(libelle__icontains=search_term) | 
             Q(numero_compte__icontains=search_term))
        )
        if balance_results.exists():
            results['resultats']['balance'] = [
                {
                    "date": b.date.strftime('%d/%m/%Y'),
                    "compte": b.numero_compte,
                    "libelle": b.libelle,
                    "solde": float((b.solde_debit or 0) - (b.solde_credit or 0))
                }
                for b in balance_results[:10]
            ]
            results['resultats']['balance_count'] = balance_results.count()
        
        # Recherche dans CompteResultat
        cr_results = CompteResultat.objects.filter(
            Q(project_id=self.project_id) & date_filters &
            (Q(libelle__icontains=search_term) | 
             Q(numero_compte__icontains=search_term))
        )
        if cr_results.exists():
            results['resultats']['compte_resultat'] = [
                {
                    "date": cr.date.strftime('%d/%m/%Y'),
                    "compte": cr.numero_compte,
                    "libelle": cr.libelle,
                    "montant": float(cr.montant_ar),
                    "nature": cr.nature
                }
                for cr in cr_results[:10]
            ]
            results['resultats']['compte_resultat_count'] = cr_results.count()
        
        # Recherche dans Bilan
        bilan_results = Bilan.objects.filter(
            Q(project_id=self.project_id) &
            (Q(libelle__icontains=search_term) | 
             Q(numero_compte__icontains=search_term))
        )
        if bilan_results.exists():
            results['resultats']['bilan'] = [
                {
                    "date": b.date.strftime('%d/%m/%Y'),
                    "compte": b.numero_compte,
                    "libelle": b.libelle,
                    "montant": float(b.montant_ar),
                    "type": b.type_bilan
                }
                for b in bilan_results[:10]
            ]
            results['resultats']['bilan_count'] = bilan_results.count()
        
        results['total_resultats'] = sum([
            results['resultats'].get('journal_count', 0),
            results['resultats'].get('balance_count', 0),
            results['resultats'].get('compte_resultat_count', 0),
            results['resultats'].get('bilan_count', 0)
        ])
        
        return results
    
    def get_compte_details(self, numero_compte, start_date=None, end_date=None, annee=None):
        """
        Récupère TOUS les détails d'un compte spécifique
        dans toutes les tables (Journal, Grand Livre, Balance)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        date_filters = Q()
        if annee:
            date_filters = Q(date__year=annee)
        elif start_date and end_date:
            date_filters = Q(date__gte=start_date, date__lte=end_date)
        
        response = {
            "numero_compte": numero_compte,
            "periode": self._format_periode(start_date, end_date, annee)
        }
        
        # Journal
        journal_data = Journal.objects.filter(
            Q(project_id=self.project_id) & 
            Q(numero_compte=numero_compte) & 
            date_filters
        ).order_by('-date')
        
        response['journal'] = {
            "nb_ecritures": journal_data.count(),
            "total_debit": float(journal_data.aggregate(total=Sum('montant_debit'))['total'] or 0),
            "total_credit": float(journal_data.aggregate(total=Sum('montant_credit'))['total'] or 0),
            "ecritures": [
                {
                    "date": j.date.strftime('%d/%m/%Y'),
                    "libelle": j.libelle,
                    "debit": float(j.montant_debit or 0),
                    "credit": float(j.montant_credit or 0)
                }
                for j in journal_data
            ]
        }
        
        # Balance
        balance_data = Balance.objects.filter(
            Q(project_id=self.project_id) & 
            Q(numero_compte=numero_compte) & 
            date_filters
        ).order_by('-date').first()
        
        if balance_data:
            response['balance'] = {
                "date": balance_data.date.strftime('%d/%m/%Y'),
                "libelle": balance_data.libelle,
                "solde_debit": float(balance_data.solde_debit or 0),
                "solde_credit": float(balance_data.solde_credit or 0),
                "solde": float((balance_data.solde_debit or 0) - (balance_data.solde_credit or 0)),
                "nature": balance_data.nature
            }
        
        # Compte de Résultat
        cr_data = CompteResultat.objects.filter(
            Q(project_id=self.project_id) & 
            Q(numero_compte=numero_compte) & 
            date_filters
        )
        
        if cr_data.exists():
            response['compte_resultat'] = {
                "total": float(cr_data.aggregate(total=Sum('montant_ar'))['total'] or 0),
                "nature": cr_data.first().nature,
                "lignes": [
                    {
                        "date": cr.date.strftime('%d/%m/%Y'),
                        "libelle": cr.libelle,
                        "montant": float(cr.montant_ar)
                    }
                    for cr in cr_data
                ]
            }
        
        return response
    
    def get_dashboard_kpis(self, start_date=None, end_date=None, annee=None):
        """
        Calcule TOUS les KPIs en utilisant EXACTEMENT les mêmes formules que
        dashboard_view_optimized.py > calculate_all_kpis().
        C'est la source de vérité unique pour le chatbot.
        """
        if not self.project:
            return {"error": "Projet non trouvé"}

        # ── Résoudre les dates ─────────────────────────────────────────────
        if not (start_date and end_date) and annee:
            start_date = f"{annee}-01-01"
            end_date   = f"{annee}-12-31"

        if not start_date or not end_date:
            # Pas de filtre date → toutes les écritures
            filters = {"project_id": self.project_id}
            cumulative_filters = {"project_id": self.project_id}
            d_end = date.today()
            d_end_str = None
        else:
            filters = {"project_id": self.project_id, "date__range": [start_date, end_date]}
            cumulative_filters = {"project_id": self.project_id, "date__lte": end_date}
            d_end = end_date
            d_end_str = end_date

        periode = self._format_periode(start_date, end_date, annee)

        def get_sum_cr(prefix_list):
            q = Q()
            for p in prefix_list:
                q |= Q(numero_compte__startswith=p)
            return CompteResultat.objects.filter(q, **filters).aggregate(
                total=Sum("montant_ar"))["total"] or Decimal("0.00")

        def get_sum_bilan(prefix_list, type_bilan=None):
            # Utilisation de la nouvelle logique anti-double-comptage
            return self._get_latest_bilan_sum(prefix_list, d_end, type_bilan=type_bilan)

        def get_total_balance_live():
            """Identique au dashboard : somme total_debit de la table Balance"""
            qs = Balance.objects.filter(**cumulative_filters)
            res = qs.aggregate(total_debit=Sum("total_debit"), total_credit=Sum("total_credit"))
            return res["total_debit"] or Decimal("0.00")

        try:
            # ── 1. MASSES DU COMPTE DE RÉSULTAT ───────────────────────────
            ca             = get_sum_cr(["70"])
            total_produits = get_sum_cr(["7"])
            achats         = get_sum_cr(["60"])
            charges_ext    = get_sum_cr(["61", "62"])
            impots         = get_sum_cr(["63"])
            personnel      = get_sum_cr(["64"])
            charges_fi     = get_sum_cr(["66"])
            dotations      = get_sum_cr(["68"])
            total_charges  = get_sum_cr(["6"])
            reprises       = get_sum_cr(["78"])

            ebe            = get_sum_cr(["70","71","72","73","74"]) - (achats + charges_ext + impots + personnel)
            resultat_net   = total_produits - total_charges
            resultat_exploit = ebe - dotations + reprises
            caf            = resultat_net + dotations - reprises

            # ── 2. BILAN ──────────────────────────────────────────────────
            stocks            = get_sum_bilan(["3"],   type_bilan="ACTIF")
            creances_clients  = get_sum_bilan(["411"], type_bilan="ACTIF")
            tresorerie_actif  = get_sum_bilan(["5"],   type_bilan="ACTIF")
            actifs_courants   = stocks + creances_clients + tresorerie_actif

            total_balance_live = get_total_balance_live()
            total_actif = total_balance_live if total_balance_live > 0 else get_sum_bilan([""], type_bilan="ACTIF")

            capitaux_propres = get_sum_bilan(["10","11","12"], type_bilan="PASSIF") + resultat_net
            dettes_fi        = get_sum_bilan(["16"], type_bilan="PASSIF") + get_sum_bilan(["512"], type_bilan="PASSIF")
            dettes_fourn     = get_sum_bilan(["401"], type_bilan="PASSIF")
            passifs_courants = dettes_fourn + get_sum_bilan(["42","43","44"], type_bilan="PASSIF") + get_sum_bilan(["512"], type_bilan="PASSIF")
            bfr              = stocks + creances_clients - dettes_fourn

            tresorerie_nette = get_sum_bilan(["5"], type_bilan="ACTIF") - get_sum_bilan(["512"], type_bilan="PASSIF")

            # ── 3. RATIOS ─────────────────────────────────────────────────
            marge_brute = ca - achats
            marge_nette = float(resultat_net / ca * 100) if ca != 0 else 0.0
            marge_op    = float(resultat_exploit / ca * 100) if ca != 0 else 0.0
            roe         = float(resultat_net / capitaux_propres * 100) if capitaux_propres != 0 else 0.0
            roa         = float(resultat_net / total_actif * 100) if total_actif != 0 else 0.0
            cur_ratio   = float(actifs_courants / passifs_courants) if passifs_courants != 0 else 0.0
            quick_ratio = float((actifs_courants - stocks) / passifs_courants) if passifs_courants != 0 else 0.0
            gearing     = float(dettes_fi / capitaux_propres) if capitaux_propres != 0 else 0.0
            cout_ventes = get_sum_cr(["607"])
            rot_stock   = float(cout_ventes / stocks) if stocks != 0 else 0.0
            duree_stock = float(360 / rot_stock) if rot_stock != 0 else 0.0
            leverage    = float(dettes_fi / ebe) if ebe != 0 else 0.0
            annuite_caf = float(dotations / caf) if caf != 0 else 0.0
            fi_ebe      = float(charges_fi / ebe) if ebe != 0 else 0.0
            fi_ca       = float(charges_fi / ca) if ca != 0 else 0.0

            return {
                "periode": periode,
                # Masses
                "ca": float(ca),
                "total_produits": float(total_produits),
                "total_charges": float(total_charges),
                "resultat_net": float(resultat_net),
                "ebe": float(ebe),
                "caf": float(caf),
                "marge_brute": float(marge_brute),
                "tresorerie": float(tresorerie_nette),
                "bfr": float(bfr),
                "leverage": leverage,
                # Bilan
                "total_actif": float(total_actif),
                "actifs_courants": float(actifs_courants),
                "passifs_courants": float(passifs_courants),
                "capitaux_propres": float(capitaux_propres),
                "stocks": float(stocks),
                "creances_clients": float(creances_clients),
                "dettes_fournisseurs": float(dettes_fourn),
                "dettes_financieres": float(dettes_fi),
                # Ratios
                "roe": roe,
                "roa": roa,
                "marge_nette": marge_nette,
                "marge_operationnelle": marge_op,
                "current_ratio": cur_ratio,
                "quick_ratio": quick_ratio,
                "gearing": gearing,
                "rotation_stock": rot_stock,
                "duree_stock_jours": duree_stock,
                "annuite_caf": annuite_caf,
                "fi_ebe": fi_ebe,
                "fi_ca": fi_ca,
            }
        except Exception as e:
            return {"error": f"Erreur calcul KPIs dashboard: {str(e)}"}

    def get_synthese_complete(self, start_date=None, end_date=None, annee=None):
        """
        Synthèse financière COMPLÈTE – délègue à get_dashboard_kpis()
        pour garantir une cohérence 100% avec le tableau de bord.
        """
        return self.get_dashboard_kpis(start_date=start_date, end_date=end_date, annee=annee)
    
    # ========================================
    # MÉTHODES DE CALCUL SPÉCIFIQUES (EXISTANTES)
    # ========================================

    def _get_latest_bilan_sum(self, prefix_list, target_date, type_bilan=None):
        """
        Helper pour récupérer la somme des montants du Bilan pour les comptes
        correspondant aux préfixes donnés, en ne prenant que la ligne la plus
        récente pour chaque compte à la date cible ou avant.
        Ceci est crucial pour éviter le double-comptage dans le bilan.
        """
        if not self.project:
            return Decimal("0.00")

        q_compte = Q()
        for p in prefix_list:
            q_compte |= Q(numero_compte__startswith=p)

        filters = Q(project_id=self.project_id) & q_compte & Q(date__lte=target_date)
        if type_bilan:
            filters &= Q(type_bilan=type_bilan)

        # Subquery pour trouver la date la plus récente pour chaque numero_compte
        latest_dates = Bilan.objects.filter(
            Q(project_id=self.project_id) & q_compte & Q(date__lte=target_date)
        ).values('numero_compte').annotate(
            max_date=Max('date')
        )

        # Construire une liste de Q objects pour filtrer par (numero_compte, max_date)
        latest_entries_filter = Q()
        for entry in latest_dates:
            latest_entries_filter |= (
                Q(numero_compte=entry['numero_compte']) & Q(date=entry['max_date'])
            )
        
        if not latest_entries_filter: # No matching accounts found
            return Decimal("0.00")

        # Filtrer le queryset principal avec les dates les plus récentes et sommer
        total_sum = Bilan.objects.filter(filters & latest_entries_filter).aggregate(
            total=Sum('montant_ar')
        )['total'] or Decimal("0.00")

        return total_sum
    
    def get_chiffre_affaires(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule le chiffre d'affaires (comptes 70x - Produits d'exploitation)
        PCG 2005: 701-709 = Ventes et produits
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id) & Q(numero_compte__startswith='70')
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
            
        queryset = CompteResultat.objects.filter(filters, nature='PRODUIT')
        resultat = queryset.aggregate(total=Sum('montant_ar'))
        ca = resultat['total'] or Decimal('0.00')
        
        response = {
            "montant": float(ca),
            "periode": self._format_periode(start_date, end_date, annee),
            "comptes": "70x (Ventes et produits)",
            "formule": "Somme des soldes créditeurs des comptes de classe 70 (Produits d'exploitation)"
        }
        
        if include_details:
            details = queryset.values(
                'date', 'numero_compte', 'libelle', 'montant_ar'
            ).order_by('-date')
            
            response['details'] = [
                {
                    "date": d['date'].strftime('%d/%m/%Y'),
                    "compte": d['numero_compte'],
                    "libelle": d['libelle'],
                    "montant": float(d['montant_ar'])
                }
                for d in details
            ]
            response['nb_lignes'] = len(response['details'])
        
        return response
        
    
    def get_produits(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule les produits (nature 'PRODUIT') - Alignement UI
        """
        if not self.project: return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id)
        if start_date and end_date: filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee: filters &= Q(date__year=annee)
        
        queryset = CompteResultat.objects.filter(filters, nature='PRODUIT')
        resultat = queryset.aggregate(total=Sum('montant_ar'))
        montant = resultat['total'] or Decimal('0.00')
        
        response = {
            "montant": float(montant),
            "comptes": "Tous les comptes avec nature 'PRODUIT'",
            "periode": self._format_periode(start_date, end_date, annee)
        }
        
        if include_details:
            details = queryset.values(
                'date', 'numero_compte', 'libelle', 'montant_ar'
            ).order_by('-date')
            
            response['details'] = [
                {
                    "date": d['date'].strftime('%d/%m/%Y'),
                    "compte": d['numero_compte'],
                    "libelle": d['libelle'],
                    "montant": float(d['montant_ar'])
                }
                for d in details
            ]
            response['nb_lignes'] = len(response['details'])
        
        return response

    def get_charges(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule les charges totales (nature 'CHARGE')
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        # Filtres basiques sur le projet
        filters = Q(project_id=self.project_id)
        
        if start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            filters &= Q(date__year=annee)
        
        queryset = CompteResultat.objects.filter(filters, nature='CHARGE')
        resultat = queryset.aggregate(total=Sum('montant_ar'))
        montant = resultat['total'] or Decimal('0.00')
        
        response = {
            "montant": float(montant),
            "comptes": "Tous les comptes avec nature 'CHARGE'",
            "periode": self._format_periode(start_date, end_date, annee),
            "formule": "Somme des soldes débiteurs des comptes de classe 6 (Charges)"
        }

        if include_details:
            details = queryset.values(
                'date', 'numero_compte', 'libelle', 'montant_ar'
            ).order_by('-date')
            
            response['details'] = [
                {
                    "date": d['date'].strftime('%d/%m/%Y'),
                    "compte": d['numero_compte'],
                    "libelle": d['libelle'],
                    "montant": float(d['montant_ar'])
                }
                for d in details
            ]
            response['nb_lignes'] = len(response['details'])
        
        return response
    
    def get_resultat_net(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule le résultat net = Produits - Charges
        """
        produits = self.get_produits(start_date, end_date, annee, include_details=include_details)
        charges = self.get_charges(start_date, end_date, annee, include_details=include_details)
        
        if "error" in produits or "error" in charges:
            return {"error": "Impossible de calculer le résultat"}
        
        resultat = produits['montant'] - charges['montant']
        
        response = {
            "montant": resultat,
            "produits": produits['montant'],
            "charges": charges['montant'],
            "periode": produits['periode']
        }

        if include_details:
            response['details'] = {
                "produits": produits.get('details', []),
                "charges": charges.get('details', [])
            }
            response['nb_lignes'] = (
                len(response['details']['produits']) +
                len(response['details']['charges'])
            )

        return response
    
    def get_ebe(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule l'EBE (Excédent Brut d'Exploitation)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        prod_filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='70') | 
            Q(numero_compte__startswith='71') |
            Q(numero_compte__startswith='72') |
            Q(numero_compte__startswith='74')
        )
        
        char_filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='60') | 
            Q(numero_compte__startswith='61') |
            Q(numero_compte__startswith='62') |
            Q(numero_compte__startswith='63') |
            Q(numero_compte__startswith='64')
        )
        
        if start_date and end_date:
            prod_filters &= Q(date__gte=start_date, date__lte=end_date)
            char_filters &= Q(date__gte=start_date, date__lte=end_date)
        elif annee:
            prod_filters &= Q(date__year=annee)
            char_filters &= Q(date__year=annee)
            
        prod_res = CompteResultat.objects.filter(prod_filters, nature='PRODUIT').aggregate(total=Sum('montant_ar'))
        char_res = CompteResultat.objects.filter(char_filters, nature='CHARGE').aggregate(total=Sum('montant_ar'))
        
        prod_total = prod_res['total'] or Decimal('0.00')
        char_total = char_res['total'] or Decimal('0.00')
        ebe = float(prod_total - char_total)
        
        response = {
            "montant": ebe,
            "produits_exploitation": float(prod_total),
            "charges_exploitation": float(char_total),
            "periode": self._format_periode(start_date, end_date, annee),
            "comptes": "70-74 vs 60-64"
        }

        if include_details:
            response['details'] = {
                "produits": [
                    {
                        "date": d['date'].strftime('%d/%m/%Y'),
                        "compte": d['numero_compte'],
                        "libelle": d['libelle'],
                        "montant": float(d['montant_ar'])
                    } for d in CompteResultat.objects.filter(prod_filters, nature='PRODUIT').values('date','numero_compte','libelle','montant_ar')
                ],
                "charges": [
                    {
                        "date": d['date'].strftime('%d/%m/%Y'),
                        "compte": d['numero_compte'],
                        "libelle": d['libelle'],
                        "montant": float(d['montant_ar'])
                    } for d in CompteResultat.objects.filter(char_filters, nature='CHARGE').values('date','numero_compte','libelle','montant_ar')
                ]
            }
            response['nb_lignes'] = len(response['details']['produits']) + len(response['details']['charges'])

        return response
    
    def get_roe(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le ROE (Rentabilité des capitaux propres)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
    def get_roe(self, start_date=None, end_date=None, annee=None):
        """
        Calcule le ROE (Rentabilité des capitaux propres)
        """
        res_net_data = self.get_resultat_net(start_date, end_date, annee)
        res_net = res_net_data.get('montant', 0)
        
        date_bilan = end_date
        if annee and not date_bilan:
            date_bilan = date(annee, 12, 31)
        elif not date_bilan:
            date_bilan = date.today()
        
        # Pour le ROE, on prend les Capitaux Propres à la date de fin
        cp_filters = Q(project_id=self.project_id) & Q(type_bilan='PASSIF') & Q(categorie='CAPITAUX_PROPRES')
        cp_filters &= Q(date__lte=date_bilan)
            
        latest_cp_date = Bilan.objects.filter(cp_filters).order_by('-date').values_list('date', flat=True).first()
        
        if latest_cp_date:
            cp_res = Bilan.objects.filter(
                project_id=self.project_id,
                date=latest_cp_date,
                categorie='CAPITAUX_PROPRES'
            ).aggregate(total=Sum('montant_ar'))
            cp_total = cp_res['total'] or Decimal('0.00')
        else:
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

    def get_marge_brute(self, start_date=None, end_date=None, annee=None, include_details=True):
        """
        Calcule la Marge Brute
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
            
        ventes_qs = CompteResultat.objects.filter(filters_70, nature='PRODUIT')
        achats_qs = CompteResultat.objects.filter(filters_60, nature='CHARGE')

        ventes = ventes_qs.aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        achats = achats_qs.aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        
        marge = float(ventes - achats)
        taux = (marge / float(ventes) * 100) if ventes != 0 else 0
        
        response = {
            "montant": marge,
            "taux": taux,
            "ventes": float(ventes),
            "achats": float(achats),
            "periode": self._format_periode(start_date, end_date, annee)
        }

        if include_details:
            response["details"] = {
                "ventes": [
                    {
                        "date": d["date"].strftime('%d/%m/%Y'),
                        "compte": d["numero_compte"],
                        "libelle": d["libelle"],
                        "montant": float(d["montant_ar"])
                    }
                    for d in ventes_qs.values("date", "numero_compte", "libelle", "montant_ar")
                ],
                "achats": [
                    {
                        "date": d["date"].strftime('%d/%m/%Y'),
                        "compte": d["numero_compte"],
                        "libelle": d["libelle"],
                        "montant": float(d["montant_ar"])
                    }
                    for d in achats_qs.values("date", "numero_compte", "libelle", "montant_ar")
                ]
            }
        
        return response

    def get_bfr(self, start_date=None, end_date=None, date_ref=None, annee=None, include_details=True):
        """
        Calcule le BFR = (Stocks + Créances clients) - Dettes fournisseurs
        Utilise la logique unifiée get_dashboard_kpis pour éviter les écarts.
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        target_date = end_date or date_ref or (date(annee, 12, 31) if annee else date.today())
        
        stocks = self._get_latest_bilan_sum(['3'], target_date, type_bilan='ACTIF')
        creances = self._get_latest_bilan_sum(['411'], target_date, type_bilan='ACTIF')
        dettes_fourn = self._get_latest_bilan_sum(['401'], target_date, type_bilan='PASSIF')
        
        bfr = float(stocks + creances - dettes_fourn)
        
        response = {
            "montant": bfr,
            "stocks": float(stocks),
            "creances_clients": float(creances),
            "dettes_fournisseurs": float(dettes_fourn),
            "date": target_date.strftime('%d/%m/%Y') if isinstance(target_date, date) else str(target_date)
        }

        if include_details:
            # Pour les détails, nous devons récupérer les lignes individuelles
            # en utilisant la même logique de "dernière ligne par compte"
            def get_bilan_details_for_prefix(prefix_list, target_date, type_bilan=None):
                q_compte = Q()
                for p in prefix_list:
                    q_compte |= Q(numero_compte__startswith=p)

                filters = Q(project_id=self.project_id) & q_compte & Q(date__lte=target_date)
                if type_bilan:
                    filters &= Q(type_bilan=type_bilan)

                latest_dates = Bilan.objects.filter(
                    Q(project_id=self.project_id) & q_compte & Q(date__lte=target_date)
                ).values('numero_compte').annotate(
                    max_date=Max('date')
                )

                latest_entries_filter = Q()
                for entry in latest_dates:
                    latest_entries_filter |= (
                        Q(numero_compte=entry['numero_compte']) & Q(date=entry['max_date'])
                    )
                
                if not latest_entries_filter:
                    return []

                return Bilan.objects.filter(filters & latest_entries_filter).values(
                    'date', 'numero_compte', 'libelle', 'montant_ar'
                ).order_by('-date')

            stocks_details = get_bilan_details_for_prefix(['3'], target_date, type_bilan='ACTIF')
            creances_details = get_bilan_details_for_prefix(['411'], target_date, type_bilan='ACTIF')
            dettes_fourn_details = get_bilan_details_for_prefix(['401'], target_date, type_bilan='PASSIF')

            response['details'] = {
                "stocks": [
                    {
                        "date": d['date'].strftime('%d/%m/%Y'),
                        "compte": d['numero_compte'],
                        "solde": float(d['montant_ar'])
                    } for d in stocks_details
                ],
                "creances_clients": [
                    {
                        "date": d['date'].strftime('%d/%m/%Y'),
                        "compte": d['numero_compte'],
                        "solde": float(d['montant_ar'])
                    } for d in creances_details
                ],
                "dettes_fournisseurs": [
                    {
                        "date": d['date'].strftime('%d/%m/%Y'),
                        "compte": d['numero_compte'],
                        "solde": float(d['montant_ar'])
                    } for d in dettes_fourn_details
                ]
            }
            response['nb_lignes'] = (
                len(response['details']['stocks']) +
                len(response['details']['creances_clients']) +
                len(response['details']['dettes_fournisseurs'])
            )

        return response

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

    def get_ratios_structure(self, start_date=None, end_date=None, date_ref=None, annee=None):
        """
        Calcule Leverage et Current Ratio
        """
        target_date = end_date or date_ref or (date(annee, 12, 31) if annee else date.today())
        
        cp_res = self.get_roe(start_date=start_date, end_date=target_date, annee=annee)
        cp = cp_res.get('capitaux_propres', 0)
        
        dettes_fin = Bilan.objects.filter(
            project_id=self.project_id,
            numero_compte__startswith='16',
            date__lte=target_date
        ).aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        dettes_fin_float = float(dettes_fin)
        
        leverage = (dettes_fin_float / cp) if cp != 0 else 0
        
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

    def get_marges_profitabilite(self, start_date=None, end_date=None, annee=None):
        """
        Calcule Marge Nette et Marge Opérationnelle
        """
        ca_data = self.get_chiffre_affaires(start_date, end_date, annee) or {}
        ca = ca_data.get('montant', 0) or 0
        
        res_net_data = self.get_resultat_net(start_date, end_date, annee) or {}
        res_net = res_net_data.get('montant', 0) or 0
        
        ebe_data = self.get_ebe(start_date, end_date, annee) or {}
        ebe = ebe_data.get('montant', 0) or 0
        
        marge_nette = (res_net / ca * 100) if ca != 0 else 0
        marge_ope = (ebe / ca * 100) if ca != 0 else 0
        
        return {
            "marge_nette": marge_nette,
            "marge_operationnelle": marge_ope,
            "ca": ca,
            "resultat_net": res_net,
            "ebe": ebe,
            "periode": ca_data.get('periode', 'N/A')
        }

    def get_rotation_stocks(self, start_date=None, end_date=None, annee=None):
        """
        Calcule la Rotation des stocks
        """
        if not annee and not (start_date and end_date):
            annee = date.today().year
            
        marge_brute = self.get_marge_brute(start_date=start_date, end_date=end_date, annee=annee)
        achats = marge_brute.get('achats', 0)
        
        bfr = self.get_bfr(start_date=start_date, end_date=end_date, annee=annee)
        stocks = bfr.get('stocks', 0)
        
        rotation = (achats / stocks) if stocks != 0 else 0
        jours = (365 / rotation) if rotation != 0 else 0
        
        return {
            "coefficient": rotation,
            "jours_stock": jours,
            "achats": achats,
            "stock_final": stocks,
            "periode": marge_brute.get('periode', 'N/A')
        }
    
    def get_tresorerie(self, start_date=None, end_date=None, date_fin=None, annee=None, include_details=True):
        """
        Calcule la trésorerie (comptes 51x + 53x)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        filters = Q(project_id=self.project_id) & (
            Q(numero_compte__startswith='51') | Q(numero_compte__startswith='53')
        )
        
        # Pour la trésorerie (Bilan), on prend l'état à la date de fin
        target_end = end_date or date_fin
        
        if target_end:
            filters &= Q(date__lte=target_end)
            final_date_str = target_end.strftime('%d/%m/%Y')
        elif annee:
            filters &= Q(date__year__lte=annee)
            final_date_str = f"31/12/{annee}"
        else:
            final_date_str = "aujourd'hui"
        
        balances = Balance.objects.filter(filters)

        totals = balances.aggregate(
            solde_debit_total=Sum('solde_debit'),
            solde_credit_total=Sum('solde_credit')
        )
        
        tresorerie = (totals['solde_debit_total'] or Decimal('0.00')) - \
                     (totals['solde_credit_total'] or Decimal('0.00'))
        
        response = {
            "montant": float(tresorerie),
            "date": final_date_str,
            "comptes": "51x (Banque) + 53x (Caisse)"
        }

        if include_details:
            details = balances.values(
                'date', 'numero_compte', 'solde_debit', 'solde_credit'
            ).order_by('-date')
            
            response['details'] = [
                {
                    "date": d['date'].strftime('%d/%m/%Y'),
                    "compte": d['numero_compte'],
                    "solde_debit": float(d['solde_debit']),
                    "solde_credit": float(d['solde_credit']),
                    "solde": float(d['solde_debit'] - d['solde_credit'])
                }
                for d in details
            ]
            response['nb_lignes'] = len(response['details'])
        
        return response
    
    def get_bilan_summary(self, date_bilan=None, annee=None, include_details=True):
        """
        Résumé du bilan (Actif vs Passif)
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
        
        target_date = date_bilan
        if annee:
            target_date = date(annee, 12, 31)
        elif not target_date:
            target_date = date.today()

        latest_date = Bilan.objects.filter(
            project_id=self.project_id,
            date__lte=target_date
        ).order_by('-date').values_list('date', flat=True).first()

        if not latest_date:
            return {
                "actif": 0.0, "passif": 0.0, "equilibre": 0.0,
                "date": f"aucune donnée avant le {target_date.strftime('%d/%m/%Y')}"
            }

        actif_qs = Bilan.objects.filter(
            project_id=self.project_id,
            date=latest_date,
            type_bilan='ACTIF'
        )
        
        passif_qs = Bilan.objects.filter(
            project_id=self.project_id,
            date=latest_date,
            type_bilan='PASSIF'
        )

        actif_total = actif_qs.aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        passif_total = passif_qs.aggregate(total=Sum('montant_ar'))['total'] or Decimal('0.00')
        
        response = {
            "actif": float(actif_total),
            "passif": float(passif_total),
            "equilibre": float(actif_total - passif_total),
            "date": latest_date.strftime('%d/%m/%Y')
        }

        if include_details:
            response['details'] = {
                "actif": [
                    {
                        "compte": d['numero_compte'],
                        "libelle": d['libelle'],
                        "montant": float(d['montant_ar'])
                    } for d in actif_qs.values('numero_compte', 'libelle', 'montant_ar')
                ],
                "passif": [
                    {
                        "compte": d['numero_compte'],
                        "libelle": d['libelle'],
                        "montant": float(d['montant_ar'])
                    } for d in passif_qs.values('numero_compte', 'libelle', 'montant_ar')
                ]
            }
            response['nb_lignes'] = (
                len(response['details']['actif']) +
                len(response['details']['passif'])
            )

        return response
    
    def compare_periodes(self, annee1, annee2):
        """
        Compare deux années (CA, Charges, Résultat) avec analyse au prorata
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

        # Calculer le nombre de mois uniques avec des mouvements pour chaque année
        def count_months(annee):
            return CompteResultat.objects.filter(
                project_id=self.project_id, 
                date__year=annee
            ).values('date__month').distinct().count() or 1 # Éviter division par 0

        nb_mois1 = count_months(annee1)
        nb_mois2 = count_months(annee2)
        
        return {
            "annee_1": {
                "annee": annee1,
                "chiffre_affaires": data_annee1["ca"]["montant"],
                "moyenne_mensuelle_ca": data_annee1["ca"]["montant"] / nb_mois1,
                "charges": data_annee1["charges"]["montant"],
                "moyenne_mensuelle_charges": data_annee1["charges"]["montant"] / nb_mois1,
                "resultat": data_annee1["resultat"]["montant"],
                "nb_mois_enregistres": nb_mois1
            },
            "annee_2": {
                "annee": annee2,
                "chiffre_affaires": data_annee2["ca"]["montant"],
                "moyenne_mensuelle_ca": data_annee2["ca"]["montant"] / nb_mois2,
                "charges": data_annee2["charges"]["montant"],
                "moyenne_mensuelle_charges": data_annee2["charges"]["montant"] / nb_mois2,
                "resultat": data_annee2["resultat"]["montant"],
                "nb_mois_enregistres": nb_mois2
            },
            "evolution": {
                "ca": data_annee2["ca"]["montant"] - data_annee1["ca"]["montant"],
                "ca_pct": ((data_annee2["ca"]["montant"] - data_annee1["ca"]["montant"]) / data_annee1["ca"]["montant"] * 100) if data_annee1["ca"]["montant"] != 0 else 0,
                
                "charges": data_annee2["charges"]["montant"] - data_annee1["charges"]["montant"],
                "charges_pct": ((data_annee2["charges"]["montant"] - data_annee1["charges"]["montant"]) / data_annee1["charges"]["montant"] * 100) if data_annee1["charges"]["montant"] != 0 else 0,
                
                "resultat": data_annee2["resultat"]["montant"] - data_annee1["resultat"]["montant"],
                "resultat_pct": ((data_annee2["resultat"]["montant"] - data_annee1["resultat"]["montant"]) / abs(data_annee1["resultat"]["montant"]) * 100) if data_annee1["resultat"]["montant"] != 0 else 0
            }
        }
    
    def _format_periode(self, start_date, end_date, annee):
        """Formate la période pour l'affichage"""
        if annee:
            return f"Année {annee}"
        elif start_date and end_date:
            return f"Du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}"
        else:
            return "Toute la période"

    def get_ventes_detaillees(self, start_date=None, end_date=None, annee=None):
        return self.get_chiffre_affaires(
            start_date=start_date,
            end_date=end_date,
            annee=annee,
            include_details=True
        )

    def get_charges_par_compte(self, start_date=None, end_date=None, annee=None):
        if not self.project:
            return {"error": "Projet non trouvé"}

        filters = Q(project_id=self.project_id) & Q(numero_compte__startswith='6')

        if annee:
            filters &= Q(date__year=annee)
        elif start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)

        queryset = CompteResultat.objects.filter(filters, nature='CHARGE')

        data = queryset.values('numero_compte', 'libelle').annotate(
            total=Sum('montant_ar')
        ).order_by('numero_compte')

        return {
            "total_charges": float(queryset.aggregate(total=Sum('montant_ar'))['total'] or 0),
            "details": [
                {
                    "compte": d['numero_compte'],
                    "libelle": d['libelle'],
                    "montant": float(d['total'])
                }
                for d in data
            ]
        }

    def get_achats_marchandises(self, start_date=None, end_date=None, annee=None):
        if not self.project:
            return {"error": "Projet non trouvé"}

        filters = Q(project_id=self.project_id) & Q(numero_compte__startswith='60')

        if annee:
            filters &= Q(date__year=annee)
        elif start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)

        queryset = CompteResultat.objects.filter(filters, nature='CHARGE')

        return {
            "total_achats": float(queryset.aggregate(total=Sum('montant_ar'))['total'] or 0),
            "details": [
                {
                    "date": cr.date.strftime('%d/%m/%Y'),
                    "compte": cr.numero_compte,
                    "libelle": cr.libelle,
                    "montant": float(cr.montant_ar)
                }
                for cr in queryset
            ]
        }

    def get_annees_ca_superieur(self, seuil: float) -> list:
        """Retourne les années où le CA dépasse un seuil donné"""
    
    
        resultats = (
            CompteResultat.objects
            .filter(project_id=self.project_id, numero_compte__startswith='70', nature='PRODUIT')
            .annotate(annee=ExtractYear('date'))
            .values('annee')
            .annotate(total=Sum('montant_ar'))
            .filter(total__gt=seuil)
            .order_by('annee')
        )
        return [{"annee": r['annee'], "montant": float(r['total'])} for r in resultats]

    # ========================================
    # NOUVELLES MÉTHODES POUR EXPERT-COMPTABLE
    # ========================================

    def verify_balance(self, start_date=None, end_date=None, annee=None):
        """
        Vérifie l'équilibre Débit/Crédit sur une période donnée
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        filters = Q(project_id=self.project_id)
        if annee:
            filters &= Q(date__year=annee)
        elif start_date and end_date:
            filters &= Q(date__gte=start_date, date__lte=end_date)
            
        stats = Journal.objects.filter(filters).aggregate(
            total_debit=Sum('debit_ar'),
            total_credit=Sum('credit_ar')
        )
        
        debit = stats['total_debit'] or Decimal('0.00')
        credit = stats['total_credit'] or Decimal('0.00')
        diff = debit - credit
        
        return {
            "is_balanced": abs(diff) < Decimal('0.01'),
            "total_debit": float(debit),
            "total_credit": float(credit),
            "variation": float(diff),
            "periode": self._format_periode(start_date, end_date, annee)
        }

    def get_structured_bilan(self, start_date=None, end_date=None, date_ref=None, annee=None):
        """
        Retourne un bilan structuré par préfixes, aligné avec le dashboard UI.
        """
        # Filtres de base
        if not self.project:
            return {"error": "Projet non trouvé"}

        base_q = Q(project_id=self.project_id)
        if annee:
            base_q &= Q(date__year=annee)
        elif start_date and end_date:
            base_q &= Q(date__gte=start_date, date__lte=end_date)
        elif end_date or date_ref:
            target = end_date or date_ref
            base_q &= Q(date=target)
        else:
            return {"error": "Veuillez préciser une année ou une période"}

        # Vérifier qu'il y a des données
        if not Bilan.objects.filter(base_q).exists():
            label = f"Exercice {annee}" if annee else (f"Période {start_date} - {end_date}" if start_date else str(date_ref or end_date))
            return {
                "date": label,
                "actif": {}, "passif_equity": {},
                "totals": {"total_actif": 0, "total_passif": 0, "total_equity": 0, "equilibre": 0}
            }

        # Agréger par compte sur toute l'année (identique à la logique du dashboard)
        def get_items_annual(prefixes, type_bilan):
            q = Q()
            for p in prefixes:
                q |= Q(numero_compte__startswith=p)
            qs = (
                Bilan.objects.filter(base_q, type_bilan=type_bilan).filter(q)
                .values('numero_compte', 'libelle')
                .annotate(montant=Sum('montant_ar'))
                .order_by('numero_compte')
            )
            return [
                {"compte": i['numero_compte'], "libelle": i['libelle'], "montant": float(i['montant'] or 0)}
                for i in qs
            ]

        # Catégories alignées avec le dashboard UI
        actif_nc = get_items_annual(["2"], "ACTIF")          # Immobilisations
        actif_c  = get_items_annual(["3", "4", "5"], "ACTIF") # Stocks, Créances (411...), Trésorerie

        res_net = self.get_resultat_net(start_date=start_date, end_date=end_date, annee=annee)
        rn_val = float(res_net.get("montant", 0)) if "error" not in res_net else 0.0

        equity    = get_items_annual(["10", "11", "12", "13"], "PASSIF")  # Capitaux propres
        equity.append({"compte": "RN", "libelle": "Résultat Net de l'exercice", "montant": rn_val})
        passif_nc = get_items_annual(["15", "16", "17"], "PASSIF")         # Dettes LT
        passif_c  = get_items_annual(["40", "41", "42", "43", "44", "45", "46", "47", "48", "51", "52"], "PASSIF")  # Dettes CT

        total_actif = sum(i['montant'] for i in actif_nc) + sum(i['montant'] for i in actif_c)
        total_e     = sum(i['montant'] for i in equity)
        total_p     = sum(i['montant'] for i in passif_nc) + sum(i['montant'] for i in passif_c)

        label_date = f"Exercice {annee}" if annee else (self._format_periode(start_date, end_date, None) if start_date else str(date_ref or end_date))

        return {
            "date": label_date,
            "actif": {
                "Actifs non courants": actif_nc,
                "Actifs courants": actif_c
            },
            "passif_equity": {
                "Capitaux propres": equity,
                "Passifs non courants": passif_nc,
                "Passifs courants": passif_c
            },
            "totals": {
                "total_actif": round(total_actif, 2),
                "total_passif": round(total_p, 2),
                "total_equity": round(total_e, 2),
                "equilibre": round(total_actif - (total_e + total_p), 2)
            }
        }
    def get_comparative_report(self, annee1, annee2):
        """
        Génère un rapport comparatif complet entre deux années
        """
        try:
            comparaison = self.compare_periodes(annee1, annee2)
            
            # Enrichir avec variations en pourcentage
            for key in ["ca", "charges", "resultat"]:
                val1 = comparaison["annee_1"]["chiffre_affaires"] if key == "ca" else comparaison["annee_1"][key]
                val2 = comparaison["annee_2"]["chiffre_affaires"] if key == "ca" else comparaison["annee_2"][key]
                
                var_abs = comparaison["evolution"][key]
                var_pct = (var_abs / val1 * 100) if val1 != 0 else 0
                
                comparaison["evolution"][f"{key}_pct"] = round(var_pct, 2)
            
            # Ajouter analyse stratégique basique
            analyse = []
            if comparaison["evolution"]["ca_pct"] > 5:
                analyse.append(f"Croissance solide du CA (+{comparaison['evolution']['ca_pct']}%).")
            elif comparaison["evolution"]["ca_pct"] < 0:
                analyse.append(f"Baisse de l'activité ({comparaison['evolution']['ca_pct']}%). À surveiller.")
                
            if comparaison["evolution"]["charges_pct"] > comparaison["evolution"]["ca_pct"]:
                analyse.append("Attention : les charges augmentent plus vite que le chiffre d'affaires. Risque sur la rentabilité.")
            
            comparaison["analyse"] = " ".join(analyse)
            return comparaison
            
        except Exception as e:
            return {"error": f"Erreur lors de la comparaison: {str(e)}"}

    def get_etats_financiers(self, start_date=None, end_date=None, annee=None):
        """
        Génère les états financiers : Bilan + Compte de Résultat
        """
        if not self.project:
            return {"error": "Projet non trouvé"}
            
        return {
            "bilan": self.get_structured_bilan(start_date=start_date, end_date=end_date, annee=annee),
            "compte_de_resultat": self.get_resultat_net(start_date=start_date, end_date=end_date, annee=annee)
        }
