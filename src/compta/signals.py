from decimal import Decimal
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from django.db import models
from ocr.pcg_loader import get_pcg_label, PCG_MAPPING
from .models import Journal, GrandLivre, Balance, Bilan, CompteResultat


@receiver(post_save, sender=Journal)
def generate_grand_livre(sender, instance, created, **kwargs):
    """
    Génère automatiquement une ligne dans le Grand Livre
    à chaque création d'une écriture Journal.
    """
    if not created:
        return

    def _generate():
        # ✅ FILTRE PAR PROJET OBLIGATOIRE
        last_entry = (
            GrandLivre.objects
            .filter(project=instance.project, numero_compte=instance.numero_compte)
            .order_by('-date', '-id')
            .first()
        )
        last_solde = last_entry.solde if last_entry else Decimal('0.00')

        debit_ar = Decimal(str(instance.debit_ar))
        credit_ar = Decimal(str(instance.credit_ar))
        new_solde = last_solde + debit_ar - credit_ar

        GrandLivre.objects.create(
            project=instance.project, # ✅ ASSIGNATION PROJET
            journal=instance,
            numero_compte=instance.numero_compte,
            date=instance.date,
            numero_piece=instance.numero_piece,
            libelle=instance.libelle,
            debit=debit_ar,
            credit=credit_ar,
            solde=new_solde,
        )
    
    from django.db import transaction
    transaction.on_commit(_generate)


@receiver(post_save, sender=Journal)
def detect_payslip_payment(sender, instance, created, **kwargs):
    """
    Détecte automatiquement les paiements de paie depuis les relevés bancaires
    """
    if not created or instance.type_journal != "BANQUE" or instance.numero_compte != "512" or instance.credit_ar <= 0:
        return
    
    import re
    import json
    pattern = r'PAIE[-_]?\d{4}[-_]?\d+'
    
    reference = None
    if re.search(pattern, str(instance.numero_piece), re.IGNORECASE):
        reference = re.search(pattern, str(instance.numero_piece), re.IGNORECASE).group(0)
    elif re.search(pattern, str(instance.libelle), re.IGNORECASE):
        reference = re.search(pattern, str(instance.libelle), re.IGNORECASE).group(0)
    
    if not reference:
        return
    
    from ocr.models import FormSource, FileSource
    
    # ✅ FILTRE PAR PROJET SI POSSIBLE (ou recherche globale si cross-project)
    # Pour l'instant on garde la recherche globale pour les documents sources
    form_source = FormSource.objects.filter(piece_type="Fiche de paie", ref_file__icontains=reference).first()
    if not form_source:
        file_source = FileSource.objects.filter(piece_type="Fiche de paie", ref_file__icontains=reference).first()
    else:
        file_source = None
    
    if not form_source and not file_source:
        return
    
    source = form_source or file_source
    try:
        description = json.loads(source.description) if isinstance(source.description, str) else source.description
    except:
        return
    
    net_a_payer = Decimal(str(description.get('net_a_payer', 0)))
    total_cotisations = Decimal(str(description.get('total_cotisation_salariale', 0))) + Decimal(str(description.get('total_cotisation_patronale', 0)))
    retenue_source = Decimal(str(description.get('retenue_source', 0)))
    
    montant = Decimal(str(instance.credit_ar))
    compte_debit = None
    tolerance = Decimal('1.00')
    
    if abs(montant - net_a_payer) < tolerance:
        compte_debit = "421"
    elif abs(montant - total_cotisations) < tolerance:
        compte_debit = "431"
    elif abs(montant - retenue_source) < tolerance:
        compte_debit = "442"
    else:
        return
    
    # ✅ FILTRE PAR PROJET POUR EXISTENCE
    existing = Journal.objects.filter(
        project=instance.project,
        date=instance.date,
        numero_piece=instance.numero_piece,
        type_journal="BANQUE",
        numero_compte=compte_debit,
        debit_ar=montant
    ).exists()
    
    if not existing:
        Journal.objects.create(
            project=instance.project, # ✅ ASSIGNATION PROJET
            date=instance.date,
            numero_piece=instance.numero_piece,
            type_journal="BANQUE",
            numero_compte=compte_debit,
            libelle=get_pcg_label(compte_debit),
            debit_ar=montant,
            credit_ar=Decimal('0.00')
        )


@receiver(post_save, sender=GrandLivre)
def generate_balance(sender, instance, **kwargs):
    """
    Met à jour automatiquement la Balance après chaque écriture du Grand Livre
    """
    # ✅ FILTRE PAR PROJET OBLIGATOIRE DANS GET_OR_CREATE
    balance, created = Balance.objects.get_or_create(
        project=instance.project,
        numero_compte=instance.numero_compte,
        date=instance.date,
        defaults={"libelle": instance.libelle}
    )
    balance.calculate_from_grand_livre()


