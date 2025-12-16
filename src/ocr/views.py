import json
import re
import unicodedata
import hashlib
from vulca_backend import settings

from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from ocr.models import FileSource, FormSource
from ocr.serializers import FileSourceSerializer, FormSourceSerializer
from ocr.utils import detect_file_type, extract_content, clean_ai_json, generate_description
from ocr.constants import EXTRACTION_FIELDS_PROMPT

from openai import OpenAI
client = OpenAI(api_key=settings.OPENAI_API_KEY) 


# Helper: traduire les clefs d'un dict (récursively) selon un mapping anglais->français
def translate_keys(obj, mapping):
    """Renomme les clefs d'un dict récursivement selon mapping.
    Si obj est une liste, applique la traduction à chaque élément.
    Retourne une nouvelle structure (n'affecte pas l'original).
    """
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            new_key = mapping.get(k, k)
            new[new_key] = translate_keys(v, mapping)
        return new
    elif isinstance(obj, list):
        return [translate_keys(item, mapping) for item in obj]
    else:
        return obj


# Normalise les champs client : si `numero_client` contient un nom (lettres),
# on le transforme en `nom_client` et on met `numero_client` à None.
def normalize_client_fields(data: dict):
    """Normalise le(s) champ(s) client dans le dict racine.
    - mappe `client` ou `client_name` -> `nom_client`
    - si `numero_client` contient des lettres, considère que c'est un nom
      et le positionne dans `nom_client`.
    Modifie le dict en place.
    """
    if not isinstance(data, dict):
        return data

    # alias possibles venant du modèle IA
    if "client" in data and "nom_client" not in data:
        data["nom_client"] = data.pop("client")

    if "client_name" in data and "nom_client" not in data:
        data["nom_client"] = data.pop("client_name")

    # Traiter numero_client : est-ce un numéro (chiffres) ou un nom (lettres) ?
    if "numero_client" in data:
        val = data.get("numero_client")
        if val is None:
            pass
        else:
            # si c'est un int → garder
            if isinstance(val, int):
                # rien à faire
                pass
            else:
                s = str(val).strip()
                # supprimer espaces et séparateurs usuels
                s_digits = re.sub(r"\D", "", s)
                # si la version ne contient que des chiffres et non vide -> considérer numéro
                if s_digits and len(s_digits) >= 4 and s_digits == re.sub(r"\D", "", s):
                    # convertir en int si nécessaire
                    try:
                        data["numero_client"] = int(s_digits)
                    except Exception:
                        data["numero_client"] = s_digits
                else:
                    # contient des lettres → c'est probablement un nom
                    # ne pas écraser un nom existant
                    if "nom_client" not in data or not data.get("nom_client"):
                        data["nom_client"] = s
                    data["numero_client"] = None

    return data


def prune_none(obj):
    """Retourne une copie de obj sans les clefs dont la valeur est None.
    Fonction récursive qui nettoie dicts et listes.
    """
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if v is None:
                continue
            cleaned = prune_none(v)
            # si cleaned devient vide dict/list, on le garde (pouvant être utile)
            new[k] = cleaned
        return new
    elif isinstance(obj, list):
        new_list = [prune_none(i) for i in obj]
        # garder éléments non vides
        return [i for i in new_list if i is not None]
    else:
        return obj


