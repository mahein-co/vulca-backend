from django.core.management.base import BaseCommand
from compta.models import Balance
from compta.signals import generate_financial_statements

class Command(BaseCommand):
    help = 'Recalcule les états financiers (Bilan) pour toutes les entrées de Balance'

    def handle(self, *args, **kwargs):
        self.stdout.write("Début du recalcul des états financiers...")
        
        balances = Balance.objects.all()
        count = balances.count()
        
        for i, balance in enumerate(balances):
            # Forcer l'appel au signal
            generate_financial_statements(sender=Balance, instance=balance, created=False)
            if i % 10 == 0:
                self.stdout.write(f"Traité {i}/{count}")
        
        self.stdout.write(self.style.SUCCESS('Recalcul terminé avec succès !'))
