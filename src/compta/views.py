import json
from decimal import Decimal
from datetime import datetime, date

from django.core.exceptions import ValidationError
from django.db.models import Sum, Max, Min, DecimalField, Case, When, Q

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from openai import OpenAI
from ocr.pcg_loader import get_pcg_label
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource, FormSource

from compta.models import Journal, GrandLivre, Bilan, CompteResultat
from compta.serializers import (
    JournalSerializer, BilanSerializer, BalanceSerializer, CompteResultatSerializer,
    ChiffreAffaireSerializer, EbeSerializer, ResultatNetSerializer, BfrSerializer,
    CafSerializer, LeverageSerializer, AnnuiteCafSerializer, MargeNetteSerializer,
    DetteLmtCafSerializer, ChargeEbeSerializer, ChargeCaSerializer, MargeEndettementSerializer,
    CurrentRatioSerializer, QuickRatioSerializer, GearingSerializer, RotationStockSerializer,
    MargeOperationnelleSerializer, MargeBruteSerializer
)

from vulca_backend import settings


client = OpenAI(api_key=settings.OPENAI_API_KEY)


@api_view(["GET"])
@permission_classes([AllowAny])
def list_journals_view(request):
    journal_type = request.GET.get("type")
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    search_term = request.GET.get("search")

    queryset = Journal.objects.all().order_by("-date", "numero_piece")

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


