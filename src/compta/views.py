import json
from decimal import Decimal
from datetime import datetime, date

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from openai import OpenAI
from django.db.utils import OperationalError as DBOperationalError

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
    RoeSerializer,RoaSerializer,CurrentRatioSerializer,QuickRatioSerializer,GearingSerializer,
    RotationStockSerializer,MargeOperationnelleSerializer,RepartitionResultatSerializer,EvolutionTresorerieSerializer,
    TopCompteSerializer,EvolutionChiffreAffaireSerializer
)

from vulca_backend import settings


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
    
    L'IA agit comme expert-comptable et choisit les comptes UNIQUEMENT depuis le PCG.
    """
    
    # ✅ CHARGER LE PCG COMPLET DEPUIS LE PDF
    from ocr.pcg_loader import load_pcg_mapping_from_pdf
    pcg_complet = load_pcg_mapping_from_pdf()
    
    # Convertir le PCG en texte lisible pour l'IA
    pcg_text = "\n".join([f"{code}: {label}" for code, label in sorted(pcg_complet.items())])

    prompt = f"""
    Tu es un expert-comptable malgache certifié, spécialiste du Plan Comptable Général de Madagascar 2005.
    
    try:
        dotations = solde_net("68")
        reprises = solde_net("78")

        caf = resultat_net + dotations - reprises

        ratio = Decimal("0.00")
        if caf != 0:
            ratio = annuite_emprunt / caf

        alerte = ratio > Decimal("0.50")

        payload = {
            "annuite_emprunt": annuite_emprunt,
            "caf": caf,
            "ratio": ratio.quantize(Decimal("0.01")),
            "alerte": alerte
        }

        serializer = AnnuiteCafSerializer(payload)
        return Response(serializer.data)
    except DBOperationalError as e:
        # Database unreachable (timeout, network issue). Return 503 so frontend can handle gracefully.
        return Response({"error": "Database unavailable", "details": str(e)}, status=503)
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
    queryset = Bilan.objects.all()
    serializer_class = BilanSerializer
    permission_classes = [AllowAny]

