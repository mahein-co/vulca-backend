from django.db import models
from compta.models import Project

# SOURCE FILE MODEL =====================================
class FileSource(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='file_sources',
        verbose_name="Projet",
        null=True,  # Temporaire pour migration
        blank=True
    )
    journal = models.ForeignKey(
        'compta.Journal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='file_sources'
    )
    file = models.FileField(upload_to='source_files/')
    file_name = models.CharField(max_length=255, blank=True, null=True)
    piece_type = models.CharField(max_length=225, null=True, blank=True, default='Autres')
    ref_file = models.CharField(max_length=225, null=True, blank=True)
    hash_ocr = models.CharField(max_length=225, null=True, blank=True)
    date = models.DateField(db_index=True, null=True, blank=True)
    is_ocr_processed = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['project', 'date']),
            models.Index(fields=['project', 'piece_type']),
            models.Index(fields=['project', 'uploaded_at']),
        ]

    def save(self, *args, **kwargs):
        if self.description:
            self.is_ocr_processed = True
        if not self.file_name and self.file:
            self.file_name = self.file.name
        return super().save(*args, **kwargs)

    def __str__(self):
        if self.file_name:
            return self.file_name
        return f"file uploaded at {self.uploaded_at}"



class FormSource(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='form_sources',
        verbose_name="Projet",
        null=True,  # Temporaire pour migration
        blank=True
    )
    journal = models.ForeignKey(
        'compta.Journal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='form_sources'
    )
    piece_type = models.CharField(max_length=225, null=True, blank=True, default='Autres')
    description = models.TextField(blank=True, null=True)
    ref_file = models.CharField(max_length=225, null=True, blank=True)
    date = models.DateField(db_index=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['project', 'date']),
            models.Index(fields=['project', 'piece_type']),
        ]
    
    def __str__(self):
        return self.piece_type