@api_view(["GET"])
@permission_classes([AllowAny])
def journal_repartition_view(request):
    """
    Retourne la répartition des montants par type de journal.
    Utilisé pour le Dashboard (widgets et barres de progression).
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")
    
    queryset = Journal.objects.all()
    
    if date_start and date_end:
        queryset = queryset.filter(date__range=[date_start, date_end])
        
    from django.db.models import Count, Sum
    
    # Agrégation par type de journal
    repartition = queryset.values('type_journal').annotate(
        total_amount=Sum('debit_ar'), # On utilise le débit comme référence de volume
        count=Count('id')
    ).order_by('-total_amount')
    
    # Calcul du total global pour les pourcentages
    total_global = sum((item['total_amount'] or 0) for item in repartition)
    if total_global == 0:
        total_global = 1 # Eviter division par zéro

    data = []
    
    # Mapping pour les labels et couleurs (optionnel, le frontend peut aussi le gérer)
    LABELS = {
        'ACHAT': 'Achats',
        'VENTE': 'Ventes',
        'BANQUE': 'Banques',
        'CAISSE': 'Caisses',
        'OD': 'Opérations diverses',
        'AN': 'À Nouveaux'
    }
    
    COLORS = {
        'ACHAT': 'bg-red-800',
        'VENTE': 'bg-emerald-900',
        'BANQUE': 'bg-blue-900',
        'CAISSE': 'bg-amber-800',
        'OD': 'bg-gray-600',
        'AN': 'bg-purple-800'
    }

    for item in repartition:
        amount = item['total_amount'] or 0
        percentage = (amount / total_global) * 100
        journal_code = item['type_journal']
        
        data.append({
            "code": journal_code,
            "name": LABELS.get(journal_code, journal_code),
            "amount": float(amount),
            "percentage": round(percentage, 1),
            "value": round(percentage, 1),
            "count": item['count'],
            "color": COLORS.get(journal_code, 'bg-gray-500')
        })
        
    return Response({
        "total_global": float(total_global) if total_global > 1 else 0,
        "journals": data
    })

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
    - Si le document contient "facture" ET un client/nom_client → type_document = "VENTE"
    - Si le document contient "facture" ET un fournisseur/nom_fournisseur → type_document = "ACHAT"
    - Si le document contient "banque", "virement", "relevé bancaire" → type_document = "BANQUE"
    - Si le document contient "caisse", "espèces", "cash" → type_document = "CAISSE"
    - Si le document contient "fiche de paie", "bulletin de salaire", "payslip", "employee_name", "salaire_brut" → type_document = "OD"
    - Si le document contient "opération diverse", "OD" → type_document = "OD"
    - Si le document contient "à-nouveau", "AN", "report" → type_document = "AN"
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
    - ACHAT : Débit 602/607 (Achats), Débit 4456 (TVA déductible si applicable), Crédit 401 (Fournisseurs)
    - BANQUE : Utilise 512 (Banques) avec contrepartie appropriée (411 pour encaissement client, 401 pour paiement fournisseur)
    - CAISSE : Utilise 531 (Caisse) avec contrepartie appropriée
    - FICHE DE PAIE (Salaire) : 
      * Débit 641 (Rémunérations du personnel) = salaire_brut
      * Débit 645 (Charges sociales patronales) = total_cotisation_patronale
      * Crédit 421 (Personnel - Rémunérations dues) = net_a_payer
      * Crédit 431 (Sécurité sociale) = SOMME(total_cotisation_salariale + total_cotisation_patronale)  <-- TRES IMPORTANT : ADDITIONNER LES DEUX MONTANTS
      * Crédit 442 (État - Impôts et taxes) = retenue_source (IRSA)
      * IMPORTANT: Total Débit = salaire_brut + cotisation_patronale
      * IMPORTANT: Total Crédit = net_a_payer + (cotisation_salariale + cotisation_patronale) + retenue_source
      * Vérifie bien que: salaire_brut + cotisation_patronale = net_a_payer + cotisation_salariale + cotisation_patronale + retenue_source
    
    
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
    
    EXEMPLE FICHE DE PAIE (salaire_brut=400000, cotisation_salariale=4000, cotisation_patronale=52000, retenue_source=2300, net_a_payer=393700):
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
    Note: Total Débit = 400000 + 52000 = 452000, Total Crédit = 393700 + 56000 + 2300 = 452000 ✓
    
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
    
    # ✅ CONVERSION DE DATE : "5 septembre 2024" → "2024-09-05"
    try:
        date_facture_raw = date_facture_raw.replace('\xa0', ' ').strip()
        parsed_date = date_parser.parse(date_facture_raw, dayfirst=True)
        date_facture = parsed_date.strftime("%Y-%m-%d")
    except:
        date_facture = str(dt_date.today())
    
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
def process_journal_generation(document_json, file_source=None, form_source=None):
    """
    Fonction utilitaire pour générer le journal sans dépendre de 'request'.
    Peut être appelée par la vue ou par d'autres processus (ex: après OCR).
    Retourne un dict avec le résultat ou lève une exception.
    """
    
    # ===================================================
    # 🚀 AFFICHAGE DE DÉMARRAGE
    # ===================================================
    print("\n🚀 START GENERATE JOURNAL VIEW")
    print(f"   Input data keys: {list(document_json.keys())}")
    print()
    
    # ===================================================
    # 🏦 TRAITEMENT SPÉCIAL POUR RELEVÉ BANCAIRE
    # ===================================================
    piece_type = document_json.get("piece_type", "")
    description_json = document_json.get("description_json", {})
    
    if piece_type == "Relevé bancaire" and "transactions_details" in description_json:
        print("   📋 Détection: Relevé bancaire avec transactions multiples")
        transactions = description_json.get("transactions_details", [])
        
        if not transactions:
            raise ValidationError("Aucune transaction dans le relevé bancaire")
        
        print(f"   📊 Traitement de {len(transactions)} transactions")
        
        all_saved_lines = []
        
        # Traiter chaque transaction séparément
        for idx, transaction in enumerate(transactions, start=1):
            print(f"\n   💳 Transaction {idx}/{len(transactions)}")
            
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
            try:
                ai_result = generate_journal_from_pcg(transaction_doc)
            except Exception as e:
                print(f"      ❌ Erreur transaction {idx}: {str(e)}")
                continue
            
            type_journal = ai_result.get("type_journal")
            numero_piece = ai_result.get("numero_piece")
            date_val = ai_result.get("date")
            ecritures = ai_result.get("ecritures", [])
            
            if not ecritures:
                print(f"      ⚠️ Aucune écriture générée pour transaction {idx}")
                continue
            
            # Vérifier l'équilibre
            total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
            total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)
            
            if total_debit != total_credit:
                print(f"      ⚠️ Transaction {idx} non équilibrée (D:{total_debit} / C:{total_credit})")
                continue
            
            # Sauvegarder les écritures
            for line in ecritures:
                numero_compte = line["numero_compte"]
                libelle = get_pcg_label(numero_compte)
                if not libelle:
                    libelle = line.get("libelle", f"Compte {numero_compte}")
                
                entry = Journal(
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
                
                print(f"      ✅ {numero_compte} | D:{line['debit_ar']} | C:{line['credit_ar']}")
        
        print(f"\n   ✅ {len(all_saved_lines)} écritures générées pour {len(transactions)} transactions")
        
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
    numero_piece = ai_result.get("numero_piece")
    date_val = ai_result.get("date")
    ecritures = ai_result.get("ecritures", [])

    if not ecritures:
        raise ValidationError("Aucune écriture générée")
    
    print(f"   📊 AI a généré {len(ecritures)} lignes d'écriture")

    # Vérification de l'équilibre du journal
    total_debit = sum(Decimal(str(e["debit_ar"])) for e in ecritures)
    total_credit = sum(Decimal(str(e["credit_ar"])) for e in ecritures)

    if total_debit != total_credit:
         raise ValidationError(f"Écritures non équilibrées (D:{total_debit} / C:{total_credit})")

    # Sauvegarde chaque ligne dans Journal
    saved_lines = []
    created_entries = []

    try:
        for idx, line in enumerate(ecritures, start=1):
            print(f"   💾 Traitement ligne {idx}/{len(ecritures)}...")
            numero_compte = line["numero_compte"]
            
            # ✅ LIBELLÉ AUTOMATIQUE VIA PCG_LOADER POUR TOUS LES COMPTES
            libelle = get_pcg_label(numero_compte)
            if not libelle:
                # Fallback si le compte n'existe pas dans PCG
                libelle = line.get("libelle", f"Compte {numero_compte}")

            entry = Journal(
                date=date_val,
                numero_piece=numero_piece,
                type_journal=type_journal,
                numero_compte=numero_compte,
                libelle=libelle,
                debit_ar=line["debit_ar"],
                credit_ar=line["credit_ar"],
            )
            
            print(f"      → Compte: {numero_compte}, Libellé: {libelle}, D:{line['debit_ar']}, C:{line['credit_ar']}")
            
            entry.clean()
            entry.save()
            created_entries.append(entry)
            
            print(f"      ✅ Ligne {idx} sauvegardée (ID: {entry.id})")

            # Lier FileSource / FormSource via ForeignKey
            if file_source:
                file_source.journal = entry
                file_source.save()

            if form_source:
                form_source.journal = entry
                form_source.save()

            saved_lines.append({
                "id": entry.id,
                "compte": entry.numero_compte,
                "debit": float(entry.debit_ar),
                "credit": float(entry.credit_ar),
                "libelle": entry.libelle
            })

    except Exception as e:
        # En cas d'erreur partielle, on pourrait vouloir rollback, mais ici simple raise
        print(f"      ❌ ERREUR lors de la sauvegarde: {str(e)}")
        raise ValidationError(f"Erreur de validation/sauvegarde: {str(e)}")

    # ===================================================
    # ✅ AFFICHAGE FORMATÉ DU JOURNAL DANS LE TERMINAL
    # ===================================================
    print("\n" + "=" * 50)
    print(f"📄 JOURNAL GÉNÉRÉ (Type: {type_journal}, Pièce: {numero_piece})")
    print("-" * 50)
    
    for idx, line in enumerate(saved_lines, start=1):
        compte = line["compte"]
        libelle = line["libelle"]
        debit = int(line["debit"]) if line["debit"] else 0
        credit = int(line["credit"]) if line["credit"] else 0
        print(f"Ligne {idx}: {compte} - {libelle} | Débit: {debit} | Crédit: {credit}")
    
    print("=" * 50)
    print()

    return {
        "message": "Journal enregistré avec succès",
        "type_journal": type_journal,
        "numero_piece": numero_piece,
        "date": date_val,
        "lignes": saved_lines
    }



class BilanListCreateView(generics.ListCreateAPIView):
    serializer_class = BilanSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Bilan.objects.all()
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
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = CompteResultat.objects.all()
        # Filtres
        date = self.request.query_params.get('date')
        date_start = self.request.query_params.get('date_start')
        date_end = self.request.query_params.get('date_end')
        
        if date:
            queryset = queryset.filter(date=date)
        if date_start and date_end:
            queryset = queryset.filter(date__range=[date_start, date_end])

        return queryset
# GENERATE JOURNAL VIEW
@api_view(["POST"])
@permission_classes([AllowAny])
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

    try:
        result = process_journal_generation(document_json, file_source, form_source)
        return Response(result, status=status.HTTP_201_CREATED)
    except ValidationError as e:
        return Response({"error": str(e)}, status=400)
    except Exception as e:
        return Response({"error": str(e)}, status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
def chiffre_affaire_view(request):
    """
    Calcul du Chiffre d'Affaires avec variation par rapport à la période précédente
    GET /api/chiffre-affaire/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_ca(start_date, end_date):
        """Fonction helper pour calculer le CA pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        total_ca = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="70", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        return total_ca

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

@api_view(["GET"])
@permission_classes([AllowAny])
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
        Formule PCG 2005 : EBE = (70+71+72) - (60+61+62) + 74 - 63 - 64
        """
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

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

        return ebe

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