def normalize_for_search(s: str) -> str:
    """Normalize a string for fuzzy search in OCR content: remove accents, lower, collapse whitespace."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def has_evidence_in_ocr(value, ocr_text: str) -> bool:
    """Return True if `value` seems present in `ocr_text`.
    Conservative heuristics:
    - for dict/list: require at least one evidenced child
    - for numbers: compare digits-only
    - for strings: normalized substring match
    """
    if value is None:
        return False

    if isinstance(value, dict):
        for v in value.values():
            if has_evidence_in_ocr(v, ocr_text):
                return True
        return False

    if isinstance(value, list):
        for it in value:
            if has_evidence_in_ocr(it, ocr_text):
                return True
        return False

    # Normalize both
    norm_val = normalize_for_search(value)
    norm_text = normalize_for_search(ocr_text)

    # If value contains digits and non-digits, try digits-only match too
    digits = re.sub(r"\D", "", norm_val)
    if digits:
        if digits in re.sub(r"\D", "", norm_text):
            return True

    # Try direct substring
    if norm_val and norm_val in norm_text:
        return True

    # Special handling: dates like YYYY-MM-DD vs DD/MM/YYYY and French text dates
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", norm_val)
    if m_iso:
        y, m, d = m_iso.groups()
        # Try DD/MM/YYYY format
        alt = f"{d}/{m}/{y}"
        if alt in norm_text:
            return True
        # Try DD-MM-YYYY format
        alt2 = f"{d}-{m}-{y}"
        if alt2 in norm_text:
            return True
        # Try French text format (e.g., "14 avril 2019")
        french_months = ['janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin',
                        'juillet', 'aout', 'septembre', 'octobre', 'novembre', 'decembre']
        month_idx = int(m) - 1
        if 0 <= month_idx < len(french_months):
            month_name = french_months[month_idx]
            # Try with and without leading zero on day
            for day_format in [d, str(int(d))]:
                french_date = f"{day_format} {month_name} {y}"
                if normalize_for_search(french_date) in norm_text:
                    return True
                # Try without spaces (OCR might concatenate)
                french_date_compact = f"{day_format}{month_name}{y}"
                if normalize_for_search(french_date_compact) in norm_text:
                    return True

    # Fallback: no evidence
    return False


def verify_against_ocr(obj, ocr_text: str):
    """Return a copy of obj where only fields with evidence in ocr_text are kept.
    Works recursively for dicts and lists. Conservative: if no evidence, field removed.
    Exception: preserve numeric fields that look like calculated amounts.
    """
    # Fields to always preserve even if not found in OCR (calculated fields)
    PRESERVE_FIELDS = {"montant_ht", "montant_ttc", "montant_tva", "prix_unitaire", "quantite", "montant"}
    
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            # Preserve fields that are likely calculated
            if k in PRESERVE_FIELDS and isinstance(v, (int, float)):
                new[k] = v
            elif isinstance(v, dict):
                cleaned = verify_against_ocr(v, ocr_text)
                if cleaned:
                    new[k] = cleaned
            elif isinstance(v, list):
                # For lists, apply verification recursively but preserve structure
                cleaned = verify_against_ocr(v, ocr_text)
                if cleaned:
                    new[k] = cleaned
            else:
                if has_evidence_in_ocr(v, ocr_text):
                    new[k] = v
        return new
    elif isinstance(obj, list):
        cleaned_list = []
        for it in obj:
            if isinstance(it, dict):
                # For dict items in a list, apply the same preservation logic
                cleaned_dict = {}
                for k, v in it.items():
                    # Preserve numeric fields in PRESERVE_FIELDS
                    if k in PRESERVE_FIELDS and isinstance(v, (int, float)):
                        cleaned_dict[k] = v
                    elif isinstance(v, (dict, list)):
                        cleaned = verify_against_ocr(v, ocr_text)
                        if cleaned:
                            cleaned_dict[k] = cleaned
                    else:
                        if has_evidence_in_ocr(v, ocr_text):
                            cleaned_dict[k] = v
                if cleaned_dict:
                    cleaned_list.append(cleaned_dict)
            else:
                cleaned = verify_against_ocr(it, ocr_text)
                if cleaned:
                    cleaned_list.append(cleaned)
        return cleaned_list
    else:
        return obj if has_evidence_in_ocr(obj, ocr_text) else None


class FileSourceListCreateView(generics.ListCreateAPIView):
    queryset = FileSource.objects.all().order_by('-uploaded_at')
    serializer_class = FileSourceSerializer
    permission_classes = [AllowAny]
 

@api_view(["POST"])
@permission_classes([AllowAny])
def file_source_list_create(request):
    file = request.FILES.get("file")
    if not file:
        return Response({"error": "Aucun fichier envoyé."}, status=400)

    # Générer le hash du fichier pour détecter les doublons
    file.seek(0)  # S'assurer qu'on lit depuis le début
    file_content = file.read()
    file_hash = hashlib.sha256(file_content).hexdigest()
    file.seek(0)  # Reset pour utilisation ultérieure
    
    print(f"\n🔐 HASH FICHIER: {file_hash}")
    
    # Vérifier si ce fichier existe déjà
    existing_file = FileSource.objects.filter(hash_ocr=file_hash).first()
    if existing_file:
        print(f"   ⚠️  Fichier déjà existant (ID: {existing_file.id})")
        return Response({
            "status": "duplicate",
            "message": "Ce fichier a déjà été importé.",
            "file_source": FileSourceSerializer(existing_file).data,
            "duplicate": True
        }, status=200)

    raw_json = request.data.get("extracted_json")
    if not raw_json:
        return Response({"error": "Le champ 'extracted_json' est manquant"}, status=400)

    try:
        extracted_json = json.loads(raw_json)
    except json.JSONDecodeError:
        return Response({"error": "extracted_json doit être un JSON valide"}, status=400)

    # Générer description IA
    description = generate_description(
        client=client,
        data=extracted_json,
        json=json,
        model=settings.OPENAI_MODEL
    )

    # Déterminer automatiquement le type de pièce
    piece_type = "Autres"
    type_doc = extracted_json.get("type_document", "").lower()
    type_field = extracted_json.get("type", "").lower()
    
    # Debug logging
    print(f"\n🔍 DÉTECTION TYPE DE PIÈCE:")
    print(f"   type_document: '{type_doc}'")
    print(f"   type: '{type_field}'")
    
    # Test explicite de la condition bancaire
    test_banc = ("banc" in type_doc or "banque" in type_doc or "bank" in type_doc or 
                 "banc" in type_field or "banque" in type_field or "bank" in type_field)
    print(f"   🔬 Test condition bancaire: {test_banc}")
    print(f"   🔬 'banc' in type_doc = {'banc' in type_doc}")
    print(f"   🔬 'banque' in type_doc = {'banque' in type_doc}")
    print(f"   🔬 Has numero_facture = {bool(extracted_json.get('numero_facture'))}")
    
    # Règles de détection améliorées
    if extracted_json.get("numero_facture") or extracted_json.get("invoice_number") or \
       type_doc in ["vente", "achat", "facture"] or type_field in ["vente", "achat", "facture"]:
        piece_type = "Facture"
        print(f"   ✅ Détecté comme: {piece_type}")
    
    # Détection bancaire améliorée: différencier virement et relevé
    elif ("banc" in type_doc or "banque" in type_doc or "bank" in type_doc or 
          "banc" in type_field or "banque" in type_field or "bank" in type_field):
        print("   📊 Document bancaire détecté")
        
        # Vérifier d'abord la référence bancaire
        ref_bancaire = str(
            extracted_json.get("reference") or 
            extracted_json.get("numero_piece") or
            extracted_json.get("ref") or
            ""
        )
        
        print(f"   Référence bancaire: '{ref_bancaire}'")
        
        # Si la référence commence par "VIRM", c'est un virement
        if ref_bancaire.upper().startswith("VIRM"):
            piece_type = "Virement bancaire"
            print(f"   ✅ Détecté comme: {piece_type} (référence commence par VIRM)")
        else:
            # Sinon, vérifier la structure du document
            # Un relevé bancaire contient généralement:
            # - une liste de transactions (plusieurs opérations)
            # - des champs de période (date_debut, date_fin, periode)
            # - le mot "relevé" ou "statement"
            has_transactions = (
                extracted_json.get("transactions") or 
                extracted_json.get("transactions_details") or
                extracted_json.get("operations")
            )
            has_period = (
                extracted_json.get("periode_date_start") or 
                extracted_json.get("date_debut") or
                extracted_json.get("period")
            )
            is_statement = "relev" in type_doc or "relev" in type_field or "statement" in type_doc
            
            # Si contient mot "virement" explicitement
            if "virement" in type_doc or "virement" in type_field or "transfer" in type_doc:
                piece_type = "Virement bancaire"
                print(f"   ✅ Détecté comme: {piece_type} (mot 'virement' trouvé)")
            # Si a des transactions multiples ou période ou mot "relevé"
            elif has_transactions or has_period or is_statement:
                piece_type = "Relevé bancaire"
                print(f"   ✅ Détecté comme: {piece_type} (transactions/période/relevé)")
            else:
                # Par défaut pour documents bancaires: relevé
                piece_type = "Relevé bancaire"
                print(f"   ✅ Détecté comme: {piece_type} (défaut bancaire)")
    
    
    elif "bon" in type_doc and "caisse" in type_doc:
        piece_type = "Bon de caisse"
    elif "fiche" in type_doc and "paie" in type_doc:
        piece_type = "Fiche de paie"
    elif type_field in ["bon_de_caisse", "fiche_paie"]:
        piece_type = type_field.replace("_", " ").title()
    
    # Extraire automatiquement la référence (ref_file)
    ref_file = request.data.get("ref_file")
    if not ref_file:
        # Essayer d'extraire depuis le JSON
        ref_file = (
            extracted_json.get("numero_facture") or 
            extracted_json.get("invoice_number") or
            extracted_json.get("reference") or
            extracted_json.get("numero_piece") or
            None
        )
    
    # Log final
    print(f"\n📋 RÉSULTAT FINAL:")
    print(f"   piece_type = '{piece_type}'")
    print(f"   ref_file = '{ref_file}'")
    print(f"   extracted_json.keys() = {list(extracted_json.keys())}\n")

    # Préparer les données pour le serializer
    data_to_save = {
        "file": file,
        "file_name": getattr(file, "name", ""),
        "description": description,
        "piece_type": piece_type,
        "ref_file": ref_file,
        "hash_ocr": file_hash  # Hash pour détecter les doublons
    }

    serializer = FileSourceSerializer(data=data_to_save)
    if serializer.is_valid():
        saved_file = serializer.save()

        # ✅ AUTOMATISATION : Générer le journal immédiatement via la vue
        try:
            from compta.views import generate_journal_view
            from rest_framework.test import APIRequestFactory

            print(f"🔄 Génération automatique du journal pour le fichier {saved_file.id}...")
            
            # Enrichir le JSON avec le type de pièce et calcul du HT si manquant
            gen_data = extracted_json.copy() if isinstance(extracted_json, dict) else {}
            
            # ✅ DÉTERMINATION DU type_document POUR LA COMPTABILITÉ
            # piece_type est pour l'affichage UI (Facture, Relevé bancaire, etc.)
            # type_document est pour la comptabilité (VENTE, ACHAT, BANQUE, etc.)
            if "type_document" not in gen_data or not gen_data["type_document"]:
                # Si pas de type_document, on le déduit du piece_type ET du contenu
                if piece_type == "Facture":
                    # ✅ Déterminer si c'est une VENTE ou un ACHAT
                    # ACHAT = si on a un fournisseur
                    # VENTE = si on a un client
                    if gen_data.get("fournisseur") or gen_data.get("nom_fournisseur"):
                        gen_data["type_document"] = "ACHAT"
                    elif gen_data.get("client") or gen_data.get("nom_client"):
                        gen_data["type_document"] = "VENTE"
                    else:
                        # Fallback : regarder si montant positif (vente) ou négatif (achat)
                        # Par défaut, on suppose VENTE
                        gen_data["type_document"] = "VENTE"
                elif "banc" in piece_type.lower() or "relev" in piece_type.lower():
                    gen_data["type_document"] = "BANQUE"
                elif "virement" in piece_type.lower():
                    gen_data["type_document"] = "BANQUE"
                elif "caisse" in piece_type.lower():
                    gen_data["type_document"] = "CAISSE"
                else:
                    gen_data["type_document"] = "OD"
            
            gen_data["file_source"] = saved_file.id # Important pour le lier


            # 🛠️ Calcul de sécurité pour Montant HT
            if not gen_data.get("montant_ht") and not gen_data.get("total_ht"):
                try:
                    ttc = float(gen_data.get("montant_total_facture_ttc") or gen_data.get("montant_ttc") or gen_data.get("amount_total") or 0)
                    tva = float(gen_data.get("montant_tva") or gen_data.get("tax_amount") or gen_data.get("vat_amount") or gen_data.get("tva") or 0)
                    
                    if ttc > 0 and tva > 0:
                        calculated_ht = ttc - tva
                        gen_data["montant_ht"] = round(calculated_ht, 2)
                        print(f"   🔧 HT calculé et injecté : {calculated_ht} (TTC {ttc} - TVA {tva})")
                except Exception as e:
                    print(f"   ⚠️ Impossible de calculer HT : {e}")
            
            # 🏭 Création d'une requête interne simulée pour satisfaire la vue
            factory = APIRequestFactory()
            internal_request = factory.post(
                '/api/compta/journals/generate/', 
                gen_data, 
                format='json'
            )
            
            # Appel direct de la vue avec la requête simulée
            response = generate_journal_view(internal_request)
            
            # ✅ VÉRIFICATION DU STATUT DE LA RÉPONSE
            if response.status_code in [200, 201]:
                print("✅ Journal généré avec succès via generate_journal_view.")
            else:
                error_detail = response.data.get("error", "Erreur inconnue") if hasattr(response, 'data') else "Erreur inconnue"
                print(f"❌ ÉCHEC de la génération du journal (status {response.status_code}): {error_detail}")
                print(f"   📋 Détails complets: {response.data if hasattr(response, 'data') else 'N/A'}")

        except Exception as e:
            print(f"❌ Erreur lors de la génération automatique du journal : {e}")
            import traceback
            print(f"   📋 Traceback complet:")
            traceback.print_exc()
            # On ne bloque pas la réponse, le fichier est bien sauvegardé.

        return Response({
            "status": "success",
            "message": "Document sauvegardé.",
            "file_source": serializer.data
        }, status=201)
    else:
        return Response(serializer.errors, status=400)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def form_source_list_create(request):
    if request.method == "GET":
        form_sources = FormSource.objects.all().order_by("-updated_at")
        serializer = FormSourceSerializer(form_sources, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == "POST":
        # 1. Convertir le JSON string en dict
        raw_json = request.data.get("description_json")

        if raw_json is None:
            return Response(
                {"error": "Le champ 'description_json' est manquant"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Si c'est déjà un dict => pas besoin de json.loads
        if isinstance(raw_json, dict):
            description_json = raw_json
        else:
            try:
                description_json = json.loads(raw_json)
            except json.JSONDecodeError:
                return Response(
                    {"error": "description_json doit être un JSON valide"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 2. Générer description GPT
        description = generate_description(
            client=client,
            data=description_json,
            json=json,
            model=settings.OPENAI_MODEL
        )

        # 3. Ajouter la description dans request.data **avant serializer**
        data = dict(request.data) 
        data["description"] = description

        # 4. Sérialisation
        serializer =  FormSourceSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "status": "success",
                    "message": "Sauvegarde avec succès.",
                    "form_source": serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([AllowAny])
def extract_content_file_view(request):
   
    file = request.FILES.get("file")

    if not file:
        return Response({"error": "Aucun fichier envoyé."}, status=400)

    # ✅ Détection du type de fichier
    file_type = detect_file_type(file.name)
    if file_type == "unknown":
        return Response({"error": "Type de fichier non supporté."}, status=400)

    # ✅ OCR BRUT
    content = extract_content(file, file_type)
    if not content:
        return Response({"error": "Impossible d'extraire le texte."}, status=400)

    print("\n" + "=" * 80)
    print("📄 TEXTE OCR BRUT EXTRAIT :")
    print("=" * 80)
    print(content)
    print("=" * 80 + "\n")

    # ==============================================
    # ✅ ÉTAPE 1 : DÉTECTION PIÈCE COMPTABLE
    # ==============================================
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Tu es expert comptable. Répond uniquement par OUI ou NON."},
                {"role": "user", "content": f"Voici un document : {content[:5000]}\nEst-ce une pièce comptable ?"}
            ],
            temperature=0
        )
        decision = response.choices[0].message.content.strip().lower()
    except Exception as e:
        return Response({"error": f"Erreur OpenAI vérification : {str(e)}"}, status=500)

    if decision not in ["oui", "yes"]:
        return Response({"error": "Document non reconnu comme pièce comptable."}, status=400)

    # ====================================================
    # ✅ ÉTAPE 2 : TYPE DOCUMENT
    # ====================================================
    try:
        type_response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Tu dois répondre STRICTEMENT par un seul mot parmi : ACHAT, VENTE, BANQUE, CAISSE, OD."
                },
                {
                    "role": "user",
                    "content": f"Voici un document : {content[:5000]}"
                }
            ],
            temperature=0
        )

        type_document = type_response.choices[0].message.content.strip().upper()

    except Exception as e:
        return Response({"error": f"Erreur OpenAI type document : {str(e)}"}, status=500)

    # ===================================================
    # ✅ ÉTAPE 3 : EXTRACTION IA LIBRE
    # ===================================================
    try:
        extraction = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_FIELDS_PROMPT},
                {"role": "user", "content": content[:6000]}
            ],
            temperature=0
        )

        extracted_json_str = extraction.choices[0].message.content.strip()
        extracted_json_str = clean_ai_json(extracted_json_str)

    except Exception as e:
        return Response({"error": f"Erreur OpenAI extraction : {str(e)}"}, status=500)

    # ==========================
    # ✅ CONVERSION JSON PYTHON
    # ==========================
    try:
        extracted_json = json.loads(extracted_json_str)
    except json.JSONDecodeError:
        return Response({
            "error": "JSON IA invalide",
            "raw": extracted_json_str
        }, status=500)

    # ============================================
    # ✅✅ ✅ CORRECTION FORCÉE CLIENT / FACTURE
    # ============================================

    cleaned_text = content.replace(" ", "").replace(",", ".")

    match = re.search(r"1\.\d{8}\d{9,}", cleaned_text)

    if match:
        full = match.group(0)
        extracted_json["client_number"] = full[:10]
        extracted_json["invoice_number"] = full[10:]
    else:
        # ✅ SECOURS : on attrape tout le bloc brut si mal séparé
        fallback = re.search(r"1\.\d{8}\d{9,}", cleaned_text)

        if fallback:
            full = fallback.group(0)
            extracted_json["client_number"] = full[:10]
            extracted_json["invoice_number"] = full[10:]

    # ==========================
    # ✅ AJOUT TYPE DOCUMENT
    # ==========================
    extracted_json["type_document"] = type_document

    # ==========================
    # ✅ FORMAT TVA
    # ==========================
    if "vat_rate" in extracted_json and "vat_amount" in extracted_json:
        extracted_json["tva"] = {
            "taux": extracted_json.pop("vat_rate"),
            "montant": extracted_json.pop("vat_amount")
        }

    if "total_invoice_amount" in extracted_json:
        extracted_json["montant_total_facture_ttc"] = extracted_json.pop("total_invoice_amount")

    extracted_json.pop("total_to_pay", None)

    # ==========================
    # ✅ AFFICHAGE FINAL + TRADUCTION CLEFS EN FRANÇAIS
    # ==========================

    # mapping des clefs anglais -> français (ajouter d'autres clefs si nécessaire)
    keys_mapping = {
        "client_number": "numero_client",
        "client": "nom_client",
        "client_name": "nom_client",
        "invoice_number": "numero_facture",
        "bank": "banque",
        "bank_name": "nom_banque",
        "bank_account": "numero_compte_bancaire",
        "vat_rate": "taux_tva",
        "vat_amount": "montant_tva",
        "tax_amount": "montant_tva",
        "total_invoice_amount": "montant_ttc",
        "total": "montant_ttc",
        "total_ttc": "montant_ttc",
        "amount_total": "montant_ttc",
        "subtotal": "sous_total",
        "subtotal_ht": "montant_ht",
        "total_ht": "montant_ht",
        "amount_ht": "montant_ht",
        "currency": "devise",
        "supplier": "fournisseur",
        "supplier_name": "nom_fournisseur",
        # Date field mappings - all variations map to date_facture
        "date": "date_facture",
        "date_facture": "date_facture",
        "date_document": "date_facture",
        "invoice_date": "date_facture",
        "document_date": "date_facture",
        "emission_date": "date_facture",
        "date_emission": "date_facture",
        "issue_date": "date_facture",
        "due_date": "date_echeance",
        "date_echeance": "date_echeance",
        "payment_date": "date_echeance",
        "description": "description",
        "type_document": "type_document",
        "items": "details",
        "details": "details",
        "quantity": "quantite",
        "unit_price": "prix_unitaire",
        "price": "prix_unitaire",
        "amount": "montant"
    }

    # Traduction récursive des clefs
    extracted_json_fr = translate_keys(extracted_json, keys_mapping)

    # Normalisation spécifique client / numéro
    extracted_json_fr = normalize_client_fields(extracted_json_fr)

    # Vérifier que chaque champ retourné a une preuve dans le texte OCR brut
    extracted_json_fr = verify_against_ocr(extracted_json_fr, content)

    # Supprimer les clefs null pour ne pas renvoyer d'informations "inventées"
    extracted_json_fr = prune_none(extracted_json_fr)

    # ✅ NORMALISATION DES DATES AU FORMAT ISO
    # S'assurer que toutes les dates sont au format YYYY-MM-DD avant envoi au frontend
    date_fields = ['date_facture', 'date_echeance', 'date_emission', 'date_document', 'date']
    for field in date_fields:
        if field in extracted_json_fr and extracted_json_fr[field]:
            try:
                normalized = normalize_date_to_iso(extracted_json_fr[field])
                if normalized:
                    extracted_json_fr[field] = normalized
            except Exception as e:
                print(f"   ⚠️ Impossible de normaliser {field}: {e}")

    # Fallback : si le modèle n'a pas fourni la banque, tenter d'extraire "Banque : ..." depuis le texte OCR
    if "banque" not in extracted_json_fr or not extracted_json_fr.get("banque"):
        # match banque but stop before the next label or a number (montant, chiffre)
        # tolerate collated words like 'BNIMadagascarMontant' by not using word-boundaries in lookahead
        m = re.search(r"banque\s*[:\-]?\s*([A-Za-zÀ-ÿ\- ]{2,200}?)\s*(?=montant|\d|$)", content, flags=re.I)
        if m:
            val = m.group(1).strip()
            # normaliser quelques séparateurs collés par l'OCR
            val = re.sub(r"\s+", " ", val)
            # retirer mots résiduels comme 'montant' ou ':' s'ils sont collés
            val = re.sub(r"(?i)\bmontant\b[:\s]*$", "", val).strip()
            # insérer un espace entre acronymes collés et mot suivant (ex: BNIMadagascar -> BNI Madagascar)
            val = re.sub(r"([A-Z]{2,})([A-Z][a-z]+)", r"\1 \2", val)
            # séparer lettres/chiffres collés (ex: BNI123 -> BNI 123)
            val = re.sub(r"([A-Za-z])(\d)", r"\1 \2", val)
            val = re.sub(r"(\d)([A-Za-z])", r"\1 \2", val)
            val = val.strip()
            extracted_json_fr["banque"] = val

    # Helper pour normaliser les dates vers le format ISO (YYYY-MM-DD)
    def normalize_date_to_iso(date_str: str) -> str:
        """Normalize various date formats to ISO format (YYYY-MM-DD).
        Handles:
        - French text dates: "14 Avril 2019" -> "2019-04-14"
        - DD/MM/YYYY: "14/04/2019" -> "2019-04-14"
        - DD-MM-YYYY: "14-04-2019" -> "2019-04-14"
        - Already ISO: "2019-04-14" -> "2019-04-14"
        """
        if not date_str:
            return date_str
        
        date_str = str(date_str).strip()
        
        # French month names mapping
        french_months = {
            'janvier': '01', 'février': '02', 'fevrier': '02', 'mars': '03',
            'avril': '04', 'mai': '05', 'juin': '06', 'juillet': '07',
            'août': '08', 'aout': '08', 'septembre': '09', 'octobre': '10',
            'novembre': '11', 'décembre': '12', 'decembre': '12',
            # Abbreviated forms
            'janv': '01', 'févr': '02', 'fevr': '02', 'avr': '04',
            'juil': '07', 'sept': '09', 'oct': '10', 'nov': '11', 'déc': '12', 'dec': '12'
        }
        
        # Try French text format: "14 Avril 2019" or "14Avril2019"
        match = re.search(r'(\d{1,2})\s*([a-zéèêàâû]+)\s*(\d{4})', date_str, re.I)
        if match:
            day, month_name, year = match.groups()
            month_name_lower = normalize_for_search(month_name)
            for fr_month, num in french_months.items():
                if fr_month in month_name_lower or month_name_lower in fr_month:
                    return f"{year}-{num}-{day.zfill(2)}"
        
        # Try DD/MM/YYYY or DD-MM-YYYY
        match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_str)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        
        # Try YYYY-MM-DD (already ISO)
        match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        
        # Return original if no pattern matched
        return date_str

    # Helper pour nettoyer les collages OCR (ex: BNIMadagascarMontant -> BNI Madagascar)
    def clean_collated_value(val: str) -> str:
        if not val:
            return val
        v = val.strip()
        v = re.sub(r"\s+", " ", v)
        # retirer éventuels mots résiduels collés en fin
        v = re.sub(r"(?i)\bmontant\b[:\s]*$", "", v).strip()
        # insérer un espace entre acronymes collés et mot suivant (ex: BNIMadagascar -> BNI Madagascar)
        v = re.sub(r"([A-Z]{2,})([A-Z][a-z]+)", r"\1 \2", v)
        # insérer un espace entre lower->Upper (camelCase) (ex: ImprimerieGraphix -> Imprimerie Graphix)
        v = re.sub(r"([a-zà-ÿ])([A-Z])", r"\1 \2", v)
        # séparer lettres/chiffres collés (ex: BNI123 -> BNI 123)
        v = re.sub(r"([A-Za-z])(\d)", r"\1 \2", v)
        v = re.sub(r"(\d)([A-Za-z])", r"\1 \2", v)
        v = re.sub(r"\s+", " ", v).strip()
        return v

    # FallBacks pour autres champs cruciaux si absents : societe, reference, remarques
    # On recherche les labels dans l'OCR (tolérant aux collages et casse)
    if "societe" not in extracted_json_fr or not extracted_json_fr.get("societe"):
        # tolerate collated labels/values
        m = re.search(r"(?:societe|soci[eé]t[eé]|client)\s*[:\-]?\s*([A-Za-zÀ-ÿ0-9\-\._ ]{2,200}?)\s*(?=(?:reference|ref|montant|date|objet|remarques|$)|\d)", content, flags=re.I)
        if m:
            val = m.group(1).strip()
            extracted_json_fr["societe"] = clean_collated_value(val)

    if "reference" not in extracted_json_fr or not extracted_json_fr.get("reference"):
        m = re.search(r"(?:reference|r[eé]f)\s*[:\-]?\s*([A-Za-zÀ-ÿ0-9\-\._/ ]{1,200}?)\s*(?=(?:montant|date|objet|remarques|$)|\n)", content, flags=re.I)
        if m:
            val = m.group(1).strip()
            extracted_json_fr["reference"] = clean_collated_value(val)

    if "remarques" not in extracted_json_fr or not extracted_json_fr.get("remarques"):
        m = re.search(r"(?:remarques|remarque|observations|notes)\s*[:\-]?\s*([A-Za-zÀ-ÿ0-9\-\._,;:/()\\\n ]{1,500}?)\s*(?=(?:reference|montant|date|objet|$)|\n)", content, flags=re.I)
        if m:
            val = m.group(1).strip()
            # nettoyer collages et remettre ponctuation correcte
            val = clean_collated_value(val)
            # remettre quelques espaces après signes de ponctuation collés par OCR
            val = re.sub(r"([,;:\.])(\w)", r"\1 \2", val)
            extracted_json_fr["remarques"] = val

    # Déduplication client/société : garder `nom_client` comme champ canonique.
    def dedupe_nom_client_and_societe(obj: dict):
        if not isinstance(obj, dict):
            return obj
        nom = obj.get("nom_client")
        soc = obj.get("societe")
        def norm(s):
            if s is None:
                return ""
            return re.sub(r"\s+", " ", unicodedata.normalize('NFKD', str(s)).strip()).lower()

        if nom and soc:
            if norm(nom) == norm(soc):
                # duplicata -> supprimer `societe`
                obj.pop("societe", None)
        elif not nom and soc:
            # promote societe -> nom_client
            obj["nom_client"] = obj.pop("societe")

        return obj

    extracted_json_fr = dedupe_nom_client_and_societe(extracted_json_fr)

    # Fallback pour fournisseur (supplier) si absent
    if "fournisseur" not in extracted_json_fr or not extracted_json_fr.get("fournisseur"):
        m = re.search(r"(?:fournisseur|supplier|vendeur)\s*[:\-]?\s*([A-Za-zÀ-ÿ0-9\-\._ ]{2,200}?)\s*(?=(?:reference|ref|montant|date|objet|$)|\d)", content, flags=re.I)
        if m:
            val = m.group(1).strip()
            extracted_json_fr["fournisseur"] = clean_collated_value(val)

    print("\n✅ JSON FINAL NETTOYÉ (FR) :")
    print("=" * 80)
    print(json.dumps(extracted_json_fr, indent=2, ensure_ascii=False))
    print("=" * 80 + "\n")

    return Response({
        "status": "success",
        "message": "OCR + extraction + correction facture/client réussis.",
        "type_document": type_document,
        "ocr_brut": content,
        "extracted_json": extracted_json_fr
    }, status=201)
