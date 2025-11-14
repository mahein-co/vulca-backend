from django.shortcuts import render

from rest_framework import generics
from rest_framework.permissions import AllowAny

from ocr.models import FileSource
from ocr.serializers import FileSourceSerializer


class FileSourceListCreateView(generics.ListCreateAPIView):
    queryset = FileSource.objects.all().order_by('-uploaded_at')
    serializer_class = FileSourceSerializer
    permission_classes = [AllowAny]
 
 

