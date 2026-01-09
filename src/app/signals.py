from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
import uuid

from app.models import CustomUser


# @receiver(post_save, sender=CustomUser)
# def assign_trial_plan(sender, instance, created, **kwargs):
#     """
#     Lorsqu'un nouvel utilisateur est créé, on lui attribue automatiquement :
#     - un plan 'trial'
#     - un abonnement actif
#     - un paiement 'completed' gratuit
#     - une facture correspondante
#     """
#     if not created:
#         return

#     try:
#         # 1 — Récupérer ou créer le plan "Trial"
#         # trial_plan = Plan.objects.filter(name="trial").first()

#         # 2 — Créer un abonnement (Subscription)
#         # start_date = timezone.now()
#         # end_date = start_date + timezone.timedelta(days=trial_plan.duration_days)

#         # subscription = Subscription.objects.create(
#         #     user=instance,
#         #     plan=trial_plan,
#         #     start_date=start_date,
#         #     end_date=end_date,
#         #     chat_limit=trial_plan.chat_limit,
#         #     is_active=True,
#         # )

#         # 3 — Créer un paiement (Payment)
#         # payment = Payment.objects.create(
#         #     user=instance,
#         #     subscription=subscription,
#         #     amount=Decimal("0.00"),
#         #     currency="USD",
#         #     status="completed",  # Paiement réussi (gratuit)
#         # )

#         # 4- Créer une facture (Billing)
#         # invoice_number = f"INV-{uuid.uuid4().hex[:10].upper()}"
#         # Billing.objects.create(
#         #     user=instance,
#         #     payment=payment,
#         #     subscription=subscription,
#         #     full_name=instance.username or instance.email,
#         #     email=instance.email,
#         #     company_name=None,
#         #     address_line1="Not provided",
#         #     city="N/A",
#         #     total_amount=Decimal("0.00"),
#         #     currency="USD",
#         #     invoice_number=invoice_number,
#         #     is_paid=True,
#         #     paid_at=timezone.now(),
#         # )

#         # print(f"[SIGNAL] Plan Trial attribué à {instance.email}")
#         pass

#     except Exception as e:
#         print(f"[SIGNAL ERROR] Impossible d’attribuer le plan Trial à {instance.email}: {e}")