@receiver(post_delete, sender=Journal)
def delete_journal_related(sender, instance, **kwargs):
    """
    Supprime toutes les écritures liées à un journal supprimé
    """
    try:
        # Note: GrandLivre est déjà filtré par le ForeignKey journal=instance
        grand_livres = GrandLivre.objects.filter(journal=instance)

        for gl in grand_livres:
            # ✅ FILTRE PAR PROJET POUR LES BALANCES
            balances = Balance.objects.filter(project=instance.project, numero_compte=gl.numero_compte, date=gl.date)
            for balance in balances:
                CompteResultat.objects.filter(balance=balance).delete()
                Bilan.objects.filter(balance=balance).delete()
                balance.delete()
            gl.delete()
    except Exception as e:
        print(f"[ERROR] ERREUR suppression cascade pour Journal ID {instance.id} : {e}")


@receiver(post_save, sender=Balance)
def generate_financial_statements(sender, instance, **kwargs):
    """
    Génère le Bilan et le Compte de Résultat avec gestion correcte des soldes nets et isolation projet
    """
    try:
        code = instance.numero_compte
        project = instance.project
        if not code or not isinstance(code, str):
            return

        label = get_pcg_label(code)
        solde_debit = Decimal(instance.solde_debit or 0)
        solde_credit = Decimal(instance.solde_credit or 0)

        regle = None
        best_len = 0
        for prefix, data in PCG_MAPPING.items():
            if code.startswith(prefix) and len(prefix) > best_len:
                regle = data
                best_len = len(prefix)

        if not regle:
            return

        # ===================================================
        # COMPTE DE RÉSULTAT (Classes 6 & 7)
        # ===================================================
        if 'nature' in regle:
            montant = solde_debit if regle['nature'] == 'CHARGE' else solde_credit
            CompteResultat.objects.update_or_create(
                project=project, # ✅ PROJET
                balance=instance,
                numero_compte=code,
                date=instance.date,
                defaults={
                    'libelle': label,
                    'montant_ar': montant,
                    'nature': regle['nature']
                }
            )
            
            return

        # ===================================================
        # BILAN (Classes 1 → 5)
        # ===================================================
        type_bilan = regle.get('type_bilan')
        categorie = regle.get('categorie')
        is_negative = regle.get('is_negative', False)

        # Logique spécifique par compte
        if code.startswith('10'):
            type_bilan, categorie, montant = 'PASSIF', 'CAPITAUX_PROPRES', solde_credit - solde_debit
            if montant <= 0:
                Bilan.objects.filter(project=project, balance=instance, numero_compte=code, date=instance.date).delete()
                _update_calculated_equity(project, instance.date)
                return
        elif code.startswith('41'):
            type_bilan, categorie, montant = 'ACTIF', 'ACTIF_COURANTS', max(solde_debit - solde_credit, Decimal('0.00'))
            label = "Clients" if montant > 0 else "Clients (soldé)"
        elif code.startswith('51'):
            montant_net = solde_debit - solde_credit
            if montant_net < 0:
                type_bilan, categorie, montant, label = 'PASSIF', 'PASSIFS_COURANTS', abs(montant_net), "Concours bancaires courants"
            else:
                type_bilan, categorie, montant, label = 'ACTIF', 'ACTIF_COURANTS', montant_net, "Banques comptes courants"
        elif code.startswith('40'):
            type_bilan, categorie, montant = 'PASSIF', 'PASSIFS_COURANTS', max(solde_credit - solde_debit, Decimal('0.00'))
            label = "Fournisseurs et comptes rattachés" if montant > 0 else "Fournisseurs (soldé)"
        else:
            if type_bilan == 'ACTIF':
                montant = max(solde_debit - solde_credit, Decimal('0.00'))
            elif type_bilan == 'PASSIF':
                montant = max(solde_credit - solde_debit, Decimal('0.00'))
            else:
                montant = max(solde_debit, solde_credit)

        if is_negative: montant = -montant

        if montant == 0 and not code.startswith(('40', '41')):
            Bilan.objects.filter(project=project, balance=instance, numero_compte=code, date=instance.date).delete()
        else:
            Bilan.objects.update_or_create(
                project=project, # ✅ PROJET
                balance=instance,
                numero_compte=code,
                date=instance.date,
                defaults={
                    'libelle': label,
                    'montant_ar': montant,
                    'type_bilan': type_bilan,
                    'categorie': categorie
                }
            )
        

    except Exception as e:
        print(f"[ERROR] ERREUR SIGNAL STATEMENTS pour {instance.numero_compte} : {e}")