@api_view(["GET"])
@permission_classes([AllowAny])
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
        """Fonction helper pour calculer la Marge Brute pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Produits (70, 71, 72)
        produits = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__regex=r"^(70|71|72)", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Achats (60)
        achats = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="60", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        return produits, achats

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

@api_view(["GET"])
@permission_classes([AllowAny])
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


    def calculate_resultat(d_start, d_end):
        # ✅ OPTIMISATION : Une seule requête au lieu de 8+ requêtes
        
        qs = GrandLivre.objects.all()
        if d_start and d_end:
            qs = qs.filter(date__range=[d_start, d_end])
        elif d_end:
            qs = qs.filter(date__lte=d_end)
        
        # Agrégation par blocs de comptes (PCG 2005)
        # On sépare les catégories pour pouvoir les afficher en détail si besoin
        data = qs.aggregate(
            # 1. Exploitation
            produits_expl_cr=Sum(Case(When(numero_compte__regex=r'^(70|71|72|74|75)', then='credit'), default=0, output_field=DecimalField())),
            produits_expl_db=Sum(Case(When(numero_compte__regex=r'^(70|71|72|74|75)', then='debit'), default=0, output_field=DecimalField())),
            charges_expl_db=Sum(Case(When(numero_compte__regex=r'^(60|61|62|63|64|65)', then='debit'), default=0, output_field=DecimalField())),
            charges_expl_cr=Sum(Case(When(numero_compte__regex=r'^(60|61|62|63|64|65)', then='credit'), default=0, output_field=DecimalField())),
            
            # 2. Financier
            produits_fin_cr=Sum(Case(When(numero_compte__startswith='76', then='credit'), default=0, output_field=DecimalField())),
            produits_fin_db=Sum(Case(When(numero_compte__startswith='76', then='debit'), default=0, output_field=DecimalField())),
            charges_fin_db=Sum(Case(When(numero_compte__startswith='66', then='debit'), default=0, output_field=DecimalField())),
            charges_fin_cr=Sum(Case(When(numero_compte__startswith='66', then='credit'), default=0, output_field=DecimalField())),
            
            # 3. Exceptionnel
            produits_exc_cr=Sum(Case(When(numero_compte__startswith='77', then='credit'), default=0, output_field=DecimalField())),
            produits_exc_db=Sum(Case(When(numero_compte__startswith='77', then='debit'), default=0, output_field=DecimalField())),
            charges_exc_db=Sum(Case(When(numero_compte__startswith='67', then='debit'), default=0, output_field=DecimalField())),
            charges_exc_cr=Sum(Case(When(numero_compte__startswith='67', then='credit'), default=0, output_field=DecimalField())),
            
            # 4. Dotations & Reprises (Amortissements/Provisions - 68/78)
            dotations_db=Sum(Case(When(numero_compte__startswith='68', then='debit'), default=0, output_field=DecimalField())),
            dotations_cr=Sum(Case(When(numero_compte__startswith='68', then='credit'), default=0, output_field=DecimalField())),
            reprises_cr=Sum(Case(When(numero_compte__startswith='78', then='credit'), default=0, output_field=DecimalField())),
            reprises_db=Sum(Case(When(numero_compte__startswith='78', then='debit'), default=0, output_field=DecimalField())),
            
            # 5. Impôts (69)
            impots_db=Sum(Case(When(numero_compte__startswith='69', then='debit'), default=0, output_field=DecimalField())),
            impots_cr=Sum(Case(When(numero_compte__startswith='69', then='credit'), default=0, output_field=DecimalField())),
        )
        
        # Calcul des soldes nets par catégorie
        prod_expl = (data["produits_expl_cr"] or 0) - (data["produits_expl_db"] or 0)
        char_expl = (data["charges_expl_db"] or 0) - (data["charges_expl_cr"] or 0)
        
        prod_fin = (data["produits_fin_cr"] or 0) - (data["produits_fin_db"] or 0)
        char_fin = (data["charges_fin_db"] or 0) - (data["charges_fin_cr"] or 0)
        
        prod_exc = (data["produits_exc_cr"] or 0) - (data["produits_exc_db"] or 0)
        char_exc = (data["charges_exc_db"] or 0) - (data["charges_exc_cr"] or 0)
        
        dotations = (data["dotations_db"] or 0) - (data["dotations_cr"] or 0)
        reprises = (data["reprises_cr"] or 0) - (data["reprises_db"] or 0)
        
        impots = (data["impots_db"] or 0) - (data["impots_cr"] or 0)

        # Résultat d'Exploitation = (Produits Expl + Reprises) - (Charges Expl + Dotations)
        res_expl = (prod_expl + reprises) - (char_expl + dotations)
        # Résultat Financier
        res_fin = prod_fin - char_fin
        # Résultat Exceptionnel
        res_exc = prod_exc - char_exc

        res_net = res_expl + res_fin + res_exc - impots
        
        return {
            "produits": prod_expl + prod_fin + prod_exc + reprises,
            "charges_exploitation": char_expl + dotations,
            "charges_financieres": char_fin,
            "produits_financiers": prod_fin,
            "charges_exceptionnelles": char_exc,
            "produits_exceptionnels": prod_exc,
            "impots_benefices": impots,
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



@api_view(["GET"])
@permission_classes([AllowAny])
def bfr_view(request):
    """
    Calcul du BFR avec variation par rapport à la période précédente
    GET /api/bfr/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_bfr(start_date, end_date):
        """Fonction helper pour calculer le BFR pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Actif circulant
        actif_circulant = (
            Bilan.objects
            .filter(type_bilan="ACTIF", categorie="ACTIF_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Passif circulant
        passif_circulant = (
            Bilan.objects
            .filter(type_bilan="PASSIF", categorie="PASSIFS_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        return actif_circulant - passif_circulant

    # Calcul période courante
    current_bfr = calculate_bfr(date_start, date_end)
    
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
            
            previous_bfr = calculate_bfr(previous_start, previous_end)
            
            # Calculer variation en pourcentage
            # Éviter les pourcentages aberrants si la valeur précédente est trop faible
            if previous_bfr != 0 and abs(previous_bfr) > 50000:  # Seuil minimum 50000 Ar
                variation = ((current_bfr - previous_bfr) / abs(previous_bfr)) * 100
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
        "bfr": current_bfr,
        "variation": variation,
        "stocks": Decimal("0.00"),
        "creances_clients": Decimal("0.00"),
        "autres_creances": Decimal("0.00"),
        "dettes_fournisseurs": Decimal("0.00"),
        "autres_dettes": Decimal("0.00")
    })


@api_view(["GET"])
@permission_classes([AllowAny])
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



@api_view(["GET"])
@permission_classes([AllowAny])
def leverage_brut_view(request):
    """
    Calcul du Leverage Brut avec variation par rapport à la période précédente
    GET /api/leverage-brut/?date_start=2025-01-01&date_end=2025-12-31
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def calculate_leverage(start_date, end_date):
        """Fonction helper pour calculer le Leverage pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Total endettement
        total_endettement = (
            Bilan.objects
            .filter(
                type_bilan="PASSIF",
                categorie__in=["PASSIFS_COURANTS", "PASSIFS_NON_COURANTS"],
                numero_compte__regex=r"^16|^17|^19",
                **filters
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Calcul EBE
        produits = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__regex=r"^7[0-4]", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        charges = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__regex=r"^6[0-4]", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        
        ebe = produits - charges

        return (total_endettement / ebe) if ebe != 0 else None

    # Calcul période courante
    current_leverage = calculate_leverage(date_start, date_end)
    
    # Calcul période précédente et variation
    variation = None
    if date_start and date_end and current_leverage is not None:
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
            
            previous_leverage = calculate_leverage(previous_start, previous_end)
            
            # Calculer variation en pourcentage
            # Pour le leverage, on utilise un seuil plus bas (0.01) car c'est un ratio
            if previous_leverage is not None and previous_leverage != 0 and abs(previous_leverage) > 0.01:
                variation = ((current_leverage - previous_leverage) / abs(previous_leverage)) * 100
            else:
                variation = None
        except:
            pass

    return Response({
        "leverage_brut": current_leverage,
        "variation": variation,
        "total_endettement": Decimal("0.00"),
        "ebe": Decimal("0.00")
    })



@api_view(["GET"])
@permission_classes([AllowAny])
def annuite_caf_view(request):
    """
    GET /api/annuite-caf/?date_debut=2025-01-01&date_fin=2025-12-31
    
    IMPORTANT: "Annuité" ici fait référence aux Charges Financières (Compte 66)
    car sans tableau d'amortissement, il est impossible de connaître la part du capital remboursée.
    Le ratio devient donc un ratio de couverture des intérêts.
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
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


