import os
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# DJANGO -------------------------------------------
from django.shortcuts import get_object_or_404
from django.conf import settings

# REST FRAMEWORK -----------------------------------
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework import viewsets, status
from rest_framework.views import APIView

# PGVECTOR -----------------------------------------
from pgvector.django import CosineDistance

# MODELS -------------------------------------------
from chatbot.models import ChatMessage, MessageHistory, DocumentPage, Document
from chatbot.serializers import ChatMessageSerializer, MessageHistorySerializer, DocumentSerializer
from chatbot.pagination import DocumentPagination
from chatbot.prompts import SYSTEM_PROMPT

from chatbot.services.embeddings import generate_embedding
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
    Récupère les données comptables selon le type de question
    """

    if not query_info:
        return ""
    
    service = AccountingQueryService(project_id=project_id)
    query_type = query_info['type']
    params = query_info['params']
    include_details = query_info.get('include_details', True)
    
    context_parts = []
    
    try:
        if query_type == 'ca':
            data = service.get_chiffre_affaires(**params, include_details=include_details)
            context_parts.append(f"**Chiffre d'affaires** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
            if 'formule' in data:
                context_parts.append(f"- Formule: {data['formule']}")

            # Afficher les détails si disponibles
            if 'details' in data:
                context_parts.append(f"\n**Détails des ventes** ({data['nb_lignes']} lignes):")
                for detail in data['details'][:10]:  # Limiter à 10 lignes max
                    context_parts.append(
                        f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR"
                    )
                if data['nb_lignes'] > 10:
                    context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")
        
        elif query_type == 'charges':
            data = service.get_charges(**params, include_details=include_details)
            context_parts.append(f"**Charges** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
            if 'formule' in data:
                context_parts.append(f"- Formule: {data['formule']}")

            if 'details' in data:
                context_parts.append(f"\n**Détails des charges** ({data['nb_lignes']} lignes):")
                for detail in data['details'][:10]:
                    context_parts.append(
                        f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR"
                    )
                if data['nb_lignes'] > 10:
                    context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")

        elif query_type == 'ebe':
            data = service.get_ebe(**params, include_details=include_details)
            context_parts.append(f"**EBE (Excédent Brut d'Exploitation)** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Produits d'exploitation: {data['produits_exploitation']:,.2f} AR")
            context_parts.append(f"- Charges d'exploitation: {data['charges_exploitation']:,.2f} AR")

            if 'details' in data:  
                context_parts.append(f"\n**Détails EBE** ({data['nb_lignes']} lignes):")
                context_parts.append(f"Produits d'exploitation:")
                for detail in data['details']['produits'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
                context_parts.append(f"Charges d'exploitation:")
                for detail in data['details']['charges'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")


            
        elif query_type == 'roe':
            data = service.get_roe(**params)
            context_parts.append(f"**ROE (Rentabilité des capitaux propres)** ({data['periode']}):")
            context_parts.append(f"- Taux: {data['valeur']:.2f}%")
            context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
            context_parts.append(f"- Capitaux propres: {data['capitaux_propres']:,.2f} AR")

        elif query_type == 'marge_brute':
            data = service.get_marge_brute(**params, include_details=include_details)
            context_parts.append(f"**Marge Brute** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Taux de marge: {data['taux']:.2f}%")
            context_parts.append(f"- Ventes: {data['ventes']:,.2f} AR")
            context_parts.append(f"- Achats: {data['achats']:,.2f} AR")

            if 'details' in data:  
                context_parts.append(f"\n**Détails Marge Brute:**")
                context_parts.append(f"Ventes:")
                for detail in data['details']['ventes'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
                context_parts.append(f"Achats:")
                for detail in data['details']['achats'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")


        elif query_type == 'bfr':
            data = service.get_bfr(date_ref=params.get('end_date'), annee=params.get('annee'), include_details=include_details)
            context_parts.append(f"**BFR (Besoin en Fonds de Roulement)** (au {data['date']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Stocks: {data['stocks']:,.2f} AR")
            context_parts.append(f"- Créances clients: {data['creances_clients']:,.2f} AR")
            context_parts.append(f"- Dettes fournisseurs: {data['dettes_fournisseurs']:,.2f} AR")

            if 'details' in data:  
                context_parts.append(f"\n**Détails BFR** ({data['nb_lignes']} comptes):")
                if data['details']['stocks']:
                    context_parts.append(f"Stocks:")
                    for d in data['details']['stocks'][:3]:
                        context_parts.append(f"  - {d['compte']}: {d['solde']:,.2f} AR")
                if data['details']['creances_clients']:
                    context_parts.append(f"Créances clients:")
                    for d in data['details']['creances_clients'][:3]:
                        context_parts.append(f"  - {d['compte']}: {d['solde']:,.2f} AR")

        elif query_type == 'leverage' or query_type == 'current_ratio':
            data = service.get_ratios_structure(date_ref=params.get('end_date'), annee=params.get('annee'))
            context_parts.append(f"**Ratios de Structure** (au {data['date']}):")
            if query_type == 'leverage':
                context_parts.append(f"- Leverage (Levier financier): {data['leverage']:.2f}")
                context_parts.append(f"- Dettes financières: {data['dettes_financieres']:,.2f} AR")
                context_parts.append(f"- Capitaux propres: {data['capitaux_propres']:,.2f} AR")
            else:
                context_parts.append(f"- Current Ratio (Ratio de liquidité): {data['current_ratio']:.2f}")
                context_parts.append(f"- Actif courant: {data['actif_courant']:,.2f} AR")
                context_parts.append(f"- Passif courant: {data['passif_courant']:,.2f} AR")

        elif query_type == 'roa':
            data = service.get_roa(**params)
            context_parts.append(f"**ROA (Return on Assets)** ({data['periode']}):")
            context_parts.append(f"- Taux: {data['valeur']:.2f}%")
            context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
            context_parts.append(f"- Total Actif: {data['total_actif']:,.2f} AR")

        elif query_type == 'marge_nette' or query_type == 'marge_operationnelle':
            data = service.get_marges_profitabilite(**params)
            context_parts.append(f"**Profitabilité** ({data['periode']}):")
            if query_type == 'marge_nette':
                context_parts.append(f"- Marge Nette: {data['marge_nette']:.2f}%")
            else:
                context_parts.append(f"- Marge Opérationnelle: {data['marge_operationnelle']:.2f}%")
            context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
            context_parts.append(f"- EBE: {data['ebe']:,.2f} AR")

        elif query_type == 'rotation_stocks':
            data = service.get_rotation_stocks(annee=params.get('annee'))
            context_parts.append(f"**Rotation des Stocks** (Année {data['annee']}):")
            context_parts.append(f"- Coefficient: {data['coefficient']:.2f} fois")
            context_parts.append(f"- Délai moyen de stockage: {data['jours_stock']:.2f} jours")
            context_parts.append(f"- Achats: {data['achats']:,.2f} AR")
            context_parts.append(f"- Stock final: {data['stock_final']:,.2f} AR")
        
        elif query_type == 'resultat':
            data = service.get_resultat_net(**params, include_details=include_details)
            context_parts.append(f"**Résultat net** ({data['periode']}):")
            context_parts.append(f"- Résultat: {data['montant']:,.2f} AR")
            context_parts.append(f"- Produits: {data['produits']:,.2f} AR")
            context_parts.append(f"- Charges: {data['charges']:,.2f} AR")

            if 'details' in data:  
                context_parts.append(f"\n**Détails Résultat** ({data['nb_lignes']} lignes):")
                context_parts.append(f"Produits:")
                for detail in data['details']['produits'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
                context_parts.append(f"Charges:")
                for detail in data['details']['charges'][:5]:
                    context_parts.append(f"  - {detail['date']} | {detail['compte']} - {detail['libelle']}: {detail['montant']:,.2f} AR")
        
        
        elif query_type == 'tresorerie':
            data = service.get_tresorerie(annee=params.get('annee'), include_details=include_details)  
            context_parts.append(f"**Trésorerie** (au {data['date']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")

            if 'details' in data:
                context_parts.append(f"\n**Détails Trésorerie** ({data['nb_lignes']} comptes):")
                for detail in data['details'][:10]:  
                    context_parts.append(f"  - {detail['compte']} au {detail['date']}: {detail['solde']:,.2f} AR")
                if data['nb_lignes'] > 10:
                    context_parts.append(f"  ... et {data['nb_lignes'] - 10} autres lignes")
        
        
        elif query_type == 'bilan':
            data = service.get_bilan_summary(annee=params.get('annee'), include_details=include_details)
            context_parts.append(f"**Bilan** ({data['date']}):")
            context_parts.append(f"- Actif total: {data['actif']:,.2f} AR")
            context_parts.append(f"- Passif total: {data['passif']:,.2f} AR")
            context_parts.append(f"- Équilibre: {data['equilibre']:,.2f} AR")

            if 'details' in data:  
                context_parts.append(f"\n**Détails Bilan** ({data['nb_lignes']} comptes):")
                context_parts.append(f"Actif:")
                for d in data['details']['actif'][:5]:
                    context_parts.append(f"  - {d['compte']} - {d['libelle']}: {d['montant']:,.2f} AR")
                context_parts.append(f"Passif:")
                for d in data['details']['passif'][:5]:
                    context_parts.append(f"  - {d['compte']} - {d['libelle']}: {d['montant']:,.2f} AR")

        elif query_type in ('analyse_globale', 'etats_financiers'):
            data = service.get_dashboard_kpis(**params)
            if "error" in data:
                context_parts.append(f"Erreur: {data['error']}")
            else:
                periode = data.get('periode', 'Période sélectionnée')
                context_parts.append(f"=== SYNTHÈSE FINANCIÈRE COMPLÈTE ({periode}) ===")
                context_parts.append("")
                context_parts.append("**Compte de Résultat :**")
                context_parts.append(f"- Chiffre d'Affaires (CA): {data.get('ca', 0):,.2f} Ar")
                context_parts.append(f"- Total Produits: {data.get('total_produits', 0):,.2f} Ar")
                context_parts.append(f"- Total Charges: {data.get('total_charges', 0):,.2f} Ar")
                context_parts.append(f"- Résultat Net: {data.get('resultat_net', 0):,.2f} Ar")
                context_parts.append(f"- EBE: {data.get('ebe', 0):,.2f} Ar")
                context_parts.append(f"- CAF: {data.get('caf', 0):,.2f} Ar")
                context_parts.append(f"- Marge Brute: {data.get('marge_brute', 0):,.2f} Ar")
                context_parts.append("")
                context_parts.append("**Indicateurs de Liquidité & Structure :**")
                context_parts.append(f"- Trésorerie Nette: {data.get('tresorerie', 0):,.2f} Ar")
                context_parts.append(f"- BFR: {data.get('bfr', 0):,.2f} Ar")
                context_parts.append(f"- Actifs Courants: {data.get('actifs_courants', 0):,.2f} Ar")
                context_parts.append(f"- Passifs Courants: {data.get('passifs_courants', 0):,.2f} Ar")
                context_parts.append(f"- Total Actif: {data.get('total_actif', 0):,.2f} Ar")
                context_parts.append(f"- Capitaux Propres: {data.get('capitaux_propres', 0):,.2f} Ar")
                context_parts.append(f"- Dettes Financières: {data.get('dettes_financieres', 0):,.2f} Ar")
                context_parts.append(f"- Créances Clients: {data.get('creances_clients', 0):,.2f} Ar")
                context_parts.append(f"- Dettes Fournisseurs: {data.get('dettes_fournisseurs', 0):,.2f} Ar")
                context_parts.append("")
                context_parts.append("**Ratios de Rentabilité :**")
                context_parts.append(f"- ROE: {data.get('roe', 0):.2f}%")
                context_parts.append(f"- ROA: {data.get('roa', 0):.2f}%")
                context_parts.append(f"- Marge Nette: {data.get('marge_nette', 0):.2f}%")
                context_parts.append(f"- Marge Opérationnelle: {data.get('marge_operationnelle', 0):.2f}%")
                context_parts.append(f"- Current Ratio: {data.get('current_ratio', 0):.2f}")
                context_parts.append(f"- Quick Ratio: {data.get('quick_ratio', 0):.2f}")
                context_parts.append(f"- Gearing: {data.get('gearing', 0):.2f}%")
                context_parts.append(f"- Leverage Brut: {data.get('leverage', 0):.2f}")
                context_parts.append(f"- Rotation Stocks: {data.get('rotation_stock', 0):.2f}x")
        
        elif query_type == 'comparaison':
            if 'annee1' in params and 'annee2' in params:
                data = service.compare_periodes(params['annee1'], params['annee2'])
                context_parts.append(f"**Comparaison {params['annee1']} vs {params['annee2']}:**")
                context_parts.append(f"\n**Année {params['annee1']} ({data['annee_1']['nb_mois_enregistres']} mois):**")
                context_parts.append(f"- CA Total: {data['annee_1']['chiffre_affaires']:,.2f} AR")
                context_parts.append(f"- Moyenne mensuelle CA: {data['annee_1']['moyenne_mensuelle_ca']:,.2f} AR")
                context_parts.append(f"- Charges Totales: {data['annee_1']['charges']:,.2f} AR")
                context_parts.append(f"- Moyenne mensuelle Charges: {data['annee_1']['moyenne_mensuelle_charges']:,.2f} AR")
                context_parts.append(f"- Résultat: {data['annee_1']['resultat']:,.2f} AR")
                
                context_parts.append(f"\n**Année {params['annee2']} ({data['annee_2']['nb_mois_enregistres']} mois):**")
                context_parts.append(f"- CA Total: {data['annee_2']['chiffre_affaires']:,.2f} AR")
                context_parts.append(f"- Moyenne mensuelle CA: {data['annee_2']['moyenne_mensuelle_ca']:,.2f} AR")
                context_parts.append(f"- Charges Totales: {data['annee_2']['charges']:,.2f} AR")
                context_parts.append(f"- Moyenne mensuelle Charges: {data['annee_2']['moyenne_mensuelle_charges']:,.2f} AR")
                context_parts.append(f"- Résultat: {data['annee_2']['resultat']:,.2f} AR")
                context_parts.append(f"\n**Évolution:**")
                context_parts.append(f"- CA: {data['evolution']['ca']:+,.2f} AR ({data['evolution']['ca_pct']:.2f}%)")
                context_parts.append(f"- Charges: {data['evolution']['charges']:+,.2f} AR ({data['evolution']['charges_pct']:.2f}%)")
                context_parts.append(f"- Résultat: {data['evolution']['resultat']:+,.2f} AR ({data['evolution']['resultat_pct']:.2f}%)")
    
    except Exception as e:
        context_parts.append(f"Erreur lors de la récupération des données: {str(e)}")
    
    return "\n".join(context_parts)


def format_details(data_key, data_dict, include_details):
    """
    Formate les informations comptables et leurs détails si demandés
    """
    text = f"**{data_key}** ({data_dict.get('periode', '')}):\n"
    text += f"- Montant: {data_dict.get('montant', 0):,.2f} AR\n"
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
                    text += f"  - {item.get('date')} | {item.get('compte')} - {libelle}: {montant:,.2f} AR\n"
                if len(items) > 10:
                    text += f"  ... et {len(items) - 10} autres lignes\n"

    text += "\n"
    return text




@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def generate_response(request):
    if request.method == 'GET':
        conversations = ChatMessage.objects.filter(user=request.user)
        obj_serializers = ChatMessageSerializer(conversations, many=True)
        return Response({"conversations": obj_serializers.data}, status=status.HTTP_200_OK)

    if request.method == 'POST':
        user = request.user
        user_input = request.data.get('user_input')
        message_history_id = request.data.get('message_history')
        project_id = request.data.get('project_id')
        filtered_data = request.data.get('filtered_data')  # NOUVEAU: Données filtrées

        if not user_input or not user_input.strip():
            return Response(
                {"error": "Le message ne peut pas être vide"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not project_id:
            return Response(
                {"error": "project_id est requis"},
                status=status.HTTP_400_BAD_REQUEST
            )
        

        print(f"\n[DEBUG] Message reçu: {user_input}")
        print(f"[DEBUG] Filtered Data présente: {filtered_data is not None}")
        if filtered_data:
            print(f"[DEBUG] Content of Filtered Data: {json.dumps(filtered_data, indent=2)}")

       
        accounting_context = ""
        intent_detected = False
        result = None
        
        # 1. ANALYSE DES INTENTS CALCULÉS PRIORITAIRES
        if project_id:
            router = QueryRouter(project_id=project_id)
            detection = IntentDetector.detect(user_input)
            
            if detection:
                # --- MEMOIRE DE CONTEXTE (Dates) ---
                # Si aucune date n'est détectée dans la question actuelle, 
                # on cherche si une période a été définie précédemment dans cette discussion.
                params = detection.get('params', {})
                if not params.get('start_date') or not params.get('end_date'):
                    last_messages = ChatMessage.objects.filter(
                        message_history_id=message_history_id
                    ).order_by('-timestamp')[:5]
                    
                    for msg in last_messages:
                        # On cherche une détection de date dans l'input utilisateur précédent
                        prev_detection = IntentDetector.detect(msg.user_input)
                        if prev_detection and prev_detection.get('params'):
                            p = prev_detection['params']
                            if p.get('start_date') and p.get('end_date'):
                                # Héritage des dates si elles manquent
                                if not params.get('start_date'): params['start_date'] = p['start_date']
                                if not params.get('end_date'): params['end_date'] = p['end_date']
                                if not params.get('annee') and p.get('annee'): params['annee'] = p['annee']
                                print(f"[DEBUG] Context Inherited: {params['start_date']} to {params['end_date']}")
                                break
                
                intent_detected = True
                result = router._use_calculated_methods(detection['types'], params)
                
                if result.get("source") == "calculated":
                    all_intents = result.get("intents", [result.get("intent", "inconnu")])
                    intents_str = ", ".join(all_intents)
                    accounting_context = f"=== DONNÉES CALCULÉES ({intents_str}) ===\n"
                    context_data = result.get("data", {})
                    accounting_context += json.dumps(context_data, ensure_ascii=False, indent=2)
                    
                    # Instruction d'analyse pour l'IA
                    accounting_context += "\n\nINSTRUCTION ANALYSE : Priorise STRICTEMENT les chiffres du bloc 'DONNÉES CALCULÉES' ci-dessus. Si l'utilisateur demande plusieurs indicateurs (ex: EBE, BFR, CAF), utilise les données correspondantes dans le JSON. Ne mélange pas ces données avec d'autres sources."
                    
                    # GESTION DES EXPORTS (REKAPY Modern Export)
                    export_keywords = ["générer", "export", "rapport", "états financiers", "excel", "pdf", "télécharger"]
                    if any(kw in user_input.lower() for kw in export_keywords):
                        try:
                            report_type = "Bilan" if any(k in user_input.lower() for k in ["bilan", "états"]) else "Rapport Financier"
                            if "compar" in user_input.lower():
                                report_type = "Rapport Comparatif"
                            
                            want_pdf = "pdf" in user_input.lower()
                            want_excel = "excel" in user_input.lower()
                            # Si aucun n'est spécifié, on propose les deux ou on assume les deux pour un "rapport"
                            if not want_pdf and not want_excel:
                                want_pdf = want_excel = True
                            
                            backend_url = getattr(settings, "BACKEND_URL", request.build_absolute_uri('/')[:-1])
                            export_links = []

                            if want_excel:
                                buffer_excel = ExportService.generate_excel_report(result["data"], report_type=report_type)
                                filename_excel = f"exports/{report_type.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                                file_path_excel = default_storage.save(filename_excel, ContentFile(buffer_excel.getvalue()))
                                full_url_excel = f"{backend_url}{settings.MEDIA_URL}{file_path_excel}"
                                export_links.append(f"📊 [Télécharger le Rapport Excel]({full_url_excel})")

                            if want_pdf:
                                buffer_pdf = ExportService.generate_pdf_report(result["data"], report_type=report_type)
                                filename_pdf = f"exports/{report_type.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                                file_path_pdf = default_storage.save(filename_pdf, ContentFile(buffer_pdf.getvalue()))
                                full_url_pdf = f"{backend_url}{settings.MEDIA_URL}{file_path_pdf}"
                                export_links.append(f"📄 [Télécharger le Rapport PDF]({full_url_pdf})")
                            
                            if export_links:
                                accounting_context += "\n\n### 📥 EXPORTS DISPONIBLES (REKAPY)\n" + "\n".join(export_links)

                        except Exception as e:
                            print(f"[ERROR] Export failed: {str(e)}")
                            accounting_context += f"\n\n(Note: L'export a échoué: {str(e)})"

        # 2. FALLBACK SUR LES DONNÉES FILTRÉES (Dashboard)
        if not intent_detected:
            if filtered_data and is_followup_empty_question(user_input):
                has_more = False
                for key in ['chiffre_affaires', 'charges', 'resultat_net', 'tresorerie', 'bilan']:
                    if key in filtered_data:
                        details = filtered_data[key].get('details')
                        if (isinstance(details, list) and len(details) > 10) or \
                           (isinstance(details, dict) and any(len(v) > 10 for v in details.values())):
                            has_more = True
                if not has_more:
                    ai_response = "Oui, ce sont toutes les informations disponibles pour cette période."
                    request.data["ai_response"] = ai_response
                    serializer = ChatMessageSerializer(data=request.data)
                    if serializer.is_valid():
                        try:
                            message_history = MessageHistory.objects.get(id=message_history_id)
                            serializer.save(user=user, message_history=message_history)
                            return Response({"conversation": serializer.data, "sources": []}, status=status.HTTP_201_CREATED)
                        except MessageHistory.DoesNotExist:
                            print(f"[ERROR] MessageHistory {message_history_id} not found in fallback save")
                            return Response({"error": f"Historique {message_history_id} introuvable"}, status=status.HTTP_404_NOT_FOUND)

            elif filtered_data:
                # Éviter le résumé trop proactif sur un simple "Bonjour"
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
                
                # Permettre l'export même depuis le dashboard si demandé
                if any(kw in user_input.lower() for kw in ["générer", "export", "rapport", "états financiers", "excel", "pdf"]):
                    accounting_context += "\n\n(Note: Pour générer un rapport professionnel complet, veuillez préciser l'année ou le format, par exemple 'États financiers 2025 en PDF'.)"

            # 3. DERNIER RECOURS : TEXT-TO-SQL
            elif project_id:
                result = router.route(user_input)
                if result["source"] == "text_to_sql":
                    nb = result.get("nb_resultats", 0)
                    accounting_context = f"=== DONNÉES BASE DE DONNÉES ({nb} résultats) ===\n"
                    accounting_context += f"Requête exécutée: {result['sql']}\n\n"
                    accounting_context += json.dumps(result["data"][:100], ensure_ascii=False, indent=2)
                elif result["source"] == "error":
                    accounting_context = f"Erreur: {result['error']}"

            if result:
                print(f"[DEBUG] Router source: {result['source']}, intent: {result.get('intent')}")
                # Récupérer le filtre suggéré si disponible (uniquement si détecté explicitement)
                query_info = IntentDetector.detect(user_input)
                if query_info and query_info.get('suggested_filter'):
                    request.data["suggested_filter"] = query_info['suggested_filter']
        
        # ✅ RECHERCHE VECTORIELLE (Documents)
        query_embedding = np.array(generate_embedding(user_input))
        results = search_similar_pages(
            query_embedding=query_embedding,
            project_id=project_id
        )
        contents = [page["content"] for page in results]
        context_text = "\n\n".join([res for res in contents])
        
        # ✅ CONSTRUCTION DU CONTEXTE COMPLET
        full_context = ""
        current_system_prompt = SYSTEM_PROMPT
        
        if accounting_context:
            full_context += "=== DONNÉES FINANCIÈRES DU TABLEAU DE BORD ===\n"
            full_context += accounting_context
            full_context += "\n\n"
            
            # Informer explicitement l'IA qu'elle a accès à ces données
            if filtered_data:
                dates = filtered_data.get('filter', {})
                current_system_prompt += f"\n\nNOTE IMPORTANTE : Tu as actuellement accès aux données réelles du tableau de bord pour la période du {dates.get('date_start')} au {dates.get('date_end')}. Analyse ces données pour répondre à l'utilisateur."
            
            # Instruction sur les valeurs à 0
            current_system_prompt += "\nSi les données sont à 0.00 AR, cela signifie qu'aucune écriture comptable n'a été trouvée pour ce compte sur la période. Interprète cela comme une absence d'activité importée plutôt que comme une erreur."
        
        if context_text:
            full_context += "=== DOCUMENTS DE RÉFÉRENCE ===\n"
            full_context += context_text
        
        # ✅ DÉBOGAGE DU PROMPT ENVOYÉ
        print(f"[DEBUG] Full Context Length: {len(full_context)} chars")
        if full_context:
            print(f"[DEBUG] Context Preview: {full_context[:200]}...")

        # ✅ APPEL À L'API OPENAI
        historique = []
        if message_history_id:
            messages_precedents = ChatMessage.objects.filter(
                message_history_id=message_history_id
            ).order_by('timestamp')[:10] 
            
            for msg in messages_precedents:
                historique.append({"role": "user", "content": msg.user_input})
                historique.append({"role": "assistant", "content": msg.ai_response})

        # 2. Construire la liste complète des messages
        messages_to_send = [
            {"role": "system", "content": current_system_prompt},
        ] + historique + [
            {"role": "user", "content": f"Contexte:\n{full_context}\n\nQuestion: {user_input}\n\nRéponds de manière claire et concise."}
        ]

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages_to_send,
            temperature=0.2
        )

        # ✅ FORMATAGE DE LA RÉPONSE
        unique_sources = [
            {"title": res["document_path"], "path": res["document_path"]}
            for res in results
        ]

        ai_response = response.choices[0].message.content
        if unique_sources:
            ai_response += "\n\n**Source(s) consultée(s) :**\n"
            for src in unique_sources:
                ai_response += f"- {src['path']}\n"
        
        request.data["ai_response"] = ai_response

        # ✅ ENREGISTREMENT DU MESSAGE
        serializer = ChatMessageSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # ✅ SÉCURITÉ : Vérifier que l'historique appartient à l'utilisateur ET au projet
                message_history = MessageHistory.objects.get(
                    id=message_history_id, 
                    user=user,
                    project_id=project_id
                )
                
                # ✅ AUTO-TITRAGE : Si le titre est générique, on le remplace par le début du message
                generic_titles = ["Nouvelle discussion", "New Chat History", "", "None"]
                if not message_history.title or message_history.title in generic_titles:
                    clean_input = user_input.strip()
                    if clean_input:
                        new_title = clean_input[:40] + ("..." if len(clean_input) > 40 else "")
                        message_history.title = new_title
                        message_history.save()
                
                serializer.save(user=user, message_history=message_history)
            except MessageHistory.DoesNotExist:
                print(f"[ERROR] MessageHistory {message_history_id} not found for user {user.id} and project {project_id}")
                return Response({"error": f"Historique {message_history_id} introuvable ou accès refusé"}, status=status.HTTP_404_NOT_FOUND)

            return Response(
                {
                    "conversation": serializer.data,
                    "sources": unique_sources,
                    "suggested_filter": request.data.get("suggested_filter")
                },
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# GET MESSAGE HISTORIES -----------------------------
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
@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def message_history_details(request, id):
    user = request.user
    project_id = request.data.get('project_id') or request.query_params.get('project_id') or getattr(request, 'project_id', None)
    
    try:
        # ✅ SÉCURITÉ : D'abord on vérifie l'existence et l'appartenance à l'utilisateur
        history = MessageHistory.objects.get(id=id, user=user)
        
        # ✅ RESILIENCE : Si l'historique est "orphelin" (legacy), on lui attribue le projet actuel
        if history.project_id is None and project_id:
            print(f"[DEBUG] Auto-assigning project {project_id} to legacy history {id}")
            history.project_id = project_id
            history.save()
        
        # ✅ SÉCURITÉ MULTI-TENANT : Si un projet est spécifié, on vérifie que c'est le bon (sauf si c'était un orphelin qu'on vient de réparer)
        elif project_id and str(history.project_id) != str(project_id):
             print(f"[DEBUG] History {id} belongs to project {history.project_id}, but request specified {project_id}")
             return Response(
                {"error": f"Accès refusé à cet historique pour le projet actuel"}, 
                status=status.HTTP_403_FORBIDDEN
            )
            
    except MessageHistory.DoesNotExist:
        print(f"[DEBUG] History {id} NOT FOUND for user {user.id}")
        return Response(
            {"error": f"Historique {id} introuvable"}, 
            status=status.HTTP_404_NOT_FOUND
        )
    
    # HISTORY DETAILS
    if request.method == "GET":
        history = MessageHistory.objects.prefetch_related('chat_messages').get(id=id, user=request.user)
        obj_serializer = MessageHistorySerializer(history)

        # pour déboguer
        print("=" * 50)
        print(f"GET /api/histories/{id}/ Response:")
        print(f"Data: {obj_serializer.data}")
        print("=" * 50)

        return Response(obj_serializer.data, status=status.HTTP_200_OK)

    # UPDATE A HISTORY 
    elif request.method == "PUT":
        history = get_object_or_404(MessageHistory, id=id, user=request.user)
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
        history = get_object_or_404(MessageHistory, id=id, user=request.user)
        history.delete()
        return Response({"message": "Historique supprimé avec succès"}, status=status.HTTP_204_NO_CONTENT)
    
# SAVE NEW HISTORY AND NEW CHAT -----------------------------
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