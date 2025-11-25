from django.db import models

# SOURCE FILE MODEL =====================================
class FileSource(models.Model):
    journal = models.ForeignKey(
        'compta.Journal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='file_sources'
    )
    file = models.FileField(upload_to='source_files/')
    file_name = models.CharField(max_length=255, blank=True, null=True)
    is_ocr_processed = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

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
    journal = models.ForeignKey(
        'compta.Journal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='form_sources'
    )
    piece_type = models.CharField(max_length=225, null=False, blank=False)
    description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.piece_type