@api_view(["GET"])
@permission_classes([AllowAny])
def dashboard_indicators_view(request):
    """
    ENDPOINT OPTIMISÉ POUR DASHBOARD
    Calcule tous les indicateurs financiers en une seule requête DB/HTTP.
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # --- HELPER INTERNE OPTIMISÉ ---
    
    def get_solde(prefix, sens="credit-debit"):
        qs = GrandLivre.objects.filter(numero_compte__startswith=prefix)
        if date_start and date_end:
            qs = qs.filter(date__range=[date_start, date_end])
            
        agg = qs.aggregate(
            d=Sum("debit"), 
            c=Sum("credit")
        )
        d = agg["d"] or Decimal("0.00")
        c = agg["c"] or Decimal("0.00")
        
        if sens == "debit": return d
        if sens == "credit": return c
        if sens == "debit-credit": return d - c
        return c - d # Default: Crédit - Débit (Produits, Passif)

    # 1. CHIFFRE D'AFFAIRES (Classe 70)
    ca = get_solde("70")
    
    # 2. EBE
    produits_7 = get_solde("7") # Total classe 7
    subventions = get_solde("74")
    achats = get_solde("60", sens="debit-credit") # Charges = Debit - Credit
    charges_ext = get_solde("61", sens="debit-credit") + get_solde("62", sens="debit-credit")
    impots = get_solde("63", sens="debit-credit")
    personnel = get_solde("64", sens="debit-credit")
    
    ebe = get_solde("70") + get_solde("74") - (achats + charges_ext + impots + personnel)

    # 3. RÉSULTAT NET
    total_produits = get_solde("7")
    total_charges = get_solde("6", sens="debit-credit")
    resultat_net = total_produits - total_charges

    # 4. CAF
    dotations = get_solde("68", sens="debit-credit")
    reprises = get_solde("78")
    caf = resultat_net + dotations - reprises

    # 5. BFR
    stocks = get_solde("3", sens="debit-credit")
    creances = get_solde("411", sens="debit-credit") + get_solde("409", sens="debit-credit") + get_solde("418", sens="debit-credit")
    dettes_fournisseurs = get_solde("401") + get_solde("408") + get_solde("419")
    bfr = stocks + creances - dettes_fournisseurs 

    # 6. LEVERAGE
    endettement = get_solde("16") + get_solde("17") + get_solde("19")
    leverage = Decimal("0.00")
    if ebe != 0:
        leverage = endettement / ebe

    # 7. RATIOS DIVERS
    remboursement_k = get_solde("164", sens="debit") + get_solde("168", sens="debit")
    frais_fi = get_solde("661", sens="debit-credit")
    annuite = remboursement_k + frais_fi
    ratio_annuite_caf = Decimal("0")
    if caf != 0:
        ratio_annuite_caf = annuite / caf

    dette_lmt = get_solde("16")
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

    capitaux_propres_base = sum(get_solde(str(c)) for c in range(101, 107))
    fonds_propres = capitaux_propres_base + resultat_net
    ratio_gearing = Decimal("0")
    if fonds_propres != 0:
        ratio_gearing = dette_lmt / fonds_propres


    # 8. TOTAL BALANCE (Total Débit ou Crédit de la période)
    # Pour afficher "X Ar" sur la carte Balance si équilibrée.
    total_balance = get_solde("", sens="debit") # Total de tous les débits
    
    # --- ASSEMBLAGE RÉPONSE ---
    return Response({
        "ca": ca,
        "ebe": ebe,
        "resultat_net": resultat_net,
        "caf": caf,
        "bfr": bfr,
        "leverage": leverage.quantize(Decimal("0.01")),
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
                "value": ratio_marge_nette.quantize(Decimal("0.01")), # Pourcentage
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
            }
        }
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def journal_date_range_view(request):
    """
    Retourne la plage de dates (min, max) des écritures comptables.
    """
    agg = Journal.objects.aggregate(min_date=Min('date'), max_date=Max('date'))
    
    return Response({
        "min_date": agg['min_date'],
        "max_date": agg['max_date']
    })

@api_view(["GET"])
@permission_classes([AllowAny])
def balance_generale_view(request):
    """
    Retourne la balance générale (agrégée par compte) pour une plage de dates.
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    qs = GrandLivre.objects.all()
    if date_start and date_end:
        qs = qs.filter(date__range=[date_start, date_end])

    # Agrégation par compte
    balance_lines = qs.values("numero_compte").annotate(
        total_debit=Sum("debit"),
        total_credit=Sum("credit"),
        # On prend le libellé le plus fréquent ou le premier/dernier trouvé
        # Max est une heuristique acceptable si le libellé est constant par compte
        libelle=Max("libelle") 
    ).order_by("numero_compte")

    results = []
    for line in balance_lines:
        d = line["total_debit"] or Decimal("0.00")
        c = line["total_credit"] or Decimal("0.00")
        solde = d - c
        
        # On ne renvoie que les comptes mouvementés ou avec solde non nul ? 
        # Généralement Balance inclut tout ce qui a bougé dans la période ou qui a un solde.
        # Ici on filtre sur les mouvements de la période, donc d et c > 0.
        
        nature = "Soldé"
        if solde > 0: nature = "Débiteur"
        elif solde < 0: nature = "Créditeur"
        
        results.append({
            "compte": line["numero_compte"],
            "libelle": line["libelle"] or f"Compte {line['numero_compte']}",
            "debit": float(d),
            "credit": float(c),
            "solde": float(abs(solde)), # Le front gère le signe ou la colonne, ici magnitude
            "nature": nature
        })

    return Response(results)


