import numpy as np
# DJANGO -------------------------------------------
from django.shortcuts import get_object_or_404
from django.conf import settings

# CONFIG -------------------------------------------
#from decouple import config as env

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

# OPENAI -------------------------------------------
from openai import OpenAI

import os

import re
import json
from datetime import datetime, date
from chatbot.services.accounting_queries import AccountingQueryService

# OPENAI -------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

greetings = ["bonjour", "bonsoir", "salut", "coucou", "allô", "bon après-midi", "hey", "yo", "coucou toi", "enchanté(e)", "hello", "hi", "salam", "hola", "ciao"]

# SEARCH VECTOR SIMILARY ------------------------------------------------
def search_similar_pages(query_embedding, top_k=5, threshold=0.9):
    results = (
        DocumentPage.objects
        .select_related("document")
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

#DETECTION DES QUESTIONS COMPTABLES
def detect_accounting_query(user_input):
    """
    Détecte le type de question comptable et extrait les paramètres
    Retourne: {'type': str, 'params': dict} ou None
    """
    user_input_lower = user_input.lower()
    
    # Patterns de détection
    patterns = {
        'ca': r'chiffre.*affaires?|ca\b|ventes?|revenus?',
        'charges': r'charges?|dépenses?|coûts?|frais',
        'resultat': r'résultat|bénéfice|profit|perte',
        'tresorerie': r'trésorerie|liquidité|banque|caisse',
        'bilan': r'bilan|actif|passif',
        'comparaison': r'compar|différence|évolution|versus|vs'
    }
    
    # Extraction d'années
    annees = re.findall(r'\b(20\d{2})\b', user_input)
    
    # Détection du type
    query_type = None
    for key, pattern in patterns.items():
        if re.search(pattern, user_input_lower):
            query_type = key
            break
    
    if not query_type:
        return None
    
    # Extraction des paramètres
    params = {}
    
    if annees:
        if len(annees) >= 2 and query_type == 'comparaison':
            params['annee1'] = int(annees[0])
            params['annee2'] = int(annees[1])
        elif len(annees) == 1:
            params['annee'] = int(annees[0])
        else:
            params['annee'] = datetime.now().year
    else:
        # Par défaut : année en cours
        params['annee'] = datetime.now().year
    
    return {
        'type': query_type,
        'params': params
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
    
    context_parts = []
    
    try:
        if query_type == 'ca':
            data = service.get_chiffre_affaires(**params)
            context_parts.append(f"**Chiffre d'affaires** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
        
        elif query_type == 'charges':
            data = service.get_charges(**params)
            context_parts.append(f"**Charges** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
        
        elif query_type == 'resultat':
            data = service.get_resultat_net(**params)
            context_parts.append(f"**Résultat net** ({data['periode']}):")
            context_parts.append(f"- Résultat: {data['montant']:,.2f} AR")
            context_parts.append(f"- Produits: {data['produits']:,.2f} AR")
            context_parts.append(f"- Charges: {data['charges']:,.2f} AR")
        
        elif query_type == 'tresorerie':
            data = service.get_tresorerie(date_fin=date.today())
            context_parts.append(f"**Trésorerie** (au {data['date']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
        
        elif query_type == 'bilan':
            data = service.get_bilan_summary()
            context_parts.append(f"**Bilan** ({data['date']}):")
            context_parts.append(f"- Actif total: {data['actif']:,.2f} AR")
            context_parts.append(f"- Passif total: {data['passif']:,.2f} AR")
            context_parts.append(f"- Équilibre: {data['equilibre']:,.2f} AR")
        
        elif query_type == 'comparaison':
            if 'annee1' in params and 'annee2' in params:
                data = service.compare_periodes(params['annee1'], params['annee2'])
                context_parts.append(f"**Comparaison {params['annee1']} vs {params['annee2']}:**")
                context_parts.append(f"\n**Année {params['annee1']}:**")
                context_parts.append(f"- CA: {data['annee_1']['chiffre_affaires']:,.2f} AR")
                context_parts.append(f"- Charges: {data['annee_1']['charges']:,.2f} AR")
                context_parts.append(f"- Résultat: {data['annee_1']['resultat']:,.2f} AR")
                context_parts.append(f"\n**Année {params['annee2']}:**")
                context_parts.append(f"- CA: {data['annee_2']['chiffre_affaires']:,.2f} AR")
                context_parts.append(f"- Charges: {data['annee_2']['charges']:,.2f} AR")
                context_parts.append(f"- Résultat: {data['annee_2']['resultat']:,.2f} AR")
                context_parts.append(f"\n**Évolution:**")
                context_parts.append(f"- CA: {data['evolution']['ca']:+,.2f} AR")
                context_parts.append(f"- Charges: {data['evolution']['charges']:+,.2f} AR")
                context_parts.append(f"- Résultat: {data['evolution']['resultat']:+,.2f} AR")
    
    except Exception as e:
        context_parts.append(f"Erreur lors de la récupération des données: {str(e)}")
    
    return "\n".join(context_parts)


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
        project_id = request.data.get('project_id')  # ✅ Récupérer le project_id

        if not user_input or not user_input.strip():
            return Response(
                {"error": "Le message ne peut pas être vide"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ DÉTECTION DE QUESTION COMPTABLE
        query_info = detect_accounting_query(user_input)
        
        # ✅ RÉCUPÉRATION DES DONNÉES COMPTABLES
        accounting_context = ""
        if query_info and project_id:
            accounting_context = get_accounting_context(user, project_id, query_info)
            print("=" * 50)
            print("Question comptable détectée:")
            print(f"Type: {query_info['type']}")
            print(f"Params: {query_info['params']}")
            print(f"Contexte comptable:\n{accounting_context}")
            print("=" * 50)
        
        # ✅ RECHERCHE VECTORIELLE (Documents)
        query_embedding = np.array(generate_embedding(user_input))
        results = search_similar_pages(query_embedding=query_embedding)
        contents = [page["content"] for page in results]
        context_text = "\n\n".join([res for res in contents])
        
        # ✅ CONSTRUCTION DU CONTEXTE COMPLET
        full_context = ""
        if accounting_context:
            full_context += "=== DONNÉES COMPTABLES ===\n"
            full_context += accounting_context
            full_context += "\n\n"
        
        if context_text:
            full_context += "=== DOCUMENTS DE RÉFÉRENCE ===\n"
            full_context += context_text
        
        # ✅ APPEL À L'API OPENAI
        response = client.chat.completions.create(
            model=env('OPENAI_MODEL'),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Contexte:\n{full_context}\n\nQuestion: {user_input}\n\nRéponds de manière claire et concise."}
            ],
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
            message_history = get_object_or_404(MessageHistory, id=message_history_id)
            serializer.save(user=user, message_history=message_history)

            return Response(
                {
                    "conversation": serializer.data,
                    "sources": unique_sources,
                },
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# GET MESSAGE HISTORIES -----------------------------
@api_view(["POST", "GET"])
@permission_classes([IsAuthenticated])
def get_message_histories(request):
    # GET ALL HISTORIES
    if request.method == "GET":
        histories = MessageHistory.objects.filter(user=request.user)
        obj_serializers = MessageHistorySerializer(histories, many=True)

        context = {"histories":obj_serializers.data, }
        return Response(context, status=status.HTTP_200_OK)

    # SAVE A HISTORY
    if request.method == "POST":
        obj_serializer = MessageHistorySerializer(data=request.data)
        if obj_serializer.is_valid():
            history_saved = obj_serializer.save(user=request.user) 
            context = {
                "history": MessageHistorySerializer(history_saved).data,  
                "error": False,
                "message": "History was saved successfully."
            }
            return Response(context, status=status.HTTP_201_CREATED)
        else:
            # Simplifie les erreurs
            errors = obj_serializer.errors
            first_error = next(iter(errors.values()))[0] if errors else "Invalid data"
            context = {"error": True, "message": first_error}
            return Response(context, status=status.HTTP_400_BAD_REQUEST)
        
    
# MESSAGE HISTORY DETAILS -----------------------------
@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def message_history_details(request, id):
    
    history = get_object_or_404(MessageHistory, id=id, user=request.user)
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

         # 1️- Vérification de l’abonnement actif
        #active_subscription = Subscription.objects.filter(user=request.user.id, is_active=True).order_by('-end_date').first()
        #if not active_subscription:
        #    return Response(
        #        {"error": "Aucun abonnement actif trouvé. Veuillez choisir un plan pour continuer."},
        #        status=status.HTTP_403_FORBIDDEN
        #    )

        #if active_subscription.plan.name == "trial" and active_subscription.has_reached_limit:
        #    return Response(
        #        {"error": "Votre période d'essai est terminée. Veuillez passer à un plan supérieur pour continuer."},
        #        status=status.HTTP_403_FORBIDDEN
        #    )

        history_data = {
            "title": request.data.get("title", "New Chat History"),
            "user": request.user.id
        }
        new_chat_data = {
            #"user_input": request.data.get("user_input"),
            "user_input": user_input,
            "user": request.user.id
        }
        history_serializer = MessageHistorySerializer(data=history_data)

        #context = {}
        if history_serializer.is_valid():
            # Save new history
            history_serializer.save(user=request.user) 
            new_chat_data["message_history"] = history_serializer.instance

            # User prompt to be vectorized
            query_embedding = np.array(generate_embedding(user_input))

            # Vector request
            results = search_similar_pages(query_embedding=query_embedding)
            contents = [page["content"] for page in results]
            context_text = "\n\n".join([res for res in contents])
            
            # -------------------------------
            response = client.chat.completions.create(
                model=env('OPENAI_MODEL'),
                messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Contexte:\n{context_text}\n\nQuestion: {user_input}\n\nRéponds de manière claire et concise."}
                    ],
                temperature=0.2
            )

            unique_sources = [
                {"title":res["document_path"], "path":res["document_path"]} 
                for res in results
            ]
        
            ai_response = response.choices[0].message.content
            if unique_sources:
                ai_response += "\n\n**Source(s) consultée(s) :**\n"
                for src in unique_sources:
                    # title = src["title"]
                    path = src["path"]
                    ai_response += f"- {path}\n"
            
            new_chat_data["ai_response"] = ai_response
            new_chat_serializer = ChatMessageSerializer(data=new_chat_data)
            
            if new_chat_serializer.is_valid():
                # save new chat message
                new_chat_serializer.save(
                    user=request.user, 
                    message_history=history_serializer.instance
                )


                return Response(
                    {
                        "conversation": history_serializer.data,
                        "sources": unique_sources,
                    },
                    status=status.HTTP_201_CREATED
                )
                #context = {
                #    "conversation": history_serializer.data, 
                #    "sources": unique_sources, 
                #}

                #active_subscription.chat_count += 1
                #active_subscription.save()
                
        return Response({"error": "Impossible de créer le chat"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def rename_history(request, id):
    history = get_object_or_404(MessageHistory, id=id, user=request.user)
    
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