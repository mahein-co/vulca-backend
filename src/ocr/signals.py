import os
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from ocr.models import FileSource


@receiver(post_save, sender=FileSource)
def generate_journal_on_filesource_save(sender, instance, created, **kwargs):
    """
    Génère automatiquement le journal comptable lors de la création d'un FileSource.
    Ce signal se déclenche uniquement lors de la création (created=True) et si des données OCR sont présentes.
    """
    if not created:
        return  # Ne rien faire si c'est une mise à jour
    
    if not instance.ocr_data:
        print(f"[WARNING] Pas de donnees OCR pour {instance.file_name}, generation de journal ignoree.")
        return
    
    try:
        from compta.views import process_journal_generation
        
        print(f"\n{'='*80}")
        print(f"[INFO] GENERATION AUTOMATIQUE DU JOURNAL POUR: {instance.file_name}")
        print(f"{'='*80}\n")
        
        # Extraire les données OCR
        ocr_data = instance.ocr_data
        
        # Pour chaque feuille (Bilan/CompteResultat)
        for sheet in ocr_data.get('sheets', []):
            sheet_name = sheet.get('sheet_name', 'Unknown')
            detected_type = sheet.get('detected_type', 'OD')
            
            print(f"[INFO] Traitement de la feuille: {sheet_name} (Type: {detected_type})")
            
            # Préparer les données pour la génération du journal
            gen_data = {
                "type_document": detected_type,
                "file_source": instance.id,
                "description": f"Import Excel - {sheet_name}",
                "date": str(instance.date) if instance.date else None,
            }
            
            # Ajouter les métadonnées d'entreprise si disponibles
            company_metadata = sheet.get('company_metadata', {})
            if company_metadata:
                gen_data.update(company_metadata)
            
            # Générer le journal
            result = process_journal_generation(
                document_json=gen_data,
                project_id=instance.project_id,
                file_source=instance,
                form_source=None
            )
            
            print(f"[SUCCESS] Journal genere pour la feuille '{sheet_name}': {result}")
        
        print(f"\n{'='*80}")
        print(f"[SUCCESS] GENERATION TERMINEE POUR: {instance.file_name}")
        print(f"{'='*80}\n")
            
    except Exception as e:
        import traceback
        print(f"\n{'='*80}")
        print(f"[ERROR] ERREUR GENERATION JOURNAL POUR: {instance.file_name}")
        print(f"{'='*80}")
        print(f"Erreur: {e}")
        print(traceback.format_exc())
        print(f"{'='*80}\n")


@receiver(post_delete, sender=FileSource)
def delete_file_on_disk(sender, instance, **kwargs):
    """
    Deletes physical file from disk when FileSource record is deleted.
    """
    if instance.file:
        if os.path.isfile(instance.file.path):
            try:
                os.remove(instance.file.path)
                print(f"[SUCCESS] Fichier supprime du disque : {instance.file.path}")
            except Exception as e:
                print(f"[ERROR] Erreur lors de la suppression du fichier {instance.file.path} : {e}")
