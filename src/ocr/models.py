from django.db import models


# SOURCE FILE MODEL =====================================
class FileSource(models.Model):
    file = models.FileField(upload_to='source_files/')
    file_name = models.CharField(max_length=255, blank=True, null=True)
    is_ocr_processed = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.file_name and self.file:
            self.file_name = self.file.name
        return super().save(*args, **kwargs)

    def __str__(self):
        if self.file_name:
            return self.file_name
        return f"file uploaded at {self.uploaded_at}"




