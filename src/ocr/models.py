from django.db import models


# SOURCE FILE MODEL =====================================
class SourceFile(models.Model):
    file = models.FileField(upload_to='source_files/')
    file_name = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.file_name and self.file:
            self.file_name = self.file.name
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"SourceFile uploaded at {self.uploaded_at}"