@api_view(["GET"])
@permission_classes([AllowAny])
def dette_lmt_caf_view(request):
    """
    Ratio : Dette LMT / CAF
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
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

@api_view(["GET"])
@permission_classes([AllowAny])
def resultat_net_ca_view(request):
    """
    Ratio : Résultat net / Chiffre d'affaires
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def solde(prefix):
        qs = GrandLivre.objects.filter(numero_compte__startswith=prefix)
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

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ebe_view(request):
    """
    Ratio : Charge financière / EBE
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def solde(prefix):
        qs = GrandLivre.objects.filter(numero_compte__startswith=prefix)
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

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ca_view(request):
    """
    Ratio : Charge financière / Chiffre d'affaires
    """

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def solde(prefix):
        qs = GrandLivre.objects.filter(numero_compte__startswith=prefix)
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

@api_view(["GET"])
@permission_classes([AllowAny])
def marge_endettement_view(request):
    """
    Ratio : Dette CMLT / Fonds Propres
    Paramètres: ?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    def solde(prefix):
        qs = GrandLivre.objects.filter(numero_compte__startswith=prefix)
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

@api_view(["GET"])
@permission_classes([AllowAny])
def get_min_journal_date_view(request):
    """
    Retourne la date de la toute première écriture comptable.
    Utile pour initialiser les filtres de date par défaut.
    """
    from django.db.models import Min
    min_date = Journal.objects.aggregate(Min('date'))['date__min']
    
    if min_date:
         return Response({"min_date": min_date})
    else:
         # Fallback to current year start if no data
         return Response({"min_date": f"{date.today().year}-01-01"})


