from compta import views
from django.urls import path

urlpatterns = [
    path("journals/generate/", views.generate_journal_view, name="generate-journal"),
    path("journals/", views.list_journals_view, name="list-journals"),
]
