import json
from decimal import Decimal
from rest_framework import generics


from django.core.exceptions import ValidationError
from ocr.pcg_loader import get_pcg_label
from compta.serializers import BilanSerializer, BalanceSerializer, JournalSerializer, CompteResultatSerializer
from vulca_backend import settings
from ocr.constants import PCG_MAPPING
from ocr.utils import clean_ai_json
from ocr.models import FileSource, FormSource
<<<<<<< HEAD
from compta.serializers import JournalSerializer
from compta.models import Journal, Bilan,CompteResultat,GrandLivre
from compta.serializers import EbeSerializer,ResultatNetSerializer,BfrSerializer,CafSerializer,LeverageSerializer,AnnuiteCafSerializer,MargeNetteSerializer,ChargeCaSerializer
from compta.serializers import JournalSerializer, BilanSerializer,CompteResultatSerializer,ChiffreAffaireSerializer,DetteLmtCafSerializer,ChargeEbeSerializer,MargeEndettementSerializer
=======
from compta.models import Journal, GrandLivre, Bilan, CompteResultat
from datetime import datetime
>>>>>>> 44de8f3eb985abde8934825a07faff29e0781211
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
from decimal import Decimal


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
    GET /api/chiffre-affaire/?compte=701&date_debut=2025-01-01&date_fin=2025-12-31
    """

    compte = request.GET.get("compte")
    date_debut = request.GET.get("date_debut")
    date_fin = request.GET.get("date_fin")

    # Comptes de CA = classe 7
    queryset = GrandLivre.objects.filter(numero_compte__startswith="7")

    if compte:
        queryset = queryset.filter(numero_compte=compte)

    if date_debut and date_fin:
        queryset = queryset.filter(date__range=[date_debut, date_fin])

    data = (
        queryset
        .values("numero_compte")
        .annotate(
            total_credit=Sum("credit"),
            total_debit=Sum("debit"),
        )
def list_comptes(request):
    comptes = (
        GrandLivre.objects
        .values("numero_compte")
        .distinct()
        .order_by("numero_compte")
    )

    result = []
    for row in data:
        credit = row["total_credit"] or Decimal("0.00")
        debit = row["total_debit"] or Decimal("0.00")

        result.append({
            "numero_compte": row["numero_compte"],
            "total_credit": credit,
            "total_debit": debit,
            "chiffre_affaire": credit - debit
        })

    serializer = ChiffreAffaireSerializer(result, many=True)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def ebe_view(request):
    """
    Calcul automatique de l'EBE depuis le Grand Livre
    """

    def solde_classe(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            total_credit=Sum("credit"),
            total_debit=Sum("debit")
        )
        return (data["total_credit"] or Decimal("0.00")) - (data["total_debit"] or Decimal("0.00"))

    chiffre_affaires = solde_classe("7")
    subventions = solde_classe("74")
    achats = solde_classe("60")
    charges_externes = solde_classe("61") + solde_classe("62")
    impots_taxes = solde_classe("63")
    charges_personnel = solde_classe("64")

    ebe = (
        chiffre_affaires
        + subventions
        - achats
        - charges_externes
        - impots_taxes
        - charges_personnel
    )

    payload = {
        "chiffre_affaires": chiffre_affaires,
        "subventions": subventions,
        "achats": achats,
        "charges_externes": charges_externes,
        "impots_taxes": impots_taxes,
        "charges_personnel": charges_personnel,
        "ebe": ebe,
    }

    serializer = EbeSerializer(payload)
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
    Calcul du BFR depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["debit"] or Decimal("0.00")) - (data["credit"] or Decimal("0.00"))

    stocks = solde("3")
    creances_clients = solde("411")
    autres_creances = solde("409") + solde("418")
    dettes_fournisseurs = solde("401")
    autres_dettes = solde("408") + solde("419")

    bfr = (
        stocks
        + creances_clients
        + autres_creances
        - dettes_fournisseurs
        - autres_dettes
    )

    payload = {
        "stocks": stocks,
        "creances_clients": creances_clients,
        "autres_creances": autres_creances,
        "dettes_fournisseurs": dettes_fournisseurs,
        "autres_dettes": autres_dettes,
        "bfr": bfr,
    }

    serializer = BfrSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def caf_view(request):
    """
    Calcul de la CAF depuis le Grand Livre
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # Résultat Net
    produits = solde("7")
    charges_exploitation = sum(solde(str(c)) for c in range(60, 66))
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

    # Dotations / Reprises
    dotations = solde("68")
    reprises = solde("78")

    caf = resultat_net + dotations - reprises

    payload = {
        "resultat_net": resultat_net,
        "dotations_amort_provisions": dotations,
        "reprises_amort_provisions": reprises,
        "caf": caf
    }

    serializer = CafSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def leverage_brut_view(request):
    """
    Calcul du Leverage brut = Total endettement / EBE
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            credit=Sum("credit"),
            debit=Sum("debit")
        )
        return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

    # Total endettement (exemple : comptes 16, 17, 19)
    total_endettement = solde("16") + solde("17") + solde("19")

    # Calcul EBE (même méthode que pour l'EBE API)
    ca = solde("7")
    subventions = solde("74")
    achats = solde("60")
    charges_ext = solde("61") + solde("62")
    impots = solde("63")
    personnel = solde("64")
    ebe = ca + subventions - achats - charges_ext - impots - personnel

    leverage_brut = Decimal("0.00")
    if ebe != 0:
        leverage_brut = total_endettement / ebe

    payload = {
        "total_endettement": total_endettement,
        "ebe": ebe,
        "leverage_brut": leverage_brut.quantize(Decimal("0.01"))
    }

    serializer = LeverageSerializer(payload)
    return Response(serializer.data)

@api_view(["GET"])
@permission_classes([AllowAny])
def annuite_caf_view(request):
    """
    Ratio : Annuité d'emprunt / CAF
    """

    def solde(prefix, sens="debit"):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        if sens == "debit":
            return data["debit"] or Decimal("0.00")
        return data["credit"] or Decimal("0.00")

    # 🔹 Annuité d'emprunt
    remboursement_capital = solde("164") + solde("168")
    interets = solde("661")
    annuite_emprunt = remboursement_capital + interets

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

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
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

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
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

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
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
    """

    def solde(prefix):
        data = GrandLivre.objects.filter(
            numero_compte__startswith=prefix
        ).aggregate(
            debit=Sum("debit"),
            credit=Sum("credit")
        )
        return (data["credit"] or Decimal("0")) - (data["debit"] or Decimal("0"))

    # 🔹 Dette CMLT (16x)
    dette_cmlt = solde("16")

    # 🔹 Fonds Propres (101–106)
    fonds_propres = sum(solde(str(c)) for c in range(101, 107))

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
