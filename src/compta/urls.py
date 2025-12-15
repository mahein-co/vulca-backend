from compta import views
from django.urls import path

urlpatterns = [
    path("journals/generate/", views.generate_journal_view, name="generate-journal"),
    #path("journals/", views.list_journals_view, name="list-journals"),
    # path("comptes/", views.list_comptes, name="list-comptes"),
    # path("grand-livre/", views.grand_livre, name="grand-livre"),
    # path("bilans/", views.bilans_view, name="bilans"),
    # path("CompteResultats/", views.CompteResultat_view, name="compte-resultat"),
]