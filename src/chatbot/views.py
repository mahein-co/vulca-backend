import os
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# DJANGO -------------------------------------------
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

# REST FRAMEWORK -----------------------------------
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework import viewsets, status, serializers
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

# PGVECTOR -----------------------------------------
from pgvector.django import CosineDistance

# MODELS -------------------------------------------
from chatbot.models import ChatMessage, MessageHistory, DocumentPage, Document
from chatbot.serializers import (
    ChatMessageSerializer, MessageHistorySerializer, DocumentSerializer,
    ChatRequestSerializer, ChatResponseSerializer, ChatSourceSerializer,
    EmptySerializer
)
from chatbot.pagination import DocumentPagination
from chatbot.prompts import SYSTEM_PROMPT

from chatbot.services.embeddings import generate_embedding
from chatbot.services.langchain_service import LangchainRAGService
from langchain_core.messages import HumanMessage, AIMessage
from chatbot.services.query_router import QueryRouter
from chatbot.services.intent_detector import IntentDetector
from chatbot.services.export_service import ExportService

# OPENAI -------------------------------------------
from openai import OpenAI

import re
import json
from datetime import datetime, date
from chatbot.services.accounting_queries import AccountingQueryService
from chatbot.services.text_to_sql import TextToSQLService
from chatbot.services.query_router import QueryRouter
from chatbot.services.export_service import ExportService
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

# OPENAI -------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

greetings = ["bonjour", "bonsoir", "salut", "coucou", "allô", "bon après-midi", "hey", "yo", "coucou toi", "enchanté(e)", "hello", "hi", "salam", "hola", "ciao"]

def format_fr(value):
    """Formatte un nombre avec des espaces pour les milliers et une virgule pour les décimales (ex: 1 000 000,00)"""
    try:
        if value is None: return "0,00"
        # On utilise le formatage standard US (1,234,567.89) puis on permute
        formatted = f"{float(value):,.2f}"
        return formatted.replace(",", " ").replace(".", ",")
    except (ValueError, TypeError):
        return str(value)

# SEARCH VECTOR SIMILARY ------------------------------------------------
def search_similar_pages(query_embedding, project_id, top_k=5, threshold=0.9):
    results = (
        DocumentPage.objects
        .select_related("document")
        .filter(document__project_id=project_id) 
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .filter(distance__lt=threshold)
        .order_by("distance")[:top_k]
    )

    backend_url = getattr(settings, "BACKEND_URL", "https://api.lexaiq.com")
        
    unique_sources = {}
    for page in results:
        document_path = page.document.file_path.url if page.document.file_path else None
        if document_path not in unique_sources:  
            full_path = f"{backend_url}{document_path}"
            unique_sources[document_path] = {
                "content": page.content,
                "document_title": page.document.title,
                "document_path": f"[{page.document.title}]({full_path})",
            }

    formatted_results = list(unique_sources.values())
    return formatted_results


def is_followup_empty_question(user_input):
    followup_phrases = [
        "c'est tout", "cest tout", "plus de détails", "plus de detail",
        "autre chose", "y a autre chose", "ya autre chose",
        "encore ?", "c'est tout ?", "autres ?", "autre ?"
    ]
    text = user_input.lower()
    return any(phrase in text for phrase in followup_phrases)


# GENERATE RESPONSE AI -----------------------------
# @api_view(['POST', 'GET'])
# @permission_classes([IsAuthenticated])
# def generate_response(request):
#     if request.method == 'GET':
#         conversations = ChatMessage.objects.filter(user=request.user)
#         obj_serializers = ChatMessageSerializer(conversations, many=True)
        
#         context = {"conversations": obj_serializers.data,}
#         return Response(context, status=status.HTTP_200_OK)

#     if request.method == 'POST':
#         user_input = request.data.get('user_input')
#         message_history_id = request.data.get('message_history')

#         # User prompt to be vectorized
#         query_embedding = np.array(generate_embedding(user_input))

#         results = search_similar_pages(query_embedding=query_embedding)

#         # Vector request
#         contents = [page["content"] for page in results]
#         context_text = "\n\n".join([res for res in contents])
        
#         # ---------------------------------------------------------------------
#         response = client.chat.completions.create(
#             model=env('OPENAI_MODEL'),
#             messages = [
#                     {"role": "system", "content": SYSTEM_PROMPT},
#                     {"role": "user", "content": f"Contexte:\n{context_text}\n\nQuestion: {user_input}\n\nRéponds de manière claire et concise."}
#                 ],
#             temperature=0.2
#         )

#         unique_sources = [
#             {"title":res["document_path"], "path":res["document_path"]} 
#             for res in results
#         ]

#         ai_response = response.choices[0].message.content
#         if unique_sources:
#             ai_response += "\n\n**Source(s) consultée(s) :**\n"
#             for src in unique_sources:
#                 # title = src["title"]
#                 path = src["path"]
#                 ai_response += f"- {path}\n"
        
#         request.data["ai_response"] = ai_response

#         serializer = ChatMessageSerializer(data=request.data)
#         if serializer.is_valid():
#             message_history = get_object_or_404(MessageHistory, id=message_history_id)
#             serializer.save(user=request.user, message_history=message_history)

#             context = {
#                 "conversation": serializer.data,
#                 "sources": unique_sources, 
#             }
            
