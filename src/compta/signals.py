from decimal import Decimal
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
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

    # Dernier solde du compte
    last_entry = (
        GrandLivre.objects
        .filter(numero_compte=instance.numero_compte)
        .order_by('-date', '-id')
        .first()
    )
    last_solde = last_entry.solde if last_entry else Decimal('0.00')

    # Convertir les montants en Decimal pour éviter les erreurs
    debit_ar = Decimal(str(instance.debit_ar))
    credit_ar = Decimal(str(instance.credit_ar))

    # ✅ SOLDE = ancien + débit - crédit
    new_solde = last_solde + debit_ar - credit_ar

    # ✅ DATE COMPTABLE = DATE DE LA FACTURE
    GrandLivre.objects.create(
        journal=instance,
        numero_compte=instance.numero_compte,
        # date=instance.created_at.date(), #changer 
        date=instance.date,
        numero_piece=instance.numero_piece,
        libelle=instance.libelle,
        debit=debit_ar,
        credit=credit_ar,
        solde=new_solde,
        # description=instance.libelle,
    )


@receiver(post_save, sender=Journal)
def detect_payslip_payment(sender, instance, created, **kwargs):
    """
    Détecte automatiquement les paiements de paie depuis les relevés bancaires
    et génère les écritures de contrepartie correspondantes.
    
    Logique :
    - Si type_journal == "BANQUE" ET compte == "512" (crédit)
    - Recherche dans le libellé ou la référence un N° de fiche de paie
    - Si trouvé, génère automatiquement l'écriture de paiement correspondante
    """
    if not created:
        return
    
    # Vérifier si c'est un relevé bancaire (sortie de banque)
    if instance.type_journal != "BANQUE" or instance.numero_compte != "512":
        return
    
    # Vérifier si c'est un crédit (sortie d'argent)
    if instance.credit_ar <= 0:
        return
    
    # Rechercher une référence de fiche de paie dans le libellé ou numero_piece
    # Pattern: PAIE-YYYY-XXX ou similaire
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
    
    # Rechercher la fiche de paie correspondante
    from ocr.models import FormSource, FileSource
    
    # Chercher dans FormSource
    form_source = FormSource.objects.filter(
        piece_type="Fiche de paie",
        ref_file__icontains=reference
    ).first()
    
    # Chercher dans FileSource si pas trouvé
    if not form_source:
        file_source = FileSource.objects.filter(
            piece_type="Fiche de paie",
            ref_file__icontains=reference
        ).first()
    else:
        file_source = None
    
    # Si aucune fiche de paie trouvée, arrêter
    if not form_source and not file_source:
        print(f"⚠️ Référence paie détectée ({reference}) mais aucune fiche de paie trouvée")
        return
    
    # Récupérer les montants depuis description_json
    source = form_source or file_source
    try:
        description = json.loads(source.description) if isinstance(source.description, str) else source.description
    except:
        print(f"❌ Erreur parsing description pour {reference}")
        return
    
    net_a_payer = Decimal(str(description.get('net_a_payer', 0)))
    total_cotisation_salariale = Decimal(str(description.get('total_cotisation_salariale', 0)))
    total_cotisation_patronale = Decimal(str(description.get('total_cotisation_patronale', 0)))
    total_cotisations = total_cotisation_salariale + total_cotisation_patronale
    retenue_source = Decimal(str(description.get('retenue_source', 0)))
    
    # Déterminer quel type de paiement c'est basé sur le montant
    montant = Decimal(str(instance.credit_ar))
    
    compte_debit = None
    libelle_paiement = None
    
    # Tolérance de 1 Ar pour les arrondis
    tolerance = Decimal('1.00')
    
    if abs(montant - net_a_payer) < tolerance:
        compte_debit = "421"
        libelle_paiement = f"Paiement salaire - {reference}"
    elif abs(montant - total_cotisations) < tolerance:
        compte_debit = "431"
        libelle_paiement = f"Paiement cotisations sociales - {reference}"
    elif abs(montant - retenue_source) < tolerance:
        compte_debit = "442"
        libelle_paiement = f"Paiement IRSA - {reference}"
    else:
        # Montant ne correspond à aucun paiement attendu
        print(f"⚠️ Montant {montant} ne correspond à aucun paiement pour {reference}")
        print(f"   Net: {net_a_payer}, Cotisations: {total_cotisations}, IRSA: {retenue_source}")
        return
    
    # Vérifier si l'écriture de contrepartie n'existe pas déjà
    existing = Journal.objects.filter(
        date=instance.date,
        numero_piece=instance.numero_piece,
        type_journal="BANQUE",
        numero_compte=compte_debit,
        debit_ar=montant
    ).exists()
    
    if existing:
        print(f"ℹ️ Écriture de paiement déjà existante pour {reference}")
        return
    
    # Générer l'écriture de contrepartie (débit du compte de dette)
    libelle_compte = get_pcg_label(compte_debit)
    
    Journal.objects.create(
        date=instance.date,
        numero_piece=instance.numero_piece,
        type_journal="BANQUE",
        numero_compte=compte_debit,
        libelle=libelle_compte,
        debit_ar=montant,
        credit_ar=Decimal('0.00')
    )
    
    print(f"✅ Paiement paie détecté et enregistré : {libelle_paiement} ({montant} Ar)")


