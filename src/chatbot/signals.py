import os 
#from decouple import config
#env = config
from django.conf import settings


from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from chatbot.models import ChatMessage, Document
from chatbot.services.embeddings import  process_pdf, extract_text_from_pdf

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
                model=env("OPENAI_MODEL"), 
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
