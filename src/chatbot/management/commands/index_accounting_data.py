from django.core.management.base import BaseCommand
from compta.models import Project, Journal, Bilan, CompteResultat
from ocr.models import FileSource, FormSource
from chatbot.services.indexing_service import AccountingIndexer

class Command(BaseCommand):
    help = "Indexe sémantiquement les données comptables d'un projet (Embeddings)"

    def add_arguments(self, parser):
        parser.add_argument('project_id', type=int, help='ID du projet à indexer')
        parser.add_argument('--clear', action='store_true', help='Effacer les index existants avant')

    def handle(self, *args, **options):
        project_id = options['project_id']
        clear_existing = options['clear']

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Projet {project_id} introuvable."))
            return

        self.stdout.write(self.style.SUCCESS(f"Début de l'indexation pour le projet: {project.name}"))

        if clear_existing:
            from chatbot.models import AccountingIndex
            deleted_count = AccountingIndex.objects.filter(project=project).delete()[0]
            self.stdout.write(self.style.WARNING(f"Suppression de {deleted_count} index existants."))

        # 1. Journal
        entries = Journal.objects.filter(project=project)
        self.stdout.write(f"Indexation de {entries.count()} écritures de journal...")
        for entry in entries:
            AccountingIndexer.index_journal_entry(entry)

        # 2. Bilan
        bilans = Bilan.objects.filter(project=project)
        self.stdout.write(f"Indexation de {bilans.count()} lignes de bilan...")
        for b in bilans:
            AccountingIndexer.index_bilan_entry(b)

        # 3. Compte de Résultat
        resultats = CompteResultat.objects.filter(project=project)
        self.stdout.write(f"Indexation de {resultats.count()} lignes de résultat...")
        for r in resultats:
            AccountingIndexer.index_resultat_entry(r)

        # 4. FileSource
        files = FileSource.objects.filter(project=project)
        self.stdout.write(f"Indexation de {files.count()} fichiers sources...")
        for f in files:
            AccountingIndexer.index_file_source(f)

        # 5. FormSource
        forms = FormSource.objects.filter(project=project)
        self.stdout.write(f"Indexation de {forms.count()} formulaires...")
        for f in forms:
            AccountingIndexer.index_form_source(f)

        self.stdout.write(self.style.SUCCESS("Indexation terminée avec succès !"))