@receiver(post_save, sender=GrandLivre)
def generate_balance(sender, instance, **kwargs):
    """
    Met à jour automatiquement la Balance après chaque écriture du Grand Livre
    """
    balance, created = Balance.objects.get_or_create(
        numero_compte=instance.numero_compte,
        date=instance.date,  # date comptable
        defaults={"libelle": instance.libelle}
    )
    balance.calculate_from_grand_livre()



@receiver(post_delete, sender=Journal)
def delete_journal_related(sender, instance, **kwargs):
    """
    Supprime toutes les écritures liées à un journal supprimé :
    - GrandLivre
    - Balance
    - Bilan
    - CompteResultat
    """
    try:
        # Récupère toutes les lignes du GrandLivre liées à ce journal
        grand_livres = GrandLivre.objects.filter(journal=instance)

        for gl in grand_livres:
            # Supprime les balances liées à ce compte et à cette date
            balances = Balance.objects.filter(numero_compte=gl.numero_compte, date=gl.date)
            for balance in balances:
                # Supprime les états financiers liés
                CompteResultat.objects.filter(balance=balance).delete()
                Bilan.objects.filter(balance=balance).delete()
                balance.delete()

            # Supprime la ligne du GrandLivre
            gl.delete()

        print(f"DEBUG: Suppression en cascade réussie pour Journal ID {instance.id}")

    except Exception as e:
        print(f"❌ ERREUR suppression cascade pour Journal ID {instance.id} : {e}")






