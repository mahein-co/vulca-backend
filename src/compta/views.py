import json
from decimal import Decimal
from datetime import datetime, date

from django.core.exceptions import ValidationError
from django.db.models import Sum, Max, Min, DecimalField

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
    DetteLmtCafSerializer, ChargeEbeSerializer, ChargeCaSerializer, MargeEndettementSerializer
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
    
    FORMAT DE SORTIE OBLIGATOIRE (JSON pur, sans markdown) :
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
    
    IMPORTANT : 
    - Retourne UNIQUEMENT le JSON, sans texte explicatif ni balises markdown
    - Le champ "type_document" est OBLIGATOIRE et ne doit JAMAIS être vide
    - Utilise les montants exacts du document (montant_ttc, montant_ht, montant_tva)
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
    numero_piece = document_json.get("numero_facture") or document_json.get("numero_piece") or document_json.get("reference") or "N/A"
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
    

    compte = request.GET.get("compte")
    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    queryset = CompteResultat.objects.filter(
        nature="PRODUIT",
        numero_compte__startswith="70"
    )

    if compte:
        queryset = queryset.filter(numero_compte=compte)

    if date_debut and date_fin:
        queryset = queryset.filter(date__range=[date_debut, date_fin])

    data = (
        queryset
        .values("numero_compte", "libelle")
        .annotate(
            chiffre_affaire=Sum("montant_ar")
        )
        .order_by("numero_compte")
    )

    result = []
    for row in data:
        result.append({
            "numero_compte": row["numero_compte"],
            "libelle": row["libelle"],
            "chiffre_affaire": row["chiffre_affaire"] or Decimal("0.00")
        })

    serializer = ChiffreAffaireSerializer(result, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def ebe_view(request):
    """
    GET /api/ebe/
    GET /api/ebe/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Produits d'exploitation (70 à 74)
    produits = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__regex=r"^7[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    # Charges d'exploitation (60 à 64)
    charges = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^6[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    ebe = produits - charges

    serializer = EbeSerializer({
        "produits_exploitation": produits,
        "charges_exploitation": charges,
        "ebe": ebe
    })

    return Response(serializer.data)

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
        from django.db.models import Case, When, Q
        
        qs = GrandLivre.objects.all()
        if d_start and d_end:
            qs = qs.filter(date__range=[d_start, d_end])
        elif d_end:
            qs = qs.filter(date__lte=d_end)
        
        # Agrégation conditionnelle pour tous les soldes en une seule requête
        data = qs.aggregate(
            # Produits (classe 7)
            produits_credit=Sum(Case(When(numero_compte__startswith='7', then='credit'), default=0, output_field=DecimalField())),
            produits_debit=Sum(Case(When(numero_compte__startswith='7', then='debit'), default=0, output_field=DecimalField())),
            
            # Charges exploitation (60-65)
            charges_60_credit=Sum(Case(When(numero_compte__startswith='60', then='credit'), default=0, output_field=DecimalField())),
            charges_60_debit=Sum(Case(When(numero_compte__startswith='60', then='debit'), default=0, output_field=DecimalField())),
            charges_61_credit=Sum(Case(When(numero_compte__startswith='61', then='credit'), default=0, output_field=DecimalField())),
            charges_61_debit=Sum(Case(When(numero_compte__startswith='61', then='debit'), default=0, output_field=DecimalField())),
            charges_62_credit=Sum(Case(When(numero_compte__startswith='62', then='credit'), default=0, output_field=DecimalField())),
            charges_62_debit=Sum(Case(When(numero_compte__startswith='62', then='debit'), default=0, output_field=DecimalField())),
            charges_63_credit=Sum(Case(When(numero_compte__startswith='63', then='credit'), default=0, output_field=DecimalField())),
            charges_63_debit=Sum(Case(When(numero_compte__startswith='63', then='debit'), default=0, output_field=DecimalField())),
            charges_64_credit=Sum(Case(When(numero_compte__startswith='64', then='credit'), default=0, output_field=DecimalField())),
            charges_64_debit=Sum(Case(When(numero_compte__startswith='64', then='debit'), default=0, output_field=DecimalField())),
            charges_65_credit=Sum(Case(When(numero_compte__startswith='65', then='credit'), default=0, output_field=DecimalField())),
            charges_65_debit=Sum(Case(When(numero_compte__startswith='65', then='debit'), default=0, output_field=DecimalField())),
            
            # Charges financières (66)
            charges_66_credit=Sum(Case(When(numero_compte__startswith='66', then='credit'), default=0, output_field=DecimalField())),
            charges_66_debit=Sum(Case(When(numero_compte__startswith='66', then='debit'), default=0, output_field=DecimalField())),
            
            # Produits financiers (76)
            produits_76_credit=Sum(Case(When(numero_compte__startswith='76', then='credit'), default=0, output_field=DecimalField())),
            produits_76_debit=Sum(Case(When(numero_compte__startswith='76', then='debit'), default=0, output_field=DecimalField())),
            
            # Charges exceptionnelles (67)
            charges_67_credit=Sum(Case(When(numero_compte__startswith='67', then='credit'), default=0, output_field=DecimalField())),
            charges_67_debit=Sum(Case(When(numero_compte__startswith='67', then='debit'), default=0, output_field=DecimalField())),
            
            # Produits exceptionnels (77)
            produits_77_credit=Sum(Case(When(numero_compte__startswith='77', then='credit'), default=0, output_field=DecimalField())),
            produits_77_debit=Sum(Case(When(numero_compte__startswith='77', then='debit'), default=0, output_field=DecimalField())),
            
            # Impôts sur bénéfices (69)
            impots_69_credit=Sum(Case(When(numero_compte__startswith='69', then='credit'), default=0, output_field=DecimalField())),
            impots_69_debit=Sum(Case(When(numero_compte__startswith='69', then='debit'), default=0, output_field=DecimalField())),
        )
        
        # Calcul des soldes (crédit - débit pour les produits, débit - crédit pour les charges)
        produits = (data["produits_credit"] or Decimal("0.00")) - (data["produits_debit"] or Decimal("0.00"))
        
        charges_exploitation = sum([
            (data[f"charges_{c}_debit"] or Decimal("0.00")) - (data[f"charges_{c}_credit"] or Decimal("0.00"))
            for c in [60, 61, 62, 63, 64, 65]
        ])
        
        charges_financieres = (data["charges_66_debit"] or Decimal("0.00")) - (data["charges_66_credit"] or Decimal("0.00"))
        produits_financiers = (data["produits_76_credit"] or Decimal("0.00")) - (data["produits_76_debit"] or Decimal("0.00"))
        charges_exceptionnelles = (data["charges_67_debit"] or Decimal("0.00")) - (data["charges_67_credit"] or Decimal("0.00"))
        produits_exceptionnels = (data["produits_77_credit"] or Decimal("0.00")) - (data["produits_77_debit"] or Decimal("0.00"))
        impots_benefices = (data["impots_69_debit"] or Decimal("0.00")) - (data["impots_69_credit"] or Decimal("0.00"))

        res_net = (
            produits
            - charges_exploitation
            - charges_financieres
            + produits_financiers
            - charges_exceptionnelles
            + produits_exceptionnels
            - impots_benefices
        )
        
        return {
            "produits": produits,
            "charges_exploitation": charges_exploitation,
            "charges_financieres": charges_financieres,
            "produits_financiers": produits_financiers,
            "charges_exceptionnelles": charges_exceptionnelles,
            "produits_exceptionnels": produits_exceptionnels,
            "impots_benefices": impots_benefices,
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
    GET /api/bfr/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Actif circulant
    actif_circulant = (
        Bilan.objects
        .filter(
            type_bilan="ACTIF",
            categorie="ACTIF_COURANTS",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Passif circulant
    passif_circulant = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="PASSIFS_COURANTS",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    bfr = actif_circulant - passif_circulant

    serializer = BfrSerializer({
        "actif_circulant": actif_circulant,
        "passif_circulant": passif_circulant,
        "bfr": bfr
    })

    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([AllowAny])
def caf_view(request):
    """
    GET /api/caf/
    GET /api/caf/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Résultat net (tous produits - toutes charges)
    produits_total = (
        CompteResultat.objects
        .filter(nature="PRODUIT", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    charges_total = (
        CompteResultat.objects
        .filter(nature="CHARGE", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    resultat_net = produits_total - charges_total

    # Dotations (681 / 687)
    dotations = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^68[1|7]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    # Reprises (781 / 787)
    reprises = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__regex=r"^78[1|7]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"]
        or Decimal("0.00")
    )

    caf = resultat_net + dotations - reprises

    serializer = CafSerializer({
        "resultat_net": resultat_net,
        "dotations": dotations,
        "reprises": reprises,
        "caf": caf
    })

    return Response(serializer.data)



@api_view(["GET"])
@permission_classes([AllowAny])
def leverage_brut_view(request):
    """
    GET /api/leverage-brut/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Total endettement = Passifs financiers (courts et longs termes)
    total_endettement = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie__in=["PASSIFS_COURANTS", "PASSIFS_NON_COURANTS"],
            numero_compte__regex=r"^16|^17|^19",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Calcul EBE (réutilisation de la logique précédente)
    produits = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__regex=r"^7[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    charges = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^6[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    ebe = produits - charges

    # Sécurité division par zéro
    leverage_brut = total_endettement / ebe if ebe != 0 else None

    return Response({
        "total_endettement": total_endettement,
        "ebe": ebe,
        "leverage_brut": leverage_brut
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
def chiffre_affaire_mensuel_view(request):
    """
    Retourne l'évolution mensuelle du CA (Classe 70).
    """
    from django.db.models.functions import TruncMonth

    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    qs = GrandLivre.objects.filter(numero_compte__startswith="70")
    if date_start and date_end:
        qs = qs.filter(date__range=[date_start, date_end])

    data = (
        qs.annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(
            ca=Sum("credit") - Sum("debit")
        )
        .order_by("month")
    )

    results = []
    for item in data:
        if item["month"]:
            results.append({
                "mois": item["month"].strftime("%Y-%m"),
                "ca": float(item["ca"] or 0)
            })

    return Response(results)


@api_view(["GET"])
@permission_classes([AllowAny])
def chiffre_affaire_annuel_view(request):
    """
    Retourne l'évolution annuelle du CA (Classe 70).
    """
    from django.db.models.functions import TruncYear

    qs = GrandLivre.objects.filter(numero_compte__startswith="70")
    
    data = (
        qs.annotate(year=TruncYear("date"))
        .values("year")
        .annotate(
            ca=Sum("credit") - Sum("debit")
        )
        .order_by("year")
    )

    results = []
    for item in data:
        if item["year"]:
            results.append({
                "annee": item["year"].strftime("%Y"),
                "ca": float(item["ca"] or 0)
            })

    return Response(results)


marge_net_view = resultat_net_ca_view


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
    """Retourne les années disponibles dans le journal"""
    from django.db.models.functions import ExtractYear
    
    years = (
        Journal.objects
        .annotate(year=ExtractYear('date'))
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    
    return Response(list(years))
