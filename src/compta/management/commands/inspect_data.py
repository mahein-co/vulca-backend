
from django.core.management.base import BaseCommand
from django.apps import apps
from django.db.models import Min, Max

class Command(BaseCommand):
    help = 'Inspect accounting data'

    def handle(self, *args, **options):
        # Dynamically get models to avoid import errors
        Bilan = apps.get_model('compta', 'Bilan')
        CompteResultat = apps.get_model('compta', 'CompteResultat')
        FormSource = apps.get_model('ocr', 'FormSource')
        FileSource = apps.get_model('ocr', 'FileSource')
        Project = apps.get_model('compta', 'Project')
        
        projects = Project.objects.all()
        self.stdout.write(self.style.SUCCESS(f"Total Projects: {projects.count()}"))
        
        for p in projects:
            self.stdout.write(f"\nProject: {p.name} (ID: {p.id})")
            
            cr_count = CompteResultat.objects.filter(project_id=p.id).count()
            bilan_count = Bilan.objects.filter(project_id=p.id).count()
            form_count = FormSource.objects.filter(project_id=p.id).count()
            file_count = FileSource.objects.filter(project_id=p.id).count()
            
            self.stdout.write(f"  CompteResultat: {cr_count}")
            self.stdout.write(f"  Bilan: {bilan_count}")
            self.stdout.write(f"  FormSource: {form_count}")
            self.stdout.write(f"  FileSource: {file_count}")
            
            if cr_count > 0:
                stats = CompteResultat.objects.filter(project_id=p.id).aggregate(min_d=Min('date'), max_d=Max('date'))
                self.stdout.write(self.style.WARNING(f"  CR Date Range: {stats['min_d']} to {stats['max_d']}"))
                
            if bilan_count > 0:
                stats = Bilan.objects.filter(project_id=p.id).aggregate(min_d=Min('date'), max_d=Max('date'))
                self.stdout.write(self.style.WARNING(f"  Bilan Date Range: {stats['min_d']} to {stats['max_d']}"))
            
            if form_count > 0:
                last_forms = FormSource.objects.filter(project_id=p.id).order_by('-id')[:5]
                self.stdout.write("  Last 5 Forms (FormSource):")
                for f in last_forms:
                    self.stdout.write(f"    - ID: {f.id}, Piece Type: {f.piece_type}, Date: {f.date}")
