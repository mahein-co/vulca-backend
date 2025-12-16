import json
from decimal import Decimal
from rest_framework import generics


from django.core.exceptions import ValidationError

from vulca_backend import settings
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource, FormSource
from compta.serializers import JournalSerializer
from compta.models import Journal, Bilan,CompteResultat,GrandLivre
from compta.serializers import EbeSerializer,ResultatNetSerializer,BfrSerializer,CafSerializer,LeverageSerializer,AnnuiteCafSerializer,MargeNetteSerializer,ChargeCaSerializer
from compta.serializers import JournalSerializer, BilanSerializer,CompteResultatSerializer,ChiffreAffaireSerializer,DetteLmtCafSerializer,ChargeEbeSerializer,MargeEndettementSerializer
from datetime import date 
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from rest_framework.pagination import PageNumberPagination
from django.db.models import Sum
from datetime import date
from decimal import Decimal


client = OpenAI(api_key=settings.OPENAI_API_KEY) 


@api_view(["GET"])
@permission_classes([AllowAny])
def list_journals_view(request):
    journal_type = request.GET.get("type")
    show_all = request.GET.get("all", "false").lower() == "true"

    queryset = Journal.objects.all().order_by("-created_at", "numero_piece")

    if journal_type:
        queryset = queryset.filter(type_journal=journal_type)

    if not show_all:
        queryset = queryset.filter(created_at__date=date.today())

    totals = queryset.aggregate(
        total_debit=Sum("debit_ar"),
        total_credit=Sum("credit_ar"),
        total_count=Sum(1)
    )

    paginator = PageNumberPagination()
    paginator.page_size = 3
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = JournalSerializer(paginated_qs, many=True)

    response = paginator.get_paginated_response(serializer.data)

    response.data["totals"] = {
        "debit": totals["total_debit"] or 0,
        "credit": totals["total_credit"] or 0,
        "count": queryset.count()
    }

    return response

# CLASSIFICATION 
def classify_accounting(document_json: dict, pcg_mapping: dict):
    """
    document_json : dict contenant les champs extraits (facture, banque, reçu…)
    pcg_mapping   : dict extrait automatiquement du PDF du Plan Comptable Général 2005
    """

    # Convert mapping PCG → string compact
    pcg_text = "\n".join([f"{k}: {v}" for k, v in pcg_mapping.items()])

    prompt = f"""
    Tu es un expert-comptable malgache utilisant le Plan Comptable Général de Madagascar 2005.

    Voici un extrait du mapping PCG à utiliser impérativement :
    {pcg_text}

    Voici un document extrait (converti en JSON) :
    {json.dumps(document_json, indent=2)}

    OBJECTIF :
    1. Déterminer le type de document (facture fournisseur, facture client, relevé bancaire, reçu, etc.)
    2. Classer l'opération comptable selon le PCG Madagascar.
    3. Déduire tous les comptes comptables correspondants.
    4. Produire les écritures comptables (débit/crédit) sous forme JSON.

    RÈGLES :
    - Utilise **uniquement** les comptes présents dans le mapping PCG fourni.
    - Si nécessaire, choisis le compte le plus approprié.
    - Donne le journal sous forme strictement JSON.

    FORMAT DE SORTIE OBLIGATOIRE :

    {
        "type_document": "...",
        "classement_pcg": {
            "compte_debit": "xxx",
            "compte_credit": "xxx",
            "libelle_ecriture": "..."
        },
        "journal": [
            {
            "compte": "xxx",
            "libelle": "...",
            "debit": montant,
            "credit": 0
            },
            {
            "compte": "xxx",
            "libelle": "...",
            "debit": 0,
            "credit": montant
            }
        ]
    }
    """

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return json.loads(response.choices[0].message["content"])


