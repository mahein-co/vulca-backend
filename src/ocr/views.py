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
from ocr.utils import detect_file_type, extract_content, clean_ai_json, generate_description
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

        # 1. Convertir le JSON string en dict
        raw_json = request.data.get("extracted_json", "{}")

        try:
            extracted_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return Response(
                {"error": "extracted_json doit être un JSON valide"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Générer description GPT
        description = generate_description(
            client=client,
            data=extracted_json,
            json=json,
            model=settings.OPENAI_MODEL
        )

        # 3. Ajouter la description dans request.data **avant serializer**
        mutable = request.data._mutable
        request.data._mutable = True
        request.data["description"] = description
        request.data._mutable = mutable

        # 4. Sérialisation
        serializer = FileSourceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "status": "success",
                    "message": "Document sauvegardé.",
                    "is_accounting_document": True,
                    "file_source": serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )

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




