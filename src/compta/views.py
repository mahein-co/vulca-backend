import json
from openai import OpenAI
from decimal import Decimal
from datetime import datetime, date

from django.core.exceptions import ValidationError
from django.db.models import Sum, Max, Min, DecimalField, Case, When, Q

from rest_framework import generics, status, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from vulca_backend import settings
from ocr.utils import clean_ai_json, generate_description
from ocr.models import FileSource, FormSource
# from chatbot.models import ChatMessage, MessageHistory, RAGContent
from compta.models import Journal, GrandLivre, Bilan, CompteResultat, Balance, Project, ProjectAccess
from compta.serializers import (
    JournalSerializer, BilanSerializer, BalanceSerializer, CompteResultatSerializer,
    ChiffreAffaireSerializer, EbeSerializer, ResultatNetSerializer, BfrSerializer,
    CafSerializer, LeverageSerializer, TresorerieSerializer, AnnuiteCafSerializer, MargeNetteSerializer,
    DetteLmtCafSerializer, ChargeEbeSerializer, ChargeCaSerializer, MargeEndettementSerializer,
    CurrentRatioSerializer, QuickRatioSerializer, GearingSerializer, RotationStockSerializer,
    MargeOperationnelleSerializer, MargeBruteSerializer, DelaisClientsSerializer, DelaisFournisseursSerializer,
    ProjectSerializer, ProjectAccessSerializer, ProjectListSerializer,
    MonthlyEvolutionDataSerializer, EvolutionResponseSerializer, TopCompteSerializer,
    BilanKpiGroupSerializer, BilanKpiResponseSerializer, JournalRepartitionSerializer,
    JournalDateRangeSerializer, DashboardIndicatorsResponseSerializer, RoeRoaSerializer, TVASerializer,
    EmptySerializer, MessageResponseSerializer
)
from compta.kpi_utils import (
    get_latest_bilan_sum, get_cr_sum, get_resultat_net, 
    get_capitaux_propres, get_chiffre_affaire, get_ebe
)
from compta.permissions import HasProjectAccess
from ocr.pcg_loader import PCG_MAPPING, get_pcg_label

from vulca_backend import settings


client = OpenAI(api_key=settings.OPENAI_API_KEY)


