# DJANGO -----------------------------------------
from django.db import models

# PGVECTOR ---------------------------------------
from pgvector.django import VectorField, HnswIndex

# SETTINGS ---------------------------------------
from vulca_backend import settings


UserModel = settings.AUTH_USER_MODEL

#class MessageHistory(models.Model):
#    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name='message_templates', null=False, blank=False)
#    title = models.CharField(max_length=255)
#    is_renamed_by_ai = models.BooleanField(default=False)
#    created_at = models.DateTimeField(auto_now_add=True)
#    updated_at = models.DateTimeField(auto_now=True)
#
#    class Meta:
#        ordering = ['-updated_at']
#
#    def __str__(self):
#        return self.title

class MessageHistory(models.Model):
    # AJOUT DU PROJET
    project = models.ForeignKey(
        'compta.Project',
        on_delete=models.CASCADE,
        related_name='message_histories',
        verbose_name="Projet",
        null=True,  # Temporairement null pour la migration
        blank=True
    )
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, default="Nouvelle discussion")
    is_renamed_by_ai = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'message_history'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} - {self.user.email}"

#class ChatMessage(models.Model):
#    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name='chat_messages', null=False, blank=False)    
#    message_history = models.ForeignKey(
#        MessageHistory, 
#        on_delete=models.CASCADE, 
#        related_name='chat_messages', 
#        null=False, 
#        blank=False
#    )
#    user_input = models.TextField()
#    ai_response = models.TextField(blank=True, null=True)
#    timestamp = models.DateTimeField(auto_now_add=True)
#    updated_at = models.DateTimeField(auto_now=True)
#
#    class Meta:
#        ordering = ['timestamp']
#
#    def __str__(self):
#        return f"Message at {self.timestamp} {self.user.username}"

class ChatMessage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message_history = models.ForeignKey(
        MessageHistory, 
        on_delete=models.CASCADE,
        related_name='chat_messages'
    )
    user_input = models.TextField()
    ai_response = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'chat_message'
        ordering = ['timestamp']
    
    def __str__(self):
        return f"{self.user.email} - {self.timestamp}"

#class Document(models.Model):
#    title = models.CharField(max_length=255)
#    file_path = models.FileField(upload_to="documents/")
#    created_at = models.DateTimeField(auto_now_add=True)
#
#    def __str__(self):
#        return self.title

class Document(models.Model):
    # AJOUT DU PROJET
    project = models.ForeignKey(
        'compta.Project',  # Référence au modèle Project
        on_delete=models.CASCADE,
        related_name='documents',
        verbose_name="Projet",
        null=True,  # Temporairement null pour la migration
        blank=True
    )
    
    title = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        null=True,
        blank=True)
    
    class Meta:
        db_table = 'document'
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return self.title

#class DocumentPage(models.Model):
#    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="pages")
#    page_number = models.IntegerField()
#    content = models.TextField()
#    embedding = VectorField(dimensions=1536) 
#    created_at = models.DateTimeField(auto_now_add=True)
#
#    class Meta:
#        indexes = [
#            HnswIndex(
#                name="embedding_vectors_index",
#                fields=["embedding"],
#                m=16,
#                ef_construction=64,
#                opclasses=["vector_cosine_ops"],
#            )
#        ]
#
#    def __str__(self):
#        return f"{self.document.title} - page:{self.page_number}"

class DocumentPage(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='pages')
    page_number = models.IntegerField()
    content = models.TextField()
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    
    class Meta:
        db_table = 'document_page'
        ordering = ['document', 'page_number']
        unique_together = ('document', 'page_number')
    
    def __str__(self):
        return f"{self.document.title} - Page {self.page_number}"

