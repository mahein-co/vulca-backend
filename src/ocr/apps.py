from django.apps import AppConfig


class OcrConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ocr'
    def ready(self):
        import ocr.signals