@extend_schema(
    parameters=[
        OpenApiParameter("type", type=str, description="Type de journal (ex: ACH, VEN, BQ)"),
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
        OpenApiParameter("search", type=str, description="Terme de recherche"),
        OpenApiParameter("page", type=int, description="Numéro de page"),
    ],
    responses={200: JournalSerializer(many=True)}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def list_journals_view(request):
    journal_type = request.GET.get("type")
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    search_term = request.GET.get("search")

    # PROJECT FILTER (STRICT)
    project_id = getattr(request, 'project_id', None)

    queryset = Journal.objects.filter(project_id=project_id).order_by("-date", "numero_piece")

    if journal_type:
        queryset = queryset.filter(type_journal=journal_type)

    if date_start and date_end:
        queryset = queryset.filter(date__range=[date_start, date_end])
    elif not request.GET.get("all"): # Default behavior if no range provided: restrict to recent? or all? sticking to previous logic of "today" if nothing specified is risky for dashboard. Let's make it allow all if no specific filter, or maybe default to valid range.
        # Actually, if date filters are empty, we might return everything (paginated).
        # The original code filtered by today() if not show_all. 
        # For the dashboard "detail" view, we usually want specific dates.
        pass

    if search_term:
        from django.db.models import Q
        queryset = queryset.filter(
            Q(numero_compte__icontains=search_term) | 
            Q(libelle__icontains=search_term) |
            Q(numero_piece__icontains=search_term)
        )

    totals = queryset.aggregate(
        total_debit=Sum("debit_ar"),
        total_credit=Sum("credit_ar"),
        total_count=Sum(1)
    )

    paginator = PageNumberPagination()
    paginator.page_size = 10 # Check frontend requirement, maybe param
    if request.GET.get("page_size"):
        try:
            paginator.page_size = int(request.GET.get("page_size"))
        except:
            pass
            
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = JournalSerializer(paginated_qs, many=True)

    response = paginator.get_paginated_response(serializer.data)

    response.data["totals"] = {
        "debit": totals["total_debit"] or 0,
        "credit": totals["total_credit"] or 0,
        "count": queryset.count()
    }

    return response


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: JournalRepartitionSerializer(many=True)}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def journal_repartition_view(request):
    """
    Retourne la répartition des montants par type de journal.
    Utilisé pour le Dashboard (widgets et barres de progression).
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    
    # PROJECT FILTER (STRICT)
    project_id = getattr(request, 'project_id', None)
    
    queryset = Journal.objects.filter(project_id=project_id)
    
    if date_start and date_end:
        queryset = queryset.filter(date__range=[date_start, date_end])
        
    from django.db.models import Count, Sum
    
    from decimal import Decimal, InvalidOperation
    
    try:
        # Agrégation par type de journal
        # ⚡ [BUGFIX] : Certains journaux ont le nom complet ('Journal des achats') au lieu du code ('ACHAT')
        repartition_qs = queryset.values('type_journal').annotate(
            total_amount=Sum('debit_ar'),
            count=Count('id')
        )

        # Normalisation des résultats agrégés
        normalized_repartition = {}
        
        DISPLAY_TO_CODE = {
            'Journal des achats': 'ACHAT',
            'Journal des ventes': 'VENTE',
            'Journal de banque': 'BANQUE',
            'Journal de caisse': 'CAISSE',
            'Journal des opérations diverses': 'OD',
            'Journal des à-nouveaux': 'AN'
        }

        for item in repartition_qs:
            raw_type = item['type_journal']
            code = DISPLAY_TO_CODE.get(raw_type, raw_type)
            
            if code not in normalized_repartition:
                normalized_repartition[code] = {'total_amount': Decimal('0.00'), 'count': 0}
            
            # Cast safe to Decimal to avoid TypeError with float
            amount_val = item['total_amount'] or 0
            try:
                amount_dec = Decimal(str(amount_val))
            except (InvalidOperation, TypeError):
                amount_dec = Decimal('0.00')
                
            normalized_repartition[code]['total_amount'] += amount_dec
            normalized_repartition[code]['count'] += item['count']

        # Calcul du total global pour les pourcentages
        total_global = sum(item['total_amount'] for item in normalized_repartition.values())
        if total_global == 0:
            total_global = Decimal('1') 

        # Transformer le dictionnaire normalisé en liste triée
        sorted_repartition = sorted(normalized_repartition.items(), key=lambda x: x[1]['total_amount'], reverse=True)

        data = []
        LABELS = {'ACHAT': 'Achats', 'VENTE': 'Ventes', 'BANQUE': 'Banques', 'CAISSE': 'Caisses', 'OD': 'Opérations diverses', 'AN': 'À Nouveaux'}
        COLORS = {'ACHAT': 'bg-red-800', 'VENTE': 'bg-emerald-900', 'BANQUE': 'bg-blue-900', 'CAISSE': 'bg-amber-800', 'OD': 'bg-gray-600', 'AN': 'bg-purple-800'}

        for journal_code, stats in sorted_repartition:
            amount = stats['total_amount']
            percentage = (amount / total_global) * 100
            
            data.append({
                "code": journal_code,
                "name": LABELS.get(journal_code, journal_code),
                "amount": float(amount),
                "percentage": round(percentage, 1),
                "value": round(percentage, 1),
                "count": stats['count'],
                "color": COLORS.get(journal_code, 'bg-gray-500')
            })
            
        return Response({
            "total_global": float(total_global) if total_global > 1 else 0,
            "journals": data
        })

    except Exception as e:
        # En cas d'erreur imprévue, on retourne l'erreur pour débugger au lieu d'un 500 muet
        return Response({"error": "Internal Server Error", "details": str(e)}, status=500)

# CLASSIFICATION 
def classify_accounting(document_json: dict, pcg_mapping: dict):
    """
    document_json : dict contenant les champs extraits (facture, banque, reçu…)
    pcg_mapping   : dict extrait automatiquement du PDF du Plan Comptable Général 2005
    
    L'IA agit comme expert-comptable et choisit les comptes UNIQUEMENT depuis le PCG.
    """
    
    # ✅ CHARGER LE PCG COMPLET DEPUIS LE PDF
    from ocr.pcg_loader import load_pcg_mapping_from_pdf
    pcg_complet = load_pcg_mapping_from_pdf()
    
    # Convertir le PCG en texte lisible pour l'IA
    pcg_text = "\n".join([f"{code}: {label}" for code, label in sorted(pcg_complet.items())])

    prompt = f"""
    Tu es un expert-comptable malgache certifié, spécialiste du Plan Comptable Général de Madagascar 2005.
    
    PLAN COMPTABLE GÉNÉRAL 2005 (COMPLET) :
    {pcg_text}
    
    DOCUMENT À ANALYSER :
    {json.dumps(document_json, indent=2, ensure_ascii=False)}
    
    MISSION :
    1. Identifier le type de document comptable
    2. Déterminer les écritures comptables selon les règles du PCG Madagascar 2005
    3. Choisir les numéros de compte UNIQUEMENT parmi ceux listés dans le PCG ci-dessus
    4. Générer les écritures en respectant le principe de la partie double (débit = crédit)
    
    DÉTECTION DU TYPE DE DOCUMENT :
    ⚠️ RÈGLE DE PRIORITÉ ABSOLUE ⚠️ : 
    Si le "DOCUMENT À ANALYSER" contient déjà une clé "type_document" avec une valeur (ex: "BANQUE", "VENTE"...), TU DOIS UTILISER CETTE VALEUR ET NE PAS CHERCHER À LA CHANGER.
    
    Sinon, utilise les règles suivantes :
    - Si le document contient "facture" ET un client/nom_client → type_document = "VENTE"
    - Si le document contient "facture" ET un fournisseur/nom_fournisseur → type_document = "ACHAT"
    - Si le document contient "banque", "virement", "relevé bancaire" → type_document = "BANQUE"
    - Si le document contient "caisse", "espèces", "cash" → type_document = "CAISSE"
    - Si le document contient "fiche de paie", "bulletin de salaire", "payslip", "employee_name", "salaire_brut" → type_document = "OD"
    - Si le document contient "opération diverse", "OD" → type_document = "OD"
    - Si le document (hors numéro de pièce) contient explicitement "report à nouveau", "solde initial", "ouverture" → type_document = "AN"
    - Par défaut, si c'est une facture émise par l'entreprise → type_document = "VENTE"
    - Par défaut, si c'est une facture reçue → type_document = "ACHAT"
    
    RÈGLES STRICTES :
    - Tu DOIS toujours spécifier un type_document valide (VENTE, ACHAT, BANQUE, CAISSE, OD, ou AN)
    - Tu DOIS utiliser UNIQUEMENT les numéros de compte présents dans le PCG fourni
    - NE PAS inventer de numéros de compte
    - Respecter les règles comptables malgaches (PCG 2005)
    - Assurer l'équilibre comptable (total débit = total crédit)
    - Pour la TVA : utilise 4456 (TVA déductible) et 4457 (TVA collectée)
    
    EXEMPLES DE RÈGLES COMPTABLES :
    - VENTE : Débit 411 (Clients), Crédit 707 (Ventes), Crédit 4457 (TVA collectée si applicable)
    - ACHAT : Débit 607 (Achats de marchandises), Débit 4456 (TVA déductible si applicable), Crédit 401 (Fournisseurs)
    - BANQUE : 
      * Standard: Utilise 512 (Banques) avec contrepartie appropriée (411 pour encaissement client, 401 pour paiement fournisseur)
      * PAIEMENT SALAIRE : Crédit 512 (Banque), Débit 421 (Personnel - Rémunérations dues). Ne pas utiliser 641 ici (déjà passé en OD).
    - CAISSE : Utilise 531 (Caisse) avec contrepartie appropriée
    - FICHE DE PAIE (OD UNIQUEMENT - JAMAIS BANQUE) : 
      ⚠️ INTERDICTION : N'utilise JAMAIS ces règles si type_document = "BANQUE".
      * Débit 641 (Rémunérations du personnel) = salaire_brut
      * Débit 645 (Charges sociales patronales) = total_cotisation_patronale
      * Crédit 421 (Personnel - Rémunérations dues) = net_a_payer
      * Crédit 431 (Sécurité sociale) = total_cotisation_salariale + total_cotisation_patronale (ADDITIONNE LES DEUX!)
      * Crédit 442 (État - Impôts et taxes) = retenue_source (IRSA)
      
      FORMULE D'ÉQUILIBRE OBLIGATOIRE:
      Total Débit = salaire_brut + total_cotisation_patronale
      Total Crédit = net_a_payer + (total_cotisation_salariale + total_cotisation_patronale) + retenue_source
      
      VERIFICATION: Ces deux totaux DOIVENT être égaux!
    
    
    FORMAT DE SORTIE OBLIGATOIRE (JSON pur, sans markdown) :
    
    EXEMPLE VENTE:
    {{
        "type_document": "VENTE",
        "journal": [
            {{
                "compte": "411",
                "libelle": "Clients",
                "debit": 120000,
                "credit": 0
            }},
            {{
                "compte": "707",
                "libelle": "Ventes de marchandises",
                "debit": 0,
                "credit": 100000
            }},
            {{
                "compte": "4457",
                "libelle": "TVA collectée",
                "debit": 0,
                "credit": 20000
            }}
        ]
    }}
    EXEMPLE BANQUE (Paiement Salaire - type_document="BANQUE"):
    {{
        "type_document": "BANQUE",
        "journal": [
            {{
                "compte": "421",
                "libelle": "Personnel - Rémunérations dues",
                "debit": 100000,
                "credit": 0
            }},
            {{
                "compte": "512",
                "libelle": "Banque",
                "debit": 0,
                "credit": 100000
            }}
        ]
    }}

    EXEMPLE FICHE DE PAIE (OD UNIQUEMENT - JAMAIS BANQUE):
    {{
        "type_document": "OD",
        "journal": [
            {{
                "compte": "641",
                "libelle": "Rémunérations du personnel",
                "debit": 400000,
                "credit": 0
            }},
            {{
                "compte": "645",
                "libelle": "Charges sociales patronales",
                "debit": 52000,
                "credit": 0
            }},
            {{
                "compte": "421",
                "libelle": "Personnel - Rémunérations dues",
                "debit": 0,
                "credit": 393700
            }},
            {{
                "compte": "431",
                "libelle": "Sécurité sociale",
                "debit": 0,
                "credit": 56000
            }},
            {{
                "compte": "442",
                "libelle": "État - Impôts et taxes",
                "debit": 0,
                "credit": 2300
            }}
        ]
    }}
    Calcul 431: 4000 + 52000 = 56000
    Total Débit = 400000 + 52000 = 452000
    Total Crédit = 393700 + 56000 + 2300 = 452000 ✓
    
    EXEMPLE FICHE DE PAIE 2 (salaire_brut=10000000, cotisation_salariale=100000, cotisation_patronale=1300000, retenue_source=1887500, net_a_payer=8012500):
    {{
        "type_document": "OD",
        "journal": [
            {{
                "compte": "641",
                "libelle": "Rémunérations du personnel",
                "debit": 10000000,
                "credit": 0
            }},
            {{
                "compte": "645",
                "libelle": "Charges sociales patronales",
                "debit": 1300000,
                "credit": 0
            }},
            {{
                "compte": "421",
                "libelle": "Personnel - Rémunérations dues",
                "debit": 0,
                "credit": 8012500
            }},
            {{
                "compte": "431",
                "libelle": "Sécurité sociale",
                "debit": 0,
                "credit": 1400000
            }},
            {{
                "compte": "442",
                "libelle": "État - Impôts et taxes",
                "debit": 0,
                "credit": 1887500
            }}
        ]
    }}
    Calcul 431: 100000 + 1300000 = 1400000 (IMPORTANT: additionner salariale ET patronale!)
    Total Débit = 10000000 + 1300000 = 11300000
    Total Crédit = 8012500 + 1400000 + 1887500 = 11300000 ✓
    
    IMPORTANT : 
    - Retourne UNIQUEMENT le JSON, sans texte explicatif ni balises markdown
    - Le champ "type_document" est OBLIGATOIRE et ne doit JAMAIS être vide
    - Utilise les montants exacts du document (montant_ttc, montant_ht, montant_tva)
    - Pour les fiches de paie, VÉRIFIE TOUJOURS que Total Débit = Total Crédit
    - NE GÉNÈRE PAS de ligne d'écriture si le montant est 0 (par exemple, si retenue_source = 0, ne pas créer de ligne pour le compte 442)
    """

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    
    ai_response = response.choices[0].message.content
    
    # ✅ Nettoyage de la réponse AI (enlève les ```json ... ``` si présents)
    cleaned_response = clean_ai_json(ai_response)
    
    result = json.loads(cleaned_response)
    
    ai_response = response.choices[0].message.content
    
    # ✅ Nettoyage de la réponse AI (enlève les ```json ... ``` si présents)
    cleaned_response = clean_ai_json(ai_response)
    
    result = json.loads(cleaned_response)
    
    # ✅ VALIDATION : type_document ne doit jamais être vide
    if not result.get("type_document") or result.get("type_document").strip() == "":
        # Fallback : essayer de détecter depuis le document_json
        if "client" in str(document_json).lower() or "nom_client" in document_json:
            result["type_document"] = "VENTE"
        elif "fournisseur" in str(document_json).lower() or "nom_fournisseur" in document_json:
            result["type_document"] = "ACHAT"
        elif "banque" in str(document_json).lower() or "virement" in str(document_json).lower() or "relevé" in str(document_json).lower():
            result["type_document"] = "BANQUE"
        elif "caisse" in str(document_json).lower():
            result["type_document"] = "CAISSE"
        else:
            result["type_document"] = "OD"  # Par défaut
    
    return result



def generate_journal_from_pcg(document_json):
    """
    Génère automatiquement le journal comptable selon les règles PCG.
    Utilise l'IA expert-comptable qui choisit les comptes depuis le PCG PDF.
    AUCUN numéro de compte n'est codé en dur.
    """
    from dateutil import parser as date_parser
    from datetime import date as dt_date
    
    # ✅ EXTRACTION DES DONNÉES DE BASE
    numero_piece = document_json.get("numero_facture") or document_json.get("numero_piece") or document_json.get("reference")
    
    # Check nested description_json for payslip_number if not found
    if not numero_piece and "description_json" in document_json:
        desc = document_json["description_json"]
        if isinstance(desc, dict):
            numero_piece = desc.get("payslip_number") or desc.get("numero_facture") or desc.get("reference")
            
    if not numero_piece:
        numero_piece = "N/A"
    date_facture_raw = document_json.get("date") or document_json.get("date_facture") or str(dt_date.today())
    
    # ✅ CONVERSION DE DATE : "5 septembre 2024" → date(2024, 9, 5)
    try:
        from datetime import date as dt_date_type, datetime as dt_datetime_type
        if isinstance(date_facture_raw, (dt_date_type, dt_datetime_type)):
            date_facture = date_facture_raw.date() if isinstance(date_facture_raw, dt_datetime_type) else date_facture_raw
        else:
            date_facture_raw = str(date_facture_raw).replace('\xa0', ' ').strip()
            
            # Vérifier si la date est déjà au format ISO (YYYY-MM-DD)
            import re
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_facture_raw):
                from datetime import datetime
                date_facture = datetime.strptime(date_facture_raw, '%Y-%m-%d').date()
            else:
                # Parser la date avec dayfirst=True pour format français
                parsed_date = date_parser.parse(date_facture_raw, dayfirst=True)
                date_facture = parsed_date.date()
    except:
        date_facture = dt_date.today()
    
    # ✅ UTILISATION DE L'IA POUR CLASSIFIER SELON PCG
    # L'IA expert-comptable détermine automatiquement les comptes depuis le PCG PDF
    try:
        ai_classification = classify_accounting(document_json, PCG_MAPPING)
    except Exception as e:
        raise ValidationError(f"Erreur lors de la classification PCG par IA: {str(e)}")
    
    # ✅ EXTRACTION DES DONNÉES GÉNÉRÉES PAR L'IA
    type_doc = ai_classification.get("type_document", "").upper()
    ecritures_ai = ai_classification.get("journal", [])
    
    if not ecritures_ai:
        raise ValidationError("L'IA n'a généré aucune écriture comptable")
    
    # ✅ FORMATAGE DES ÉCRITURES AVEC LIBELLÉS AUTOMATIQUES VIA PCG_LOADER
    ecritures = []
    for ecriture in ecritures_ai:
        numero_compte = str(ecriture.get("compte", "")).strip()
        
        if not numero_compte:
            continue
        
        # ✅ RÉCUPÉRATION AUTOMATIQUE DU LIBELLÉ DEPUIS PCG
        libelle = get_pcg_label(numero_compte)
        if not libelle or libelle == "-":
            # Fallback: utiliser le libellé fourni par l'IA si PCG ne trouve pas
            libelle = ecriture.get("libelle", f"Compte {numero_compte}")
        
        # Conversion des montants
        debit = float(ecriture.get("debit", 0) or 0)
        credit = float(ecriture.get("credit", 0) or 0)
        
        ecritures.append({
            "numero_compte": numero_compte,
            "libelle": libelle,
            "debit_ar": debit,
            "credit_ar": credit
        })
    
    return {
        "type_journal": type_doc,
        "numero_piece": numero_piece,
        "date": date_facture,
        "ecritures": ecritures
    }



# REFACTORED LOGIC FOR REUSE
def process_journal_generation(document_json, project_id=None, file_source=None, form_source=None):
    """
    Fonction utilitaire pour générer le journal.
    project_id: ID du projet auquel assigner les écritures.
    """
    
    # ===================================================
    # 🚀 AFFICHAGE DE DÉMARRAGE
    # ===================================================
    print("\n[INFO] START GENERATE JOURNAL VIEW")
    print(f"   Input data keys: {list(document_json.keys())}")
    print()
    
    # ===================================================
    # 🏦 TRAITEMENT SPÉCIAL POUR RELEVÉ BANCAIRE
    # ===================================================
    piece_type = document_json.get("piece_type", "")
    description_json = document_json.get("description_json", {})
    
    if piece_type == "Relevé bancaire" and "transactions_details" in description_json:
        print("   [INFO] Detection: Releve bancaire avec transactions multiples")
        transactions = description_json.get("transactions_details", [])
        
        if not transactions:
            raise ValidationError("Aucune transaction dans le relevé bancaire")
        
        print(f"   [INFO] Traitement de {len(transactions)} transactions")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time
        
        all_saved_lines = []
        
        # Fonction interne pour traiter une transaction
        def process_single_transaction(idx, transaction):
            try:
                # Créer un document JSON pour cette transaction
                transaction_doc = {
                    "numero_facture": transaction.get("reference") or f"BANK-{idx}",
                    "reference": transaction.get("reference") or f"BANK-{idx}",
                    "date": transaction.get("date"),
                    "objet_description": transaction.get("description", ""),
                    "banque": description_json.get("bank_name", ""),
                    "type_document": "BANQUE",
                    "montant_ttc": transaction.get("debit", 0) or transaction.get("credit", 0),
                    "debit": transaction.get("debit", 0),
                    "credit": transaction.get("credit", 0),
                }
                
                # Générer le journal pour cette transaction
                ai_result = generate_journal_from_pcg(transaction_doc)
                ai_result["project_id"] = project_id
                
                return {
                    "success": True,
                    "idx": idx,
                    "result": ai_result
                }
            except Exception as e:
                return {
                    "success": False,
                    "idx": idx,
                    "error": str(e)
                }

        # Exécution parallèle
        print(f"   [INFO] Demarrage de l'execution parallele (max_workers=5)...")
        start_time = time.time()
        
        saved_entries_data = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_tx = {
                executor.submit(process_single_transaction, idx, tx): idx 
                for idx, tx in enumerate(transactions, start=1)
            }
            
            for future in as_completed(future_to_tx):
                idx = future_to_tx[future]
                try:
                    res = future.result()
                    if res["success"]:
                        saved_entries_data.append(res["result"])
                        print(f"      [SUCCESS] Transaction {res['idx']} traitee avec succes")
                    else:
                        print(f"      [ERROR] Erreur transaction {res['idx']}: {res['error']}")
                except Exception as exc:
                    print(f"      [ERROR] Exception fatale transaction {idx}: {exc}")

        print(f"   [INFO] Fin du traitement parallele en {time.time() - start_time:.2f}s")
        
        # Sauvegarde séquentielle pour éviter les conflits DB
        print("   [INFO] Sauvegarde des ecritures en base...")
        
        for ai_result in saved_entries_data:
            type_journal = ai_result.get("type_journal")
            numero_piece = ai_result.get("numero_piece")
            date_val = ai_result.get("date")
            ecritures = ai_result.get("ecritures", [])
            
            if not ecritures:
                continue
                
            # Vérifier l'équilibre
            total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
            total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)
            
            if total_debit != total_credit:
                print(f"      [WARNING] Transaction {numero_piece} non equilibree (D:{total_debit} / C:{total_credit})")
                continue
                
            for line in ecritures:
                numero_compte = line["numero_compte"]
                libelle = get_pcg_label(numero_compte)
                if not libelle:
                    libelle = line.get("libelle", f"Compte {numero_compte}")
                
                entry = Journal(
                    project_id=project_id,
                    date=date_val,
                    numero_piece=numero_piece,
                    type_journal=type_journal,
                    numero_compte=numero_compte,
                    libelle=libelle,
                    debit_ar=line["debit_ar"],
                    credit_ar=line["credit_ar"],
                )
                
                entry.clean()
                entry.save()
                
                if form_source:
                    form_source.journal = entry
                    form_source.save()
                
                all_saved_lines.append({
                    "id": entry.id,
                    "compte": entry.numero_compte,
                    "debit": float(entry.debit_ar),
                    "credit": float(entry.credit_ar),
                    "libelle": entry.libelle
                })
        
        print(f"\n   [SUCCESS] {len(all_saved_lines)} ecritures sauvegardees pour {len(transactions)} transactions")
        
        return {
            "message": "Journal enregistré avec succès",
            "type_journal": "BANQUE",
            "numero_piece": "RELEVE-BANCAIRE",
            "date": description_json.get("periode_date_end"),
            "lignes": all_saved_lines
        }
    
    # ===================================================
    # 📄 TRAITEMENT NORMAL POUR AUTRES DOCUMENTS
    # ===================================================
    try:
        # ✅ GÉNÉRATION AUTOMATIQUE PAR RÈGLES PCG (pas d'IA pour les comptes)
        ai_result = generate_journal_from_pcg(document_json)
    except Exception as e:
        raise Exception(f"Erreur génération PCG: {str(e)}")

    type_journal = ai_result.get("type_journal")
    
    # ⚠️ MAPPING DE SÉCURITÉ : "PAIE" -> "OD" pour respecter les choix du modèle Journal
    if type_journal == "PAIE":
        type_journal = "OD"
    
    numero_piece = ai_result.get("numero_piece")
    date_val = ai_result.get("date")
    ecritures = ai_result.get("ecritures", [])

    if not ecritures:
        raise ValidationError("Aucune écriture générée")
    
    print(f"   [INFO] AI a genere {len(ecritures)} lignes d'ecriture")

    # Vérification de l'équilibre du journal
    total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
    total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)

    if total_debit != total_credit:
         raise ValidationError(f"Écritures non équilibrées (D:{total_debit} / C:{total_credit})")

    # Sauvegarde chaque ligne dans Journal
    saved_lines = []
    journal_entries_to_create = []
    
    # 🚀 OPTIMISATION: Préparation des objets pour Bulk Create
    print(f"   [INFO] Preparation des objets Journal ({len(ecritures)} lignes)...")
    
    for idx, line in enumerate(ecritures, start=1):
        numero_compte = line["numero_compte"]
        libelle = get_pcg_label(numero_compte)
        if not libelle:
            libelle = line.get("libelle", f"Compte {numero_compte}")

        entry = Journal(
            project_id=project_id,
            date=date_val,
            numero_piece=numero_piece,
            type_journal=type_journal,
            numero_compte=numero_compte,
            libelle=libelle,
            debit_ar=Decimal(str(line["debit_ar"])),
            credit_ar=Decimal(str(line["credit_ar"])),
        )
        entry.clean() # Validation basique
        journal_entries_to_create.append(entry)
        
        saved_lines.append({
            "compte": entry.numero_compte,
            "debit": float(entry.debit_ar),
            "credit": float(entry.credit_ar),
            "libelle": entry.libelle
        })
        
    try:
        # 1. BULK CREATE JOURNAL (Bypasses save(), so no signals fired yet)
        print(f"   [INFO] Sauvegarde en masse des {len(journal_entries_to_create)} lignes journal...")
        created_entries = Journal.objects.bulk_create(journal_entries_to_create)
        
        # 2. GESTION MANUELLE DES CONSÉQUENCES (Grand Livre, Balance, Bilan)
        # Puisque bypass des signaux, on doit le faire manuellement mais de façon optimisée (1 seule fois)
        
        print("   [INFO] Generation optimisee du Grand Livre...")
        gl_entries_to_create = []
        affected_accounts = set()
        
        # Pour le calcul des soldes, on doit récupérer les soldes actuels des comptes impactés
        affected_accounts_list = [e.numero_compte for e in created_entries]
        
        # Récupérer les derniers soldes connus
        from django.db.models import OuterRef, Subquery
        
        # On peut iterer et chercher le dernier solde. 
        # Note: Si plusieurs écritures sur le même compte dans le batch, faut incrémenter le solde en mémoire.
        
        # Dictionnaire pour suivre le solde courant pendant l'itération du batch
        current_soldes = {} # {numero_compte: decimal_solde}
        
        for entry in created_entries:
            compte = entry.numero_compte
            affected_accounts.add(compte)
            
            # Si solde pas encore chargé en mémoire, le chercher
            if compte not in current_soldes:
                last_gl = GrandLivre.objects.filter(project_id=project_id, numero_compte=compte).order_by('-date', '-id').first()
                current_soldes[compte] = last_gl.solde if last_gl else Decimal('0.00')
            
            # Calcul nouveau solde
            new_solde = current_soldes[compte] + entry.debit_ar - entry.credit_ar
            current_soldes[compte] = new_solde
            
            gl_entries_to_create.append(GrandLivre(
                project_id=project_id,
                journal=entry,
                numero_compte=compte,
                date=entry.date,
                numero_piece=entry.numero_piece,
                libelle=entry.libelle,
                debit=entry.debit_ar,
                credit=entry.credit_ar,
                solde=new_solde
            ))
            
            # Link sources manually since signals didn't run
            if file_source:
                # Note: file_source.journal is ForeignKey to ONE journal entry. 
                # If multiple lines, we can only link one? usually link the first one or changing model to ManyToMany.
                # Assuming standard usage: link to first entry if not None
                 # file_source.journal = entry # Validation error if unique constraint? usually FK
                 pass 

        GrandLivre.objects.bulk_create(gl_entries_to_create)
        print(f"   [SUCCESS] {len(gl_entries_to_create)} lignes Grand Livre creees.")
        
        # 3. MISE À JOUR BALANCE & ÉTATS FINANCIERS (Une fois par compte/date)
        print("   [INFO] Mise a jour optimisee Balance \u0026 Etats Financiers...")
        
        affected_dates = {e.date for e in created_entries}
        
        # Pour chaque compte unique modifié
        for compte in affected_accounts:
            # Pour simplifier, on met à jour la balance pour la date de l'écriture
            # (Note: Si écritures sur plusieurs dates, boucler sur (compte, date))
            # Ici date_val est unique pour tout le document, donc ok.
            
            # Appel manuel à la logique du signal generate_balance
            balance, _ = Balance.objects.get_or_create(
                project_id=project_id,
                numero_compte=compte, 
                date=date_val,
                defaults={"libelle": get_pcg_label(compte)}
            )
            balance.calculate_from_grand_livre()
            # Note: calculate_from_grand_livre saves the Balance. 
            # The save() on Balance triggers generate_financial_statements signal.
            # So Bilan updates happen here automatically via Balance signal.
            # This is acceptable because it runs only once per unique account (e.g. 5 fois), not per line (could be 50).
            # If still slow, we could decouple Balance signal too, but usually N_accounts << N_lines.
            
        print("   [INFO] Traitement termine.")

    except Exception as e:
        print(f"      [ERROR] ERREUR lors de la sauvegarde: {str(e)}")
        raise ValidationError(f"Erreur de validation/sauvegarde: {str(e)}")

    # ===================================================
    # ✅ AFFICHAGE FORMATÉ DU JOURNAL DANS LE TERMINAL
    # ===================================================
    print("\n" + "=" * 50)
    print(f"   [INFO] JOURNAL GENERE (Type: {type_journal}, Piece: {numero_piece})")
    print("-" * 50)
    
    for idx, line in enumerate(saved_lines, start=1):
        compte = line["compte"]
        libelle = line["libelle"]
        debit = int(line["debit"]) if line["debit"] else 0
        credit = int(line["credit"]) if line["credit"] else 0
        print(f"Ligne {idx}: {compte} - {libelle} | Debit: {debit} | Credit: {credit}")
    
    print("=" * 50)
    print()

    return {
        "message": "Journal enregistré avec succès",
        "type_journal": type_journal,
        "numero_piece": numero_piece,
        "date": date_val,
        "lignes": saved_lines
    }



from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.db.models import Sum, Max, Case, When, DecimalField
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal

# PAGINATION STANDARD
class StandardPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100

class BilanListCreateView(generics.ListCreateAPIView):
    serializer_class = BilanSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]
    pagination_class = StandardPagination

    def get_queryset(self):
        project_id = getattr(self.request, 'project_id', None)
        if not project_id:
            return Bilan.objects.none()
            
        queryset = Bilan.objects.filter(project_id=project_id)
        # Filtres
        date = self.request.query_params.get('date')
        date_start = self.request.query_params.get('date_start')
        date_end = self.request.query_params.get('date_end')
        
        if date:
            queryset = queryset.filter(date=date)
        
        # LOGIQUE BILAN : Affichage par période (mensuel, trimestriel, annuel)
        # Aligné avec le comportement du Compte de Résultat
        if date_start and date_end:
            queryset = queryset.filter(date__range=[date_start, date_end])

        return queryset

class CompteResultatListCreateView(generics.ListCreateAPIView):
    serializer_class = CompteResultatSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]
    pagination_class = StandardPagination

    def get_queryset(self):
        project_id = getattr(self.request, 'project_id', None)
        if not project_id:
            return CompteResultat.objects.none()

        queryset = CompteResultat.objects.filter(project_id=project_id)
        # Filtres
        date = self.request.query_params.get('date')

        date_start = self.request.query_params.get('date_start')
        date_end = self.request.query_params.get('date_end')
        
        if date:
            queryset = queryset.filter(date=date)
        if date_start and date_end:
            queryset = queryset.filter(date__range=[date_start, date_end])

        return queryset


class BilanDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BilanSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]
    lookup_field = 'id'

    def get_queryset(self):
        project_id = getattr(self.request, 'project_id', None)
        if not project_id:
            return Bilan.objects.none()
        return Bilan.objects.filter(project_id=project_id)


class CompteResultatDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CompteResultatSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]
    lookup_field = 'id'

    def get_queryset(self):
        project_id = getattr(self.request, 'project_id', None)
        if not project_id:
            return CompteResultat.objects.none()
        return CompteResultat.objects.filter(project_id=project_id)


@extend_schema(responses={200: serializers.Serializer})
@extend_schema(request=EmptySerializer, responses={200: MessageResponseSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def ai_dashboard_analysis_view(request):
    """
    Analyse les indicateurs du dashboard avec l'IA et retourne des insights intelligents.
    POST /api/dashboard/ai-analysis/
    
    Body: {
        "indicators": {...},
        "ratios": {...}
    }
    """
    from compta.ai_dashboard_analysis import analyze_dashboard_with_ai
    
    project_id = getattr(request, 'project_id', None)
    
    try:
        # Récupérer les données du dashboard
        dashboard_data = request.data
        
        if not dashboard_data:
            return Response(
                {"error": "Aucune donnée fournie"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Extraire les dates si disponibles
        date_range = dashboard_data.get('date_range', {})
        start_date = date_range.get('start_date')
        end_date = date_range.get('end_date')
        
        # Appeler l'analyse IA avec les dates
        result = analyze_dashboard_with_ai(dashboard_data, start_date, end_date, project_id)
        
        if result.get("success"):
            return Response({
                "success": True,
                "analysis": result["analysis"]
            })
        else:
            return Response({
                "success": False,
                "error": result.get("error", "Erreur inconnue"),
                "details": result.get("details")
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({
            "success": False,
            "error": "Erreur lors de l'analyse IA",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(responses={200: serializers.Serializer})
@extend_schema(request=EmptySerializer, responses={200: MessageResponseSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def compte_resultat_ai_analysis_view(request):
    """
    Analyse les données du Compte de Résultat ou du Bilan avec l'IA.
    POST /api/compte-resultat/ai-analysis/
    """
    from compta.ai_compte_resultat_analysis import analyze_compte_resultat_with_ai
    
    project_id = getattr(request, 'project_id', None)
    
    try:
        data = request.data
        view_type = data.get('view_type', 'compteResultat')
        
        if not data:
            return Response(
                {"error": "Aucune donnée fournie"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Appeler l'analyse IA
        result = analyze_compte_resultat_with_ai(data, view_type, project_id)
        
        if result.get("success"):
            return Response({
                "success": True,
                "analysis": result["analysis"]
            })
        else:
            return Response({
                "success": False,
                "error": result.get("error", "Erreur inconnue"),
                "details": result.get("details")
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        return Response({
            "success": False,
            "error": "Erreur lors de l'analyse IA",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(request=BilanSerializer, responses={201: BilanSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def create_bilan_manual_view(request):
    """
    Create a single Bilan entry from manual form input.
    POST /api/bilans/manual/
    """
    project_id = getattr(request, "project_id", None)

    # 1. Générer description intelligente via GPT
    try:
        description = generate_description(
            data=request.data,
            json=json,
            client=client,
            model=settings.OPENAI_MODEL
        )
    except Exception as e:
        print(f"[WARNING] Erreur génération description Bilan: {e}")
        description = f"Saisie manuelle Bilan - Compte {request.data.get('numero_compte')}"

    # 2. Créer une instance FormSource pour traçabilité dans "Gestion des pièces"
    try:
        FormSource.objects.create(
            project_id=project_id,
            piece_type="État financier",
            description=description,
            data_json=request.data,
            date=request.data.get("date")
        )
    except Exception as e:
        print(f"[ERROR] Impossible de créer FormSource pour Bilan: {e}")

    # 3. Sauvegarder l'entrée Bilan
    serializer = BilanSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(project_id=project_id, description=description)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(request=CompteResultatSerializer, responses={201: CompteResultatSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def create_compte_resultat_manual_view(request):
    """
    Create a single CompteResultat entry from manual form input.
    POST /api/CompteResultats/manual/
    """
    project_id = getattr(request, "project_id", None)

    # 1. Générer description intelligente via GPT
    try:
        description = generate_description(
            data=request.data,
            json=json,
            client=client,
            model=settings.OPENAI_MODEL
        )
    except Exception as e:
        print(f"[WARNING] Erreur génération description CR: {e}")
        description = f"Saisie manuelle Compte de Résultat - Compte {request.data.get('numero_compte')}"

    # 2. Créer une instance FormSource pour traçabilité dans "Gestion des pièces"
    try:
        FormSource.objects.create(
            project_id=project_id,
            piece_type="État financier",
            description=description,
            data_json=request.data,
            date=request.data.get("date")
        )
    except Exception as e:
        print(f"[ERROR] Impossible de créer FormSource pour CompteResultat: {e}")

    # 3. Sauvegarder l'entrée Compte de Résultat
    serializer = CompteResultatSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(project_id=project_id, description=description)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# GENERATE JOURNAL VIEW
@extend_schema(responses={200: serializers.Serializer})
@extend_schema(request=EmptySerializer, responses={200: MessageResponseSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def generate_journal_view(request):
    document_json = request.data
    
    # Récupération du FileSource si fourni
    file_source = None
    file_source_id = request.data.get("file_source")
    if file_source_id:
        try:
            file_source = FileSource.objects.get(id=file_source_id)
        except FileSource.DoesNotExist:
            pass

    # Récupération du FormSource si fourni
    form_source = None
    form_source_id = request.data.get("form_source")
    if form_source_id:
        try:
            form_source = FormSource.objects.get(id=form_source_id)
        except FormSource.DoesNotExist:
            pass

    project_id = getattr(request, 'project_id', None)
    
    try:
        result = process_journal_generation(document_json, project_id, file_source, form_source)
        return Response(result, status=status.HTTP_201_CREATED)
    except ValidationError as e:
        return Response({"error": str(e)}, status=400)
    except Exception as e:
        return Response({"error": str(e)}, status=500)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: ChiffreAffaireSerializer}
)
@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: ChiffreAffaireSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def chiffre_affaire_view(request):
    """
    Calcul du Chiffre d'Affaires avec variation par rapport à la période précédente
    GET /api/chiffre-affaire/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    def calculate_ca(start_date, end_date):
        """Fonction helper pour calculer le CA pour une période donnée"""
        return get_chiffre_affaire(project_id, start_date, end_date)

    # Calcul période courante
    current_ca = calculate_ca(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            # Calculer la durée de la période
            delta_days = (end_date_obj - start_date_obj).days
            
            # Déterminer la période précédente
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée : décaler de la même durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            # Calculer CA période précédente
            previous_ca = calculate_ca(previous_start, previous_end)
            
            # Calculer variation en pourcentage
            # Éviter les pourcentages aberrants si la valeur précédente est trop faible
            if previous_ca != 0 and abs(previous_ca) > 50000:  # Seuil minimum 50000 Ar
                variation = ((current_ca - previous_ca) / abs(previous_ca)) * 100
                # Plafonner la variation à ±1000%
                if variation > 1000:
                    variation = 1000
                elif variation < -1000:
                    variation = -1000
            else:
                variation = None
        except:
            pass

    return Response({
        "chiffre_affaire": current_ca,
        "variation": variation,
        "numero_compte": "70",
        "total_credit": current_ca,
        "total_debit": Decimal("0.00")
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EbeSerializer}
)
@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EbeSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def ebe_view(request):
    """
    Calcul de l'EBE avec variation par rapport à la période précédente
    GET /api/ebe/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_ebe(start_date, end_date):
        """
        Fonction helper pour calculer l'EBE pour une période donnée
        """
        return get_ebe(project_id, start_date, end_date)

    # Calcul période courante
    current_ebe = calculate_ebe(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_ebe = calculate_ebe(previous_start, previous_end)
            
            # Calculer variation en pourcentage
            # Éviter les pourcentages aberrants si la valeur précédente est trop faible
            if previous_ebe != 0 and abs(previous_ebe) > 50000:  # Seuil minimum 50000 Ar
                variation = ((current_ebe - previous_ebe) / abs(previous_ebe)) * 100
                # Plafonner la variation à ±1000%
                if variation > 1000:
                    variation = 1000
                elif variation < -1000:
                    variation = -1000
            else:
                variation = None
        except:
            pass

    return Response({
        "ebe": current_ebe,
        "variation": variation,
        "produits_exploitation": Decimal("0.00"),  # Compatibility
        "charges_exploitation": Decimal("0.00"),   # Compatibility
        "chiffre_affaires": Decimal("0.00"),
        "subventions": Decimal("0.00"),
        "achats": Decimal("0.00"),
        "charges_externes": Decimal("0.00"),
        "impots_taxes": Decimal("0.00"),
        "charges_personnel": Decimal("0.00")
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MargeBruteSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def marge_brute_view(request):
    """
    Calcul de la Marge Brute selon PCG 2005
    Formule : (Ventes 70 + Production Stockée 71 + Production Immobilisée 72) - Achats (60)
    GET /api/marge-brute/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_marge_brute(start_date, end_date):
        """Fonction helper pour calculer la Marge Brute pour une période donnée
        Formule PCG 2005 : Marge Brute = (70+71+72) - (60+61+62)
        """
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        filters = {"project_id": project_id}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Produits (70, 71, 72)
        produits = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__regex=r"^(70|71|72)", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Achats et services extérieurs (60, 61, 62)
        charges = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__regex=r"^(60|61|62)", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        return produits, charges

    # Calcul période courante
    current_prod, current_ach = calculate_marge_brute(date_start, date_end)
    current_marge = current_prod - current_ach
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:
                prev_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:
                prev_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:
                prev_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                prev_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                prev_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            prev_prod, prev_ach = calculate_marge_brute(prev_start, prev_end)
            prev_marge = prev_prod - prev_ach
            
            if prev_marge != 0 and abs(prev_marge) > 50000:
                variation = ((current_marge - prev_marge) / abs(prev_marge)) * 100
                if variation > 1000: variation = 1000
                elif variation < -1000: variation = -1000
        except:
            pass

    return Response({
        "ventes": current_prod,
        "achats": current_ach,
        "marge_brute": current_marge,
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MargeNetteSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def marge_nette_view(request):
    """
    Calcul de la Marge Nette avec variation par rapport à la période précédente
    Formule : Marge Nette (%) = (Résultat Net / Chiffre d'Affaires) × 100
    GET /api/marge-nette/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_marge_nette(start_date, end_date):
        """Fonction helper pour calculer la Marge Nette pour une période donnée"""
        ca = get_chiffre_affaire(project_id, start_date, end_date)
        resultat_net = get_resultat_net(project_id, start_date, end_date)
        
        if ca != 0 and abs(ca) > 1000:
            marge_nette = (resultat_net / ca) * 100
        else:
            marge_nette = None
        
        return ca, resultat_net, marge_nette

    # Calcul période courante
    current_ca, current_rn, current_marge = calculate_marge_nette(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end and current_marge is not None:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                prev_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                prev_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                prev_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                prev_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                prev_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            prev_ca, prev_rn, prev_marge = calculate_marge_nette(prev_start, prev_end)
            
            # Calculer variation en points de pourcentage
            if prev_marge is not None:
                variation = current_marge - prev_marge
                # Plafonner la variation à ±100 points de pourcentage
                if variation > 100:
                    variation = 100
                elif variation < -100:
                    variation = -100
        except:
            pass

    return Response({
        "resultat_net": current_rn,
        "chiffre_affaire": current_ca,
        "marge_nette": current_marge,
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: BfrSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def bfr_view(request):
    """
    Calcul du BFR (Besoin en Fonds de Roulement) avec variation par rapport à la période précédente
    Formule : BFR = (Stocks + Créances Clients + Autres Créances) - (Dettes Fournisseurs + Autres Dettes)
    GET /api/bfr/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_bfr(start_date, end_date):
        """Fonction helper pour calculer le BFR pour une période donnée"""
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        filters = {"project_id": project_id}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        # Stocks (Compte 3*)
        stocks = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="3", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Créances Clients (Compte 411)
        creances_clients = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="411", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Autres Créances (Comptes 41* sauf 411, 42*, 43*, 44*, 46*, 47*)
        autres_creances = (
            Bilan.objects
            .filter(
                type_bilan="ACTIF", 
                numero_compte__regex=r'^4[1-7]',
                **filters
            )
            .exclude(numero_compte__startswith="411")
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Dettes Fournisseurs (Compte 401)
        dettes_fournisseurs = (
            Bilan.objects
            .filter(type_bilan="PASSIF", numero_compte__startswith="401", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Autres Dettes (Comptes 40* sauf 401, 42*, 43*, 44*, 46*, 47*)
        autres_dettes = (
            Bilan.objects
            .filter(
                type_bilan="PASSIF", 
                numero_compte__regex=r'^4[0-7]',
                **filters
            )
            .exclude(numero_compte__startswith="401")
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Calcul du BFR
        bfr = (stocks + creances_clients + autres_creances) - (dettes_fournisseurs + autres_dettes)
        
        return {
            "stocks": stocks,
            "creances_clients": creances_clients,
            "autres_creances": autres_creances,
            "dettes_fournisseurs": dettes_fournisseurs,
            "autres_dettes": autres_dettes,
            "bfr": bfr
        }

    # Calcul période courante
    current_data = calculate_bfr(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                prev_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                prev_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                prev_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                prev_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                prev_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            prev_data = calculate_bfr(prev_start, prev_end)
            
            # Calculer variation en pourcentage
            if prev_data["bfr"] != 0 and abs(prev_data["bfr"]) > 50000:
                variation = ((current_data["bfr"] - prev_data["bfr"]) / abs(prev_data["bfr"])) * 100
                # Plafonner la variation à ±1000%
                if variation > 1000:
                    variation = 1000
                elif variation < -1000:
                    variation = -1000
            else:
                variation = None
        except:
            pass

    return Response({
        "stocks": current_data["stocks"],
        "creances_clients": current_data["creances_clients"],
        "autres_creances": current_data["autres_creances"],
        "dettes_fournisseurs": current_data["dettes_fournisseurs"],
        "autres_dettes": current_data["autres_dettes"],
        "bfr": current_data["bfr"],
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: TresorerieSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def tresorerie_view(request):
    """
    Calcul de la Trésorerie avec variation par rapport à la période précédente
    Formule : Trésorerie = Sum(montant_ar) WHERE numero_compte LIKE '5%' AND type_bilan='ACTIF'
    GET /api/tresorerie/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_tresorerie(start_date, end_date):
        """Fonction helper pour calculer la Trésorerie pour une période donnée"""
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        filters = {"project_id": project_id}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        # Calcul de la trésorerie depuis le Bilan (comptes de classe 5 - Actif)
        # Classe 5 = Comptes financiers (Caisse, Banques, etc.)
        tresorerie_total = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="5", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Détail par type de compte
        caisse = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="57", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        banques = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__regex=r'^5[0-6]', **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        return tresorerie_total, caisse, banques

    # Calcul période courante
    current_tresorerie, current_caisse, current_banques = calculate_tresorerie(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                prev_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                prev_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                prev_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                prev_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                prev_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                prev_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            prev_tresorerie, _, _ = calculate_tresorerie(prev_start, prev_end)
            
            # Calculer variation en pourcentage
            if prev_tresorerie != 0 and abs(prev_tresorerie) > 1000:
                variation = ((current_tresorerie - prev_tresorerie) / abs(prev_tresorerie)) * 100
                # Plafonner la variation à ±1000%
                if variation > 1000:
                    variation = 1000
                elif variation < -1000:
                    variation = -1000
            else:
                variation = None
        except:
            pass

    return Response({
        "tresorerie": current_tresorerie,
        "variation": variation,
        "caisse": current_caisse,
        "banques": current_banques
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_tresorerie_view(request):
    """
    Évolution de la trésorerie sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-tresorerie/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum
    
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        # Récupérer la date max dans le Bilan
        max_date = Bilan.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)  # 6 mois au total
        else:
            # Fallback si pas de données
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
    
    # Générer les mois entre start et end
    evolution_data = []
    current_date = start_date_obj.replace(day=1)  # Premier jour du mois
    
    while current_date <= end_date_obj:
        # Calculer le dernier jour du mois
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day_of_month = next_month - timedelta(days=1)
        
        # Calculer la trésorerie pour ce mois
        tresorerie_mois = (
            Bilan.objects
            .filter(
                project_id=project_id,
                type_bilan="ACTIF",
                numero_compte__startswith="5",
                date__range=[current_date, last_day_of_month]
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Formater le mois pour l'affichage
        mois_label = current_date.strftime("%b %Y")  # Ex: "Jan 2025"
        
        evolution_data.append({
            "mois": mois_label,
            "montant": float(tresorerie_mois),
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        # Passer au mois suivant
        current_date = next_month
    
    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_marges_view(request):
    """
    Évolution de la marge brute et marge nette sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-marges/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum
    
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        # Récupérer la date max dans CompteResultat
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)  # 6 mois au total
        else:
            # Fallback si pas de données
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
    
    # Générer les mois entre start et end
    evolution_data = []
    current_date = start_date_obj.replace(day=1)  # Premier jour du mois
    
    while current_date <= end_date_obj:
        # Calculer le dernier jour du mois
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day_of_month = next_month - timedelta(days=1)
        
        # Calculer Produits (70, 71, 72) pour marge brute
        produits_marge = (
            CompteResultat.objects
            .filter(
                project_id=project_id,
                nature="PRODUIT",
                numero_compte__regex=r"^(70|71|72)",
                date__range=[current_date, last_day_of_month]
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Calculer Charges (60, 61, 62) pour marge brute
        charges_marge = (
            CompteResultat.objects
            .filter(
                project_id=project_id,
                nature="CHARGE",
                numero_compte__regex=r"^(60|61|62)",
                date__range=[current_date, last_day_of_month]
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # Calculer Résultat Net pour marge nette
        produits = (
            CompteResultat.objects
            .filter(
                project_id=project_id,
                nature="PRODUIT",
                numero_compte__startswith="7",
                date__range=[current_date, last_day_of_month]
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        charges = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__startswith="6",
                date__range=[current_date, last_day_of_month]
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        resultat_net = produits - charges
        
        # Calculer les marges en pourcentage
        marge_brute = None
        marge_nette = None
        
        if produits_marge != 0 and abs(produits_marge) > 1000:
            marge_brute_montant = produits_marge - charges_marge
            marge_brute = float((marge_brute_montant / produits_marge) * 100)
            marge_nette = float((resultat_net / produits_marge) * 100)
        
        # Formater le mois pour l'affichage
        mois_label = current_date.strftime("%b %Y")  # Ex: "Jan 2025"
        
        evolution_data.append({
            "mois": mois_label,
            "marge_brute": marge_brute,
            "marge_nette": marge_nette,
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        # Passer au mois suivant
        current_date = next_month
    
    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_caf_view(request):
    """
    Évolution de la CAF sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-caf/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_caf_for_period(start, end):
        filters = {"project_id": project_id, "date__range": [start, end]}
        
        # Helper variables for EBE
        c70 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="70", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c71 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="71", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c72 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="72", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c74 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="74", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c60 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="60", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c61 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="61", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c62 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="62", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c63 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="63", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c64 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="64", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        
        ebe = (c70 + c71 + c72) - (c60 + c61 + c62) + c74 - c63 - c64
        
        # CAF specific accounts
        c75 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="75", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c65 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="65", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c77 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="77", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c67 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="67", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c69 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="69", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        
        caf = ebe + c75 - c65 + c77 - c67 - c69
        return caf

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day = next_month - timedelta(days=1)
        
        caf_val = get_caf_for_period(current_date, last_day)
        
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "montant": float(caf_val),
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_marge_operationnelle_view(request):
    """
    Évolution de la marge opérationnelle sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-marge-operationnelle/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_marge_op_for_period(start, end):
        filters = {"project_id": project_id, "date__range": [start, end]}
        
        # CA = 70, 71, 72
        c70 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="70", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c71 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="71", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c72 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="72", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        ca = c70 + c71 + c72

        # Produits Opérationnels = 70, 71, 72, 74, 75
        c74 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="74", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c75 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="75", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        prod_op = ca + c74 + c75

        # Charges d'exploitation = 60, 61, 62, 63, 64, 65
        charges_op = Decimal("0")
        for p in ["60", "61", "62", "63", "64", "65"]:
            charges_op += CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith=p, **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        
        res_op = prod_op - charges_op
        
        marge_op = None
        if ca != 0 and abs(ca) >= 1000:
            marge_op = float((res_op / ca) * 100)
            if marge_op > 500: marge_op = 500
            elif marge_op < -500: marge_op = -500
            
        return marge_op

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day = next_month - timedelta(days=1)
        
        marge_op_val = get_marge_op_for_period(current_date, last_day)
        
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "marge_op": marge_op_val,
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_roe_view(request):
    """
    Évolution du ROE (Return on Equity) sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-roe/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max, Case, When
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    from django.db.models import DecimalField # Added this import
    from decimal import Decimal

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_roe_for_period(start, end):
        from compta.kpi_utils import get_latest_bilan_sum, get_resultat_net
        
        # 1. Résultat Net
        resultat_net = get_resultat_net(project_id, start, end)

        # 2. Fonds propres
        fonds_propres = get_latest_bilan_sum(
            project_id, start, end, categorie="CAPITAUX_PROPRES", type_bilan="PASSIF"
        )

        # 3. Calcul du ROE
        roe = None
        if fonds_propres != 0 and abs(fonds_propres) >= 100000:
            roe = float((resultat_net / fonds_propres * 100))
            # Protection contre les extrêmes
            if roe > 1000: roe = 1000
            elif roe < -1000: roe = -1000
        
        return roe

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day = next_month - timedelta(days=1)
        
        roe_val = get_roe_for_period(current_date, last_day)
        
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "roe": roe_val,
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_roa_view(request):
    """
    Évolution du ROA (Return on Assets) sur plusieurs mois
    Par défaut : 6 derniers mois
    Supporte le filtrage par date (date_start, date_end)
    GET /api/evolution-roa/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max, Case, When
    from django.db.models import DecimalField # Added this import
    from decimal import Decimal

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_roa_for_period(start, end):
        from compta.kpi_utils import get_latest_bilan_sum, get_resultat_net
        
        # 1. Résultat Net
        resultat_net = get_resultat_net(project_id, start, end)

        # 2. Total Actif
        total_actif = get_latest_bilan_sum(
            project_id, start, end, type_bilan="ACTIF"
        )

        # 3. Calcul du ROA
        roa = None
        if total_actif != 0 and abs(total_actif) >= 100000:
            roa = float((resultat_net / total_actif * 100))
            # Protection contre les extrêmes
            if roa > 1000: roa = 1000
            elif roa < -1000: roa = -1000
        
        return roa

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        
        last_day = next_month - timedelta(days=1)
        
        roa_val = get_roa_for_period(current_date, last_day)
        
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "roa": roa_val,
            "date": current_date.strftime("%Y-%m-%d")
        })
        
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


# ============================================================
# EVOLUTION BFR
# ============================================================
@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_bfr_view(request):
    """
    Évolution du BFR sur plusieurs mois
    BFR = Stocks + Créances clients - Dettes fournisseurs
    GET /api/evolution-bfr/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    project_id = getattr(request, "project_id", None)

    if not date_start or not date_end:
        max_date = Bilan.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_bfr_for_period(start, end):
        filters = {"project_id": project_id, "date__range": [start, end]}
        stocks = (
            Bilan.objects.filter(type_bilan="ACTIF", numero_compte__startswith="3", **filters)
            .aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        )
        creances = (
            Bilan.objects.filter(type_bilan="ACTIF", numero_compte__startswith="41", **filters)
            .aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        )
        dettes_fourn = (
            Bilan.objects.filter(type_bilan="PASSIF", numero_compte__startswith="40", **filters)
            .aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        )
        return stocks + creances - dettes_fourn

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        last_day = next_month - timedelta(days=1)

        bfr_val = get_bfr_for_period(current_date, last_day)
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "bfr": float(bfr_val),
            "date": current_date.strftime("%Y-%m-%d")
        })
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


# ============================================================
# EVOLUTION EBE
# ============================================================
@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_ebe_view(request):
    """
    Évolution de l'EBE sur plusieurs mois
    EBE = (70+71+72) - (60+61+62) + 74 - 63 - 64
    GET /api/evolution-ebe/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    project_id = getattr(request, "project_id", None)

    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_ebe_for_period(start, end):
        filters = {"project_id": project_id, "date__range": [start, end]}
        c70 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="70", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c71 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="71", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c72 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="72", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c74 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="74", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c60 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="60", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c61 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="61", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c62 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="62", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c63 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="63", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c64 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="64", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        return (c70 + c71 + c72) - (c60 + c61 + c62) + c74 - c63 - c64

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        last_day = next_month - timedelta(days=1)

        ebe_val = get_ebe_for_period(current_date, last_day)
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "ebe": float(ebe_val),
            "date": current_date.strftime("%Y-%m-%d")
        })
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


# ============================================================
# EVOLUTION LEVERAGE BRUT
# ============================================================
@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_leverage_brut_view(request):
    """
    Évolution du Leverage Brut sur plusieurs mois
    Leverage Brut = Dettes Financières / EBE
    GET /api/evolution-leverage-brut/?date_start=2024-01-01&date_end=2024-12-31
    """
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    from django.db.models import Sum, Max
    from decimal import Decimal

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    project_id = getattr(request, "project_id", None)

    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()

    def get_leverage_for_period(start, end):
        bilan_filters = {"project_id": project_id, "date__range": [start, end]}
        cr_filters = {"project_id": project_id, "date__range": [start, end]}

        # Dettes financières (Bilan PASSIF comptes 16)
        dettes_fin = (
            Bilan.objects.filter(type_bilan="PASSIF", numero_compte__startswith="16", **bilan_filters)
            .aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        )

        # EBE = (70+71+72) - (60+61+62) + 74 - 63 - 64
        c70 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="70", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c71 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="71", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c72 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="72", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c74 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="74", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c60 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="60", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c61 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="61", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c62 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="62", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c63 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="63", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c64 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="64", **cr_filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        ebe = (c70 + c71 + c72) - (c60 + c61 + c62) + c74 - c63 - c64

        if ebe != 0 and abs(ebe) >= 1000:
            leverage = float(dettes_fin / ebe)
            if leverage > 1000: leverage = 1000
            elif leverage < -1000: leverage = -1000
        else:
            leverage = None

        return leverage

    evolution_data = []
    current_date = start_date_obj.replace(day=1)

    while current_date <= end_date_obj:
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1)
        last_day = next_month - timedelta(days=1)

        leverage_val = get_leverage_for_period(current_date, last_day)
        evolution_data.append({
            "mois": current_date.strftime("%b %Y"),
            "leverage": leverage_val,
            "date": current_date.strftime("%Y-%m-%d")
        })
        current_date = next_month

    return Response({
        "evolution": evolution_data,
        "periode_debut": start_date_obj.strftime("%Y-%m-%d"),
        "periode_fin": end_date_obj.strftime("%Y-%m-%d")
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def resultat_net_view(request):  #partie corriger
    """
    Calcul du Résultat Net avec variation par rapport à la période précédente
    """
    from dateutil.relativedelta import relativedelta
    from datetime import datetime, date

    # Récupération des dates
    date_start_str = request.query_params.get('date_start')
    date_end_str = request.query_params.get('date_end')

    today = date.today()
    
    # Par défaut : Année courante vs Année précédente
    if not date_start_str or not date_end_str:
        current_start = date(today.year, 1, 1)
        current_end = date(today.year, 12, 31)
        
        previous_start = date(today.year - 1, 1, 1)
        previous_end = date(today.year - 1, 12, 31)
    else:
        try:
            current_start = datetime.strptime(date_start_str, '%Y-%m-%d').date()
            current_end = datetime.strptime(date_end_str, '%Y-%m-%d').date()

            # Détermination de la période précédente
            # Si c'est un mois complet (ou presque), on compare au mois précédent
            # Si c'est une année, on compare à l'année précédente
            delta_days = (current_end - current_start).days
            
            if 28 <= delta_days <= 32: # Mensuel
                # Mois précédent
                previous_start = current_start - relativedelta(months=1)
                # Gestion fin de mois
                previous_end = previous_start + relativedelta(day=31)
                # Ajustement si on a sauté un mois (ex: mars -> fevrier)
                if previous_end.month != previous_start.month:
                    previous_end = previous_start + relativedelta(months=1, days=-1)
                    
                # Plus simple: Juste reculer d'un mois
                previous_start = current_start - relativedelta(months=1)
                previous_end = current_end - relativedelta(months=1)
                
                 # Si on veut matcher le mois exact (1er au 31)
                if current_start.day == 1:
                     previous_start = (current_start - relativedelta(months=1)).replace(day=1)
                     previous_end = (previous_start + relativedelta(months=1)) - relativedelta(days=1)

            elif 88 <= delta_days <= 92: # Trimestriel
                previous_start = current_start - relativedelta(months=3)
                previous_end = (current_start - relativedelta(days=1))
            
            elif delta_days >= 360: # Annuel
                previous_start = current_start - relativedelta(years=1)
                previous_end = current_end - relativedelta(years=1)
            
            else:
                 # Fallback: Glissement de la même durée
                duration = current_end - current_start
                previous_end = current_start - relativedelta(days=1)
                previous_start = previous_end - duration

        except ValueError:
            return Response({"error": "Format de date invalide (YYYY-MM-DD)"}, status=400)


    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    def calculate_resultat(d_start, d_end):
        """
        Calcul du résultat net unifié (Classe 7 - Classe 6)
        """
        res_net = get_resultat_net(project_id, d_start, d_end)
        
        return {
            "produits": Decimal("0.00"), # Non utilisé par le front pour le RN direct
            "charges_exploitation": Decimal("0.00"),
            "charges_financieres": Decimal("0.00"),
            "produits_financiers": Decimal("0.00"),
            "charges_exceptionnelles": Decimal("0.00"),
            "produits_exceptionnels": Decimal("0.00"),
            "impots_benefices": Decimal("0.00"),
            "resultat_net": res_net,
        }

    # Calcul Période Actuelle
    current_data = calculate_resultat(current_start, current_end)
    
    # Calcul Période Précédente
    previous_data = calculate_resultat(previous_start, previous_end)

    # Calcul Cumulé (pour Capitaux Propres)
    cumulative_data = calculate_resultat(None, current_end)
    
    # Variation
    current_net = current_data["resultat_net"]
    prev_net = previous_data["resultat_net"]
    variation = current_net - prev_net
    
    variation_pct = Decimal(0)
    if prev_net != 0:
        variation_pct = (variation / abs(prev_net)) * 100

    payload = {
        **current_data,
        "previous_resultat_net": prev_net,
        "variation": variation,
        "variation_percentage": variation_pct,
        "resultat_net_cumule": cumulative_data["resultat_net"]
    }

    serializer = ResultatNetSerializer(payload)
    return Response(serializer.data)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: CafSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def caf_view(request):
    """
    Calcul de la CAF avec variation par rapport à la période précédente
    GET /api/caf/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_caf(start_date, end_date):
        """
        Fonction helper pour calculer la CAF pour une période donnée
        Formule PCG 2005 : CAF = EBE + 75 - 65 + 77 - 67 - 69
        """
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # === Calcul de l'EBE : (70+71+72) - (60+61+62) + 74 - 63 - 64 ===
        # Ventes de marchandises (70)
        compte_70 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="70", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Production stockée (71)
        compte_71 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="71", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Production immobilisée (72)
        compte_72 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="72", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Subventions d'exploitation (74)
        compte_74 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="74", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Achats consommés (60)
        compte_60 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="60", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Services extérieurs A (61)
        compte_61 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="61", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Services extérieurs B (62)
        compte_62 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="62", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Impôts et taxes (63)
        compte_63 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="63", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges de personnel (64)
        compte_64 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="64", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # EBE = (70+71+72) - (60+61+62) + 74 - 63 - 64
        ebe = (compte_70 + compte_71 + compte_72) - (compte_60 + compte_61 + compte_62) + compte_74 - compte_63 - compte_64

        # === Comptes supplémentaires pour la CAF ===
        # Autres produits de gestion courante (75)
        compte_75 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="75", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Autres charges de gestion courante (65)
        compte_65 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="65", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Produits exceptionnels (77)
        compte_77 = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="77", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges exceptionnelles (67)
        compte_67 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="67", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Impôts sur les bénéfices (69)
        compte_69 = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="69", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # CAF = EBE + 75 - 65 + 77 - 67 - 69
        caf = ebe + compte_75 - compte_65 + compte_77 - compte_67 - compte_69

        return caf

    # Calcul période courante
    current_caf = calculate_caf(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_caf = calculate_caf(previous_start, previous_end)
            
            # Calculer variation en pourcentage
            # Éviter les pourcentages aberrants si la valeur précédente est trop faible
            if previous_caf != 0 and abs(previous_caf) > 50000:  # Seuil minimum 50000 Ar
                variation = ((current_caf - previous_caf) / abs(previous_caf)) * 100
                # Plafonner la variation à ±1000%
                if variation > 1000:
                    variation = 1000
                elif variation < -1000:
                    variation = -1000
            else:
                variation = None
        except:
            pass

    return Response({
        "caf": current_caf,
        "variation": variation,
        "resultat_net": Decimal("0.00"),
        "dotations_amort_provisions": Decimal("0.00"),
        "reprises_amort_provisions": Decimal("0.00")
    })



@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def leverage_brut_view(request):
    """
    Calcul du Leverage Brut avec variation par rapport à la période précédente
    Formule : Leverage Brut = Total Dettes / EBE
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_leverage(start_date, end_date):
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        base_filters = {"project_id": project_id}
        if start_date and end_date:
            base_filters["date__range"] = [start_date, end_date]

        # 1. Endettement (Bilan Passif 16*)
        dettes = (
            Bilan.objects
            .filter(type_bilan="PASSIF", numero_compte__startswith="16", **base_filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # 2. EBE (CompteResultat)
        # EBE = (70+71+72) - (60+61+62) + 74 - 63 - 64
        filters = base_filters
        c70 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="70", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c71 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="71", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c72 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="72", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c74 = CompteResultat.objects.filter(nature="PRODUIT", numero_compte__startswith="74", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        
        c60 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="60", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c61 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="61", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c62 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="62", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c63 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="63", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        c64 = CompteResultat.objects.filter(nature="CHARGE", numero_compte__startswith="64", **filters).aggregate(t=Sum("montant_ar"))["t"] or Decimal("0")
        
        ebe = (c70 + c71 + c72) - (c60 + c61 + c62) + c74 - c63 - c64

        if ebe != 0:
            return float(dettes / ebe)
        return 0.0

    current_lev = calculate_leverage(date_start, date_end)
    
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            delta_days = (end_date_obj - start_date_obj).days
            
            # Période précédente
            prev_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
            prev_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            prev_lev = calculate_leverage(prev_start, prev_end)
            if prev_lev != 0:
                variation = ((current_lev - prev_lev) / abs(prev_lev)) * 100
        except: pass

    return Response({
        "leverage_brut": current_lev,
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def evolution_ca_resultat_view(request):
    """
    Évolution combinée CA et Résultat Net sur plusieurs mois ou années (Optimisé)
    GET /api/evolution-ca-resultat/?date_start=2024-01-01&date_end=2024-12-31&group_by=year
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    group_by = request.GET.get("group_by", "month") # Default: month
    
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Si pas de dates fournies, utiliser les 6 derniers mois
    if not date_start or not date_end:
        max_date = CompteResultat.objects.filter(project_id=project_id).aggregate(max_date=Max("date"))["max_date"]
        if max_date:
            end_date_obj = max_date
            start_date_obj = end_date_obj - relativedelta(months=5)
        else:
            end_date_obj = datetime.now().date()
            start_date_obj = end_date_obj - relativedelta(months=5)
    else:
        start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
    
    evolution_data = []
    
    if group_by == "year":
        current_year = start_date_obj.year
        end_year = end_date_obj.year
        
        while current_year <= end_year:
            year_start = date(current_year, 1, 1)
            year_end = date(current_year, 12, 31)
            
            # Ajustement si le filtre ne couvre pas toute l'année
            if current_year == start_date_obj.year:
                year_start = start_date_obj
            if current_year == end_date_obj.year:
                year_end = end_date_obj
                
            stats = CompteResultat.objects.filter(
                project_id=project_id,
                date__range=[year_start, year_end]
            ).aggregate(
                ca=Sum(Case(When(nature="PRODUIT", numero_compte__startswith="70", then="montant_ar"), default=0, output_field=DecimalField())),
                total_produits=Sum(Case(When(nature="PRODUIT", then="montant_ar"), default=0, output_field=DecimalField())),
                total_charges=Sum(Case(When(nature="CHARGE", then="montant_ar"), default=0, output_field=DecimalField()))
            )
            
            ca_val = stats['ca'] or Decimal("0.00")
            total_prod = stats['total_produits'] or Decimal("0.00")
            total_charg = stats['total_charges'] or Decimal("0.00")
            resultat_net = total_prod - total_charg
            
            evolution_data.append({
                "name": str(current_year),
                "ca": float(ca_val),
                "charges": float(total_charg),
                "resultatNet": float(resultat_net),
                "date": f"{current_year}-01-01"
            })
            current_year += 1
    else:
        # Default: month-by-month
        current_date = start_date_obj.replace(day=1)
        while current_date <= end_date_obj:
            if current_date.month == 12:
                next_month = current_date.replace(year=current_date.year + 1, month=1)
            else:
                next_month = current_date.replace(month=current_date.month + 1)
            
            last_day = next_month - timedelta(days=1)
            
            stats = CompteResultat.objects.filter(
                project_id=project_id,
                date__range=[current_date, last_day]
            ).aggregate(
                ca=Sum(Case(When(nature="PRODUIT", numero_compte__startswith="70", then="montant_ar"), default=0, output_field=DecimalField())),
                total_produits=Sum(Case(When(nature="PRODUIT", then="montant_ar"), default=0, output_field=DecimalField())),
                total_charges=Sum(Case(When(nature="CHARGE", then="montant_ar"), default=0, output_field=DecimalField()))
            )
            
            ca_val = stats['ca'] or Decimal("0.00")
            total_prod = stats['total_produits'] or Decimal("0.00")
            total_charg = stats['total_charges'] or Decimal("0.00")
            resultat_net = total_prod - total_charg
            
            # Dictionnaire de mois pour le formatage manuel (évite les soucis de locale)
            mois_fr = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
            label = f"{mois_fr[current_date.month - 1]} {current_date.year}"
            
            evolution_data.append({
                "name": label,
                "ca": float(ca_val),
                "charges": float(total_charg),
                "resultatNet": float(resultat_net),
                "date": current_date.strftime("%Y-%m-%d")
            })
            
            current_date = next_month
    
    return Response(evolution_data)






@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: EvolutionResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def annuite_caf_view(request):
    """
    GET /api/annuite-caf/?date_debut=2025-01-01&date_fin=2025-12-31
    
    IMPORTANT: "Annuité" ici fait référence aux Charges Financières (Compte 66)
    car sans tableau d'amortissement, il est impossible de connaître la part du capital remboursée.
    Le ratio devient donc un ratio de couverture des intérêts.
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)
    base_filter = {"project_id": project_id}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    try:
        # === Calcul du Résultat Net ===
        # Produits (Classe 7)
        produits_total = (
            CompteResultat.objects
            .filter(
                nature="PRODUIT",
                numero_compte__regex=r"^7",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges exploitation (60-64)
        charges_exploitation = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__regex=r"^6[0-4]",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges financières (66)
        charges_financieres = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__startswith="66",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges exceptionnelles (67)
        charges_exceptionnelles = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__startswith="67",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Dotations (68)
        dotations = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__startswith="68",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Impôts sur bénéfices (69)
        impots_benefices = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__startswith="69",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Reprises (78)
        reprises = (
            CompteResultat.objects
            .filter(
                nature="PRODUIT",
                numero_compte__startswith="78",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Résultat Net
        resultat_net = (
            produits_total
            - charges_exploitation
            - charges_financieres
            - charges_exceptionnelles
            - dotations
            - impots_benefices
        )

        # === Calcul de la CAF (Méthode additive) ===
        # CAF = Résultat Net + Dotations - Reprises
        caf = resultat_net + dotations - reprises

        # === "Annuité" = Charges financières (proxy) ===
        # Note: En l'absence de tableau d'amortissement, on utilise les charges financières
        # Ce n'est pas l'annuité réelle mais donne un ratio de couverture des intérêts
        annuite_emprunt = charges_financieres

        # Ratio Charges Financières / CAF
        ratio_annuite_caf = annuite_emprunt / caf if caf != 0 else None

        serializer = AnnuiteCafSerializer({
            "annuite_emprunt": annuite_emprunt,
            "caf": caf,
            "ratio_annuite_caf": ratio_annuite_caf
        })

        return Response(serializer.data)
    except DBOperationalError as e:
        return Response({"error": "Database unavailable", "details": str(e)}, status=503)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: DashboardIndicatorsResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def dashboard_indicators_view(request):
    """
    ENDPOINT OPTIMISÉ POUR DASHBOARD
    Calcule tous les indicateurs financiers en une seule requête DB/HTTP.
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # --- HELPER INTERNE OPTIMISÉ ---
    
    def get_solde(prefix, sens="credit-debit", mode="periode"):
        # Initial queryset
        if prefix == "":
            qs = GrandLivre.objects.filter(project_id=project_id)
        else:
            qs = GrandLivre.objects.filter(project_id=project_id, numero_compte__startswith=str(prefix))
            
        # Filtre de date:
        # - "periode" : Flux entre date_start et date_end (Compte de Résultat)
        # - "cumulative" : Solde à l'instant T (Bilan: Trésorerie, Stocks, Dettes)
        if mode == "periode":
            if date_start and date_end:
                qs = qs.filter(date__range=[date_start, date_end])
        elif mode == "cumulative":
            if date_end:
                qs = qs.filter(date__lte=date_end)
            
        agg = qs.aggregate(
            d=Sum("debit"), 
            c=Sum("credit")
        )
        d = agg["d"] or Decimal("0.00")
        c = agg["c"] or Decimal("0.00")
        
        if sens == "debit": return d
        if sens == "credit": return c
        if sens == "debit-credit": return d - c
        return c - d # Default: Crédit - Débit (Produits, Passif, CAP)

    # 1. CHIFFRE D'AFFAIRES (Classe 70) -> Période
    ca = get_solde("70", mode="periode")
    
    # 2. EBE (Charges et Produits) -> Période
    # Simplification : On prend les classes 6 et 7 de la période
    achats = get_solde("60", sens="debit-credit", mode="periode") 
    charges_ext = get_solde("61", sens="debit-credit", mode="periode") + get_solde("62", sens="debit-credit", mode="periode")
    impots = get_solde("63", sens="debit-credit", mode="periode")
    personnel = get_solde("64", sens="debit-credit", mode="periode")
    
    ebe = get_solde("70", mode="periode") + get_solde("74", mode="periode") - (achats + charges_ext + impots + personnel)

    # 3. RÉSULTAT NET -> Période
    total_produits = get_solde("7", mode="periode")
    total_charges = get_solde("6", sens="debit-credit", mode="periode")
    resultat_net = total_produits - total_charges

    # 4. CAF -> Période
    dotations = get_solde("68", sens="debit-credit", mode="periode")
    reprises = get_solde("78", mode="periode")
    caf = resultat_net + dotations - reprises

    # 5. BFR -> Cumulative (Position au date_end)
    stocks = get_solde("3", sens="debit-credit", mode="cumulative")
    creances = get_solde("411", sens="debit-credit", mode="cumulative") + get_solde("409", sens="debit-credit", mode="cumulative") + get_solde("418", sens="debit-credit", mode="cumulative")
    dettes_fournisseurs = get_solde("401", mode="cumulative") + get_solde("408", mode="cumulative") + get_solde("419", mode="cumulative")
    bfr = stocks + creances - dettes_fournisseurs 

    # 6. LEVERAGE (Dettes 16 / EBE)
    # Dettes = Cumulative, EBE = Période
    endettement = get_solde("16", mode="cumulative") 
    
    leverage = Decimal("0.00")
    if ebe != 0:
        leverage = endettement / ebe

    # 7. TRÉSORERIE NETTE
    # Classe 5 : Cumulative (Position réelle à l'instant T)
    tresorerie = get_solde("5", sens="debit-credit", mode="cumulative")

    # 8. RATIOS DIVERS
    remboursement_k = get_solde("164", sens="debit", mode="periode") + get_solde("168", sens="debit", mode="periode")
    frais_fi = get_solde("661", sens="debit-credit", mode="periode")
    annuite = remboursement_k + frais_fi
    ratio_annuite_caf = Decimal("0")
    if caf != 0:
        ratio_annuite_caf = annuite / caf

    # Dette LMT vs CAF -> Dette = Cumulative, CAF = Période
    dette_lmt = get_solde("16", mode="cumulative")
    ratio_dette_caf = Decimal("0")
    if caf != 0:
        ratio_dette_caf = dette_lmt / caf

    ratio_marge_nette = Decimal("0")
    if ca != 0:
        ratio_marge_nette = (resultat_net / ca) * 100

    ratio_fi_ebe = Decimal("0")
    if ebe != 0:
        ratio_fi_ebe = frais_fi / ebe
        
    ratio_fi_ca = Decimal("0")
    if ca != 0:
        ratio_fi_ca = frais_fi / ca

    # Capitaux Propres = Cumulative
    capitaux_propres_base = sum(get_solde(str(c), mode="cumulative") for c in range(101, 107))
    # Approximation : Fonds propres = Capital historique (cumulative) + résultat de la période
    fonds_propres = capitaux_propres_base + resultat_net
    
    ratio_gearing = Decimal("0")
    if fonds_propres != 0:
        ratio_gearing = dette_lmt / fonds_propres

    # 9. TOTAL BALANCE (Total Actif pour ROA)
    # On prend le total des débits du bilan (Classes 1 à 5) cumulativement
    total_balance = get_solde("", sens="debit", mode="cumulative")

    
    # --- ASSEMBLAGE RÉPONSE ---
    return Response({
        "ca": ca,
        "ebe": ebe,
        "resultat_net": resultat_net,
        "caf": caf,
        "bfr": bfr,
        "leverage": leverage.quantize(Decimal("0.01")),
        "tresorerie": tresorerie,
        "total_balance": total_balance,
        "ratios": {
            "annuite_caf": {
                 "value": ratio_annuite_caf.quantize(Decimal("0.01")),
                 "alerte": ratio_annuite_caf > Decimal("0.50")
            },
            "dette_caf": {
                 "value": ratio_dette_caf.quantize(Decimal("0.01")),
                 "alerte": ratio_dette_caf >= Decimal("3.50")
            },
            "marge_nette": {
                "value": ratio_marge_nette.quantize(Decimal("0.01")), 
            },
            "fi_ebe": {
                "value": ratio_fi_ebe.quantize(Decimal("0.01")),
                "alerte": ratio_fi_ebe >= Decimal("0.30")
            },
            "fi_ca": {
                 "value": ratio_fi_ca, 
                 "alerte": ratio_fi_ca >= Decimal("0.05")
            },
            "gearing": {
                 "value": ratio_gearing.quantize(Decimal("0.01")),
                 "alerte": ratio_gearing >= Decimal("1.3")
            },
            "leverage": {
                 "value": leverage.quantize(Decimal("0.01")),
                 "alerte": leverage >= Decimal("3.5")
            }
        },
        # --- DETAILS MANQUANTS ---
        "roe_data": {
            "roe": (resultat_net / fonds_propres * 100) if fonds_propres != 0 else 0,
            "resultat_net": resultat_net,
            "fonds_propres": fonds_propres,
            "variation": None
        },
        "roa_data": {
             "roa": (resultat_net / total_balance * 100) if total_balance != 0 else 0,
             "resultat_net": resultat_net,
             "total_actif": total_balance,
             "variation": None
        },
        "gearing_data": {
            "gearing": ratio_gearing,
            "dettes_financieres": dette_lmt,
            "fonds_propres": fonds_propres,
            "variation": None
        },
        "rotation_stock_data": {
            "rotation_stock": (get_solde("60", sens="debit-credit") / get_solde("3", sens="debit-credit")) if get_solde("3", sens="debit-credit") != 0 else 0,
            "cout_ventes": get_solde("60", sens="debit-credit"),
            "stocks": get_solde("3", sens="debit-credit"),
            "variation": None
        },
        "marge_operationnelle_data": {
            "marge_operationnelle": ((ebe / ca) * 100) if ca != 0 else 0,
            "chiffre_affaire": ca,
            "variation": None
        },
        "variations": {
            "ca": None, "caf": None, "ebe": None, "leverage": None, "bfr": None, "marge_brute": None, "marge_nette": None, "tresorerie": None
        }
    })




@extend_schema(responses={200: JournalDateRangeSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def journal_date_range_view(request):
    """
    Retourne la plage de dates (min, max) des écritures comptables.
    """
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)
    agg = Journal.objects.filter(project_id=project_id).aggregate(min_date=Min('date'), max_date=Max('date'))
    
    return Response({
        "min_date": agg['min_date'],
        "max_date": agg['max_date']
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: BalanceSerializer(many=True)}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def balance_generale_view(request):
    """
    Retourne la balance générale (agrégée par compte) pour une plage de dates.
    🔥 UTILISE LA TABLE BALANCE (V2)
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)
    
    # On filtre sur Balance qui est déjà agrégé par compte/date
    from compta.models import Balance
    qs = Balance.objects.filter(project_id=project_id)

    try:
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])

        # Même si c'est déjà agrégé dans Balance, si on a plusieurs dates dans la plage,
        # il faut ré-agréger par numéro_compte
        balance_lines = qs.values("numero_compte").annotate(
            total_debit=Sum("total_debit"),
            total_credit=Sum("total_credit"),
            libelle=Max("libelle") 
        ).order_by("numero_compte")

        results = []
        for line in balance_lines:
            d = line["total_debit"]
            if d is None: d = Decimal("0.00")
            
            c = line["total_credit"]
            if c is None: c = Decimal("0.00")
                
            solde = d - c
            
            nature = "Soldé"
            if solde > 0: nature = "Débiteur"
            elif solde < 0: nature = "Créditeur"
            
            results.append({
                "compte": line["numero_compte"],
                "libelle": line["libelle"] or f"Compte {line['numero_compte']}",
                "debit": float(d),
                "credit": float(c),
                "solde": float(abs(solde)),
                "nature": nature
            })

        return Response(results)
    except Exception as e:
        print(f"ERROR in balance_generale_view: {e}")
        return Response({"error": str(e)}, status=500)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: DetteLmtCafSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def dette_lmt_caf_view(request):
    """
    Ratio : Dette LMT / CAF
    """

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)

    def solde(prefix):
        data = GrandLivre.objects.filter(
            project_id=project_id,
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # 🔹 Dette LMT (comptes 16x)
    dette_lmt = solde("16")

    # 🔹 CAF
    def solde_net(prefix):
        data = GrandLivre.objects.filter(
            project_id=project_id,
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    produits = solde_net("7")
    charges_exploitation = sum(solde_net(str(c)) for c in range(60, 66))
    charges_financieres = solde_net("66")
    produits_financiers = solde_net("76")
    charges_exceptionnelles = solde_net("67")
    produits_exceptionnels = solde_net("77")
    impots_benefices = solde_net("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    dotations = solde_net("68")
    reprises = solde_net("78")

    caf = resultat_net + dotations - reprises

    ratio = Decimal("0.00")
    if caf != 0:
        ratio = dette_lmt / caf

    alerte = ratio >= Decimal("3.50")

    payload = {
        "dette_lmt": dette_lmt,
        "caf": caf,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = DetteLmtCafSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MargeNetteSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def resultat_net_ca_view(request):
    """
    Ratio : Résultat net / Chiffre d'affaires
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)

    def solde(prefix):
        qs = GrandLivre.objects.filter(project_id=project_id, numero_compte__startswith=prefix)
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])

        data = qs.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Chiffre d'affaires (70x)
    chiffre_affaire = solde("70")

    # 🔹 Résultat net
    produits = solde("7")
    charges_exploitation = sum(solde(str(c)) for c in range(60, 66))
    charges_financieres = solde("66")
    produits_financiers = solde("76")
    charges_exceptionnelles = solde("67")
    produits_exceptionnels = solde("77")
    impots = solde("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots
    )

    ratio = Decimal("0.00")
    ratio_pourcent = Decimal("0.00")

    if chiffre_affaire != 0:
        ratio = resultat_net / chiffre_affaire
        ratio_pourcent = ratio * Decimal("100")

    payload = {
        "resultat_net": resultat_net,
        "chiffre_affaire": chiffre_affaire,
        "ratio": ratio.quantize(Decimal("0.0001")),
        "ratio_pourcent": ratio_pourcent.quantize(Decimal("0.01")),
    }

    serializer = MargeNetteSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: ChargeEbeSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def charge_ebe_view(request):
    """
    Ratio : Charge financière / EBE
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)

    def solde(prefix):
        qs = GrandLivre.objects.filter(project_id=project_id, numero_compte__startswith=prefix)
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])

        data = qs.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Charge financière (661)
    charge_financiere = solde("661")

    # 🔹 EBE
    ca = solde("7")
    subventions = solde("74")
    achats = solde("60")
    charges_ext = solde("61") + solde("62")
    impots = solde("63")
    personnel = solde("64")

    ebe = ca + subventions - achats - charges_ext - impots - personnel

    ratio = Decimal("0.00")
    if ebe != 0:
        ratio = charge_financiere / ebe

    alerte = ratio >= Decimal("0.30")

    payload = {
        "charge_financiere": charge_financiere,
        "ebe": ebe,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = ChargeEbeSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: ChargeCaSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def charge_ca_view(request):
    """
    Ratio : Charge financière / Chiffre d'affaires
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)

    def solde(prefix):
        qs = GrandLivre.objects.filter(project_id=project_id, numero_compte__startswith=prefix)
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])

        data = qs.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Charge financière
    charge_financiere = solde("661")

    # 🔹 Chiffre d'affaires
    chiffre_affaire = solde("70")

    ratio = Decimal("0.00")
    if chiffre_affaire != 0:
        ratio = charge_financiere / chiffre_affaire

    alerte = ratio >= Decimal("0.05")  # 5%

    payload = {
        "charge_financiere": charge_financiere,
        "chiffre_affaire": chiffre_affaire,
        "ratio": ratio.quantize(Decimal("0.02")),
        "alerte": alerte
    }

    serializer = ChargeCaSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MargeEndettementSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def marge_endettement_view(request):
    """
    Ratio : Dette CMLT / Fonds Propres
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, 'project_id', None)

    def solde(prefix):
        qs = GrandLivre.objects.filter(project_id=project_id, numero_compte__startswith=prefix)
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])
        
        data = qs.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Dette CMLT (16x)
    dette_cmlt = solde("16")

    # 🔹 Fonds Propres (101–106) + Résultat Net
    capitaux_propres_base = sum(solde(str(c)) for c in range(101, 107))
    
    # Résultat Net = Solde(7) + Solde(6) [car solde(6) est négatif]
    resultat_net = solde("7") + solde("6")
    
    fonds_propres = capitaux_propres_base + resultat_net

    ratio = Decimal("0.00")
    if fonds_propres != 0:
        ratio = dette_cmlt / fonds_propres

    alerte = ratio >= Decimal("1.3")

    payload = {
        "dette_cmlt": dette_cmlt,
        "fonds_propres": fonds_propres,
        "ratio": ratio.quantize(Decimal("0.01")),
        "alerte": alerte
    }

    serializer = MargeEndettementSerializer(payload)
    return Response(serializer.data)

@extend_schema(responses={200: JournalDateRangeSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def get_min_journal_date_view(request):
    """
    Retourne la date de la toute première écriture comptable.
    Utile pour initialiser les filtres de date par défaut.
    """
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)
    from django.db.models import Min
    min_date = Journal.objects.filter(project_id=project_id).aggregate(Min('date'))['date__min']
    
    if min_date:
         return Response({"min_date": min_date})
    else:
         # Fallback to current year start if no data
         return Response({"min_date": f"{date.today().year}-01-01"})




@extend_schema(responses={200: TopCompteSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def top_comptes_mouvementes_view(request):
    """
    Retourne les 10 comptes les plus mouvementés en se basant sur le Bilan et le Compte de Résultat.
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    project_id = getattr(request, "project_id", None)

    from django.db.models.functions import Abs

    # 1. Récupérer les comptes du Bilan dans la période
    bilan_qs = Bilan.objects.filter(project_id=project_id)
    if date_start and date_end:
        bilan_qs = bilan_qs.filter(date__range=[date_start, date_end])
    
    bilan_data = bilan_qs.values("numero_compte", "libelle").annotate(
        total_mvt=Sum(Abs("montant_ar"))
    )

    # 2. Récupérer les comptes du Compte de Résultat dans la période
    cr_qs = CompteResultat.objects.filter(project_id=project_id)
    if date_start and date_end:
        cr_qs = cr_qs.filter(date__range=[date_start, date_end])
    
    cr_data = cr_qs.values("numero_compte", "libelle").annotate(
        total_mvt=Sum(Abs("montant_ar"))
    )

    # 3. Fusionner et agréger les deux sources
    combined_data = {}
    
    for item in bilan_data:
        compte = item["numero_compte"]
        if compte not in combined_data:
            combined_data[compte] = {"libelle": item["libelle"], "total": Decimal("0.00")}
        combined_data[compte]["total"] += item["total_mvt"] or Decimal("0.00")

    for item in cr_data:
        compte = item["numero_compte"]
        if compte not in combined_data:
            combined_data[compte] = {"libelle": item["libelle"], "total": Decimal("0.00")}
        combined_data[compte]["total"] += item["total_mvt"] or Decimal("0.00")

    # 4. Trier et prendre le Top 10
    sorted_data = sorted(combined_data.items(), key=lambda x: x[1]["total"], reverse=True)[:10]

    results = []
    for compte, info in sorted_data:
        results.append({
            "compte": compte,
            "libelle": info["libelle"] or f"Compte {compte}",
            "mt_mvt": float(info["total"])
        })

    return Response(results)



@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: RoeRoaSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def roe_view(request):
    """
    Calcul du ROE (Return on Equity) : (Résultat Net / Fonds Propres) * 100
    GET /api/roe/
    GET /api/roe/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    def calculate_roe(start_date, end_date):
        """Fonction helper pour calculer le ROE pour une période donnée"""
        resultat_net = get_resultat_net(project_id, start_date, end_date)
        fonds_propres = get_capitaux_propres(project_id, start_date, end_date)

        if fonds_propres != 0 and abs(fonds_propres) >= 100000:
            roe = (resultat_net / fonds_propres * 100)
            if roe > 1000: roe = 1000
            elif roe < -1000: roe = -1000
        else:
            roe = None
        
        return {
            "resultat_net": resultat_net,
            "fonds_propres": fonds_propres,
            "roe": roe
        }

    # Calcul période courante
    current_data = calculate_roe(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            # Calculer la durée de la période
            delta_days = (end_date_obj - start_date_obj).days
            
            # Déterminer la période précédente
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée : décaler de la même durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            # Calculer ROE période précédente
            previous_data = calculate_roe(previous_start, previous_end)
            
            # Calculer variation
            if current_data["roe"] is not None and previous_data["roe"] is not None:
                variation = current_data["roe"] - previous_data["roe"]
        except:
            pass

    return Response({
        "resultat_net": current_data["resultat_net"],
        "fonds_propres": current_data["fonds_propres"],
        "roe": current_data["roe"],
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: RoeRoaSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def roa_view(request):
    """
    Calcul du ROA (Return on Assets) : (Résultat Net / Total Actif) * 100
    GET /api/roa/
    GET /api/roa/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    def calculate_roa(start_date, end_date):
        """Fonction helper pour calculer le ROA pour une période donnée"""
        resultat_net = get_resultat_net(project_id, start_date, end_date)
        
        # Total Actif unifié via get_latest_bilan_sum
        total_actif = get_latest_bilan_sum(project_id, start_date, end_date, prefix_list=[""], type_bilan="ACTIF")

        if total_actif != 0 and abs(total_actif) >= 100000:
            roa = (resultat_net / total_actif * 100)
            if roa > 1000: roa = 1000
            elif roa < -1000: roa = -1000
        else:
            roa = None
        
        return {
            "resultat_net": resultat_net,
            "total_actif": total_actif,
            "roa": roa
        }

    # Calcul période courante
    current_data = calculate_roa(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            # Calculer la durée de la période
            delta_days = (end_date_obj - start_date_obj).days
            
            # Déterminer la période précédente
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée : décaler de la même durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            # Calculer ROA période précédente
            previous_data = calculate_roa(previous_start, previous_end)
            
            # Calculer variation
            if current_data["roa"] is not None and previous_data["roa"] is not None:
                variation = current_data["roa"] - previous_data["roa"]
        except:
            pass

    return Response({
        "resultat_net": current_data["resultat_net"],
        "total_actif": current_data["total_actif"],
        "roa": current_data["roa"],
        "variation": variation
    })

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: CurrentRatioSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def current_ratio_view(request):
    """
    Calcul du Current Ratio : (Actifs Courants / Passifs Courants)
    GET /api/current-ratio/
    GET /api/current-ratio/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_current_ratio(start_date, end_date):
        """Fonction helper pour calculer le Current Ratio pour une période donnée"""
        from compta.kpi_utils import get_latest_bilan_sum
        
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        
        actifs_courants = get_latest_bilan_sum(
            project_id, start_date, end_date, categorie="ACTIF_COURANTS", type_bilan="ACTIF"
        )

        passifs_courants = get_latest_bilan_sum(
            project_id, start_date, end_date, categorie="PASSIFS_COURANTS", type_bilan="PASSIF"
        )

        current_ratio = (actifs_courants / passifs_courants) if passifs_courants != 0 else None
        
        return {
            "actifs_courants": actifs_courants,
            "passifs_courants": passifs_courants,
            "current_ratio": current_ratio
        }

    # Calcul période courante
    current_data = calculate_current_ratio(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_data = calculate_current_ratio(previous_start, previous_end)
            
            if current_data["current_ratio"] is not None and previous_data["current_ratio"] is not None:
                variation = current_data["current_ratio"] - previous_data["current_ratio"]
        except:
            pass

    payload = {
        "actifs_courants": current_data["actifs_courants"],
        "passifs_courants": current_data["passifs_courants"],
        "current_ratio": current_data["current_ratio"],
        "variation": variation
    }
    
    serializer = CurrentRatioSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: QuickRatioSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def quick_ratio_view(request):
    """
    Calcul du Quick Ratio : ((Actifs Courants - Stocks) / Passifs Courants)
    GET /api/quick-ratio/
    GET /api/quick-ratio/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_quick_ratio(start_date, end_date):
        """Fonction helper pour calculer le Quick Ratio pour une période donnée"""
        from compta.kpi_utils import get_latest_bilan_sum
        
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        
        # Actifs courants
        actifs_courants = get_latest_bilan_sum(
            project_id, start_date, end_date, categorie="ACTIF_COURANTS", type_bilan="ACTIF"
        )

        # Stocks (classe 3)
        stocks = get_latest_bilan_sum(
            project_id, start_date, end_date, prefix_list=["3"], type_bilan="ACTIF"
        )

        # Passifs courants
        passifs_courants = get_latest_bilan_sum(
            project_id, start_date, end_date, categorie="PASSIFS_COURANTS", type_bilan="PASSIF"
        )

        quick_ratio = (
            (actifs_courants - stocks) / passifs_courants
            if passifs_courants != 0
            else None
        )

        return {
            "actifs_courants": actifs_courants,
            "stocks": stocks,
            "passifs_courants": passifs_courants,
            "quick_ratio": quick_ratio
        }

    # Calcul période courante
    current_data = calculate_quick_ratio(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_data = calculate_quick_ratio(previous_start, previous_end)
            
            if current_data["quick_ratio"] is not None and previous_data["quick_ratio"] is not None:
                variation = current_data["quick_ratio"] - previous_data["quick_ratio"]
        except:
            pass

    payload = {
        "actifs_courants": current_data["actifs_courants"],
        "stocks": current_data["stocks"],
        "passifs_courants": current_data["passifs_courants"],
        "quick_ratio": current_data["quick_ratio"],
        "variation": variation
    }
    
    serializer = QuickRatioSerializer(payload)
    return Response(serializer.data)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: GearingSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def gearing_view(request):
    """
    Calcul du Gearing : (Dettes Financières / Fonds Propres) * 100
    GET /api/gearing/
    GET /api/gearing/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_gearing(start_date, end_date):
        """Fonction helper pour calculer le Gearing pour une période donnée"""
        # Dettes financières (classe 16) + Concours bancaires (512 Passif)
        dettes_financieres = get_latest_bilan_sum(
            project_id, start_date, end_date, prefix_list=["16", "512"], type_bilan="PASSIF"
        )

        # Fonds propres unifiés
        fonds_propres = get_capitaux_propres(project_id, start_date, end_date)

        gearing = (
            (dettes_financieres / fonds_propres) * 100
            if fonds_propres != 0
            else None
        )

        return {
            "dettes_financieres": dettes_financieres,
            "fonds_propres": fonds_propres,
            "gearing": gearing
        }

    # Calcul période courante
    current_data = calculate_gearing(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_data = calculate_gearing(previous_start, previous_end)
            
            if current_data["gearing"] is not None and previous_data["gearing"] is not None:
                variation = current_data["gearing"] - previous_data["gearing"]
        except:
            pass

    payload = {
        "dettes_financieres": current_data["dettes_financieres"],
        "fonds_propres": current_data["fonds_propres"],
        "gearing": current_data["gearing"],
        "variation": variation
    }
    
    serializer = GearingSerializer(payload)
    return Response(serializer.data)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: RotationStockSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def rotation_stock_view(request):
    """
    Calcul de la Rotation des stocks : Coût des ventes / Stock moyen
    GET /api/rotation-stock/
    GET /api/rotation-stock/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_rotation_stock(start_date, end_date):
        """Fonction helper pour calculer la Rotation des stocks pour une période donnée"""
        from compta.kpi_utils import get_latest_bilan_sum, get_cr_sum
        
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        
        # Coût des ventes (charges classe 6)
        cout_ventes = get_cr_sum(project_id, start_date, end_date, prefix_list=["6"], nature="CHARGE")

        # Stock moyen (simplifié : stock fin de période)
        stocks = get_latest_bilan_sum(
            project_id, start_date, end_date, prefix_list=["3"], type_bilan="ACTIF"
        )

        rotation_stock = (
            cout_ventes / stocks
            if stocks != 0
            else None
        )

        duree_stock = (
            Decimal("365") / rotation_stock
            if rotation_stock and rotation_stock != 0
            else None
        )

        return {
            "cout_ventes": cout_ventes,
            "stocks": stocks,
            "rotation_stock": rotation_stock,
            "duree_stock_jours": duree_stock
        }

    # Calcul période courante
    current_data = calculate_rotation_stock(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_data = calculate_rotation_stock(previous_start, previous_end)
            
            if current_data["rotation_stock"] is not None and previous_data["rotation_stock"] is not None:
                variation = current_data["rotation_stock"] - previous_data["rotation_stock"]
        except:
            pass

    payload = {
        "cout_ventes": current_data["cout_ventes"],
        "stocks": current_data["stocks"],
        "rotation_stock": current_data["rotation_stock"],
        "duree_stock_jours": current_data["duree_stock_jours"],
        "variation": variation
    }
    
    serializer = RotationStockSerializer(payload)
    return Response(serializer.data)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MargeOperationnelleSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def marge_operationnelle_view(request):
    """
    Calcul de la Marge opérationnelle : (Résultat opérationnel / CA) * 100
    GET /api/marge-operationnelle/
    GET /api/marge-operationnelle/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_marge_operationnelle(start_date, end_date):
        """Fonction helper pour calculer la Marge opérationnelle pour une période donnée"""
        from compta.kpi_utils import get_cr_sum
        
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        
        # CA = Comptes 70, 71, 72 (PRODUITS uniquement)
        chiffre_affaire = get_cr_sum(project_id, start_date, end_date, prefix_list=["70", "71", "72"], nature="PRODUIT")

        # Produits opérationnels = 70 + 71 + 72 + 74 + 75 (PRODUITS uniquement)
        produits_operationnels = get_cr_sum(project_id, start_date, end_date, prefix_list=["70", "71", "72", "74", "75"], nature="PRODUIT")

        # Charges d'exploitation = 60 + 61 + 62 + 63 + 64 + 65 (CHARGES uniquement)
        charges_exploitation = get_cr_sum(project_id, start_date, end_date, prefix_list=["60", "61", "62", "63", "64", "65"], nature="CHARGE")

        # Résultat opérationnel = Produits opérationnels - Charges d'exploitation
        res_op = produits_operationnels - charges_exploitation

        # Calcul de la marge opérationnelle : (Résultat opérationnel / CA) * 100
        # On évite d'afficher des valeurs aberrantes si les charges écrasent totalement le CA.
        marge_operationnelle = None
        if chiffre_affaire != 0 and abs(chiffre_affaire) >= 1000:
            raw_marge = (res_op / chiffre_affaire) * 100

            # Si l'écart est extrême (charges >> CA), on retourne None pour signaler un ratio non pertinent
            if abs(res_op) > abs(chiffre_affaire) * 20:
                marge_operationnelle = None
            else:
                # Plafonner les valeurs aberrantes
                if raw_marge > 1000:
                    marge_operationnelle = 1000
                elif raw_marge < -1000:
                    marge_operationnelle = -1000
                else:
                    marge_operationnelle = raw_marge

        return {
            "chiffre_affaire": chiffre_affaire,
            "charges_exploitation": charges_exploitation,
            "resultat_operationnel": res_op,
            "marge_operationnelle": marge_operationnelle
        }

    # Calcul période courante
    current_data = calculate_marge_operationnelle(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous_data = calculate_marge_operationnelle(previous_start, previous_end)
            
            if current_data["marge_operationnelle"] is not None and previous_data["marge_operationnelle"] is not None:
                variation = current_data["marge_operationnelle"] - previous_data["marge_operationnelle"]
        except:
            pass

    payload = {
        "chiffre_affaire": current_data["chiffre_affaire"],
        "charges_exploitation": current_data["charges_exploitation"],
        "resultat_operationnel": current_data["resultat_operationnel"],
        "marge_operationnelle": current_data["marge_operationnelle"],
        "variation": variation
    }
    
    serializer = MargeOperationnelleSerializer(payload)
    return Response(serializer.data)

@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: JournalRepartitionSerializer(many=True)}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def repartition_produits_charges_view(request):
    """
    Retourne 3 jeux de données pour les camemberts:
    1. Top 5 des produits par catégorie
    2. Top 5 des charges par catégorie
    3. Total Produits vs Total Charges
    GET /api/repartition-resultat/?date_start=2025-01-01&date_end=2025-12-31
    """
    from django.db.models import Q

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)
    filters = {"project_id": project_id}
    if date_start and date_end:
        filters["date__range"] = [date_start, date_end]

    # === TOP 5 PRODUITS PAR CATÉGORIE ===
    produits_data = (
        CompteResultat.objects
        .filter(nature="PRODUIT", **filters)
        .values("numero_compte", "libelle")
        .annotate(total=Sum("montant_ar"))
        .order_by("-total")[:5]
    )
    
    produits_list = [
        {
            "label": item["libelle"] or f"Compte {item['numero_compte']}",
            "montant": float(item["total"] or 0),
        }
        for item in produits_data
    ]

    # === TOP 5 CHARGES PAR CATÉGORIE ===
    charges_data = (
        CompteResultat.objects
        .filter(nature="CHARGE", **filters)
        .values("numero_compte", "libelle")
        .annotate(total=Sum("montant_ar"))
        .order_by("-total")[:5]
    )
    
    charges_list = [
        {
            "label": item["libelle"] or f"Compte {item['numero_compte']}",
            "montant": float(item["total"] or 0),
        }
        for item in charges_data
    ]

    # === TOTAL PRODUITS VS CHARGES ===
    total_produits = (
        CompteResultat.objects
        .filter(nature="PRODUIT", **filters)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    total_charges = (
        CompteResultat.objects
        .filter(nature="CHARGE", **filters)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    comparison = [
        {
            "label": "Produits",
            "montant": float(total_produits),
        },
        {
            "label": "Charges",
            "montant": float(total_charges),
        }
    ]

    return Response({
        "produits": produits_list,
        "charges": charges_list,
        "comparison": comparison
    })






@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: BilanKpiResponseSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def bilan_kpis_with_variations_view(request):
    """
    Calcule tous les KPIs du Bilan avec variations par rapport à la période précédente.
    Optimisé pour une seule requête par période.
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    from dateutil.relativedelta import relativedelta
    from datetime import datetime
    from django.db.models import Case, When, Q
    
    date_start_str = request.GET.get('date_start')
    date_end_str = request.GET.get('date_end')
    
    if not date_start_str or not date_end_str:
        return Response({"error": "date_start et date_end sont requis"}, status=400)
    
    try:
        current_start = datetime.strptime(date_start_str, '%Y-%m-%d').date()
        current_end = datetime.strptime(date_end_str, '%Y-%m-%d').date()
        
        # Détermination de la période précédente
        delta_days = (current_end - current_start).days
        
        if 28 <= delta_days <= 32:  # Mensuel
            previous_start = current_start - relativedelta(months=1)
            previous_end = current_end - relativedelta(months=1)
        elif 88 <= delta_days <= 92:  # Trimestriel
            previous_start = current_start - relativedelta(months=3)
            previous_end = current_end - relativedelta(months=3)
        elif delta_days >= 360:  # Annuel
            previous_start = current_start - relativedelta(years=1)
            previous_end = current_end - relativedelta(years=1)
        else:  # Fallback
            duration = current_end - current_start
            previous_end = current_start - relativedelta(days=1)
            previous_start = previous_end - duration
            
    except ValueError:
        return Response({"error": "Format de date invalide (YYYY-MM-DD)"}, status=400)
    
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    def calculate_bilan_kpis(d_start, d_end):
        """Calcule tous les KPIs du Bilan en une seule requête optimisée"""
        qs = Bilan.objects.filter(project_id=project_id, date__range=[d_start, d_end])
        
        # Agrégation conditionnelle pour tous les montants
        data = qs.aggregate(
            # Actif Courant
            actif_courant=Sum(Case(
                When(Q(categorie__icontains='ACTIF') & Q(categorie__icontains='COURANT'), then='montant_ar'),
                default=0,
                output_field=DecimalField()
            )),
            # Actif Non Courant
            actif_non_courant=Sum(Case(
                When(Q(categorie__icontains='ACTIF') & ~Q(categorie__icontains='COURANT'), then='montant_ar'),
                default=0,
                output_field=DecimalField()
            )),
            # Passif Courant
            passif_courant=Sum(Case(
                When(Q(categorie__icontains='PASSIF') & Q(categorie__icontains='COURANT'), then='montant_ar'),
                default=0,
                output_field=DecimalField()
            )),
            # Passif Non Courant
            passif_non_courant=Sum(Case(
                When(Q(categorie__icontains='PASSIF') & ~Q(categorie__icontains='COURANT'), then='montant_ar'),
                default=0,
                output_field=DecimalField()
            )),
            # Capitaux Propres (comptes de la catégorie CAPITAUX_PROPRES)
            capitaux_propres_bilan=Sum(Case(
                When(categorie__icontains='CAPITAUX', then='montant_ar'),
                default=0,
                output_field=DecimalField()
            )),
        )
        
        # Calcul du Résultat Net de la période (depuis CompteResultat)
        cr_data = CompteResultat.objects.filter(project_id=project_id, date__range=[d_start, d_end]).aggregate(
            produits=Sum(Case(When(nature='PRODUIT', then='montant_ar'), default=0, output_field=DecimalField())),
            charges=Sum(Case(When(nature='CHARGE', then='montant_ar'), default=0, output_field=DecimalField())),
        )
        
        resultat_net = (cr_data['produits'] or Decimal('0.00')) - (cr_data['charges'] or Decimal('0.00'))
        
        # Capitaux Propres = Comptes CAPITAUX_PROPRES + Résultat Net de la période
        capitaux_propres_total = (data['capitaux_propres_bilan'] or Decimal('0.00')) + resultat_net
        
        # Calcul du ratio d'endettement
        total_dettes = (data['passif_courant'] or Decimal('0.00')) + (data['passif_non_courant'] or Decimal('0.00'))
        ratio_endettement = (total_dettes / capitaux_propres_total * 100) if capitaux_propres_total != 0 else Decimal('0.00')
        
        return {
            'actif_courant': data['actif_courant'] or Decimal('0.00'),
            'actif_non_courant': data['actif_non_courant'] or Decimal('0.00'),
            'passif_courant': data['passif_courant'] or Decimal('0.00'),
            'passif_non_courant': data['passif_non_courant'] or Decimal('0.00'),
            'capitaux_propres': capitaux_propres_total,
            'ratio_endettement': ratio_endettement,
            'produits': cr_data['produits'] or Decimal('0.00'),
            'charges': cr_data['charges'] or Decimal('0.00'),
        }
    
    # Calcul période actuelle et précédente
    current_kpis = calculate_bilan_kpis(current_start, current_end)
    previous_kpis = calculate_bilan_kpis(previous_start, previous_end)
    
    # Calcul des variations (en montant absolu, pas en pourcentage)
    variations = {
        'actif_courant': current_kpis['actif_courant'] - previous_kpis['actif_courant'],
        'actif_non_courant': current_kpis['actif_non_courant'] - previous_kpis['actif_non_courant'],
        'passif_courant': current_kpis['passif_courant'] - previous_kpis['passif_courant'],
        'passif_non_courant': current_kpis['passif_non_courant'] - previous_kpis['passif_non_courant'],
        'capitaux_propres': current_kpis['capitaux_propres'] - previous_kpis['capitaux_propres'],
        'ratio_endettement': current_kpis['ratio_endettement'] - previous_kpis['ratio_endettement'],  # Variation en points
    }
    
    # Assemblage de la réponse
    response_data = {
        'current': {
            'actif_courant': float(current_kpis['actif_courant']),
            'actif_non_courant': float(current_kpis['actif_non_courant']),
            'passif_courant': float(current_kpis['passif_courant']),
            'passif_non_courant': float(current_kpis['passif_non_courant']),
            'capitaux_propres': float(current_kpis['capitaux_propres']),
            'ratio_endettement': float(current_kpis['ratio_endettement']),
            'produits': float(current_kpis['produits']),
            'charges': float(current_kpis['charges']),
        },
        'previous': {
            'actif_courant': float(previous_kpis['actif_courant']),
            'actif_non_courant': float(previous_kpis['actif_non_courant']),
            'passif_courant': float(previous_kpis['passif_courant']),
            'passif_non_courant': float(previous_kpis['passif_non_courant']),
            'capitaux_propres': float(previous_kpis['capitaux_propres']),
            'ratio_endettement': float(previous_kpis['ratio_endettement']),
        },
        'variations': {
            'actif_courant': float(variations['actif_courant']),
            'actif_non_courant': float(variations['actif_non_courant']),
            'passif_courant': float(variations['passif_courant']),
            'passif_non_courant': float(variations['passif_non_courant']),
            'capitaux_propres': float(variations['capitaux_propres']),
            'ratio_endettement': float(variations['ratio_endettement']),
        }
    }
    
    return Response(response_data)



@extend_schema(responses={200: serializers.ListField(child=serializers.IntegerField())})
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def get_available_years_view(request):
    """
    Retourne la liste des années disponibles dans les écritures comptables,
    le Bilan et le Compte de Résultat pour le projet sélectionné.
    Triées par ordre décroissant (plus récent en premier).
    """
    from django.db.models.functions import ExtractYear
    from datetime import date
    
    # 1. Filtre par PROJET (STRICT)
    project_id = getattr(request, 'project_id', None)
    
    # 1. Années du Journal des écritures
    journal_years = set(
        Journal.objects.filter(project_id=project_id)
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )
    
    # 2. Années du Bilan
    bilan_years = set(
        Bilan.objects.filter(project_id=project_id)
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )

    # 3. Années du Compte de Résultat
    cr_years = set(
        CompteResultat.objects.filter(project_id=project_id)
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )

    # Fusion des ensembles pour éviter les doublons
    all_years_set = journal_years | bilan_years | cr_years
    
    # Filtrer les None éventuels, convertir en liste et trier
    available_years = sorted([y for y in all_years_set if y is not None], reverse=True)
    
    # Si vide, retourner l'année en cours par défaut (comportement d'origine)
    if not available_years:
        available_years = [date.today().year]
        
    return Response(available_years)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: TVASerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def tva_view(request):
    """
    Calcul de la TVA (TVA collectée, TVA déductible, TVA nette) avec variation par rapport à la période précédente
    GET /api/tva/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_tva(start_date, end_date):
        """Fonction helper pour calculer la TVA pour une période donnée"""
        filters = {}
        project_id = getattr(request, "project_id", None)
        if project_id:
            filters["project_id"] = project_id
            
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        # TVA collectée (compte 4457) - modèle Bilan
        tva_collectee = (
            Bilan.objects
            .filter(numero_compte__startswith="4457", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        # TVA déductible (compte 4456) - modèle Bilan
        tva_deductible = (
            Bilan.objects
            .filter(numero_compte__startswith="4456", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        
        # TVA nette = TVA collectée - TVA déductible
        tva_nette = tva_collectee - tva_deductible
        
        return {
            "tva_collectee": tva_collectee,
            "tva_deductible": tva_deductible,
            "tva_nette": tva_nette
        }

    # Calcul période courante
    current_tva = calculate_tva(date_start, date_end)
    
    # Calcul période précédente et variation
    variation_collectee = None
    variation_deductible = None
    variation_nette = None
    
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            # Calculer la durée de la période
            delta_days = (end_date_obj - start_date_obj).days
            
            # Déterminer la période précédente
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:  # Autre durée : décaler de la même durée
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            # Calculer TVA période précédente
            previous_tva = calculate_tva(previous_start, previous_end)
            
            # Calculer variations en pourcentage
            # TVA collectée
            if previous_tva["tva_collectee"] != 0 and abs(previous_tva["tva_collectee"]) > 10000:
                variation_collectee = ((current_tva["tva_collectee"] - previous_tva["tva_collectee"]) / abs(previous_tva["tva_collectee"])) * 100
                if variation_collectee > 1000:
                    variation_collectee = 1000
                elif variation_collectee < -1000:
                    variation_collectee = -1000
            
            # TVA déductible
            if previous_tva["tva_deductible"] != 0 and abs(previous_tva["tva_deductible"]) > 10000:
                variation_deductible = ((current_tva["tva_deductible"] - previous_tva["tva_deductible"]) / abs(previous_tva["tva_deductible"])) * 100
                if variation_deductible > 1000:
                    variation_deductible = 1000
                elif variation_deductible < -1000:
                    variation_deductible = -1000
            
            # TVA nette
            if previous_tva["tva_nette"] != 0 and abs(previous_tva["tva_nette"]) > 10000:
                variation_nette = ((current_tva["tva_nette"] - previous_tva["tva_nette"]) / abs(previous_tva["tva_nette"])) * 100
                if variation_nette > 1000:
                    variation_nette = 1000
                elif variation_nette < -1000:
                    variation_nette = -1000
        except:
            pass

    return Response({
        "tva_collectee": current_tva["tva_collectee"],
        "tva_deductible": current_tva["tva_deductible"],
        "tva_nette": current_tva["tva_nette"],
        "variation_collectee": variation_collectee,
        "variation_deductible": variation_deductible,
        "variation_nette": variation_nette
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: MonthlyEvolutionDataSerializer(many=True)}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def amortissements_exercice_view(request):
    """
    Retourne les amortissements (compte 68) par mois.
    GET /api/amortissements-exercice/?date_start=2024-01-01&date_end=2024-12-31
    Par défaut: 6 derniers mois
    """
    from django.db.models.functions import TruncMonth
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    
    # Si pas de dates spécifiées, utiliser les 6 derniers mois
    if not date_start or not date_end:
        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=6)
        date_start = start_date.strftime('%Y-%m-%d')
        date_end = end_date.strftime('%Y-%m-%d')
    
    # PROJECT FILTER
    project_id = getattr(request, "project_id", None)

    # Filtrer et grouper par mois
    amortissements = (
        CompteResultat.objects
        .filter(
            project_id=project_id,
            nature="CHARGE", 
            numero_compte__startswith="68",
            date__range=[date_start, date_end]
        )
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total_amortissement=Sum('montant_ar'))
        .order_by('month')
    )
    
    # Formater les données pour le graphique
    data = [
        {
            "month": item['month'].strftime('%Y-%m') if item['month'] else '',
            "amount": float(item['total_amortissement'] or 0)
        }
        for item in amortissements if item['month'] is not None
    ]
    
    return Response(data)


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: DelaisClientsSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def delais_clients_view(request):
    """
    Calcul des délais clients (DSO - Days Sales Outstanding) avec variation
    Formule: (Créances Clients / CA) × Nombre de jours
    GET /api/delais-clients/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_delais_clients(start_date, end_date):
        """Fonction helper pour calculer les délais clients"""
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        filters = {"project_id": project_id}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
            
            # Calculer le nombre de jours dans la période
            start_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            nb_jours = (end_obj - start_obj).days + 1
        else:
            nb_jours = 365  # Par défaut

        # Créances clients (compte 411 du Bilan)
        creances_clients = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="411", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Chiffre d'affaires (comptes 70x du Compte de Résultat)
        chiffre_affaire = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="70", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Calcul DSO
        if chiffre_affaire > 0:
            delais_jours = (creances_clients / chiffre_affaire) * nb_jours
        else:
            delais_jours = None

        return {
            "creances_clients": creances_clients,
            "chiffre_affaire": chiffre_affaire,
            "delais_jours": delais_jours
        }

    # Calcul période courante
    current = calculate_delais_clients(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous = calculate_delais_clients(previous_start, previous_end)
            
            # Calculer variation en jours
            if current["delais_jours"] is not None and previous["delais_jours"] is not None:
                variation = float(current["delais_jours"] - previous["delais_jours"])
        except:
            pass

    return Response({
        "creances_clients": current["creances_clients"],
        "chiffre_affaire": current["chiffre_affaire"],
        "delais_jours": float(current["delais_jours"]) if current["delais_jours"] is not None else None,
        "variation": variation
    })


@extend_schema(
    parameters=[
        OpenApiParameter("date_start", type=str, description="Date de début (YYYY-MM-DD)"),
        OpenApiParameter("date_end", type=str, description="Date de fin (YYYY-MM-DD)"),
    ],
    responses={200: DelaisFournisseursSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def delais_fournisseurs_view(request):
    """
    Calcul des délais fournisseurs (DPO - Days Payable Outstanding) avec variation
    Formule: (Dettes Fournisseurs / Achats) × Nombre de jours
    GET /api/delais-fournisseurs/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_delais_fournisseurs(start_date, end_date):
        """Fonction helper pour calculer les délais fournisseurs"""
        # PROJECT FILTER
        project_id = getattr(request, "project_id", None)
        filters = {"project_id": project_id}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
            
            # Calculer le nombre de jours dans la période
            start_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            nb_jours = (end_obj - start_obj).days + 1
        else:
            nb_jours = 365  # Par défaut

        # Dettes fournisseurs (compte 401 du Bilan)
        dettes_fournisseurs = (
            Bilan.objects
            .filter(type_bilan="PASSIF", numero_compte__startswith="401", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Achats (comptes 60x du Compte de Résultat)
        achats = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="60", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Calcul DPO
        if achats > 0:
            delais_jours = (dettes_fournisseurs / achats) * nb_jours
        else:
            delais_jours = None

        return {
            "dettes_fournisseurs": dettes_fournisseurs,
            "achats": achats,
            "delais_jours": delais_jours
        }

    # Calcul période courante
    current = calculate_delais_fournisseurs(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end:
        try:
            start_date_obj = datetime.strptime(date_start, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(date_end, '%Y-%m-%d').date()
            
            delta_days = (end_date_obj - start_date_obj).days
            
            if delta_days >= 360:  # Annuel
                previous_start = (start_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(years=1)).strftime('%Y-%m-%d')
            elif 28 <= delta_days <= 32:  # Mensuel
                previous_start = (start_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=1)).strftime('%Y-%m-%d')
            elif 88 <= delta_days <= 92:  # Trimestriel
                previous_start = (start_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
                previous_end = (end_date_obj - relativedelta(months=3)).strftime('%Y-%m-%d')
            else:
                previous_end = (start_date_obj - relativedelta(days=1)).strftime('%Y-%m-%d')
                previous_start = (start_date_obj - relativedelta(days=delta_days + 1)).strftime('%Y-%m-%d')
            
            previous = calculate_delais_fournisseurs(previous_start, previous_end)
            
            # Calculer variation en jours
            if current["delais_jours"] is not None and previous["delais_jours"] is not None:
                variation = float(current["delais_jours"] - previous["delais_jours"])
        except:
            pass

    return Response({
        "dettes_fournisseurs": current["dettes_fournisseurs"],
        "achats": current["achats"],
        "delais_jours": float(current["delais_jours"]) if current["delais_jours"] is not None else None,
        "variation": variation
    })


# =========================================================
# GESTION DES PROJETS (MULTI-TENANT)
# =========================================================

class ProjectListCreateView(generics.ListCreateAPIView):
    """
    GET: Liste tous les projets (Admin) ou les projets accessibles (User)
    POST: Crée un nouveau projet (Admin ou User, l'utilisateur devient créateur et admin du projet)
    """

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return ProjectListSerializer
        return ProjectSerializer

    def get_queryset(self):
        user = self.request.user
        return Project.objects.filter(is_active=True).prefetch_related('user_accesses')

    def perform_create(self, serializer):
        project = serializer.save(created_by=self.request.user)
        # Créer automatiquement l'accès admin pour le créateur
        ProjectAccess.objects.create(
            user=self.request.user,
            project=project,
            status='approved',
            approved_by=self.request.user, # Auto-approuvé
            approved_at=datetime.now()
        )
class ProjectDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET, PUT, DELETE un projet spécifique.
    Nécessite d'être admin ou d'avoir accès au projet.
    """
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]
    lookup_field = 'id'

    def perform_destroy(self, instance):
        """
        Suppression optimisée et sécurisée d'un projet.
        Utilise des suppressions par bloc pour éviter l'explosion des signaux Django.
        """
        from compta.models import Journal, Balance, GrandLivre, Bilan, CompteResultat
        from chatbot.models import AccountingIndex, MessageHistory, Document
        
        project_id = instance.id
        print(f"[INFO] Démarrage suppression rapide du Projet {project_id}...")
        
        # 1. Suppression brutale des données comptables (bypasse les signaux Journal)
        # On utilise .delete() sur le QuerySet, ce qui est une opération SQL unique
        Journal.objects.filter(project_id=project_id).delete()
        GrandLivre.objects.filter(project_id=project_id).delete()
        Balance.objects.filter(project_id=project_id).delete()
        Bilan.objects.filter(project_id=project_id).delete()
        CompteResultat.objects.filter(project_id=project_id).delete()
        
        # 2. Nettoyage Chatbot & RAG
        AccountingIndex.objects.filter(project_id=project_id).delete()
        MessageHistory.objects.filter(project_id=project_id).delete()
        
        # 3. Documents (attention aux fichiers physiques)
        docs = Document.objects.filter(project_id=project_id)
        for doc in docs:
            doc.delete() # On garde le .delete() individuel pour Document car il y a peu de fichiers et on veut supprimer le disque
            
        # 4. Enfin, supprimer le projet lui-même
        instance.delete()
        print(f"[SUCCESS] Projet {project_id} supprimé avec succès.")

class UserProjectsView(generics.ListAPIView):
    """
    Liste les projets accessibles pour l'utilisateur connecté avec leur statut.
    Utilisé pour la page de sélection de projet.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectListSerializer

    def get_queryset(self):
        # On retourne tous les projets actifs pour que l'utilisateur puisse demander l'accès
        # Le serializer enrichira avec le statut d'accès
        return Project.objects.filter(is_active=True).prefetch_related('user_accesses')

class ProjectAccessRequestView(generics.CreateAPIView):
    """
    Demande d'accès à un projet.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectAccessSerializer

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project_id')
        if not project_id:
            return Response({"error": "project_id requis"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

        # Vérifier si demande existe déjà
        existing_access = ProjectAccess.objects.filter(user=request.user, project=project).first()
        
        if existing_access:
            if existing_access.status == 'approved':
                return Response({"error": "Accès déjà accordé"}, status=status.HTTP_400_BAD_REQUEST)
            elif existing_access.status == 'pending':
                return Response({"error": "Demande déjà en attente"}, status=status.HTTP_400_BAD_REQUEST)
            elif existing_access.status == 'rejected':
                # Re-passer à pending pour redemander
                existing_access.status = 'pending'
                existing_access.save()
                return Response(ProjectAccessSerializer(existing_access).data, status=status.HTTP_200_OK)

        access = ProjectAccess.objects.create(
            user=request.user,
            project=project,
            status='pending'
        )
        
        return Response(ProjectAccessSerializer(access).data, status=status.HTTP_201_CREATED)

class ManageAccessRequestsView(generics.ListAPIView):
    """
    Admin: Liste les demandes en attente et permet d'approuver/rejeter.
    """
    permission_classes = [IsAuthenticated] # Devrait être AdminOnly idéalement
    serializer_class = ProjectAccessSerializer

    def get_queryset(self):
        user = self.request.user
        if not (user.is_superuser or user.role == 'admin'):
            # Si pas admin global, peut-être admin d'un projet ? (Implémentation future)
            return ProjectAccess.objects.none()
            
        status_filter = self.request.query_params.get('status', 'pending')
        return ProjectAccess.objects.filter(status=status_filter)

    @extend_schema(
        request=serializers.Serializer, # Technical placeholder or specific one
        responses={200: ProjectAccessSerializer}
    )
    def post(self, request):
        """Approuver ou rejeter une demande"""
        access_id = request.data.get('access_id')
        action = request.data.get('action') # 'approve' or 'reject'
        
        if not access_id or not action:
             return Response({"error": "access_id et action requis"}, status=status.HTTP_400_BAD_REQUEST)
             
        try:
            access = ProjectAccess.objects.get(id=access_id)
        except ProjectAccess.DoesNotExist:
            return Response({"error": "Demande introuvable"}, status=status.HTTP_404_NOT_FOUND)

        user = self.request.user
        if not (user.is_superuser or user.role == 'admin'):
             return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        if action == 'approve':
            access.status = 'approved'
            access.approved_by = user
            access.approved_at = datetime.now()
            access.save()
        elif action == 'reject':
            access.status = 'rejected'
            access.approved_by = user # On note qui a rejeté
            access.save() # Ou delete() ? Gardons trace pour l'instant
        else:
            return Response({"error": "Action invalide"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProjectAccessSerializer(access).data)


