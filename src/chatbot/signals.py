from dotenv import load_dotenv
import os
load_dotenv()
from django.conf import settings


from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from chatbot.models import ChatMessage, Document, AccountingIndex
from chatbot.services.embeddings import process_pdf, extract_text_from_pdf
from chatbot.services.indexing_service import AccountingIndexer
from compta.models import Journal, Bilan, CompteResultat
from ocr.models import FileSource, FormSource

from openai import OpenAI

# OPENAI -------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

@receiver(post_save, sender=ChatMessage)
def generate_message_history_title(sender, instance, created, **kwargs):
    if created:  
        try:
            # Demande à GPT-4 de générer un titre court
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), 
                messages=[
                    {"role": "system", "content": "Tu es un assistant qui génère des titres concis et clairs (max 24 caractères)."},
                    {"role": "user", "content": f"Génère un titre court et pertinent pour ce texte: {instance.user_input}"}
                ],
                max_tokens=20
            )
            generated_title = response.choices[0].message.content.strip()
        except Exception:
            generated_title = "Sans titre"

        # Récupère l'historique associé
        history = instance.message_history

        # Si le titre est vide ou non encore renommé par l’IA → on met à jour
        if not history.title.strip() or not history.is_renamed_by_ai:
            history.title = generated_title
            history.is_renamed_by_ai = True
            history.save()


@receiver(post_save, sender=Document)
def process_document_after_save(sender, instance, created, **kwargs):
    if created:  
        # process_pdf(instance)
        extract_text_from_pdf(instance)


@receiver(post_delete, sender=Document)
def delete_file_on_document_delete(sender, instance, **kwargs):
    if instance.file_path:
        if os.path.isfile(instance.file_path.path):  
            os.remove(instance.file_path.path)


@receiver(pre_save, sender=Document)
def delete_old_file_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return  

    try:
        old_instance = Document.objects.get(pk=instance.pk)
    except Document.DoesNotExist:
        return

    old_file = old_instance.file_path
    new_file = instance.file_path

    # Si le fichier change → supprimer l'ancien
    if old_file and old_file != new_file:
        if os.path.isfile(old_file.path):
            os.remove(old_file.path)

# AUTOMATION DE L'INDEXATION COMPTABLE ---------------------------

@receiver(post_save, sender=Journal)
def index_journal_after_save(sender, instance, **kwargs):
    AccountingIndexer.index_journal_entry(instance)

@receiver(post_save, sender=Bilan)
def index_bilan_after_save(sender, instance, **kwargs):
    AccountingIndexer.index_bilan_entry(instance)

@receiver(post_save, sender=CompteResultat)
def index_resultat_after_save(sender, instance, **kwargs):
    AccountingIndexer.index_resultat_entry(instance)

@receiver(post_save, sender=FileSource)
def index_file_source_after_save(sender, instance, **kwargs):
    AccountingIndexer.index_file_source(instance)

@receiver(post_save, sender=FormSource)
def index_form_source_after_save(sender, instance, **kwargs):
    AccountingIndexer.index_form_source(instance)

# SUPPRESSION AUTOMATIQUE DES INDEX ------------------------------

def _delete_accounting_index(model_name, instance_id):
    """Helper pour supprimer un index vectoriel orphelin"""
    try:
        AccountingIndex.objects.filter(
            source_model=model_name,
            source_id=instance_id
        ).delete()
    except Exception as e:
        print(f"[Error] Deleting index for {model_name} #{instance_id}: {str(e)}")

@receiver(post_delete, sender=Journal)
def delete_journal_index(sender, instance, **kwargs):
    _delete_accounting_index("Journal", instance.id)

@receiver(post_delete, sender=Bilan)
def delete_bilan_index(sender, instance, **kwargs):
    _delete_accounting_index("Bilan", instance.id)

@receiver(post_delete, sender=CompteResultat)
def delete_resultat_index(sender, instance, **kwargs):
    _delete_accounting_index("CompteResultat", instance.id)

@receiver(post_delete, sender=FileSource)
def delete_file_source_index(sender, instance, **kwargs):
    _delete_accounting_index("FileSource", instance.id)

@receiver(post_delete, sender=FormSource)
def delete_form_source_index(sender, instance, **kwargs):
    _delete_accounting_index("FormSource", instance.id)