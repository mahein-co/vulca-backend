import json

from django.shortcuts import render
from vulca_backend import settings

from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from ocr.models import FileSource
from ocr.serializers import FileSourceSerializer
from ocr.utils import detect_file_type, extract_content, clean_ai_json
from ocr.constants import EXTRACTION_FIELDS_PROMPT

from openai import OpenAI
client = OpenAI(api_key=settings.OPENAI_API_KEY) 


class FileSourceListCreateView(generics.ListCreateAPIView):
    queryset = FileSource.objects.all().order_by('-uploaded_at')
    serializer_class = FileSourceSerializer
    permission_classes = [AllowAny]
 

@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def file_source_list_create(request):
    if request.method == "GET":
        files = FileSource.objects.all().order_by("-uploaded_at")
        serializer = FileSourceSerializer(files, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == "POST":
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"error": "Aucun fichier envoyé."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Detecter le type de fichier
        file_type = detect_file_type(file.name)
        if file_type == "unknown":
            return Response(
                {"error": "Type de fichier non supporté."},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Content extraction
        content = extract_content(file, file_type)
        if not content:
            return Response(
                {"error": "Impossible d'extraire du texte du fichier."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verification - piece comptable via OpenAI
        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Tu es un expert en comptabilité. Réponds uniquement par OUI ou NON."},
                    {"role": "user",
                     "content": f"Voici le contenu d'un fichier : {content[:5000]}\n"
                                "Dis-moi si c'est une pièce comptable (facture, reçu, devis, note de frais, bon de commande, etc.)."}
                ],
                temperature=0
            )
            decision = response.choices[0].message.content.strip().lower()
        except Exception as e:
            return Response({"error": f"Erreur OpenAI : {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if decision not in ["oui", "yes"]:
            return Response({"error": "Le fichier n'est pas reconnu comme une pièce comptable.",
                             "ai_decision": decision},
                            status=status.HTTP_400_BAD_REQUEST)

        # Extraction JSON structuré du document
        try:
            extraction = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": EXTRACTION_FIELDS_PROMPT 
                    },
                    {
                        "role": "user",
                        "content": content[:6000]
                    }
                ],
                temperature=0
            )

            extracted_json_str = extraction.choices[0].message.content.strip()
            extracted_json_str = clean_ai_json(extracted_json_str)

        except Exception as e:
            return Response({"error": f"Erreur OpenAI extraction JSON : {str(e)}"}, status=500)

        # Convertir texte → JSON Python
        try:
            extracted_json = json.loads(extracted_json_str)
        except json.JSONDecodeError:
            return Response({
                "error": "JSON renvoyé par OpenAI non valide.",
                "raw_response": extracted_json_str
            }, status=500)

        # Serializer et sauvegarde
        serializer = FileSourceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            context = {
                "status": "success",
                "message": "Document vérifié, analysé et sauvegardé.",
                "is_accounting_document": True,
                "extracted_json": extracted_json,
                "file_source": serializer.data
            }
            return Response(context, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST) 


@api_view(["POST"])
@permission_classes([AllowAny])
def extract_content_file_view(request):
    if request.method == "POST":
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"error": "Aucun fichier envoyé."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Detecter le type de fichier
        file_type = detect_file_type(file.name)
        if file_type == "unknown":
            return Response(
                {"error": "Type de fichier non supporté."},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Content extraction
        content = extract_content(file, file_type)
        if not content:
            return Response(
                {"error": "Impossible d'extraire du texte du fichier."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verification - piece comptable via OpenAI
        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Tu es un expert en comptabilité. Réponds uniquement par OUI ou NON."},
                    {"role": "user",
                     "content": f"Voici le contenu d'un fichier : {content[:5000]}\n"
                                "Dis-moi si c'est une pièce comptable (facture, reçu, devis, note de frais, bon de commande, etc.)."}
                ],
                temperature=0
            )
            decision = response.choices[0].message.content.strip().lower()
        except Exception as e:
            return Response({"error": f"Erreur OpenAI : {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if decision not in ["oui", "yes"]:
            return Response({"error": "Le fichier n'est pas reconnu comme une pièce comptable.",
                             "ai_decision": decision},
                            status=status.HTTP_400_BAD_REQUEST)

        # Extraction JSON structuré du document
        try:
            extraction = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": EXTRACTION_FIELDS_PROMPT 
                    },
                    {
                        "role": "user",
                        "content": content[:6000]
                    }
                ],
                temperature=0
            )

            extracted_json_str = extraction.choices[0].message.content.strip()
            extracted_json_str = clean_ai_json(extracted_json_str)

        except Exception as e:
            return Response({"error": f"Erreur OpenAI extraction JSON : {str(e)}"}, status=500)

        # Convertir texte → JSON Python
        try:
            extracted_json = json.loads(extracted_json_str)
        except json.JSONDecodeError:
            return Response({
                "error": "JSON renvoyé par OpenAI non valide.",
                "raw_response": extracted_json_str
            }, status=500)
        
        context = {
                "status": "success",
                "message": "Informations vérifiées et extraites.",
                "extracted_json": extracted_json,
            }
        return Response(context, status=status.HTTP_201_CREATED)


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
    2. Classer l’opération comptable selon le PCG Madagascar.
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




