import os
from django.db.models.signals import post_delete
from django.dispatch import receiver
from ocr.models import FileSource

@receiver(post_delete, sender=FileSource)
def delete_file_on_disk(sender, instance, **kwargs):
    """
    Deletes physical file from disk when FileSource record is deleted.
    """
    if instance.file:
        if os.path.isfile(instance.file.path):
            try:
                os.remove(instance.file.path)
                print(f"✅ Fichier supprimé du disque : {instance.file.path}")
            except Exception as e:
                print(f"❌ Erreur lors de la suppression du fichier {instance.file.path} : {e}")
