import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from ocr.pcg_loader import get_pcg_label
from compta.serializers import BilanSerializer, BalanceSerializer, JournalSerializer, CompteResultatSerializer
from vulca_backend import settings
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource, FormSource
from compta.models import Journal, GrandLivre, Bilan, CompteResultat
from datetime import datetime
from datetime import date 
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from rest_framework.pagination import PageNumberPagination
from django.db.models import Sum
from datetime import date

client = OpenAI(api_key=settings.OPENAI_API_KEY) 


@api_view(["GET"])
@permission_classes([AllowAny])
def list_journals_view(request):
    journal_type = request.GET.get("type")
    show_all = request.GET.get("all", "false").lower() == "true"

    queryset = Journal.objects.all().order_by("-created_at", "numero_piece")

    if journal_type:
        queryset = queryset.filter(type_journal=journal_type)

    if not show_all:
        queryset = queryset.filter(created_at__date=date.today())

    totals = queryset.aggregate(
        total_debit=Sum("debit_ar"),
        total_credit=Sum("credit_ar"),
        total_count=Sum(1)
    )

    paginator = PageNumberPagination()
    paginator.page_size = 3
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = JournalSerializer(paginated_qs, many=True)

    response = paginator.get_paginated_response(serializer.data)

    response.data["totals"] = {
        "debit": totals["total_debit"] or 0,
        "credit": totals["total_credit"] or 0,
        "count": queryset.count()
    }

    return response

# CLASSIFICATION 
def classify_accounting(document_json: dict, pcg_mapping: dict):
    """
    document_json : dict contenant les champs extraits (facture, banque, reçu…)
    pcg_mapping   : dict extrait automatiquement du PDF du Plan Comptable Général 2005
    """

    # Convert mapping PCG → string compact
    pcg_text = "\n".join([f"{k}: {v}" for k, v in pcg_mapping.items()])

    prompt = f"""
    Tu es un expert-comptable malgache utilisant le Plan Comptable Général de Madagascar 2005.

    Voici un extrait du mapping PCG à utiliser impérativement :
    {pcg_text}

    Voici un document extrait (converti en JSON) :
    {json.dumps(document_json, indent=2)}

    OBJECTIF :
    1. Déterminer le type de document (facture fournisseur, facture client, relevé bancaire, reçu, etc.)
    2. Classer l'opération comptable selon le PCG Madagascar.
    3. Déduire tous les comptes comptables correspondants.
    4. Produire les écritures comptables (débit/crédit) sous forme JSON.

    RÈGLES :
    - Utilise **uniquement** les comptes présents dans le mapping PCG fourni.
    - Si nécessaire, choisis le compte le plus approprié.
    - Donne le journal sous forme strictement JSON.

    FORMAT DE SORTIE OBLIGATOIRE :

    {
        "type_document": "...",
        "classement_pcg": {
            "compte_debit": "xxx",
            "compte_credit": "xxx",
            "libelle_ecriture": "..."
        },
        "journal": [
            {
            "compte": "xxx",
            "libelle": "...",
            "debit": montant,
            "credit": 0
            },
            {
            "compte": "xxx",
            "libelle": "...",
            "debit": 0,
            "credit": montant
            }
        ]
    }
    """

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return json.loads(response.choices[0].message["content"])


