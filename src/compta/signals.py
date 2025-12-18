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

@receiver(post_save, sender=Balance)
def generate_financial_statements(sender, instance, **kwargs):
    try:
        code = instance.numero_compte

        if not code or not isinstance(code, str):
            return

        label = get_pcg_label(code)
        solde_debit = Decimal(instance.solde_debit or 0)
        solde_credit = Decimal(instance.solde_credit or 0)

        # ============================
        # ✅ RECHERCHE DE LA RÈGLE PCG
        # ============================
        regle = None
        for prefix, data in PCG_MAPPING.items():
            if code.startswith(prefix):
                regle = data
                break

        if not regle:
            return

        # ============================
        # ✅ COMPTE DE RÉSULTAT (6 & 7)
        # ============================
        if 'nature' in regle:
            montant = solde_debit if regle['nature'] == 'CHARGE' else solde_credit

            CompteResultat.objects.update_or_create(
                balance=instance,
                defaults={
                    'numero_compte': code,
                    'libelle': label,
                    'montant_ar': montant,
                    'nature': regle['nature'],
                    'date': instance.date
                }
            )
            return

        # ============================
        # ✅ BILAN (Classes 1 → 5)
        # ============================
        type_bilan = regle.get('type_bilan')
        categorie = regle.get('categorie')
        is_negative = regle.get('is_negative', False)

        # ✅ LOGIQUE SPÉCIFIQUE BANQUE (51)
        if code.startswith('51'):
            if solde_credit > 0:
                type_bilan = 'PASSIF'
                categorie = 'PASSIFS_COURANTS'
                label = "Concours bancaires courants"
            else:
                type_bilan = 'ACTIF'
                categorie = 'ACTIF_COURANTS'
                label = "Banque"

        # ✅ Règle de solde automatique
        if solde_debit > 0:
            montant = solde_debit
        elif solde_credit > 0:
            montant = solde_credit
        else:
            return

        # ✅ Cas soustractif (amortissements, provisions)
        if is_negative:
            montant = -montant

        Bilan.objects.update_or_create(
            balance=instance,
            defaults={
                'numero_compte': code,
                'libelle': label,
                'montant_ar': montant,
                'type_bilan': type_bilan,
                'categorie': categorie,
                'date': instance.date
            }
        )

    except Exception as e:
        print(f"❌ ERREUR SIGNAL BILAN / RÉSULTAT pour {instance.numero_compte} : {e}")