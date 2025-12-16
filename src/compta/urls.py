from compta import views
from django.urls import path

urlpatterns = [
    path("journals/generate/", views.generate_journal_view, name="generate-journal"),
    path("journals/", views.list_journals_view, name="list-journals"),
    path("bilans/", views.BilanListCreateView.as_view(), name="list-bilan"),
    path("CompteResultats/", views.CompteResultatListCreateView.as_view(), name="list-CompteResultat"),
    path("chiffre-affaire/", views.chiffre_affaire_view, name="chiffre-affaire"),
    path("ebe/", views.ebe_view, name="ebe"),
    path("resultat-net/", views.resultat_net_view, name="resultat-net"),
    path("bfr/", views.bfr_view, name="bfr"),
    path("caf/", views.caf_view, name="caf"),
    path("leverage-brut/", views.leverage_brut_view, name="leverage-brut"),
    path("annuite-caf/", views.annuite_caf_view, name="annuite-caf"),
    path("dette-lmt-caf/", views.dette_lmt_caf_view, name="dette-lmt-caf"),
    path("resultat-net-ca/",views.resultat_net_ca_view,name="resultat-net-ca"),
    path("charge-ebe/", views.charge_ebe_view, name="charge-ebe"),
    path("charge-ca/", views.charge_ca_view, name="charge-ca"),
    # path("marge-endettement/", views.marge_endettement_view, name="marge-endettement"),
]
    #path("journals/", views.list_journals_view, name="list-journals"),
    # path("comptes/", views.list_comptes, name="list-comptes"),
    # path("grand-livre/", views.grand_livre, name="grand-livre"),
    # path("bilans/", views.bilans_view, name="bilans"),
    # path("CompteResultats/", views.CompteResultat_view, name="compte-resultat"),