def generate_journal_from_pcg(document_json):
    """
    Génère automatiquement le journal comptable selon les règles PCG
    SANS demander à l'IA de choisir les comptes.
    Les comptes sont déterminés par les règles comptables strictes.
    """
    from dateutil import parser as date_parser
    from datetime import date as dt_date
    
    type_doc = document_json.get("type_document", "").upper()
    numero_piece = document_json.get("numero_facture") or document_json.get("numero_piece") or document_json.get("reference") or "N/A"
    date_facture_raw = document_json.get("date") or document_json.get("date_facture") or str(dt_date.today())
    
    # ✅ CONVERSION DE DATE : "5 septembre 2024" → "2024-09-05"
    try:
        # Nettoyer les espaces insécables
        date_facture_raw = date_facture_raw.replace('\xa0', ' ').strip()
        
        # Parser la date (supporte DD/MM/YYYY, YYYY-MM-DD, "5 septembre 2024", etc.)
        parsed_date = date_parser.parse(date_facture_raw, dayfirst=True)
        date_facture = parsed_date.strftime("%Y-%m-%d")
    except:
        # Fallback : date du jour
        date_facture = str(dt_date.today())
    
    # Extraction des montants
    montant_ttc = float(document_json.get("montant_ttc", 0) or 0)
    montant_ht = float(document_json.get("montant_ht", 0) or 0)
    montant_tva = float(document_json.get("montant_tva", 0) or 0)
    
    # Calcul automatique si manquant
    if montant_ttc > 0 and montant_tva > 0 and montant_ht == 0:
        montant_ht = montant_ttc - montant_tva
    elif montant_ttc == 0 and montant_ht > 0 and montant_tva > 0:
        montant_ttc = montant_ht + montant_tva
    
    # Noms pour les tiers
    nom_client = document_json.get("nom_client") or document_json.get("client") or ""
    nom_fournisseur = document_json.get("fournisseur") or document_json.get("nom_fournisseur") or ""
    
    ecritures = []
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RÈGLES PCG AUTOMATIQUES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    if type_doc == "VENTE":
        # Client au débit (TTC)
        ecritures.append({
            "numero_compte": "411",
            "libelle": get_pcg_label("411") or "Clients",
            "debit_ar": montant_ttc,
            "credit_ar": 0
        })
        
        # Ventes au crédit (HT)
        ecritures.append({
            "numero_compte": "707",
            "libelle": get_pcg_label("707") or "Ventes de marchandises",
            "debit_ar": 0,
            "credit_ar": montant_ht if montant_tva > 0 else montant_ttc
        })
        
        # TVA collectée si > 0
        if montant_tva > 0:
            ecritures.append({
                "numero_compte": "4457",
                "libelle": get_pcg_label("4457") or "TVA collectée",
                "debit_ar": 0,
                "credit_ar": montant_tva
            })
    
    elif type_doc == "ACHAT":
        # Fournitures ou Achats (déterminé par description)
        description = document_json.get("objet_description", "").lower() or ""
        if "fourniture" in description or "bureau" in description:
            compte_charge = "602"
        else:
            compte_charge = "607"
        
        ecritures.append({
            "numero_compte": compte_charge,
            "libelle": get_pcg_label(compte_charge) or "Achats",
            "debit_ar": montant_ht if montant_tva > 0 else montant_ttc,
            "credit_ar": 0
        })
        
        # TVA déductible si > 0
        if montant_tva > 0:
            ecritures.append({
                "numero_compte": "4456",
                "libelle": get_pcg_label("4456") or "TVA déductible",
                "debit_ar": montant_tva,
                "credit_ar": 0
            })
        
        # Fournisseur au crédit (TTC)
        ecritures.append({
            "numero_compte": "401",
            "libelle": get_pcg_label("401") or "Fournisseurs",
            "debit_ar": 0,
            "credit_ar": montant_ttc
        })
    
    elif type_doc == "BANQUE" or type_doc == "CAISSE":
        # Opérations bancaires ou de caisse
        compte_tresorerie = "512" if type_doc == "BANQUE" else "531"
        
        # Déterminer le compte contrepartie selon l'objet
        objet = document_json.get("objet", "").lower() or document_json.get("objet_description", "").lower() or ""
        
        if "encaissement" in objet or "reçu" in objet or "virement reçu" in objet:
            # Encaissement client : Banque/Caisse au débit, Client au crédit
            ecritures.append({
                "numero_compte": compte_tresorerie,
                "libelle": get_pcg_label(compte_tresorerie) or ("Banques" if type_doc == "BANQUE" else "Caisse"),
                "debit_ar": montant_ttc,
                "credit_ar": 0
            })
            ecritures.append({
                "numero_compte": "411",
                "libelle": get_pcg_label("411") or "Clients",
                "debit_ar": 0,
                "credit_ar": montant_ttc
            })
        elif "paiement" in objet or "décaissement" in objet or "virement émis" in objet:
            # Paiement fournisseur : Fournisseur au débit, Banque/Caisse au crédit
            ecritures.append({
                "numero_compte": "401",
                "libelle": get_pcg_label("401") or "Fournisseurs",
                "debit_ar": montant_ttc,
                "credit_ar": 0
            })
            ecritures.append({
                "numero_compte": compte_tresorerie,
                "libelle": get_pcg_label(compte_tresorerie) or ("Banques" if type_doc == "BANQUE" else "Caisse"),
                "debit_ar": 0,
                "credit_ar": montant_ttc
            })
        else:
            # Opération bancaire générique : on enregistre juste le mouvement
            ecritures.append({
                "numero_compte": compte_tresorerie,
                "libelle": get_pcg_label(compte_tresorerie) or ("Banques" if type_doc == "BANQUE" else "Caisse"),
                "debit_ar": montant_ttc,
                "credit_ar": 0
            })
            # Contrepartie générique (à préciser manuellement)
            ecritures.append({
                "numero_compte": "471",  # Compte d'attente
                "libelle": get_pcg_label("471") or "Comptes d'attente",
                "debit_ar": 0,
                "credit_ar": montant_ttc
            })
    
    else:
        # Pour OD, AN, etc. - non supporté pour le moment
        raise ValidationError(f"Type de document '{type_doc}' non supporté par la génération automatique PCG")
    
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
def list_comptes(request):
    comptes = (
        GrandLivre.objects
        .values("numero_compte")
        .distinct()
        .order_by("numero_compte")
    )

    result = []
    for c in comptes:
        num = c["numero_compte"]
        label = get_pcg_label(num) or "-"
        result.append({
            "numero_compte": num,
            "libelle": label
        })

    return Response(result)

