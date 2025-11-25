import json
from decimal import Decimal
from datetime import date
from collections import defaultdict

from django.core.exceptions import ValidationError

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework import status
from datetime import datetime
from openai import OpenAI

from vulca_backend import settings
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource
from .serializers import JournalSerializer
from .models import Journal

# Initialisation OpenAI
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ===========================
#   LIST JOURNALS
# ===========================
@api_view(["GET"])
@permission_classes([AllowAny])
def list_journals_view(request):

    journal_type = request.GET.get("type")
    show_all = request.GET.get("all", "true").lower() == "true"

    queryset = Journal.objects.all().order_by("-created_at", "numero_piece")

    if journal_type:
        queryset = queryset.filter(type_journal=journal_type.upper())

    # 🔥 CORRECTION : filtrer selon created_at et non date facture
    if not show_all:
        today_start = datetime.combine(date.today(), datetime.min.time())
        queryset = queryset.filter(created_at__gte=today_start)

    paginator = PageNumberPagination()
    paginator.page_size = 4

    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = JournalSerializer(paginated_qs, many=True)

    return paginator.get_paginated_response(serializer.data)


# ===========================
#   OPENAI CLASSIFICATION
# ===========================

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


# ===========================
#   GENERATE JOURNAL
# ===========================

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
    date_str = ai_result.get("date")
    ecritures = ai_result.get("ecritures", [])

    if not type_journal or not numero_piece or not date_str:
        return Response({"error": "Champs obligatoires manquants dans le JSON AI", "data": ai_result}, status=400)

    if not ecritures:
        return Response({"error": "Aucune écriture générée"}, status=400)

    # Vérif équilibre
    total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
    total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)

    if total_debit != total_credit:
        return Response({
            "error": "Écritures non équilibrées",
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "ecritures": ecritures
        }, status=400)

    # File source
    file_source = None
    file_source_id = request.data.get("file_source")
    if file_source_id:
        try:
            file_source = FileSource.objects.get(id=file_source_id)
        except FileSource.DoesNotExist:
            file_source = None

    # Sauvegarde des lignes du journal
    saved_lines = []
    for line in ecritures:
        entry = Journal(
            file_source=file_source,
            date=date_str,
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
        except ValidationError as e:
            return Response(
                {"error": "Erreur validation", "details": str(e)},
                status=400,
            )

        saved_lines.append({
            "id": entry.id,
            "compte": entry.numero_compte,
            "libelle": entry.libelle,
            "debit": float(entry.debit_ar),
            "credit": float(entry.credit_ar)
        })

    return Response({
        "message": "Journal enregistré avec succès",
        "type_journal": type_journal,
        "numero_piece": numero_piece,
        "date": date_str,
        "lignes": saved_lines
    }, status=201)