def classify_document_with_openai(document_json, pcg_mapping):
    pcg_text = "\n".join([f"{k}: {v}" for k, v in pcg_mapping.items()])

    # Le prompt est renforcé pour obliger l'IA à analyser la nature du document
    # (qui est le client/fournisseur) avant de comptabiliser.
    prompt = f"""
    Tu es un expert-comptable malgache, spécialisé dans le Plan Comptable Général de Madagascar 2005. Ton rôle est de classifier le document ci-dessous et de générer l'écriture comptable correspondante.

    CONTEXTE ET RÈGLES DE CLASSIFICATION :
    1.  **ACHAT** : Nous sommes le client, le document est une facture fournisseur. Utilise la **TVA DÉDUCTIBLE** (Débit) et le compte **401 Fournisseurs** (Crédit).
    2.  **VENTE** : Nous sommes le fournisseur, le document est une facture client. Utilise la **TVA COLLECTÉE** (Crédit) et le compte **411 Clients** (Débit).
    3.  **BANQUE/CAISSE** : Mouvement de trésorerie (512 ou 53X).
    4.  **OD/AN** : Opération diverse ou à-nouveaux.

    Voici un extrait du PCG (Comptes disponibles) :
    {pcg_text}

    Voici le contenu extrait du document (à analyser pour la classification) :
    {json.dumps(document_json, ensure_ascii=False, indent=2)}

    CONSIGNES STRICTES DE SORTIE :
    - Utilise **uniquement** les comptes présents dans le mapping PCG fourni.
    - Le champ "type_journal" doit être l'une des valeurs suivantes : ACHAT, VENTE, BANQUE, CAISSE, OD, AN.
    - Le total Débit doit toujours égaler le total Crédit.
    - Retourne STRICTEMENT ce JSON (sans aucun texte d'explication ou de markdown) :

    {{
      "type_journal": "ACHAT | VENTE | BANQUE | CAISSE | OD | AN",
      "numero_piece": "<référence du document>",
      "date": "YYYY-MM-DD",
      "ecritures": [
          {{
            "numero_compte": "XXX",
            "libelle": "Description",
            "debit_ar": 0,
            "credit_ar": 0
          }}
      ]
    }}
    """

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        # Ajout d'un System Prompt pour renforcer l'adhésion au format JSON.
        messages=[
            {
                "role": "system",
                "content": "Tu es expert-comptable malgache (PCG 2005). Ton unique sortie doit être le JSON de l'écriture comptable demandée. Ne réponds rien d'autre."
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    cleaned = clean_ai_json(response.choices[0].message.content)
    return json.loads(cleaned)
# GENERATE JOURNAL.
@api_view(["POST"])
@permission_classes([AllowAny])
def generate_journal_view(request):
    try:
        document_json = request.data
        ai_result = classify_document_with_openai(document_json, PCG_MAPPING)
    except Exception as e:
        return Response(
            {"error": "Erreur OpenAI", "details": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    type_journal = ai_result.get("type_journal")
    numero_piece = ai_result.get("numero_piece")
    date = ai_result.get("date")
    ecritures = ai_result.get("ecritures", [])

    if not ecritures:
        return Response({"error": "Aucune écriture générée"}, status=400)

    # Vérification de l'équilibre du journal
    total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
    total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)

    if total_debit != total_credit:
        return Response({
            "error": "Écritures non équilibrées",
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "ecritures": ecritures
        }, status=400)

    # Lien avec FileSource si fourni
    file_source = None
    file_source_id = request.data["file_source"]
    if file_source_id:
        try:
            file_source = FileSource.objects.get(id=file_source_id)
        except FileSource.DoesNotExist:
            pass

    # Récupération du FormSource si fourni
    form_source = None
    form_source_id = request.data["form_source"]
    if form_source_id:
        try:
            form_source = FormSource.objects.get(id=form_source_id)
        except FormSource.DoesNotExist:
            pass


    # Sauvegarde chaque ligne dans Journal
    saved_lines = []
    for line in ecritures:
        entry = Journal(
            date=date,
            numero_piece=numero_piece,
            type_journal=type_journal,
            numero_compte=line["numero_compte"],
            libelle=line["libelle"],
            debit_ar=line["debit_ar"],
            credit_ar=line["credit_ar"],
        )
        try:
            entry.clean()
            entry.save()

            # Lier FileSource / FormSource via ForeignKey
            if file_source:
                file_source.journal = entry
                file_source.save()

            if form_source:
                form_source.journal = entry
                form_source.save()

            saved_lines.append({
                "id": entry.id,
                "compte": entry.numero_compte,
                "debit": float(entry.debit_ar),
                "credit": float(entry.credit_ar),
                "libelle": entry.libelle
            })

        except ValidationError as e:
            return Response({"error": "Erreur de validation", "details": str(e)}, status=400)

    return Response({
        "message": "Journal enregistré avec succès",
        "type_journal": type_journal,
        "numero_piece": numero_piece,
        "date": date,
        "lignes": saved_lines
    }, status=201)



class BilanListCreateView(generics.ListCreateAPIView):
    queryset = Bilan.objects.all()
    serializer_class = BilanSerializer
    permission_classes = [AllowAny]

class CompteResultatListCreateView(generics.ListCreateAPIView):
    queryset = CompteResultat.objects.all()
    serializer_class = CompteResultatSerializer
    permission_classes = [AllowAny]


@api_view(["GET"])
@permission_classes([AllowAny])
def chiffre_affaire_view(request):
    """
    GET /api/chiffre-affaire/?compte=701&date_debut=2025-01-01&date_fin=2025-12-31
    """

    compte = request.GET.get("compte")
    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    # Comptes de CA = classe 7
    queryset = GrandLivre.objects.filter(numero_compte__startswith="7")

    if compte:
        queryset = queryset.filter(numero_compte=compte)

    if date_debut and date_fin:
        queryset = queryset.filter(date__range=[date_debut, date_fin])

    data = (
        queryset
        .values("numero_compte")
        .annotate(
            total_credit=Sum("credit"),
            total_debit=Sum("debit"),
        )
        .order_by("numero_compte")
    )

    result = []
    for row in data:
        credit = row["total_credit"] or Decimal("0.00")
        debit = row["total_debit"] or Decimal("0.00")

        result.append({
            "numero_compte": row["numero_compte"],
            "total_credit": credit,
            "total_debit": debit,
            "chiffre_affaire": credit - debit
        })

    serializer = ChiffreAffaireSerializer(result, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def ebe_view(request):
    """
    Calcul automatique de l'EBE depuis le Grand Livre
    """

    def solde_classe(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            total_credit=Sum("credit"),
            total_debit=Sum("debit")
        )
        return (data["total_credit"] or Decimal("0.00")) - (data["total_debit"] or Decimal("0.00"))

    chiffre_affaires = solde_classe("7")
    subventions = solde_classe("74")
    achats = solde_classe("60")
    charges_externes = solde_classe("61") + solde_classe("62")
    impots_taxes = solde_classe("63")
    charges_personnel = solde_classe("64")

    ebe = (
        chiffre_affaires
        + subventions
        - achats
        - charges_externes
        - impots_taxes
        - charges_personnel
    )

    payload = {
        "chiffre_affaires": chiffre_affaires,
        "subventions": subventions,
        "achats": achats,
        "charges_externes": charges_externes,
        "impots_taxes": impots_taxes,
        "charges_personnel": charges_personnel,
        "ebe": ebe,
    }

    serializer = EbeSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def resultat_net_view(request):
    """
    Calcul du Résultat Net depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    produits = solde("7")
    charges_exploitation = sum(
        solde(str(c)) for c in range(60, 66)
    )
    charges_financieres = solde("66")
    produits_financiers = solde("76")
    charges_exceptionnelles = solde("67")
    produits_exceptionnels = solde("77")
    impots_benefices = solde("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    payload = {
        "produits": produits,
        "charges_exploitation": charges_exploitation,
        "charges_financieres": charges_financieres,
        "produits_financiers": produits_financiers,
        "charges_exceptionnelles": charges_exceptionnelles,
        "produits_exceptionnels": produits_exceptionnels,
        "impots_benefices": impots_benefices,
        "resultat_net": resultat_net,
    }

    serializer = ResultatNetSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def bfr_view(request):
    """
    Calcul du BFR depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["debit"] or Decimal("0.00")) - (data["credit"] or Decimal("0.00"))

    stocks = solde("3")
    creances_clients = solde("411")
    autres_creances = solde("409") + solde("418")
    dettes_fournisseurs = solde("401")
    autres_dettes = solde("408") + solde("419")

    bfr = (
        stocks
        + creances_clients
        + autres_creances
        - dettes_fournisseurs
        - autres_dettes
    )

    payload = {
        "stocks": stocks,
        "creances_clients": creances_clients,
        "autres_creances": autres_creances,
        "dettes_fournisseurs": dettes_fournisseurs,
        "autres_dettes": autres_dettes,
        "bfr": bfr,
    }

    serializer = BfrSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def caf_view(request):
    """
    Calcul de la CAF depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # Résultat Net
    produits = solde("7")
    charges_exploitation = sum(solde(str(c)) for c in range(60, 66))
    charges_financieres = solde("66")
    produits_financiers = solde("76")
    charges_exceptionnelles = solde("67")
    produits_exceptionnels = solde("77")
    impots_benefices = solde("69")
    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    # Dotations / Reprises
    dotations = solde("68")
    reprises = solde("78")

    caf = resultat_net + dotations - reprises

    payload = {
        "resultat_net": resultat_net,
        "dotations_amort_provisions": dotations,
        "reprises_amort_provisions": reprises,
        "caf": caf
    }

    serializer = CafSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def leverage_brut_view(request):
    """
    Calcul du Leverage brut = Total endettement / EBE
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # Total endettement (exemple : comptes 16, 17, 19)
    total_endettement = solde("16") + solde("17") + solde("19")

    # Calcul EBE (même méthode que pour l'EBE API)
    ca = solde("7")
    subventions = solde("74")
    achats = solde("60")
    charges_ext = solde("61") + solde("62")
    impots = solde("63")
    personnel = solde("64")
    ebe = ca + subventions - achats - charges_ext - impots - personnel

    leverage_brut = Decimal("0.00")
    if ebe != 0:
        leverage_brut = total_endettement / ebe

    payload = {
        "total_endettement": total_endettement,
        "ebe": ebe,
        "leverage_brut": leverage_brut.quantize(Decimal("0.01"))
    }

    serializer = LeverageSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def annuite_caf_view(request):
    """
    Ratio : Annuité d'emprunt / CAF
    """

    def solde(prefix, sens="debit"):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        if sens == "debit":
            return data["debit"] or Decimal("0.00")
        return data["credit"] or Decimal("0.00")

    # 🔹 Annuité d'emprunt
    remboursement_capital = solde("164") + solde("168")
    interets = solde("661")
    annuite_emprunt = remboursement_capital + interets

    # 🔹 CAF
    def solde_net(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    produits = solde_net("7")
    charges_exploitation = sum(solde_net(str(c)) for c in range(60, 66))
    charges_financieres = solde_net("66")
    produits_financiers = solde_net("76")
    charges_exceptionnelles = solde_net("67")
    produits_exceptionnels = solde_net("77")
    impots_benefices = solde_net("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    dotations = solde_net("68")
    reprises = solde_net("78")

    caf = resultat_net + dotations - reprises

    ratio = Decimal("0.00")
    if caf != 0:
        ratio = annuite_emprunt / caf

    alerte = ratio > Decimal("0.50")

    payload = {
        "annuite_emprunt": annuite_emprunt,
        "caf": caf,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = AnnuiteCafSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def dette_lmt_caf_view(request):
    """
    Ratio : Dette LMT / CAF
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # 🔹 Dette LMT (comptes 16x)
    dette_lmt = solde("16")

    # 🔹 CAF
    def solde_net(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    produits = solde_net("7")
    charges_exploitation = sum(solde_net(str(c)) for c in range(60, 66))
    charges_financieres = solde_net("66")
    produits_financiers = solde_net("76")
    charges_exceptionnelles = solde_net("67")
    produits_exceptionnels = solde_net("77")
    impots_benefices = solde_net("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    dotations = solde_net("68")
    reprises = solde_net("78")

    caf = resultat_net + dotations - reprises

    ratio = Decimal("0.00")
    if caf != 0:
        ratio = dette_lmt / caf

    alerte = ratio >= Decimal("3.50")

    payload = {
        "dette_lmt": dette_lmt,
        "caf": caf,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = DetteLmtCafSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def resultat_net_ca_view(request):
    """
    Ratio : Résultat net / Chiffre d'affaires
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Chiffre d'affaires (70x)
    chiffre_affaire = solde("70")

    # 🔹 Résultat net
    produits = solde("7")
    charges_exploitation = sum(solde(str(c)) for c in range(60, 66))
    charges_financieres = solde("66")
    produits_financiers = solde("76")
    charges_exceptionnelles = solde("67")
    produits_exceptionnels = solde("77")
    impots = solde("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots
    )

    ratio = Decimal("0.00")
    ratio_pourcent = Decimal("0.00")

    if chiffre_affaire != 0:
        ratio = resultat_net / chiffre_affaire
        ratio_pourcent = ratio * Decimal("100")

    payload = {
        "resultat_net": resultat_net,
        "chiffre_affaire": chiffre_affaire,
        "ratio": ratio.quantize(Decimal("0.0001")),
        "ratio_pourcent": ratio_pourcent.quantize(Decimal("0.01")),
    }

    serializer = MargeNetteSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ebe_view(request):
    """
    Ratio : Charge financière / EBE
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Charge financière (661)
    charge_financiere = solde("661")

    # 🔹 EBE
    ca = solde("7")
    subventions = solde("74")
    achats = solde("60")
    charges_ext = solde("61") + solde("62")
    impots = solde("63")
    personnel = solde("64")

    ebe = ca + subventions - achats - charges_ext - impots - personnel

    ratio = Decimal("0.00")
    if ebe != 0:
        ratio = charge_financiere / ebe

    alerte = ratio >= Decimal("0.30")

    payload = {
        "charge_financiere": charge_financiere,
        "ebe": ebe,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = ChargeEbeSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ca_view(request):
    """
    Ratio : Charge financière / Chiffre d'affaires
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Charge financière
    charge_financiere = solde("661")

    # 🔹 Chiffre d'affaires
    chiffre_affaire = solde("70")

    ratio = Decimal("0.00")
    if chiffre_affaire != 0:
        ratio = charge_financiere / chiffre_affaire

    alerte = ratio >= Decimal("0.05")  # 5%

    payload = {
        "charge_financiere": charge_financiere,
        "chiffre_affaire": chiffre_affaire,
        "ratio": ratio.quantize(Decimal("0.02")),
        "alerte": alerte
    }

    serializer = ChargeCaSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def marge_endettement_view(request):
    """
    Ratio : Dette CMLT / Fonds Propres
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Dette CMLT (16x)
    dette_cmlt = solde("16")

    # 🔹 Fonds Propres (101–106)
    fonds_propres = sum(solde(str(c)) for c in range(101, 107))

    ratio = Decimal("0.00")
    if fonds_propres != 0:
        ratio = dette_cmlt / fonds_propres

    alerte = ratio >= Decimal("1.3")

    payload = {
        "dette_cmlt": dette_cmlt,
        "fonds_propres": fonds_propres,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = MargeEndettementSerializer(payload)
    return Response(serializer.data)