class CompteResultatListCreateView(generics.ListCreateAPIView):
    queryset = CompteResultat.objects.all()
    serializer_class = CompteResultatSerializer
    permission_classes = [AllowAny]
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
    GET /api/chiffre-affaire/
    GET /api/chiffre-affaire/?compte=701
    GET /api/chiffre-affaire/?date_debut=2025-01-01&date_fin=2025-12-31
    """

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
def resultat_net_view(request):
    """
    Calcul du Résultat Net depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    produits = solde("7")
    charges_exploitation = sum(
        solde(str(c)) for c in range(60, 66)
    )
    charges_financieres = solde("66")
    produits_financiers = solde("76")
    charges_exceptionnelles = solde("67")
    produits_exceptionnels = solde("77")
    impots_benefices = solde("69")

    resultat_net = (
        produits
        - charges_exploitation
        - charges_financieres
        + produits_financiers
        - charges_exceptionnelles
        + produits_exceptionnels
        - impots_benefices
    )

    payload = {
        "produits": produits,
        "charges_exploitation": charges_exploitation,
        "charges_financieres": charges_financieres,
        "produits_financiers": produits_financiers,
        "charges_exceptionnelles": charges_exceptionnelles,
        "produits_exceptionnels": produits_exceptionnels,
        "impots_benefices": impots_benefices,
        "resultat_net": resultat_net,
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
def dette_lmt_caf_view(request):
    """
    GET /api/dette-lmt-caf/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Dette long terme = Passifs non courants (17x)
    dette_lmt = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="PASSIFS_NON_COURANTS",
            numero_compte__regex=r"^17",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # === Calcul de la CAF (Méthode additive CORRECTE) ===
    # Produits totaux (Classe 7)
    produits_total = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__regex=r"^7",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Charges totales (Classe 6)
    charges_total = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^6",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Résultat Net
    resultat_net = produits_total - charges_total

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

    # CAF = Résultat Net + Dotations - Reprises
    caf = resultat_net + dotations - reprises

    # Ratio Dette LMT / CAF
    ratio_dette_lmt_caf = dette_lmt / caf if caf != 0 else None

    serializer = DetteLmtCafSerializer({
        "dette_lmt": dette_lmt,
        "caf": caf,
        "ratio_dette_lmt_caf": ratio_dette_lmt_caf
    })

    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([AllowAny])
def marge_net_view(request):
    """
    GET /api/marge-net/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Chiffre d'affaires = Produits de classe 70
    ca = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__startswith="70",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Résultat net = tous produits - toutes charges
    total_produits = (
        CompteResultat.objects
        .filter(nature="PRODUIT", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    total_charges = (
        CompteResultat.objects
        .filter(nature="CHARGE", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    resultat_net = total_produits - total_charges

    # Ratio Résultat net / CA
    ratio_resultat_ca = resultat_net / ca if ca != 0 else None

    serializer = MargeNetteSerializer({
        "chiffre_affaire": ca,
        "resultat_net": resultat_net,
        "ratio_resultat_ca": ratio_resultat_ca
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ebe_view(request):
    """
    GET /api/charge-ebe/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Charge financière = comptes 66x
    charge_financiere = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^66",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # EBE (Excédent Brut d'Exploitation)
    produits = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__regex=r"^7[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    charges_exploitation = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__regex=r"^6[0-4]",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    ebe = produits - charges_exploitation

    # Ratio Charge financière / EBE
    ratio_charge_ebe = charge_financiere / ebe if ebe != 0 else None

    serializer = ChargeEbeSerializer({
        "charge_financiere": charge_financiere,
        "ebe": ebe,
        "ratio_charge_ebe": ratio_charge_ebe
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def charge_ca_view(request):
    """
    GET /api/charge-ca/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    try:
        # Charge financière = comptes 66x
        charge_financiere = (
            CompteResultat.objects
            .filter(
                nature="CHARGE",
                numero_compte__regex=r"^66",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Chiffre d'affaires = comptes 70x
        chiffre_affaire = (
            CompteResultat.objects
            .filter(
                nature="PRODUIT",
                numero_compte__startswith="70",
                **base_filter
            )
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        # Ratio Charge financière / CA
        ratio_charge_ca = charge_financiere / chiffre_affaire if chiffre_affaire != 0 else None

        serializer = ChargeCaSerializer({
            "charge_financiere": charge_financiere,
            "chiffre_affaire": chiffre_affaire,
            "ratio_charge_ca": ratio_charge_ca
        })

        return Response(serializer.data)
    except DBOperationalError as e:
        return Response({"error": "Database unavailable", "details": str(e)}, status=503)

@api_view(["GET"])
@permission_classes([AllowAny])
def marge_endettement_view(request):
    """
    GET /api/marge-endettement/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Dette CMLT = Passifs financiers courants + non courants (16x + 17x)
    dette_cmlt = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie__in=["PASSIFS_COURANTS", "PASSIFS_NON_COURANTS"],
            numero_compte__regex=r"^16|^17",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Fonds propres
    fonds_propres = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="CAPITAUX_PROPRES",
            **base_filter
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    # Ratio Dette CMLT / Fonds propres
    ratio_marge_endettement = dette_cmlt / fonds_propres if fonds_propres != 0 else None

    serializer = MargeEndettementSerializer({
        "dette_cmlt": dette_cmlt,
        "fonds_propres": fonds_propres,
        "ratio_marge_endettement": ratio_marge_endettement
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def roe_view(request):
    """
    GET /api/roe/?date_debut=2025-01-01&date_fin=2025-12-31
    Optionnel: date_debut_prev & date_fin_prev pour calcul variation
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")
    date_debut_prev = request.GET.get("date_debut_prev")
    date_fin_prev = request.GET.get("date_fin_prev")

    base_filter = {}
    if date_debut and date_fin:
        base_filter["date__range"] = [date_debut, date_fin]

    # Résultat net période courante
    total_produits = (
        CompteResultat.objects
        .filter(nature="PRODUIT", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    total_charges = (
        CompteResultat.objects
        .filter(nature="CHARGE", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )
    resultat_net = total_produits - total_charges

    # Fonds propres période courante
    fonds_propres = (
        Bilan.objects
        .filter(type_bilan="PASSIF", categorie="CAPITAUX_PROPRES", **base_filter)
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
    )

    roe = (resultat_net / fonds_propres * 100) if fonds_propres != 0 else None

    # Résultat précédent pour calcul variation
    variation = None
    if date_debut_prev and date_fin_prev:
        base_filter_prev = {"date__range": [date_debut_prev, date_fin_prev]}
        total_produits_prev = (
            CompteResultat.objects
            .filter(nature="PRODUIT", **base_filter_prev)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        total_charges_prev = (
            CompteResultat.objects
            .filter(nature="CHARGE", **base_filter_prev)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )
        resultat_net_prev = total_produits_prev - total_charges_prev

        fonds_propres_prev = (
            Bilan.objects
            .filter(type_bilan="PASSIF", categorie="CAPITAUX_PROPRES", **base_filter_prev)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0.00")
        )

        roe_prev = (resultat_net_prev / fonds_propres_prev * 100) if fonds_propres_prev != 0 else None

        if roe is not None and roe_prev is not None:
            variation = roe - roe_prev

    serializer = RoeSerializer({
        "roe": roe,
        "variation": variation
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def roa_view(request):
    """
    GET /api/roa/?date_debut=2025-01-01&date_fin=2025-12-31
    Optionnel :
    date_debut_prev=2024-01-01&date_fin_prev=2024-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")
    date_debut_prev = request.GET.get("date_debut_prev")
    date_fin_prev = request.GET.get("date_fin_prev")

    def calcul_roa(filters):
        # Résultat net
        produits = (
            CompteResultat.objects
            .filter(nature="PRODUIT", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
        )
        charges = (
            CompteResultat.objects
            .filter(nature="CHARGE", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
        )
        resultat_net = produits - charges

        # Total Actif
        total_actif = (
            Bilan.objects
            .filter(type_bilan="ACTIF", **filters)
            .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
        )

        if total_actif == 0:
            return None

        return (resultat_net / total_actif) * 100

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    roa = calcul_roa(filters)

    variation = None
    if date_debut_prev and date_fin_prev:
        roa_prev = calcul_roa({
            "date__range": [date_debut_prev, date_fin_prev]
        })
        if roa is not None and roa_prev is not None:
            variation = roa - roa_prev

    serializer = RoaSerializer({
        "roa": roa,
        "variation": variation
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def current_ratio_view(request):
    """
    GET /api/current-ratio/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    actifs_courants = (
        Bilan.objects
        .filter(
            type_bilan="ACTIF",
            categorie="ACTIF_COURANTS",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    passifs_courants = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="PASSIFS_COURANTS",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    current_ratio = (
        actifs_courants / passifs_courants
        if passifs_courants != 0
        else None
    )

    serializer = CurrentRatioSerializer({
        "current_ratio": current_ratio
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def quick_ratio_view(request):
    """
    GET /api/quick-ratio/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    # Actifs courants
    actifs_courants = (
        Bilan.objects
        .filter(
            type_bilan="ACTIF",
            categorie="ACTIF_COURANTS",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    # Stocks (classe 3)
    stocks = (
        Bilan.objects
        .filter(
            type_bilan="ACTIF",
            numero_compte__startswith="3",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    # Passifs courants
    passifs_courants = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="PASSIFS_COURANTS",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    quick_ratio = (
        (actifs_courants - stocks) / passifs_courants
        if passifs_courants != 0
        else None
    )

    serializer = QuickRatioSerializer({
        "quick_ratio": quick_ratio
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def gearing_view(request):
    """
    GET /api/gearing/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    # Dettes financières (classe 16)
    dettes_financieres = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            numero_compte__startswith="16",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    # Fonds propres
    fonds_propres = (
        Bilan.objects
        .filter(
            type_bilan="PASSIF",
            categorie="CAPITAUX_PROPRES",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    gearing = (
        dettes_financieres / fonds_propres
        if fonds_propres != 0
        else None
    )

    serializer = GearingSerializer({
        "gearing": gearing
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def rotation_stock_view(request):
    """
    GET /api/rotation-stock/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    # Coût des ventes (charges classe 6)
    cout_ventes = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__startswith="6",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    # Stock moyen (simplifié : stock fin de période)
    stocks = (
        Bilan.objects
        .filter(
            type_bilan="ACTIF",
            numero_compte__startswith="3",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
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

    serializer = RotationStockSerializer({
        "rotation_stock": rotation_stock,
        "duree_stock_jours": duree_stock
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def marge_operationnelle_view(request):
    """
    GET /api/marge-operationnelle/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    # Chiffre d'affaires
    chiffre_affaire = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__startswith="7",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    # Charges d'exploitation
    charges_exploitation = (
        CompteResultat.objects
        .filter(
            nature="CHARGE",
            numero_compte__startswith="6",
            **filters
        )
        .aggregate(total=Sum("montant_ar"))["total"] or Decimal("0")
    )

    resultat_operationnel = chiffre_affaire - charges_exploitation

    marge_operationnelle = (
        (resultat_operationnel / chiffre_affaire) * 100
        if chiffre_affaire != 0
        else None
    )

    serializer = MargeOperationnelleSerializer({
        "marge_operationnelle": marge_operationnelle
    })

    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def repartition_produits_charges_view(request):
    """
    GET /api/repartition-resultat/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

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

    total_global = total_produits + total_charges

    def pct(val):
        return (val / total_global * 100) if total_global != 0 else Decimal("0")

    data = [
        {
            "label": "Produits",
            "montant": total_produits,
            "pourcentage": pct(total_produits),
        },
        {
            "label": "Charges",
            "montant": total_charges,
            "pourcentage": pct(total_charges),
        }
    ]

    serializer = RepartitionResultatSerializer(data, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def evolution_tresorerie_view(request):
    """
    GET /api/evolution-tresorerie/?annee=2025
    """
    annee = request.GET.get("annee")

    # User requested Bilan source
    queryset = Bilan.objects.filter(numero_compte__startswith="5")

    if annee:
        queryset = queryset.filter(date__year=annee)

    data = (
        queryset
        .annotate(mois=TruncMonth("date"))
        .values("mois")
        .annotate(total=Sum("montant_ar"))
        .order_by("mois")
    )

    result = []
    for row in data:
        result.append({
            "periode": row["mois"].strftime("%Y-%m"),
            "tresorerie": row["total"] or Decimal("0")
        })

    serializer = EvolutionTresorerieSerializer(result, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def top_comptes_mouvementes_view(request):
    """
    GET /api/top-comptes-mouvementes/?date_debut=2025-01-01&date_fin=2025-12-31
    """

    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    # Filtre de date
    filters = {}
    if date_debut and date_fin:
        filters["date__range"] = [date_debut, date_fin]

    # Total par compte pour CompteResultat
    cr_totaux = (
        CompteResultat.objects.filter(**filters)
        .values("numero_compte", "libelle")
        .annotate(montant_total=Sum("montant_ar"))
    )

    # Total par compte pour Bilan
    bilan_totaux = (
        Bilan.objects.filter(**filters)
        .values("numero_compte", "libelle")
        .annotate(montant_total=Sum("montant_ar"))
    )

    # Fusion des deux QuerySets
    comptes_dict = {}
    for item in list(cr_totaux) + list(bilan_totaux):
        key = item["numero_compte"]
        if key in comptes_dict:
            comptes_dict[key]["montant_total"] += item["montant_total"] or Decimal("0")
        else:
            comptes_dict[key] = {
                "numero_compte": key,
                "libelle": item["libelle"],
                "montant_total": item["montant_total"] or Decimal("0")
            }

    # Trier par montant total décroissant
    top_comptes = sorted(
        comptes_dict.values(), key=lambda x: x["montant_total"], reverse=True
    )[:10]

    serializer = TopCompteSerializer(top_comptes, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def chiffre_affaire_mensuel_view(request):
    """
    GET /api/chiffre-affaire-mensuel/?annee=2025
    """

    annee = request.GET.get("annee")

    queryset = CompteResultat.objects.filter(
        nature="PRODUIT",
        numero_compte__startswith="7"
    )

    if annee:
        queryset = queryset.filter(date__year=annee)

    data = (
        queryset
        .annotate(mois=TruncMonth("date"))
        .values("mois")
        .annotate(total=Sum("montant_ar"))
        .order_by("mois")
    )

    result = [
        {
            "periode": row["mois"].strftime("%Y-%m"),
            "chiffre_affaire": row["total"] or Decimal("0")
        }
        for row in data
    ]

    serializer = EvolutionChiffreAffaireSerializer(result, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def chiffre_affaire_annuel_view(request):
    """
    GET /api/chiffre-affaire-annuel/
    """

    data = (
        CompteResultat.objects
        .filter(
            nature="PRODUIT",
            numero_compte__startswith="7"
        )
        .annotate(annee=TruncYear("date"))
        .values("annee")
        .annotate(total=Sum("montant_ar"))
        .order_by("annee")
    )

    result = [
        {
            "periode": row["annee"].strftime("%Y"),
            "chiffre_affaire": row["total"] or Decimal("0")
        }
        for row in data
    ]

    serializer = EvolutionChiffreAffaireSerializer(result, many=True)
    return Response(serializer.data)