@api_view(["GET"])
@permission_classes([AllowAny])

def grand_livre(request):

    account = request.GET.get("account")
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    if not account:
        return Response({"error": "Le paramètre 'account' est requis."}, status=400)

    # 🔥 OBTAIN PCG LABEL FROM YOUR FUNCTION
    account_label = get_pcg_label(account) or "-"

    # Première écriture du compte
    first_entry = GrandLivre.objects.filter(numero_compte=account).order_by("date", "id").first()
    if not first_entry:
        return Response({
            "account": account,
            "accountLabel": account_label,      # ← ajout ici
            "entries": [],
            "openingBalance": 0,
            "closingBalance": 0,
            "movements": {"totalDebit": 0, "totalCredit": 0},
            "firstDate": None,
            "today": None,
            "startDate": None,
            "endDate": None
        })

    first_date = first_entry.date
    today = date.today()

    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else first_date
        end = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else today
    except ValueError:
        return Response({"error": "Format de date invalide (YYYY-MM-DD)."}, status=400)

    if start < first_date:
        start = first_date
    if end > today:
        end = today
    if start > end:
        return Response({"error": "La date de début est après la date de fin."}, status=400)

    # Solde d'ouverture
    opening_agg = GrandLivre.objects.filter(
        numero_compte=account,
        date__lt=start
    ).aggregate(
        total_debit=Sum("debit"),
        total_credit=Sum("credit")
    )

    opening_balance = Decimal(opening_agg["total_debit"] or 0) - Decimal(opening_agg["total_credit"] or 0)

    # Écritures de la période
    entries_qs = GrandLivre.objects.filter(
        numero_compte=account,
        date__range=[start, end]
    ).order_by("date", "id")

    total_debit = Decimal(0)
    total_credit = Decimal(0)
    closing_balance = opening_balance
    entries_list = []

    for e in entries_qs:
        debit = e.debit or Decimal("0.00")
        credit = e.credit or Decimal("0.00")

        closing_balance += debit - credit
        total_debit += debit
        total_credit += credit

        entries_list.append({
            "date": str(e.date),
            "journal_source": getattr(e.journal, "type_journal", "OD"),
            "numero_piece": e.numero_piece,
            "libelle": e.libelle,
            "debit": float(debit),
            "credit": float(credit),
            "solde_cumule": float(closing_balance)
        })

    return Response({
        "account": account,
        "accountLabel": account_label,   # 🔥 Ajout propre et dynamique
        "startDate": str(start),
        "endDate": str(end),
        "openingBalance": float(opening_balance),
        "movements": {
            "totalDebit": float(total_debit),
            "totalCredit": float(total_credit)
        },
        "closingBalance": float(closing_balance),
        "entries": entries_list,
        "firstDate": str(first_date),
        "today": str(today)
    })



@api_view(['POST', 'GET'])
def bilans_view(request):
    if request.method == 'POST':
        serializer = BilanSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'GET':
        bilans = Bilan.objects.all()
        serializer = BilanSerializer(bilans, many=True)
        return Response(serializer.data)
    
@api_view(['GET', 'POST'])
def CompteResultat_view(request):
    if request.method == 'POST':
        serializer = CompteResultatSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'GET':
        comptes = CompteResultat.objects.all()
        serializer = CompteResultatSerializer(comptes, many=True)
        return Response(serializer.data)