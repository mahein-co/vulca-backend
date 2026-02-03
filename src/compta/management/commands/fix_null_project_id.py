from django.core.management.base import BaseCommand
from compta.models import Project, Journal, GrandLivre, Balance, Bilan, CompteResultat
from chatbot.models import ChatMessage, MessageHistory, RAGContent
from ocr.models import FileSource, FormSource
from django.db.models import Count

class Command(BaseCommand):
    help = 'Fix NULL project_id by assigning them to a default project'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('🚀 DÉBUT DE LA MIGRATION DES DONNÉES'))

        # 1. Créer ou récupérer le projet par défaut
        default_project, created = Project.objects.get_or_create(
            name="Projet par défaut",
            defaults={
                "description": "Projet créé automatiquement pour récupérer les données existantes",
                "is_active": True
            }
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f'✅ Projet par défaut créé: {default_project.name} (ID: {default_project.id})'))
        else:
            self.stdout.write(self.style.SUCCESS(f'ℹ️ Projet par défaut existant trouvé: {default_project.name} (ID: {default_project.id})'))

        # 2. Migrer les modèles Chatbot
        models_to_migrate = [
            (MessageHistory, 'MessageHistory'),
            (ChatMessage, 'ChatMessage'),
            (RAGContent, 'RAGContent'),
            (FileSource, 'FileSource'),
            (FormSource, 'FormSource'),
            (Journal, 'Journal'),
            (GrandLivre, 'GrandLivre'),
            (Balance, 'Balance'),
            (Bilan, 'Bilan'),
            (CompteResultat, 'CompteResultat'),
        ]

        total_migrated = 0

        for model_class, model_name in models_to_migrate:
            # Compter les enregistrements sans projet
            null_count = model_class.objects.filter(project_id__isnull=True).count()
            
            if null_count > 0:
                self.stdout.write(self.style.WARNING(f'⏳ Migration de {null_count} enregistrements pour {model_name}...'))
                updated = model_class.objects.filter(project_id__isnull=True).update(project=default_project)
                self.stdout.write(self.style.SUCCESS(f'✅ {model_name}: {updated} enregistrements mis à jour'))
                total_migrated += updated
            else:
                self.stdout.write(f'👌 {model_name}: Aucun enregistrement orphelin')

        self.stdout.write(self.style.SUCCESS(f'\n✨ MIGRATION TERMINÉE AVEC SUCCÈS ({total_migrated} enregistrements corrigés)'))
