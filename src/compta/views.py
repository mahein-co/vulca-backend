import json
from decimal import Decimal

from django.core.exceptions import ValidationError

from vulca_backend import settings
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource, FormSource
from compta.serializers import JournalSerializer
from compta.models import Journal
from collections import defaultdict

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


client = OpenAI(api_key=settings.OPENAI_API_KEY) 

@api_view(["GET"])
@permission_classes([AllowAny])
def list_journals_view(request):
    """
    Retourne les journaux filtrés par type et par date (aujourd'hui)
    Avec possibilité pagination pour toutes les écritures
    """

    journal_type = request.GET.get("type")  # ex: VENTE, ACHAT...
    show_all = request.GET.get("all", "false").lower() == "true"  # si true => toutes les dates

    # Filtre de base
    queryset = Journal.objects.all().order_by("date", "numero_piece")

    if journal_type:
        queryset = queryset.filter(type_journal=journal_type)

    if not show_all:
        queryset = queryset.filter(date=date.today())

    # Pagination
    paginator = PageNumberPagination()
    paginator.page_size = 4
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = JournalSerializer(paginated_qs, many=True)

    return paginator.get_paginated_response(serializer.data)

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

    prompt = f"""
    Tu es un expert-comptable malgache.
    Tu dois classifier et générer les écritures comptables selon le PCG 2005.

    Voici un extrait du PCG :
    {pcg_text}

    Voici le contenu extrait du document :
    {json.dumps(document_json, ensure_ascii=False, indent=2)}

    Retourne STRICTEMENT ce JSON :
    {{
      "type_journal": "ACHAT | VENTE | BANQUE | CAISSE | OD | AN",
      "numero_piece": "<référence du document>",
      "date": "YYYY-MM-DD",
      "ecritures": [
         {{
            "numero_compte": "401",
            "libelle": "Achat fournitures",
            "debit_ar": 800000,
            "credit_ar": 0
         }},
         {{
            "numero_compte": "512",
            "libelle": "Paiement banque",
            "debit_ar": 0,
            "credit_ar": 800000
         }}
      ]
    }}
    """

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
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