#             return Response(context, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#DETECTION DES QUESTIONS FINANCIÈRES (Délégué au service centralisé)
def detect_financial_query(user_input):
    """
    Détecte le type de question financière et extrait les paramètres
    Retourne: {'type': str, 'params': dict} ou None
    """
    return IntentDetector.detect(user_input)

    return {
        'type': query_type,
        'params': params,
        'include_details': demande_details,
        'suggested_filter': suggested_filter
    }


#RECUPERER LES DONNEES COMPTABLES
def get_accounting_context(user, project_id, query_info):
    """
    Récupère les données comptables selon le ou les types de questions détectées
    """
    if not query_info:
        return ""
    
    service = AccountingQueryService(project_id=project_id)
    # On traite TOUS les types d'intentions détectés (multi-intents)
    query_types = query_info.get('types') or [query_info['type']]
    params = query_info['params']
    include_details = query_info.get('include_details', True)
    
    context_parts = []
    
    # ── Header de Période (Crucial pour l'IA) ──────────────────────────
    annee = params.get('annee')
    start_date = params.get('start_date')
    end_date = params.get('end_date')
    
    if annee:
        context_parts.append(f"### PÉRIODE DEMANDÉE : ANNÉE {annee} (01/01/{annee} AU 31/12/{annee}) ###")
    elif start_date and end_date:
        context_parts.append(f"### PÉRIODE DEMANDÉE : DU {start_date} AU {end_date} ###")
    
    processed_types = set()

    for q_type in query_info.get('types', []):
        if q_type in processed_types:
            continue
        processed_types.add(q_type)

        try:
            if q_type == 'ca':
                data = service.get_chiffre_affaires(**params, include_details=include_details)
                context_parts.append(f"**Chiffre d'affaires** ({data['periode']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Comptes: {data['comptes']}")
                if 'formule' in data:
                    context_parts.append(f"- Formule: {data['formule']}")

                if include_details and 'details' in data:
                    context_parts.append(f"\n**Détails des ventes** ({data['nb_lignes']} lignes):")
                    for detail in data['details'][:10]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
                    if data['nb_lignes'] > 10:
                        context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")
            
            elif q_type == 'charges':
                data = service.get_charges(**params, include_details=include_details)
                context_parts.append(f"**Charges** ({data['periode']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Comptes: {data['comptes']}")
                if 'formule' in data:
                    context_parts.append(f"- Formule: {data['formule']}")

                if include_details and 'details' in data:
                    context_parts.append(f"\n**Détails des charges** ({data['nb_lignes']} lignes):")
                    for detail in data['details'][:10]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
                    if data['nb_lignes'] > 10:
                        context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")

            elif q_type == 'ebe':
                data = service.get_ebe(**params, include_details=include_details)
                context_parts.append(f"**EBE (Excédent Brut d'Exploitation)** ({data['periode']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Produits d'exploitation: {format_fr(data['produits_exploitation'])} AR")
                context_parts.append(f"- Charges d'exploitation: {format_fr(data['charges_exploitation'])} AR")

                if include_details and 'details' in data:  
                    context_parts.append(f"\n**Détails EBE** ({data['nb_lignes']} lignes):")
                    context_parts.append(f"Produits d'exploitation:")
                    for detail in data['details']['produits'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")
                    context_parts.append(f"Charges d'exploitation:")
                    for detail in data['details']['charges'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")
            
            elif q_type == 'roe':
                data = service.get_roe(**params)
                context_parts.append(f"**ROE (Rentabilité des capitaux propres)** ({data['periode']}):")
                context_parts.append(f"- Taux: {data['valeur']:.2f}%")
                context_parts.append(f"- Résultat net: {format_fr(data['resultat_net'])} AR")
                context_parts.append(f"- Capitaux propres: {format_fr(data['capitaux_propres'])} AR")

            elif q_type == 'marge_brute':
                data = service.get_marge_brute(**params, include_details=include_details)
                context_parts.append(f"**Marge Brute** ({data['periode']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Taux de marge: {data['taux']:.2f}%")
                context_parts.append(f"- Ventes: {data['ventes']:,.2f} AR")
                context_parts.append(f"- Achats: {data['achats']:,.2f} AR")

                if include_details and 'details' in data:  
                    context_parts.append(f"\n**Détails Marge Brute:**")
                    context_parts.append(f"Ventes:")
                    for detail in data['details']['ventes'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")
                    context_parts.append(f"Achats:")
                    for detail in data['details']['achats'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")

            elif q_type == 'bfr':
                data = service.get_bfr(date_ref=params.get('end_date'), annee=params.get('annee'), include_details=include_details)
                context_parts.append(f"**BFR (Besoin en Fonds de Roulement)** (au {data['date']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Stocks: {data['stocks']:,.2f} AR")
                context_parts.append(f"- Créances clients: {data['creances_clients']:,.2f} AR")
                context_parts.append(f"- Dettes fournisseurs: {data['dettes_fournisseurs']:,.2f} AR")

                if include_details and 'details' in data:  
                    context_parts.append(f"\n**Détails BFR** ({data['nb_lignes']} comptes):")
                    if data['details']['stocks']:
                        context_parts.append(f"Stocks:")
                        for d in data['details']['stocks'][:3]:
                            context_parts.append(f"  - {d['compte']}: {format_fr(d['solde'])} AR")
                    if data['details']['creances_clients']:
                        context_parts.append(f"Créances clients:")
                        for d in data['details']['creances_clients'][:3]:
                            context_parts.append(f"  - {d['compte']}: {format_fr(d['solde'])} AR")

            elif q_type in ('leverage', 'current_ratio'):
                data = service.get_ratios_structure(date_ref=params.get('end_date'), annee=params.get('annee'))
                context_parts.append(f"**Ratios de Structure** (au {data['date']}):")
                if q_type == 'leverage':
                    context_parts.append(f"- Leverage (Levier financier): {data['leverage']:.2f}")
                    context_parts.append(f"- Dettes financières: {data['dettes_financieres']:,.2f} AR")
                    context_parts.append(f"- Capitaux propres: {data['capitaux_propres']:,.2f} AR")
                else:
                    context_parts.append(f"- Current Ratio (Ratio de liquidité): {data['current_ratio']:.2f}")
                    context_parts.append(f"- Actif courant: {data['actif_courant']:,.2f} AR")
                    context_parts.append(f"- Passif courant: {data['passif_courant']:,.2f} AR")

            elif q_type == 'roa':
                data = service.get_roa(**params)
                context_parts.append(f"**ROA (Return on Assets)** ({data['periode']}):")
                context_parts.append(f"- Taux: {data['valeur']:.2f}%")
                context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
                context_parts.append(f"- Total Actif: {data['total_actif']:,.2f} AR")

            elif q_type in ('marge_nette', 'marge_operationnelle'):
                data = service.get_marges_profitabilite(**params)
                context_parts.append(f"**Profitabilité** ({data['periode']}):")
                if q_type == 'marge_nette':
                    context_parts.append(f"- Marge Nette: {data['marge_nette']:.2f}%")
                else:
                    context_parts.append(f"- Marge Opérationnelle: {data['marge_operationnelle']:.2f}%")
                context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
                context_parts.append(f"- EBE: {data['ebe']:,.2f} AR")

            elif q_type == 'rotation_stocks':
                data = service.get_rotation_stocks(annee=params.get('annee'))
                context_parts.append(f"**Rotation des Stocks** (Année {data['annee']}):")
                context_parts.append(f"- Coefficient: {data['coefficient']:.2f} fois")
                context_parts.append(f"- Délai moyen de stockage: {data['jours_stock']:.2f} jours")
                context_parts.append(f"- Achats: {data['achats']:,.2f} AR")
                context_parts.append(f"- Stock final: {data['stock_final']:,.2f} AR")
            
            elif q_type == 'resultat':
                data = service.get_resultat_net(**params, include_details=include_details)
                context_parts.append(f"**Résultat net** ({data['periode']}):")
                context_parts.append(f"- Résultat: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Produits: {data['produits']:,.2f} AR")
                context_parts.append(f"- Charges: {data['charges']:,.2f} AR")

                if include_details and 'details' in data:  
                    context_parts.append(f"\n**Détails Résultat** ({data['nb_lignes']} lignes):")
                    context_parts.append(f"Produits:")
                    for detail in data['details']['produits'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")
                    context_parts.append(f"Charges:")
                    for detail in data['details']['charges'][:5]:
                        context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {format_fr(detail['montant'])} AR")
            
            elif q_type == 'tresorerie':
                data = service.get_tresorerie(annee=params.get('annee'), include_details=include_details)  
                context_parts.append(f"**Trésorerie** (au {data['date']}):")
                context_parts.append(f"- Montant: {format_fr(data['montant'])} AR")
                context_parts.append(f"- Comptes: {data['comptes']}")

                if include_details and 'details' in data:
                    context_parts.append(f"\n**Détails Trésorerie** ({data['nb_lignes']} comptes):")
                    for detail in data['details'][:10]:  
                        context_parts.append(f"  - {detail['compte']} au {detail['date']}: {detail['solde']:,.2f} AR")
                    if data['nb_lignes'] > 10:
                        context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")
            
            elif q_type == 'bilan':
                data = service.get_bilan_summary(annee=params.get('annee'), include_details=include_details)
                context_parts.append(f"**Bilan** ({data['date']}):")
                context_parts.append(f"- Actif total: {format_fr(data['actif'])} AR")
                context_parts.append(f"- Passif total: {format_fr(data['passif'])} AR")
                context_parts.append(f"- Capitaux propres (Total): {format_fr(data.get('capitaux_propres', 0.0))} AR")
                context_parts.append(f"- Équilibre: {format_fr(data['equilibre'])} AR")

                if include_details and 'details' in data:  
                    context_parts.append(f"\n**Détails Bilan** ({data['nb_lignes']} comptes):")
                    context_parts.append(f"Actif:")
                    for d in data['details']['actif'][:5]:
                        context_parts.append(f"  - {d['compte']} - {d['libelle']}: {format_fr(d['montant'])} AR")
                    context_parts.append(f"Passif:")
                    for d in data['details']['passif'][:5]:
                        context_parts.append(f"  - {d['compte']} - {d['libelle']}: {format_fr(d['montant'])} AR")

            elif q_type in ('analyse_globale', 'etats_financiers'):
                data = service.get_dashboard_kpis(**params)
                if "error" in data:
                    context_parts.append(f"Erreur intent {q_type}: {data['error']}")
                else:
                    periode = data.get('periode', 'Période sélectionnée')
                    context_parts.append(f"=== SYNTHÈSE FINANCIÈRE ({periode}) ===")
                    context_parts.append(f"- Chiffre d'Affaires: {format_fr(data.get('ca', 0))} Ar")
                    context_parts.append(f"- Résultat Net: {format_fr(data.get('resultat_net', 0))} Ar")
                    context_parts.append(f"- Capitaux Propres: {format_fr(data.get('capitaux_propres', 0))} Ar")
                    context_parts.append(f"- Trésorerie: {format_fr(data.get('tresorerie', 0))} Ar")
            
            elif q_type == 'comparaison':
                if 'annee1' in params and 'annee2' in params:
                    data = service.compare_periodes(params['annee1'], params['annee2'])
                    context_parts.append(f"**Comparaison {params['annee1']} vs {params['annee2']}:**")
                    context_parts.append(f"- Évolution CA: {data['evolution']['ca_pct']:.2f}%")
                    context_parts.append(f"- Évolution Résultat: {data['evolution']['resultat_pct']:.2f}%")
        
        except Exception as e:
            context_parts.append(f"Erreur pour l'intention '{q_type}': {str(e)}")
            
        context_parts.append("\n" + "-"*30 + "\n")
    
    return "\n".join(context_parts).strip("- \n")


def format_details(data_key, data_dict, include_details):
    """
    Formate les informations comptables et leurs détails si demandés
    """
    text = f"**{data_key}** ({data_dict.get('periode', '')}):\n"
    text += f"- Montant: {format_fr(data_dict.get('montant', 0))} AR\n"
    text += f"- Comptes: {data_dict.get('comptes', '')}\n"
    
    if 'details' in data_dict and data_dict['details']:
        details = data_dict['details']

        # Cas 1: details est une liste (charges, ventes, trésorerie, etc.)
        if isinstance(details, list):
            text += f"\n**Détails {data_key}** ({data_dict.get('nb_lignes', len(details))} lignes):\n"
            for detail in details[:10]:
                montant = detail.get('montant') or detail.get('solde') or 0
                libelle = detail.get('libelle') or detail.get('compte') or ''
                text += f"  - {detail.get('date')} | {detail.get('compte')} - {libelle}: {montant:,.2f} AR\n"
            if data_dict.get('nb_lignes', len(details)) > 10:
                text += f"  ... et {data_dict.get('nb_lignes') - 10} autres lignes\n"

        # Cas 2: details est un dictionnaire (résultat net)
        elif isinstance(details, dict):
            text += f"\n**Détails {data_key}**:\n"
            for section, items in details.items():
                text += f"- {section.capitalize()}:\n"
                for item in items[:10]:
                    montant = item.get('montant') or item.get('solde') or 0
                    libelle = item.get('libelle') or item.get('compte') or ''
                    text += f"  - {item.get('date')} | {item.get('compte')} - {libelle}: {format_fr(montant)} AR\n"
                if len(items) > 10:
                    text += f"  ... et {len(items) - 10} autres lignes\n"

    text += "\n"
    return text




@extend_schema(
    request=ChatRequestSerializer,
    responses={201: ChatResponseSerializer},
    description="Génère une réponse de l'IA basée sur l'input utilisateur et le contexte financier."
)
@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def generate_response(request):
    if request.method == 'GET':
        conversations = ChatMessage.objects.filter(user=request.user)
        obj_serializers = ChatMessageSerializer(conversations, many=True)
        return Response({"conversations": obj_serializers.data}, status=status.HTTP_200_OK)

    if request.method == 'POST':
        try:
            user = request.user
            user_input = request.data.get('user_input')
            message_history_id = request.data.get('message_history')
            project_id = request.data.get('project_id')
            filtered_data = request.data.get('filtered_data')

            if not user_input or not user_input.strip():
                return Response({"error": "Le message ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)

            if not project_id:
                return Response({"error": "project_id est requis"}, status=status.HTTP_400_BAD_REQUEST)
            
            print(f"\n[DEBUG] Message reçu: {user_input}")
            print(f"[DEBUG] Filtered Data présente: {filtered_data is not None}")
            if filtered_data:
                print(f"[DEBUG] Content of Filtered Data: {json.dumps(filtered_data, indent=2)}")

            # ─── COURT-CIRCUIT SALUTATIONS ────────────────────────────────────────
            is_greeting = any(g == user_input.lower().strip() for g in greetings)
            if is_greeting:
                greeting_replies = [
                    "Bonjour ! Je suis votre assistant comptable VULCA. Comment puis-je vous aider aujourd'hui ?",
                    "Bonjour ! Ravi de vous retrouver. Quelle analyse financière souhaitez-vous effectuer ?",
                    "Bonsoir ! Je suis à votre disposition pour toute question comptable ou financière.",
                ]
                import hashlib
                idx = int(hashlib.md5(user_input.encode()).hexdigest(), 16) % len(greeting_replies)
                ai_response = greeting_replies[idx]
                request.data["ai_response"] = ai_response
                serializer = ChatMessageSerializer(data=request.data)
                if serializer.is_valid():
                    message_history = MessageHistory.objects.get(id=message_history_id, user=user, project_id=project_id)
                    if not message_history.title or message_history.title in ["Nouvelle discussion", "New Chat History", "", "None"]:
                        message_history.title = user_input.strip()[:40]
                        message_history.save()
                    serializer.save(user=user, message_history=message_history)
                    return Response({"conversation": serializer.data, "sources": [], "suggested_filter": None}, status=status.HTTP_201_CREATED)

            accounting_context = ""
            intent_detected = False
            result = None
            
            # --- ÉTAPE 1 : DÉTECTION SÉMANTIQUE DES INTENTIONS ET DATES ---
            # RÈGLE ABSOLUE : Les dates dépendent uniquement de l'utilisateur (ou global).
            detection = IntentDetector.detect(user_input)
            params = detection.get('params', {}) if detection else {}
            current_types = detection.get('types', []) if detection else []

            # Héritage du contexte de conversation si nécessaire
            if project_id and message_history_id:
                # On hérite si la question est un export seul ou si des paramètres sont manquants
                is_pure_export = 'export' in current_types and len(current_types) == 1
                needs_inheritance = is_pure_export or not params.get('start_date') or \
                                  ('grand_livre' in current_types and not params.get('numero_compte'))
                
                if needs_inheritance:
                    last_messages = ChatMessage.objects.filter(
                        message_history_id=message_history_id
                    ).order_by('-timestamp')[:10]
                    
                    for msg in last_messages:
                        prev_detection = IntentDetector.detect(msg.user_input)
                        if prev_detection and prev_detection.get('params'):
                            p = prev_detection['params']
                            if not params.get('start_date'):
                                params['start_date'] = p.get('start_date')
                                params['end_date'] = p.get('end_date')
                                params['annee'] = p.get('annee')
                            if not params.get('numero_compte') and p.get('numero_compte'):
                                params['numero_compte'] = p['numero_compte']
                            if (not current_types or current_types == ['export']) and prev_detection.get('types'):
                                prev_types = prev_detection['types']
                                if 'export' in current_types:
                                    # Garder 'export' mais ajouter les types métier précédents
                                    current_types = list(set(current_types + prev_types))
                                else:
                                    current_types = prev_types

            # --- ÉTAPE 2 : RÉCUPÉRATION DES DONNÉES ---
            if project_id:
                router = QueryRouter(project_id=project_id)
                
                # Si on a détecté des intentions financières
                if current_types:
                    intent_detected = True
                    result = router._use_calculated_methods(current_types, params)
                
                # Si aucune intention spécifique mais input textuel → Text-to-SQL ou RAG
                elif user_input:
                    # On tente le routage intelligent (Text-to-SQL)
                    result = router.route(user_input)
                    if result and result.get("source") != "error":
                        intent_detected = True

            # --- ÉTAPE 3 : CONSTRUCTION DU CONTEXTE COMPTABLE ---
            if intent_detected and result:
                if result.get("source") == "calculated":
                    all_intents = result.get("intents", [result.get("intent", "inconnu")])
                    intents_str = ", ".join(all_intents)
                    accounting_context = f"=== DONNÉES CALCULÉES ({intents_str}) ===\n"
                    context_data = result.get("data", {})
                    accounting_context += json.dumps(context_data, ensure_ascii=False, indent=2)
                    accounting_context += "\n\nINSTRUCTION ANALYSE : Utilise EXCLUSIVEMENT les chiffres ci-dessus."
                
                elif result.get("source") == "text_to_sql":
                    nb = result.get("nb_resultats", 0)
                    accounting_context = f"=== DONNÉES BASE DE DONNÉES ({nb} résultats) ===\n"
                    accounting_context += f"Requête exécutée: {result['sql']}\n\n"
                    accounting_context += json.dumps(result["data"][:100], ensure_ascii=False, indent=2)

                # Gestion du filtre suggéré pour le frontend (synchronisation inverse)
                if detection and detection.get('suggested_filter'):
                    request.data["suggested_filter"] = detection['suggested_filter']

            # --- INJECTION DES FILTERED_DATA DU DASHBOARD (si aucun contexte calculé) ---
            if not accounting_context and filtered_data:
                filter_info = filtered_data.get('filter', {})
                date_start_str = filter_info.get('date_start', 'N/A')
                date_end_str = filter_info.get('date_end', 'N/A')
                accounting_context = f"=== DONNÉES FINANCIÈRES DU TABLEAU DE BORD ===\n"
                accounting_context += f"Période analysée : {date_start_str} au {date_end_str}\n\n"
                for key, label in [('chiffre_affaires', "Chiffre d'affaires"), ('charges', "Charges"),
                                   ('resultat_net', "Résultat net"), ('tresorerie', "Trésorerie"), ('bilan', "Bilan")]:
                    if key in filtered_data:
                        accounting_context += format_details(label, filtered_data[key], True)
                print(f"[DEBUG] filtered_data injecté dans generate_response (fallback Dashboard).")

            # --- ÉTAPE 4 : GESTION DES EXPORTS ---
            if intent_detected and result and result.get("source") == "calculated":
                export_keywords = ["générer", "export", "rapport", "états financiers", "excel", "pdf", "télécharger"]
                if any(kw in user_input.lower() for kw in export_keywords):
                    try:
                        report_type = "Bilan" if any(k in user_input.lower() for k in ["bilan", "états"]) else "Rapport Financier"
                        if "compar" in user_input.lower(): report_type = "Rapport Comparatif"
                        
                        want_pdf = "pdf" in user_input.lower()
                        want_excel = "excel" in user_input.lower()
                        if not want_pdf and not want_excel: want_pdf = want_excel = True
                        
                        backend_url = getattr(settings, "BACKEND_URL", request.build_absolute_uri('/')[:-1])
                        export_links = []
                        
                        if want_excel:
                            buffer_excel = ExportService.generate_excel_report(result["data"], report_type=report_type)
                            filename_excel = f"exports/{report_type.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                            file_path_excel = default_storage.save(filename_excel, ContentFile(buffer_excel.getvalue()))
                            export_links.append(f"📊 [Télécharger le Rapport Excel]({backend_url}{settings.MEDIA_URL}{file_path_excel})")
                            
                        if want_pdf:
                            buffer_pdf = ExportService.generate_pdf_report(result["data"], report_type=report_type)
                            filename_pdf = f"exports/{report_type.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                            file_path_pdf = default_storage.save(filename_pdf, ContentFile(buffer_pdf.getvalue()))
                            export_links.append(f"📄 [Télécharger le Rapport PDF]({backend_url}{settings.MEDIA_URL}{file_path_pdf})")
                            
                        if export_links:
                            accounting_context += "\n\n### 📥 EXPORTS DISPONIBLES (REKAPY)\n" + "\n".join(export_links)
                    except Exception as e:
                        print(f"[ERROR] Export failed: {str(e)}")
                        accounting_context += f"\n\n(Note: L'export a échoué: {str(e)})"
            
            # ✅ RECHERCHE ET GÉNÉRATION VIA LANGCHAIN
            date_start = None
            date_end = None
            if 'params' in locals() and params:
                date_start = params.get('start_date')
                date_end = params.get('end_date')
                if not date_start and params.get('annee'):
                    date_start, date_end = f"{params['annee']}-01-01", f"{params['annee']}-12-31"

            langchain_history = []
            if message_history_id:
                messages_precedents = ChatMessage.objects.filter(message_history_id=message_history_id).order_by('timestamp')[:10]
                for msg in messages_precedents:
                    langchain_history.append(HumanMessage(content=msg.user_input))
                    langchain_history.append(AIMessage(content=msg.ai_response))

            rag_service = LangchainRAGService(project_id=project_id, date_start=date_start, date_end=date_end)
            print(f"[DEBUG] RAG: Final call with dates {date_start} to {date_end}")
            ai_response, retrieved_docs = rag_service.get_response(user_input=user_input, history_messages=langchain_history, accounting_context=accounting_context)

            # ✅ ÉTAPE 5 : INJECTION FORCÉE DES LIENS D'EXPORT DANS LA RÉPONSE FINALE
            links = locals().get('export_links', [])
            if links:
                if "📥 EXPORTS DISPONIBLES" not in ai_response:
                    ai_response += "\n\n### 📥 EXPORTS DISPONIBLES (REKAPY)\n" + "\n".join(links)

            # ✅ FORMATAGE ET ENREGISTREMENT
            unique_sources = []
            seen_paths = set()
            for doc in retrieved_docs:
                path = doc.metadata.get("path")
                if path and path not in seen_paths:
                    unique_sources.append({"title": doc.metadata.get("source"), "path": f"[{doc.metadata.get('source')}]({settings.BACKEND_URL}{path})"})
                    seen_paths.add(path)
            
            if unique_sources:
                ai_response += "\n\n**Source(s) consultée(s) :**\n"
                for src in unique_sources: ai_response += f"- {src['path']}\n"
            
            request.data["ai_response"] = ai_response
            serializer = ChatMessageSerializer(data=request.data)
            if serializer.is_valid():
                message_history = MessageHistory.objects.get(id=message_history_id, user=user, project_id=project_id)
                if not message_history.title or message_history.title in ["Nouvelle discussion", "New Chat History", "", "None"]:
                    new_title = user_input.strip()[:40] + ("..." if len(user_input.strip()) > 40 else "")
                    message_history.title = new_title
                    message_history.save()
                serializer.save(user=user, message_history=message_history)
                return Response({"conversation": serializer.data, "sources": unique_sources, "suggested_filter": request.data.get("suggested_filter")}, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            import traceback
            print(f"[CRITICAL ERROR] Chatbot failure: {str(e)}")
            print(traceback.format_exc())
            return Response({"error": "Désolé, une erreur interne est survenue.", "details": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# GET MESSAGE HISTORIES -----------------------------
@extend_schema(
    methods=['GET'],
    parameters=[
        OpenApiParameter("project_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="ID du projet"),
    ],
    responses={200: MessageHistorySerializer(many=True)},
    description="Récupère les historiques de discussion pour un projet donné."
)
@extend_schema(
    methods=['POST'],
    parameters=[
        OpenApiParameter("project_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="ID du projet"),
    ],
    request=MessageHistorySerializer,
    responses={201: MessageHistorySerializer},
    description="Crée un nouvel historique de discussion pour un projet donné."
)
@api_view(["POST", "GET"])
@permission_classes([IsAuthenticated])
def get_message_histories(request):
    project_id = request.query_params.get('project_id')

    if not project_id:
        return Response(
            {"error": "project_id est requis"}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    # GET ALL HISTORIES
    if request.method == "GET":
        histories = MessageHistory.objects.filter(
            user=request.user,
            project_id=project_id
        )
        obj_serializers = MessageHistorySerializer(histories, many=True)

        context = {"histories":obj_serializers.data, }
        return Response(context, status=status.HTTP_200_OK)

    # SAVE A HISTORY
    if request.method == "POST":
        print("=" * 50)
        print("POST /api/histories/ - Debugging:")
        print(f"request.data: {request.data}")
        print(f"project_id from query_params: {project_id}")
        print("=" * 50)
        
        data = request.data.copy()
        data['project'] = project_id

        obj_serializer = MessageHistorySerializer(data=data)
        if obj_serializer.is_valid():
            history_saved = obj_serializer.save(
                user=request.user,
                project_id=project_id
            ) 
            context = {
                "history": MessageHistorySerializer(history_saved).data, 
                "id": history_saved.id, 
                "error": False,
                "message": "History was saved successfully."
            }
            return Response(context, status=status.HTTP_201_CREATED)
        else:
            # Afficher les erreurs
            print("Erreurs de validation:", obj_serializer.errors)
            errors = obj_serializer.errors
            first_error = next(iter(errors.values()))[0] if errors else "Invalid data"
            context = {
                "error": True, 
                "message": first_error, 
                "details": obj_serializer.errors
            }
            return Response(context, status=status.HTTP_400_BAD_REQUEST)
        
    
# MESSAGE HISTORY DETAILS -----------------------------
@extend_schema(
    methods=["GET"],
    responses={200: MessageHistorySerializer},
    description="Récupère les détails d'un historique avec ses messages."
)
@extend_schema(
    methods=["PUT"],
    request=MessageHistorySerializer,
    responses={200: MessageHistorySerializer},
    description="Met à jour un historique."
)
@extend_schema(
    methods=["DELETE"],
    responses={204: None},
    description="Supprime un historique."
)
@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def message_history_details(request, id):
    user = request.user
    project_id = request.data.get('project_id') or request.query_params.get('project_id') or getattr(request, 'project_id', None)
    
    try:
        # ✅ SÉCURITÉ : D'abord on vérifie l'existence et l'appartenance à l'utilisateur
        history = MessageHistory.objects.get(id=id, user=user)
    except MessageHistory.DoesNotExist:
        # Fallback: try to fetch by project_id if provided (allows shared project histories)
        if project_id:
            try:
                history = MessageHistory.objects.get(id=id, project_id=project_id)
            except MessageHistory.DoesNotExist:
                print(f"[DEBUG] History {id} NOT FOUND for project {project_id}")
                return Response(
                    {"error": f"Historique {id} introuvable pour le projet {project_id}"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            print(f"[DEBUG] History {id} NOT FOUND for user {user.id}")
            return Response(
                {"error": f"Historique {id} introuvable"},
                status=status.HTTP_404_NOT_FOUND,
            )

    # ✅ RESILIENCE : Si l'historique est "orphelin" (legacy), on lui attribue le projet actuel
    if history.project_id is None and project_id:
        print(f"[DEBUG] Auto-assigning project {project_id} to legacy history {id}")
        history.project_id = project_id
        history.save()

    # ✅ SÉCURITÉ MULTI-TENANT : Si un projet est spécifié, on vérifie que c'est le bon
    if project_id and str(history.project_id) != str(project_id):
        print(f"[DEBUG] History {id} belongs to project {history.project_id}, but request specified {project_id}")
        return Response(
            {"error": f"Accès refusé à cet historique pour le projet actuel"},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    # HISTORY DETAILS
    if request.method == "GET":
        # Reload with prefetched chat_messages
        history = MessageHistory.objects.prefetch_related('chat_messages').get(id=id)
        obj_serializer = MessageHistorySerializer(history)

        # pour déboguer
        print("=" * 50)
        print(f"GET /api/histories/{id}/ Response (fallback-aware):")
        print(f"Data: {obj_serializer.data}")
        print("=" * 50)

        return Response(obj_serializer.data, status=status.HTTP_200_OK)

    # UPDATE A HISTORY 
    elif request.method == "PUT":
        # reuse the history object already validated
        obj_serializer = MessageHistorySerializer(history, data=request.data, partial=True)
        if obj_serializer.is_valid():
            obj_serializer.save()
            return Response(
                {"message": "Historique mis à jour avec succès", "history": obj_serializer.data},
                status=status.HTTP_200_OK
            )
        return Response(obj_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    # DELETE A HISTORY
    elif request.method == "DELETE":
        history.delete()
        return Response({"message": "Historique supprimé avec succès"}, status=status.HTTP_204_NO_CONTENT)
    
# SAVE NEW HISTORY AND NEW CHAT -----------------------------
@extend_schema(
    request=ChatRequestSerializer,
    responses={201: ChatResponseSerializer},
    description="Crée un nouvel historique et y ajoute un premier message avec réponse de l'IA."
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_new_history_and_new_chat(request):
    #from payment.models import Subscription 
    import traceback

    if request.method == "POST":
        # User input
        user_input = request.data.get("user_input")

        if not user_input or not user_input.strip():
            return Response(
                {"error": "Le message ne peut pas être vide"},
                status=status.HTTP_400_BAD_REQUEST
            )

        project_id = request.data.get('project_id') or getattr(request, 'project_id', None)
        if not project_id:
            return Response(
                {"error": "project_id est requis"},
                status=status.HTTP_400_BAD_REQUEST
            )

        filtered_data = request.data.get('filtered_data')

        history_data = {
            "title": request.data.get("title", "Nouvelle discussion"),
            "user": request.user.id,
            "project": project_id  # ✅ CRITICAL: Associer au projet
        }
        new_chat_data = {
            "user_input": user_input,
            "user": request.user.id
        }
        history_serializer = MessageHistorySerializer(data=history_data)

        if history_serializer.is_valid():
            # Save new history
            history_saved = history_serializer.save(user=request.user, project_id=project_id)
            new_chat_data["message_history"] = history_saved.id

            # ── 1. CONTEXTE FINANCIER (même logique que generate_response) ─────────
            accounting_context = ""
            current_system_prompt = SYSTEM_PROMPT
            router = QueryRouter(project_id=project_id)

            intent = router._detect_calculated_intent(user_input)
            if intent:
                result = router.route(user_input)
                if result["source"] == "calculated":
                    accounting_context = f"=== DONNÉES CALCULÉES ({result['intent']}) ===\n"
                    accounting_context += json.dumps(result["data"], ensure_ascii=False, indent=2)
                    print(f"[DEBUG][new_chat] Intent détecté: {intent}, données injectées.")
            elif filtered_data:
                is_greeting = any(g == user_input.lower().strip() for g in greetings)
                if is_greeting:
                    accounting_context = "=== CONTEXTE ACTUEL ===\n"
                    filter_info = filtered_data.get('filter', {})
                    accounting_context += f"Période active sur le dashboard: {filter_info.get('date_start')} au {filter_info.get('date_end')}\n"
                    accounting_context += "(Réponds simplement à la salutation sans résumer toutes les données financières sauf si demandé.)\n"
                else:
                    accounting_context = "=== DONNÉES COMPTABLES FILTRÉES ===\n"
                    filter_info = filtered_data.get('filter', {})
                    accounting_context += f"Période analysée: {filter_info.get('date_start')} au {filter_info.get('date_end')}\n\n"
                    for key, label in [('chiffre_affaires', "Chiffre d'affaires"), ('charges', "Charges"),
                                       ('resultat_net', "Résultat net"), ('tresorerie', "Trésorerie"), ('bilan', "Bilan")]:
                        if key in filtered_data:
                            accounting_context += format_details(label, filtered_data[key], True)
                    print(f"[DEBUG][new_chat] filtered_data injecté depuis le dashboard.")

            if accounting_context and filtered_data:
                dates = filtered_data.get('filter', {})
                current_system_prompt += f"\n\nNOTE IMPORTANTE : Tu as actuellement accès aux données réelles du tableau de bord pour la période du {dates.get('date_start')} au {dates.get('date_end')}. Analyse ces données pour répondre à l'utilisateur."

            current_system_prompt += "\nSi les données sont à 0.00 AR, cela signifie qu'aucune écriture comptable n'a été trouvée pour ce compte sur la période. Interprète cela comme une absence d'activité importée plutôt que comme une erreur."

            # ── 2. RECHERCHE VECTORIELLE (Documents) ───────────────────────────────
            query_embedding = np.array(generate_embedding(user_input))
            results = search_similar_pages(query_embedding=query_embedding, project_id=project_id)
            contents = [page["content"] for page in results]
            context_text = "\n\n".join([res for res in contents])

            # ── 3. CONSTRUCTION DU CONTEXTE COMPLET ───────────────────────────────
            full_context = ""
            if accounting_context:
                full_context += "=== DONNÉES FINANCIÈRES DU TABLEAU DE BORD ===\n"
                full_context += accounting_context
                full_context += "\n\n"
            if context_text:
                full_context += "=== DOCUMENTS DE RÉFÉRENCE ===\n"
                full_context += context_text

            print(f"[DEBUG][new_chat] Full context length: {len(full_context)} chars")

            # ── 4. APPEL OPENAI ────────────────────────────────────────────────────
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": current_system_prompt},
                    {"role": "user", "content": f"Contexte:\n{full_context}\n\nQuestion: {user_input}\n\nRéponds de manière claire et concise."}
                ],
                temperature=0.2
            )

            unique_sources = [
                {"title": res["document_path"], "path": res["document_path"]}
                for res in results
            ]

            ai_response = response.choices[0].message.content
            if unique_sources:
                ai_response += "\n\n**Source(s) consultée(s) :**\n"
                for src in unique_sources:
                    ai_response += f"- {src['path']}\n"

            new_chat_data["ai_response"] = ai_response
            new_chat_serializer = ChatMessageSerializer(data=new_chat_data)

            if new_chat_serializer.is_valid():
                new_chat_serializer.save(
                    user=request.user,
                    message_history=history_serializer.instance
                )
                return Response(
                    {
                        "conversation": new_chat_serializer.data,
                        "sources": unique_sources,
                    },
                    status=status.HTTP_201_CREATED
                )

        return Response({"error": "Impossible de créer le chat"}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=serializers.Serializer, # Inline simple serializer potential
    responses={200: MessageHistorySerializer},
    description="Renomme le titre d'un historique."
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def rename_history(request, id):
    user = request.user
    project_id = request.data.get('project_id') or request.query_params.get('project_id') or getattr(request, 'project_id', None)
    
    try:
        history = MessageHistory.objects.get(id=id, user=user)
        # Auto-réparation si orphelin
        if history.project_id is None and project_id:
             history.project_id = project_id
             history.save()
        elif project_id and str(history.project_id) != str(project_id):
             return Response({"error": "Accès refusé"}, status=status.HTTP_403_FORBIDDEN)
    except MessageHistory.DoesNotExist:
        print(f"[DEBUG] History {id} not found for rename by user {user.id}")
        return Response({"error": f"Historique {id} introuvable"}, status=status.HTTP_404_NOT_FOUND)
    
    new_title = request.data.get("title")
    if not new_title or not new_title.strip():
        return Response(
            {"error": "Le titre ne peut pas être vide"}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    history.title = new_title.strip()
    history.save()
    
    return Response(
        {
            "message": "Titre modifié avec succès",
            "history": MessageHistorySerializer(history).data
        },
        status=status.HTTP_200_OK
    )      