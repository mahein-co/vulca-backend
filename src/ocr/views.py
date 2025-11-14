from django.shortcuts import render
from vulca_backend import settings

from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from ocr.models import FileSource
from ocr.serializers import FileSourceSerializer
from ocr.utils import detect_file_type, extract_content

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
        print(f"Detected file type: {file_type}")
        # Content extraction
        content = extract_content(file, file_type)
        if not content:
            return Response(
                {"error": "Impossible d'extraire du texte du fichier."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verification via OpenAI
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

        # Serializer et sauvegarde
        serializer = FileSourceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST) 