@api_view(["GET"])
@permission_classes([AllowAny])
def get_available_years_view(request):
    """
    Retourne la liste des années disponibles dans les écritures comptables.
    Triées par ordre décroissant (plus récent en premier).
    """
    from django.db.models.functions import ExtractYear
    from datetime import date
    
    years = (
        Journal.objects
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    
    # Filtrer les None éventuels et convertir en liste
    available_years = [y for y in years if y is not None]
    
    # Si vide, retourner l'année courante par défaut
    if not available_years:
        available_years = [date.today().year]
        
    return Response(available_years)


@api_view(["GET"])
@permission_classes([AllowAny])
def top_comptes_mouvementes_view(request):
    """
    Retourne les 5 comptes les plus mouvementés (débit + crédit).
    """
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    qs = GrandLivre.objects.all()
    if date_start and date_end:
        qs = qs.filter(date__range=[date_start, date_end])

    # Somme des mouvements (débit + crédit)
    data = (
        qs.values("numero_compte", "libelle")
        .annotate(
            total_mouvement=Sum("debit") + Sum("credit")
        )
        .order_by("-total_mouvement")[:5]
    )

    results = []
    for item in data:
        results.append({
            "compte": item["numero_compte"],
            "libelle": item["libelle"] or f"Compte {item['numero_compte']}",
            "mt_mvt": float(item["total_mouvement"] or 0)
        })

    return Response(results)


@api_view(["GET"])
@permission_classes([AllowAny])
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

    def calculate_roe(start_date, end_date):
        """Fonction helper pour calculer le ROE pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        elif end_date:
            filters["date__lte"] = end_date
        
        # 1. Résultat Net : On prend la dernière date disponible dans la période pour CompteResultat
        latest_res_date = CompteResultat.objects.filter(**filters).aggregate(Max('date'))['date__max']
        
        resultat_net = Decimal("0.00")
        if latest_res_date:
            agg_res = CompteResultat.objects.filter(date=latest_res_date).aggregate(
                prod=Sum(Case(When(nature="PRODUIT", then="montant_ar"), default=0, output_field=DecimalField())),
                char=Sum(Case(When(nature="CHARGE", then="montant_ar"), default=0, output_field=DecimalField()))
            )
            resultat_net = (agg_res["prod"] or Decimal("0.00")) - (agg_res["char"] or Decimal("0.00"))

        # 2. Fonds propres : On prend la dernière date disponible dans la période pour Bilan
        latest_bilan_date = Bilan.objects.filter(categorie="CAPITAUX_PROPRES", **filters).aggregate(Max('date'))['date__max']
        
        fonds_propres = Decimal("0.00")
        if latest_bilan_date:
            fonds_propres = (
                Bilan.objects
                .filter(type_bilan="PASSIF", categorie="CAPITAUX_PROPRES", date=latest_bilan_date)
                .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
            )

        # 3. Calcul du ROE
        if fonds_propres != 0 and abs(fonds_propres) >= 100000:
            roe = (resultat_net / fonds_propres * 100)
            # Protection contre les extrêmes
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

@api_view(["GET"])
@permission_classes([AllowAny])
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

    def calculate_roa(start_date, end_date):
        """Fonction helper pour calculer le ROA pour une période donnée"""
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        elif end_date:
            filters["date__lte"] = end_date
        
        # 1. Résultat Net : On prend la dernière date disponible dans la période pour CompteResultat
        latest_res_date = CompteResultat.objects.filter(**filters).aggregate(Max('date'))['date__max']
        
        resultat_net = Decimal("0.00")
        if latest_res_date:
            agg_res = CompteResultat.objects.filter(date=latest_res_date).aggregate(
                prod=Sum(Case(When(nature="PRODUIT", then="montant_ar"), default=0, output_field=DecimalField())),
                char=Sum(Case(When(nature="CHARGE", then="montant_ar"), default=0, output_field=DecimalField()))
            )
            resultat_net = (agg_res["prod"] or Decimal("0.00")) - (agg_res["char"] or Decimal("0.00"))

        # 2. Total Actif : On prend la dernière date disponible dans la période pour Bilan
        latest_bilan_date = Bilan.objects.filter(type_bilan="ACTIF", **filters).aggregate(Max('date'))['date__max']
        
        total_actif = Decimal("0.00")
        if latest_bilan_date:
            total_actif = (
                Bilan.objects
                .filter(type_bilan="ACTIF", date=latest_bilan_date)
                .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
            )

        # 3. Calcul du ROA
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

@api_view(["GET"])
@permission_classes([AllowAny])
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
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        actifs_courants = (
            Bilan.objects
            .filter(type_bilan="ACTIF", categorie="ACTIF_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        passifs_courants = (
            Bilan.objects
            .filter(type_bilan="PASSIF", categorie="PASSIFS_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
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

@api_view(["GET"])
@permission_classes([AllowAny])
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
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Actifs courants
        actifs_courants = (
            Bilan.objects
            .filter(type_bilan="ACTIF", categorie="ACTIF_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Stocks (classe 3)
        stocks = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="3", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Passifs courants
        passifs_courants = (
            Bilan.objects
            .filter(type_bilan="PASSIF", categorie="PASSIFS_COURANTS", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
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


@api_view(["GET"])
@permission_classes([AllowAny])
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
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Dettes financières (classe 16)
        dettes_financieres = (
            Bilan.objects
            .filter(type_bilan="PASSIF", numero_compte__startswith="16", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Fonds propres
        fonds_propres = (
            Bilan.objects
            .filter(type_bilan="PASSIF", categorie="CAPITAUX_PROPRES", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

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


@api_view(["GET"])
@permission_classes([AllowAny])
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
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]

        # Coût des ventes (charges classe 6)
        cout_ventes = (
            CompteResultat.objects
            .filter(nature="CHARGE", numero_compte__startswith="6", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Stock moyen (simplifié : stock fin de période)
        stocks = (
            Bilan.objects
            .filter(type_bilan="ACTIF", numero_compte__startswith="3", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
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


@api_view(["GET"])
@permission_classes([AllowAny])
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
        filters = {}
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        elif end_date:
            filters["date__lte"] = end_date

        # CA = Comptes 70, 71, 72 (PRODUITS uniquement)
        chiffre_affaire = (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="70", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        chiffre_affaire += (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="71", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        chiffre_affaire += (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="72", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Produits opérationnels = 70 + 71 + 72 + 74 + 75 (PRODUITS uniquement)
        produits_operationnels = chiffre_affaire  # 70+71+72 déjà calculé
        produits_operationnels += (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="74", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        produits_operationnels += (
            CompteResultat.objects
            .filter(nature="PRODUIT", numero_compte__startswith="75", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Charges d'exploitation = 60 + 61 + 62 + 63 + 64 + 65 (CHARGES uniquement)
        charges_exploitation = Decimal("0.00")
        for compte_prefix in ["60", "61", "62", "63", "64", "65"]:
            charges_exploitation += (
                CompteResultat.objects
                .filter(nature="CHARGE", numero_compte__startswith=compte_prefix, **filters)
                .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
            )

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

@api_view(["GET"])
@permission_classes([AllowAny])
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

    filters = {}
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






@api_view(["GET"])
@permission_classes([AllowAny])
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
    
    def calculate_bilan_kpis(d_start, d_end):
        """Calcule tous les KPIs du Bilan en une seule requête optimisée"""
        qs = Bilan.objects.filter(date__range=[d_start, d_end])
        
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
        cr_data = CompteResultat.objects.filter(date__range=[d_start, d_end]).aggregate(
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



@api_view(["GET"])
@permission_classes([AllowAny])
def get_available_years_view(request):
    """
    Retourne la liste des années disponibles dans les écritures comptables,
    le Bilan et le Compte de Résultat.
    Triées par ordre décroissant (plus récent en premier).
    """
    from django.db.models.functions import ExtractYear
    from datetime import date
    
    # 1. Années du Journal des écritures
    journal_years = set(
        Journal.objects
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )
    
    # 2. Années du Bilan
    bilan_years = set(
        Bilan.objects
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )

    # 3. Années du Compte de Résultat
    cr_years = set(
        CompteResultat.objects
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
    )

    # Fusion des ensembles pour éviter les doublons
    all_years_set = journal_years | bilan_years | cr_years
    
    # Filtrer les None éventuels, convertir en liste et trier
    available_years = sorted([y for y in all_years_set if y is not None], reverse=True)
    
    # Si vide, retourner l'année courante par défaut
    if not available_years:
        available_years = [date.today().year]
        
    return Response(available_years)


@api_view(["GET"])
@permission_classes([AllowAny])
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
        if start_date and end_date:
            filters["date__range"] = [start_date, end_date]
        
        # TVA collectée (compte 4457) - somme des crédits
        tva_collectee = (
            Journal.objects
            .filter(numero_compte__startswith="4457", **filters)
            .aggregate(total=Sum("credit_ar"))["total"] or Decimal("0.00")
        )
        
        # TVA déductible (compte 4456) - somme des débits
        tva_deductible = (
            Journal.objects
            .filter(numero_compte__startswith="4456", **filters)
            .aggregate(total=Sum("debit_ar"))["total"] or Decimal("0.00")
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

