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

#DETECTION DES QUESTIONS FINANCIÈRES
def detect_financial_query(user_input):
    """
    Détecte le type de question financière et extrait les paramètres
    Retourne: {'type': str, 'params': dict} ou None
    """
    user_input_lower = user_input.lower()
    
    # Patterns de détection
    patterns = {
        'ca': r'chiffre.*affaires?|ca\b|ventes?|revenus?',
        'charges': r'charges?|dépenses?|coûts?|frais',
        'ebe': r'ebe\b|excédent brut d\'exploitation',
        'roe': r'roe\b|rentabilité des capitaux propres',
        'marge_brute': r'marge brute|marge commerciale',
        'bfr': r'bfr\b|besoin en fonds de roulement',
        'roa': r'roa\b|rentabilité des actifs',
        'leverage': r'leverage\b|levier Financier|endettement',
        'marge_nette': r'marge nette',
        'marge_operationnelle': r'marge opérationnelle',
        'current_ratio': r'current ratio|ratio de liquidité',
        'rotation_stocks': r'rotation des stocks|rotation stock',
        'resultat': r'résultat|bénéfice|profit|perte',
        'tresorerie': r'trésorerie|liquidité|banque|caisse',
        'bilan': r'bilan|actif|passif',
        'comparaison': r'compar|différence|évolution|versus|vs',
        'analyse_globale': r'analyser|interpréter|audit|santé|vue|résumé|situation|dashboard|tableau|rapport|exercice|période'
    }
    
    # 1. Extraction de dates précises (DD/MM/YYYY ou DD-MM-YYYY ou DD.MM.YYYY)
    date_matches = re.findall(r'(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})', user_input)
    
    # 2. Extraction d'années (20XX)
    annees = re.findall(r'\b(20\d{2})\b', user_input)
    
    # Normalisation des dates (remplacer les séparateurs par /)
    date_matches = [d.replace('-', '/').replace('.', '/') for d in date_matches]
    
    # Détection du type
    query_type = None
    for key, pattern in patterns.items():
        if re.search(pattern, user_input_lower):
            query_type = key
            break
    
    if not query_type:
        # Si on a des dates mais pas de type, on assume une analyse globale
        if date_matches or annees:
            query_type = 'analyse_globale'
        else:
            return None
    
    # Extraction des paramètres
    params = {}
    
    # Priorité aux dates précises
    if len(date_matches) >= 2:
        try:
            params['start_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
            params['end_date'] = datetime.strptime(date_matches[1], '%d/%m/%Y').date()
        except ValueError:
            pass
    elif len(date_matches) == 1:
        try:
            # Si une seule date, on considère que c'est la date de fin
            params['end_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
        except ValueError:
            pass
            
    # Sinon on regarde les années
    if not params.get('start_date') and not params.get('end_date'):
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
    
    print(f"[DEBUG] Query Type détecté: {query_type}")
    print(f"[DEBUG] Paramètres extraits: {params}")
    
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

        elif query_type == 'ebe':
            data = service.get_ebe(**params)
            context_parts.append(f"**EBE (Excédent Brut d'Exploitation)** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Produits d'exploitation: {data['produits_exploitation']:,.2f} AR")
            context_parts.append(f"- Charges d'exploitation: {data['charges_exploitation']:,.2f} AR")
            
        elif query_type == 'roe':
            data = service.get_roe(**params)
            context_parts.append(f"**ROE (Rentabilité des capitaux propres)** ({data['periode']}):")
            context_parts.append(f"- Taux: {data['valeur']:.2f}%")
            context_parts.append(f"- Résultat net: {data['resultat_net']:,.2f} AR")
            context_parts.append(f"- Capitaux propres: {data['capitaux_propres']:,.2f} AR")

        elif query_type == 'marge_brute':
            data = service.get_marge_brute(**params)
            context_parts.append(f"**Marge Brute** ({data['periode']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Taux de marge: {data['taux']:.2f}%")
            context_parts.append(f"- Ventes: {data['ventes']:,.2f} AR")
            context_parts.append(f"- Achats: {data['achats']:,.2f} AR")

        elif query_type == 'bfr':
            data = service.get_bfr(date_ref=params.get('end_date'), annee=params.get('annee'))
            context_parts.append(f"**BFR (Besoin en Fonds de Roulement)** (au {data['date']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Stocks: {data['stocks']:,.2f} AR")
            context_parts.append(f"- Créances clients: {data['creances_clients']:,.2f} AR")
            context_parts.append(f"- Dettes fournisseurs: {data['dettes_fournisseurs']:,.2f} AR")

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
            data = service.get_resultat_net(**params)
            context_parts.append(f"**Résultat net** ({data['periode']}):")
            context_parts.append(f"- Résultat: {data['montant']:,.2f} AR")
            context_parts.append(f"- Produits: {data['produits']:,.2f} AR")
            context_parts.append(f"- Charges: {data['charges']:,.2f} AR")
        
        elif query_type == 'tresorerie':
            data = service.get_tresorerie(annee=params.get('annee'))
            context_parts.append(f"**Trésorerie** (au {data['date']}):")
            context_parts.append(f"- Montant: {data['montant']:,.2f} AR")
            context_parts.append(f"- Comptes: {data['comptes']}")
        
        elif query_type == 'bilan':
            data = service.get_bilan_summary(annee=params.get('annee'))
            context_parts.append(f"**Bilan** ({data['date']}):")
            context_parts.append(f"- Actif total: {data['actif']:,.2f} AR")
            context_parts.append(f"- Passif total: {data['passif']:,.2f} AR")
            context_parts.append(f"- Équilibre: {data['equilibre']:,.2f} AR")

        elif query_type == 'analyse_globale':
            annee = params.get('annee')
            ca = service.get_chiffre_affaires(**params)
            mb = service.get_marge_brute(**params)
            ebe = service.get_ebe(**params)
            marges = service.get_marges_profitabilite(**params)
            roa_data = service.get_roa(**params)
            bfr = service.get_bfr(date_ref=params.get('end_date'), annee=annee)
            ratios = service.get_ratios_structure(date_ref=params.get('end_date'), annee=annee)
            bilan = service.get_bilan_summary(annee=annee, date_bilan=params.get('end_date'))
            
            periode = ca.get('periode', str(annee) if annee else "Toute la période")
            
            context_parts.append(f"=== ANALYSE GLOBALE ({periode}) ===")
            
            if "error" not in ca:
                context_parts.append(f"**Performance & Rentabilité :**")
                context_parts.append(f"- Chiffre d'Affaires: {ca.get('montant', 0):,.2f} AR")
                context_parts.append(f"- Marge Brute: {mb.get('montant', 0):,.2f} AR ({mb.get('taux', 0):.2f}%)")
                context_parts.append(f"- EBE: {ebe.get('montant', 0):,.2f} AR")
                context_parts.append(f"- Résultat Net: {marges.get('resultat_net', 0):,.2f} AR ({marges.get('marge_nette', 0):.2f}%)")
                context_parts.append(f"- Marge Opérationnelle: {marges.get('marge_operationnelle', 0):.2f}%")
                context_parts.append(f"- ROA (Rentabilité Actifs): {roa_data.get('valeur', 0):.2f}%")
            
            context_parts.append(f"\n**Gestion & Structure (au {bilan.get('date')}) :**")
            context_parts.append(f"- BFR: {bfr.get('montant', 0):,.2f} AR")
            context_parts.append(f"- Leverage: {ratios.get('leverage', 0):.2f}")
            context_parts.append(f"- Current Ratio (Liquidité): {ratios.get('current_ratio', 0):.2f}")
            context_parts.append(f"- Bilan: Actif {bilan.get('actif', 0):,.2f} AR / Passif {bilan.get('passif', 0):,.2f} AR")
        
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

        # ✅ DÉTECTION DE QUESTION FINANCIÈRE
        query_info = detect_financial_query(user_input)
        
        # ✅ CONSTRUCTION DU CONTEXTE FINANCIER
        accounting_context = ""
        
        # NOUVEAU: Utiliser les données filtrées si disponibles
        if filtered_data:
            print("=" * 50)
            print("[INFO] UTILISATION DES DONNÉES FILTRÉES")
            print(f"Période: {filtered_data.get('filter', {}).get('date_start')} → {filtered_data.get('filter', {}).get('date_end')}")
            print("=" * 50)
            
            accounting_context = "=== DONNÉES COMPTABLES FILTRÉES ===\n"
            filter_info = filtered_data.get('filter', {})
            accounting_context += f"Période analysée: {filter_info.get('date_start')} au {filter_info.get('date_end')}\n\n"
            
            # Formater les données pour le chatbot
            if 'chiffre_affaires' in filtered_data:
                ca = filtered_data['chiffre_affaires']
                accounting_context += f"**Chiffre d'affaires** ({ca.get('periode', '')}):\n"
                accounting_context += f"- Montant: {ca.get('montant', 0):,.2f} AR\n"
                accounting_context += f"- Comptes: {ca.get('comptes', '')}\n\n"
            
            if 'charges' in filtered_data:
                charges = filtered_data['charges']
                accounting_context += f"**Charges** ({charges.get('periode', '')}):\n"
                accounting_context += f"- Montant: {charges.get('montant', 0):,.2f} AR\n"
                accounting_context += f"- Comptes: {charges.get('comptes', '')}\n\n"
            
            if 'resultat_net' in filtered_data:
                resultat = filtered_data['resultat_net']
                accounting_context += f"**Résultat net** ({resultat.get('periode', '')}):\n"
                accounting_context += f"- Résultat: {resultat.get('montant', 0):,.2f} AR\n"
                accounting_context += f"- Produits: {resultat.get('produits', 0):,.2f} AR\n"
                accounting_context += f"- Charges: {resultat.get('charges', 0):,.2f} AR\n\n"
            
            if 'tresorerie' in filtered_data:
                treso = filtered_data['tresorerie']
                accounting_context += f"**Trésorerie** (au {treso.get('date', '')}):\n"
                accounting_context += f"- Montant: {treso.get('montant', 0):,.2f} AR\n"
                accounting_context += f"- Comptes: {treso.get('comptes', '')}\n\n"
            
            if 'bilan' in filtered_data:
                bilan = filtered_data['bilan']
                accounting_context += f"**Bilan** ({bilan.get('date', '')}):\n"
                accounting_context += f"- Actif total: {bilan.get('actif', 0):,.2f} AR\n"
                accounting_context += f"- Passif total: {bilan.get('passif', 0):,.2f} AR\n"
                accounting_context += f"- Équilibre: {bilan.get('equilibre', 0):,.2f} AR\n\n"
        
        elif query_info and project_id:
            # Fallback: utiliser l'ancien système si pas de données filtrées
            accounting_context = get_accounting_context(user, project_id, query_info)
            print("=" * 50)
            print("Question comptable détectée (mode classique):")
            print(f"Type: {query_info['type']}")
            print(f"Params: {query_info['params']}")
            print(f"Contexte comptable:\n{accounting_context}")
            print("=" * 50)
        
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
        response = client.chat.completions.create(
            model=OPENAI_MODEL,  # Utiliser la variable définie en haut
            messages=[
                {"role": "system", "content": current_system_prompt},
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
                model=os.getenv('OPENAI_MODEL'),
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