@receiver(post_save, sender=GrandLivre)
def generate_balance(sender, instance, **kwargs):
    """
    Met à jour automatiquement la Balance après chaque écriture du Grand Livre
    """
    balance, created = Balance.objects.get_or_create(
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
        grand_livres = GrandLivre.objects.filter(journal=instance)

        for gl in grand_livres:
            balances = Balance.objects.filter(numero_compte=gl.numero_compte, date=gl.date)
            for balance in balances:
                CompteResultat.objects.filter(balance=balance).delete()
                Bilan.objects.filter(balance=balance).delete()
                balance.delete()
            gl.delete()

        print(f"✅ Suppression en cascade réussie pour Journal ID {instance.id}")

    except Exception as e:
        print(f"❌ ERREUR suppression cascade pour Journal ID {instance.id} : {e}")


@receiver(post_save, sender=Balance)
def generate_financial_statements(sender, instance, **kwargs):
    """
    Génère le Bilan et le Compte de Résultat avec gestion correcte des soldes nets
    """
    try:
        code = instance.numero_compte
        if not code or not isinstance(code, str):
            return

        label = get_pcg_label(code)
        solde_debit = Decimal(instance.solde_debit or 0)
        solde_credit = Decimal(instance.solde_credit or 0)

        # Recherche de la règle PCG
        # ============================
        regle = None
        best_len = 0
        for prefix, data in PCG_MAPPING.items():
            if code.startswith(prefix):
                # On garde la regle avec le préfixe le plus long (plus précis)
                if len(prefix) > best_len:
                    regle = data
                    best_len = len(prefix)

        if not regle:
            return

        # ============================
        # COMPTE DE RÉSULTAT (6 & 7)
        # ============================
        if 'nature' in regle:
            montant = solde_debit if regle['nature'] == 'CHARGE' else solde_credit

            CompteResultat.objects.update_or_create(
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

        # ============================
        # BILAN (Classes 1 → 5)
        # ============================
        type_bilan = regle.get('type_bilan')
        categorie = regle.get('categorie')
        is_negative = regle.get('is_negative', False)

        # ✅ CORRECTION 1 : CAPITAL (10x)
        # Capital = Solde créditeur net
        if code.startswith('10'):
            type_bilan = 'PASSIF'
            categorie = 'CAPITAUX_PROPRES'
            
            # ✅ Calcul du solde net créditeur
            montant = solde_credit - solde_debit
            
            # Ne créer que si montant > 0
            if montant <= 0:
                # Supprimer si existant
                Bilan.objects.filter(
                    balance=instance,
                    numero_compte=code,
                    date=instance.date
                ).delete()
                return
            
            Bilan.objects.update_or_create(
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
            
            # ✅ IMPORTANT : Supprimer le CP temporaire si un capital réel existe
            Bilan.objects.filter(
                date=instance.date,
                numero_compte='101',
                libelle__icontains='calculé'
            ).delete()
            
            return

        # ✅ CORRECTION 2 : CLIENTS (41)
        # Clients = Solde débiteur net
        if code.startswith('41'):
            type_bilan = 'ACTIF'
            categorie = 'ACTIF_COURANTS'
            
            # ✅ Calcul du solde net débiteur
            montant = solde_debit - solde_credit
            
            # Si négatif (client créditeur), mettre à 0
            if montant < 0:
                montant = Decimal('0.00')
            
            # ✅ Adapter le libellé
            if montant == 0:
                label = "Clients (soldé)"
            else:
                label = "Clients"
            
            # ✅ Toujours créer pour la traçabilité
            Bilan.objects.update_or_create(
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
            return

        # ✅ CORRECTION 3 : BANQUE (51)
        if code.startswith('51'):
            # Calculer le solde net
            montant = solde_debit - solde_credit
            
            if montant < 0:
                # Découvert bancaire → Passif
                type_bilan = 'PASSIF'
                categorie = 'PASSIFS_COURANTS'
                label = "Concours bancaires courants"
                montant = abs(montant)
            else:
                # Solde positif → Actif
                type_bilan = 'ACTIF'
                categorie = 'ACTIF_COURANTS'
                label = "Banques comptes courants"

        # ✅ CORRECTION 4 : FOURNISSEURS (40)
        elif code.startswith('40'):
            type_bilan = 'PASSIF'
            categorie = 'PASSIFS_COURANTS'
            
            # Calculer le solde net créditeur
            montant = solde_credit - solde_debit
            
            if montant < 0:
                montant = Decimal('0.00')
            
            if montant == 0:
                label = "Fournisseurs (soldé)"
            else:
                label = "Fournisseurs et comptes rattachés"

        # ✅ AUTRES COMPTES : Logique par défaut
        else:
            # Pour l'actif : solde débiteur net
            if type_bilan == 'ACTIF':
                montant = solde_debit - solde_credit
                if montant < 0:
                    montant = Decimal('0.00')
            
            # Pour le passif : solde créditeur net
            elif type_bilan == 'PASSIF':
                montant = solde_credit - solde_debit
                if montant < 0:
                    montant = Decimal('0.00')
            
            else:
                # Fallback : prendre le plus grand
                if solde_debit > solde_credit:
                    montant = solde_debit
                elif solde_credit > solde_debit:
                    montant = solde_credit
                else:
                    return

        # Cas soustractif (amortissements, provisions)
        if is_negative:
            montant = -montant

        # Ne créer que si montant significatif (SAUF pour Clients/Fournisseurs qu'on veut voir soldés)
        if montant == 0 and not code.startswith(('40', '41')):
            # Si le montant est 0, on SUPPRIME l'entrée du Bilan si elle existe
            # (pour éviter que des dettes soldées restent affichées)
            Bilan.objects.filter(
                balance=instance,
                numero_compte=code,
                date=instance.date
            ).delete()
            return

        # Création ou mise à jour du Bilan
        Bilan.objects.update_or_create(
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

        # ============================
        # ✅ CP TEMPORAIRE : UNIQUEMENT pour comptes bancaires (51x)
        # ============================
        
        # Vérifier si un capital réel existe
        capital_reel_existe = Bilan.objects.filter(
            date=instance.date,
            numero_compte__startswith='10',
            type_bilan='PASSIF'
        ).exclude(libelle__icontains='calculé').exists()

        # ❌ Si un capital réel existe, supprimer tout CP temporaire
        if capital_reel_existe:
            Bilan.objects.filter(
                date=instance.date,
                numero_compte='101',
                libelle__icontains='calculé'
            ).delete()
            return

        # ✅ Supprimer l'ancien CP temporaire s'il existe (pour le recréer à jour)
        Bilan.objects.filter(
            date=instance.date,
            numero_compte='101',
            libelle__icontains='calculé'
        ).delete()
        
        # Calculer les totaux
        total_actif = sum([
            b.montant_ar for b in Bilan.objects.filter(
                date=instance.date,
                type_bilan='ACTIF'
            )
        ])
        
        total_passif = sum([
            b.montant_ar for b in Bilan.objects.filter(
                date=instance.date,
                type_bilan='PASSIF'
            ).exclude(numero_compte='101')  # Exclure le CP temporaire du calcul
        ])

        # ✅ AJOUT : Inclure le Résultat Net dans le passif théorique
        # (Car Bénéfice = Capitaux Propres)
        resultat_net = Decimal('0.00')
        cr_items = CompteResultat.objects.filter(date=instance.date)
        for item in cr_items:
            if item.nature == 'PRODUIT':
                resultat_net += item.montant_ar
            elif item.nature == 'CHARGE':
                resultat_net -= item.montant_ar
        
        # Le Passif Total doit inclure le Résultat pour équilibrer l'Actif
        passif_avec_resultat = total_passif + resultat_net

        cp_temp = total_actif - passif_avec_resultat
        
        # S'assurer qu'on ne crée pas de tout petits montants (arrondis floating point)
        if abs(cp_temp) < Decimal('0.01'):
            cp_temp = Decimal('0.00')

        # ✅ Créer CP temporaire uniquement si positif (et significatif)
        if cp_temp > 0:
            # ✅ Créer une balance fictive pour le CP temporaire
            balance_cp, _ = Balance.objects.get_or_create(
                numero_compte='101',
                date=instance.date,
                defaults={
                    'libelle': 'Capitaux propres (calculé - encaissement)',
                    'solde_debit': Decimal('0.00'),
                    'solde_credit': cp_temp
                }
            )
            
            Bilan.objects.update_or_create(
                numero_compte='101',
                date=instance.date,
                defaults={
                    'balance': balance_cp,
                    'libelle': 'Capitaux propres (calculé - encaissement)',
                    'montant_ar': cp_temp,
                    'type_bilan': 'PASSIF',
                    'categorie': 'CAPITAUX_PROPRES'
                }
            )

    except Exception as e:
        import traceback
        print(f"❌ ERREUR SIGNAL BILAN pour {instance.numero_compte} : {e}")
        print(traceback.format_exc())