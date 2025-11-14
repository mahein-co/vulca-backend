from django.shortcuts import render

from rest_framework import generics
from rest_framework.permissions import AllowAny

from ocr.models import SourceFile
from ocr.serializers import SourceFileSerializer


class SourceFileListCreateView(generics.ListCreateAPIView):
    queryset = SourceFile.objects.all().order_by('-uploaded_at')
    serializer_class = SourceFileSerializer
    permission_classes = [AllowAny]
 
 

