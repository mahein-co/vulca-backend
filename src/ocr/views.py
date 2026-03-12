import json
import re
import unicodedata
import hashlib
import pandas as pd
from datetime import date
from django.db import OperationalError
from vulca_backend import settings

from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from ocr.models import FileSource, FormSource
from compta.models import Bilan, CompteResultat, Project
from ocr.serializers import FileSourceSerializer, FormSourceSerializer
from ocr.utils import safe_openai_call, clean_ai_json, detect_file_type, generate_description
from ocr.openai_vision_ocr import extract_content_with_vision
from ocr.constants import UNIFIED_EXTRACTION_PROMPT
from compta.permissions import HasProjectAccess

from openai import OpenAI
client = OpenAI(api_key=settings.OPENAI_API_KEY) 


def normalize_phone(phone: str) -> str:
    """Normalise un numéro de téléphone : enlève points, espaces, tirets."""
    if not phone:
        return phone
    return re.sub(r"[\s\.\-]", "", str(phone))


def normalize_date_to_iso(date_str: str) -> str:
    """Normalise diverses formats de dates vers ISO (YYYY-MM-DD)."""
    if not date_str:
        return date_str
    
    date_str = str(date_str).strip()
    
    french_months = {
        'janvier': '01', 'février': '02', 'fevrier': '02', 'mars': '03',
        'avril': '04', 'mai': '05', 'juin': '06', 'juillet': '07',
        'août': '08', 'aout': '08', 'septembre': '09', 'octobre': '10',
        'novembre': '11', 'décembre': '12', 'decembre': '12'
    }
    
    # Format texte français : "14 Avril 2019"
    match = re.search(r'(\d{1,2})\s*([a-zéèêàâû]+)\s*(\d{4})', date_str, re.I)
    if match:
        day, month_name, year = match.groups()
        month_name_lower = unicodedata.normalize('NFKD', month_name.lower()).encode('ascii', 'ignore').decode('ascii')
        for fr_month, num in french_months.items():
            fr_month_norm = unicodedata.normalize('NFKD', fr_month).encode('ascii', 'ignore').decode('ascii')
            if fr_month_norm in month_name_lower or month_name_lower in fr_month_norm:
                return f"{year}-{num}-{day.zfill(2)}"
    
    # Format DD/MM/YYYY ou DD-MM-YYYY
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_str)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # Déjà ISO YYYY-MM-DD
    match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    return date_str


def normalize_extracted_json(data: dict, ocr_text: str = "") -> dict:
    """
    Post-traite le JSON extrait par l'IA pour garantir cohérence et nettoyage.
    """
    if not isinstance(data, dict):
        return data
    
    # 1. Normaliser les téléphones
    for field in ['telephone_client', 'telephone_commercial', 'telephone']:
        if field in data and data[field]:
            data[field] = normalize_phone(data[field])
    
    # 2. Normaliser les dates vers ISO
    date_fields = [
        'date_facture', 'date_echeance', 'date_emission', 'date_document', 'date',
        'date_paie', 'date_bon', 'date_operation', 'date_valeur', 'date_transaction'
    ]
    for field in date_fields:
        if field in data and data[field]:
            data[field] = normalize_date_to_iso(data[field])

    # S'assurer qu'il y a un champ 'date' générique
    if 'date' not in data or not data.get('date'):
        for k in date_fields:
            if k != 'date' and data.get(k):
                data['date'] = data[k]
                break
    
    # 3. Convertir devise "Ar" ou "Ariary" → "MGA"
    if 'devise' in data:
        devise = str(data['devise']).strip().upper()
        if devise in ['AR', 'ARIARY', 'ARIARIES']:
            data['devise'] = 'MGA'
    
    # 4. S'assurer que description est un array
    if 'description' in data:
        if isinstance(data['description'], str):
            # Convertir string → array en splittant intelligemment
            desc_str = data['description']
            # Split par lignes ou virgules
            items = re.split(r',\s*|\n', desc_str)
            data['description'] = [item.strip() for item in items if item.strip()]
    
    # 5. Fallback : extraire banque depuis OCR si manquant
    if ocr_text and ('banque' not in data or not data.get('banque')):
        m = re.search(r"banque\s*[:\-]?\s*([A-Za-zÀ-ÿ\- ]{2,100}?)(?=\s*(?:montant|compte|reference|\d|$))", ocr_text, flags=re.I)
        if m:
            val = m.group(1).strip()
            val = re.sub(r"\s+", " ", val)
            val = re.sub(r"([A-Z]{2,})([A-Z][a-z]+)", r"\1 \2", val)
            data['banque'] = val
    
    # 6. S'assurer qu'on a une référence
    if 'reference' not in data or not data.get('reference'):
        data['reference'] = (
            data.get("numero_facture") or 
            data.get("numero_piece") or
            data.get("identifiant") or
            None
        )
    
    # 7. Normalisation client : si numero_client contient lettres → nom_client
    if 'numero_client' in data:
        val = data.get('numero_client')
        if val and not str(val).isdigit():
            # Contient des lettres → c'est un nom
            if 'nom_client' not in data or not data.get('nom_client'):
                data['nom_client'] = str(val)
            data['numero_client'] = None
    
    # 8. Supprimer les clés null
    return {k: v for k, v in data.items() if v is not None}


def translate_keys(obj, mapping):
    """Renomme les clés récursivement selon mapping."""
    if isinstance(obj, dict):
        return {mapping.get(k, k): translate_keys(v, mapping) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [translate_keys(item, mapping) for item in obj]
    return obj


def fallback_invoice_number(content: str) -> str:
    """Tente d'extraire un numéro de facture par regex depuis le texte OCR brut."""
    import re
    from datetime import datetime
    patterns = [
        r'(?:Facture|FACTURE|Invoice|INVOICE|Devis|DEVIS|Proforma)\s*N[°o]?\.?\s*:?\s*([A-Z0-9][\w\-/]{1,30})',
        r'N[°o]?\.?\s*(?:Facture|Invoice|Devis)?\s*:?\s*([A-Z0-9][\w\-/]{1,30})',
        r'(?:Ref|REF|Réf|Référence)\s*:?\s*([A-Z0-9][\w\-/]{1,30})',
        r'#([A-Z0-9][\w\-]{2,20})',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    # Fallback : timestamp unique
    return f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S')}"


class FileSourceListCreateView(generics.ListCreateAPIView):
    serializer_class = FileSourceSerializer
    permission_classes = [IsAuthenticated, HasProjectAccess]

    def get_queryset(self):
        project_id = getattr(self.request, 'project_id', None)
        if not project_id:
            return FileSource.objects.none()
        return FileSource.objects.filter(project_id=project_id).order_by('-uploaded_at')


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def file_source_list_create(request):
    project_id = getattr(request, 'project_id', None)  # Injecté par middleware
    if not project_id:
        return Response({"error": "Project non fourni (middleware absent ou non autorisé)."}, status=400)

    file = request.FILES.get("file")
    if not file:
        return Response({"error": "Aucun fichier envoyé."}, status=400)

    # Hash pour détecter doublons
    file.seek(0)
    file_content = file.read()
    file_hash = hashlib.sha256(file_content).hexdigest()
    file.seek(0)

    # Vérifier doublons DANS LE MÊME PROJET
    existing_file = FileSource.objects.filter(
        hash_ocr=file_hash,
        project_id=project_id
    ).first()
    if existing_file:
        return Response({
            "status": "duplicate",
            "message": "Ce fichier a déjà été importé pour ce projet.",
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

    # Déterminer type de pièce (logique inchangée)
    piece_type = "Autres"
    type_doc = extracted_json.get("type_document", "").lower()
    type_field = extracted_json.get("type", "").lower()
    has_banque_field = bool(extracted_json.get("banque") or extracted_json.get("nom_banque"))

    if has_banque_field or any(k in type_doc for k in ["banc", "banq", "relev", "virement"]):
        ref_bancaire = str(extracted_json.get("reference", ""))
        if ref_bancaire.upper().startswith("VIRM") or "virement" in type_doc:
            piece_type = "Virement bancaire"
        else:
            piece_type = "Relevé bancaire"
    elif "fiche" in type_doc and "paie" in type_doc:
        piece_type = "Fiche de paie"
    elif "bon" in type_doc and "caisse" in type_doc:
        piece_type = "Bon d'achat"
    elif extracted_json.get("numero_facture") or type_doc in ["vente", "achat", "facture"]:
        piece_type = "Facture"

    ref_file = request.data.get("ref_file") or extracted_json.get("numero_facture") or extracted_json.get("reference")

    # Extraire date
    date_keys = [
        "date", "date_facture", "date_emission", "date_document",
        "date_paie", "date_bon", "date_operation", "date_valeur", "date_transaction"
    ]
    date_val = request.data.get("date")
    if not date_val:
        for k in date_keys:
            if extracted_json.get(k):
                date_val = extracted_json.get(k)
                break

    data_to_save = {
        "file": file,
        "project": project_id,  # utiliser 'project' pour le serializer
        "file_name": getattr(file, "name", ""),
        "description": description,
        "piece_type": piece_type,
        "ref_file": ref_file,
        "date": date_val,
        "is_ocr_processed": True,
        "hash_ocr": file_hash
    }

    serializer = FileSourceSerializer(data=data_to_save)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    saved_file = serializer.save()

    # Génération automatique du journal (SYNCHRONE)
    try:
        from compta.views import process_journal_generation

        gen_data = extracted_json.copy() if isinstance(extracted_json, dict) else {}

        # Forcer type_document BANQUE si bancaire
        if any(k in piece_type.lower() for k in ["banc", "banq", "relev", "virement", "salaire", "paiement", "cheque", "chèque", "retrait", "depot", "dépôt"]):
            gen_data["type_document"] = "BANQUE"
        elif "type_document" not in gen_data:
            if piece_type == "Facture":
                if gen_data.get("fournisseur"):
                    gen_data["type_document"] = "ACHAT"
                else:
                    gen_data["type_document"] = "VENTE"
            elif "caisse" in piece_type.lower():
                gen_data["type_document"] = "CAISSE"
            else:
                gen_data["type_document"] = "OD"

        gen_data["file_source"] = saved_file.id

        # Calcul HT si manquant
        if not gen_data.get("montant_ht"):
            try:
                ttc = float(gen_data.get("montant_ttc") or 0)
                tva = float(gen_data.get("montant_tva") or 0)
                if ttc > 0 and tva > 0:
                    gen_data["montant_ht"] = round(ttc - tva, 2)
            except:
                pass

        result = process_journal_generation(
            document_json=gen_data,
            project_id=project_id,
            file_source=saved_file,
            form_source=None
        )
    except Exception as e:
        import traceback
        print("[ERROR] Erreur generation journal:", e)
        print(traceback.format_exc())

    return Response({
        "status": "success",
        "message": "Document et journal enregistrés avec succès.",
        "file_source": FileSourceSerializer(saved_file).data
    }, status=201)

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def form_source_list_create(request):
    project_id = getattr(request, 'project_id', None)  # Injecté par middleware
    if not project_id:
        return Response({"error": "Project non fourni (middleware absent ou non autorisé)."}, status=400)

    if request.method == "GET":
        form_sources = FormSource.objects.filter(project_id=project_id).order_by("-updated_at")
        serializer = FormSourceSerializer(form_sources, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # POST
    raw_json = request.data.get("description_json")
    if raw_json is None:
        return Response({"error": "Le champ 'description_json' est manquant"}, status=status.HTTP_400_BAD_REQUEST)

    if isinstance(raw_json, dict):
        description_json = raw_json
    else:
        try:
            description_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return Response({"error": "description_json doit être un JSON valide"}, status=status.HTTP_400_BAD_REQUEST)

    description = generate_description(
        client=client,
        data=description_json,
        json=json,
        model=settings.OPENAI_MODEL
    )

    data = dict(request.data)
    data["description"] = description
    data["data_json"] = description_json  # Sauvegarder les données brutes
    data["project"] = project_id  # utiliser 'project' pour le serializer

    # Extraire date si manquante
    date_val = data.get("date")
    if not date_val and isinstance(description_json, dict):
        date_keys = [
            "date_facture", "date_emission", "date_document",
            "date_paie", "date_bon", "date_operation", "date_valeur", "date_transaction", "date"
        ]
        for k in date_keys:
            if description_json.get(k):
                date_val = description_json.get(k)
                break

    if date_val:
        data["date"] = normalize_date_to_iso(date_val)

    serializer = FormSourceSerializer(data=data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    saved = serializer.save()
    return Response({
        "status": "success",
        "message": "Sauvegarde avec succès.",
        "form_source": FormSourceSerializer(saved).data,
    }, status=status.HTTP_201_CREATED)

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def extract_content_file_view(request):
    file = request.FILES.get("file")
    if not file:
        return Response({"error": "Aucun fichier envoyé."}, status=400)

    file_type = detect_file_type(file.name)
    print(f"\n[DEBUG] === DEBUT EXTRACTION POUR : {file.name} ===")
    print(f"[DEBUG] Type detecte : {file_type}")
    
    if file_type == "unknown":
        print(f"[ERROR] Type de fichier non supporté : {file.name}")
        return Response({"error": "Type de fichier non supporté."}, status=400)

    # Mots-clés indiquant un refus/réponse bavarde de l'API Vision (pas un vrai texte OCR)
    REFUSAL_KEYWORDS = [
        "je suis désolé", "je ne peux pas", "i'm sorry", "i cannot",
        "i'm unable", "i'm here to help", "je ne suis pas", "please provide", "veuillez fournir"
    ]

    def is_ocr_refusal(text: str) -> bool:
        """Détecte si l'IA Vision a refusé de lire l'image."""
        text_lower = (text or "").lower().strip()
        return any(kw in text_lower for kw in REFUSAL_KEYWORDS)

    # OCR avec OpenAI Vision API
    try:
        content = extract_content_with_vision(file, file_type, client, settings.OPENAI_MODEL)

        # Détecter les réponses vides ou de refus, et réessayer
        if not content or content.strip() == "[VIDE]" or len(content.strip()) < 10 or is_ocr_refusal(content):
            reason = "refus IA" if is_ocr_refusal(content) else "vide/insuffisant"
            print(f"[WARNING] OCR {reason} pour {file.name}, nouvelle tentative... ('{(content or '')[:80]}'...)")
            file.seek(0)
            content = extract_content_with_vision(file, file_type, client, settings.OPENAI_MODEL)

        if not content or content.strip() == "[VIDE]" or len(content.strip()) < 10 or is_ocr_refusal(content):
            print(f"[ERROR] OCR échoué même après retry pour {file.name}")
            print(f"[DEBUG] Contenu OCR invalide : {(content or 'VIDE')[:150]}")
            return Response({"error": f"Impossible d'extraire le texte du fichier '{file.name}'. Assurez-vous que l'image est lisible."}, status=400)
    except Exception as e:
        print(f"[ERROR] Exception OCR pour {file.name} : {str(e)}")
        return Response({"error": f"Erreur OCR : {str(e)}"}, status=500)

    print(f"[DEBUG] Texte OCR extrait ({len(content)} chars)")
    print(f"[DEBUG] Aperçu du contenu : \n{content[:500]}...")

    # ANALYSE ET EXTRACTION UNIFIEE
    try:
        response = safe_openai_call(
            client=client,
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": f"{UNIFIED_EXTRACTION_PROMPT}\n\nFICHIER : {file.name}"},
                {"role": "user", "content": content}
            ],
            temperature=0
        )
        extracted_json_str = clean_ai_json(response.choices[0].message.content.strip())
        extracted_json = json.loads(extracted_json_str)
        print(f"[DEBUG] Analyse unifiee reussie pour {file.name}")
    except Exception as e:
        print(f"[ERROR] Echec analyse unifiee pour {file.name} : {str(e)}")
        return Response({"error": f"Erreur analyse : {str(e)}"}, status=500)

    # Vérification reconnaissance (très permissive - on n'accepte de rejeter que si is_professional est EXACTEMENT False)
    is_professional = extracted_json.get("is_professional", True)
    if is_professional is False:  # strict boolean False only
        print(f"[WARNING] Document marque non-pro par IA : {file.name}")
        # On accepte quand même si le JSON contient des données financières
        has_financial_data = any([
            extracted_json.get("montant_ttc"),
            extracted_json.get("montant_ht"),
            extracted_json.get("numero_facture"),
        ])
        if has_financial_data:
            print(f"[INFO] Document accepté malgré flag non-pro car données financières présentes : {file.name}")
        else:
            return Response({"error": "Document non reconnu comme pièce comptable."}, status=400)

    type_document = extracted_json.get("document_type", "OD")

    # Supprimer les champs internes (ne pas envoyer au frontend)
    INTERNAL_FIELDS = ["is_professional", "document_type"]
    for f in INTERNAL_FIELDS:
        extracted_json.pop(f, None)

    extracted_json["numero_facture"] = extracted_json.get("numero_facture") or fallback_invoice_number(content)
    
    # Dates
    raw_date = extracted_json.get("date")
    if raw_date:
        extracted_json["date_facture"] = normalize_date_to_iso(raw_date)
    
    # Montants
    extracted_json["montant_ht"] = extracted_json.get("montant_ht", 0)
    extracted_json["montant_tva"] = extracted_json.get("montant_tva", 0)
    extracted_json["montant_ttc"] = extracted_json.get("montant_ttc", 0)
    
    # Designation
    designation = extracted_json.get("description", f"Extraction {type_document}")

    # Ajouter type_document
    content_lower = content.lower()
    is_bank = any(k in content_lower for k in ["banque", "relev", "virement", "statement"])
    
    if is_bank:
        extracted_json["type_document"] = "BANQUE"
    else:
        extracted_json["type_document"] = type_document

    # [WARNING] FALLBACK : Extraction du numéro de facture si l'IA ne l'a pas trouvé
    if not extracted_json.get("numero_facture"):
        print("[WARNING] Numero de facture manquant, tentative d'extraction par regex...")
        
        # Patterns de recherche pour numéro de facture (PAR ORDRE DE PRIORITÉ)
        # Les patterns plus spécifiques en premier pour éviter les faux positifs
        patterns = [
            # Patterns très spécifiques (haute priorité)
            r'(?:N[°o]|Num[ée]ro|Number)[\s:]*(?:Facture|Invoice|Bill|Devis|Proforma)[\s:]*(\w+[-/]?\w+)',  # N° Facture/Devis: XXX
            r'(?:Facture|Invoice|Bill|Devis|Proforma)[\s:]*(?:N[°o]|Num[ée]ro|#)[\s:]*(\w+[-/]?\w+)',  # Facture/Devis N°: XXX
            r'(?:R[ée]f[ée]rence|Ref)[\s:]*(?:Facture|Invoice|Devis|Proforma)?[\s:]*(\w+[-/]?\w+)',  # Référence: XXX
            
            # Patterns pour formats longs (numéros de 6+ chiffres)
            r'(?:NeFacure|N[°o]Facture|NumFacture)[\s:]*(\d{6,})',  # NeFacure 0000636289
            r'(?:^|\s)(\d{6,})(?=\s|$)',  # Numéro isolé de 6+ chiffres
            
            # Patterns standards
            r'FACTURE[\s:]+N[°o][\s:]*(\S+)',  # FACTURE N°001
            r'Invoice[\s:]*[#:][\s:]*(\S+)',  # Invoice #001
            r'NUM[ÉE]RO[\s:]+(\S+)',  # NUMÉRO: 001
            
            # Patterns génériques (basse priorité - peuvent capturer des faux positifs)
            r'N[°o][\s:]*(\d{4,})',  # N° suivi de 4+ chiffres minimum
            r'#(\d{3,})',  # # suivi de 3+ chiffres minimum
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
            if match:
                numero = match.group(1).strip()
                # Validation : ignorer si c'est juste "1" ou trop court
                if len(numero) >= 3 or (len(numero) >= 1 and not numero.isdigit()):
                    extracted_json["numero_facture"] = numero
                    print(f"[SUCCESS] Numero de facture extrait par regex : {numero}")
                    break
        
        if not extracted_json.get("numero_facture"):
            print("[ERROR] Aucun numero de facture trouve, meme avec regex")
            # Générer un numéro temporaire basé sur la date
            from datetime import datetime
            temp_num = f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            extracted_json["numero_facture"] = temp_num
            print(f"[WARNING] Numero temporaire genere : {temp_num}")

    print("\n[SUCCESS] JSON FINAL NORMALISE :")
    print("=" * 80)
    print(json.dumps(extracted_json, indent=2, ensure_ascii=True))
    print("=" * 80 + "\n")

    return Response({
        "status": "success",
        "message": "OCR + extraction réussis.",
        "type_document": extracted_json.get("type_document", type_document),
        "ocr_brut": content,
        "extracted_json": extracted_json
    }, status=201)

# ...existing code...
@api_view(["GET"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def all_pieces_list_view(request):
    """
    Retourne la liste combinée de FileSource et FormSource pour affichage dans GestionPiecesBoard
    Filtrée STRICTEMENT par project_id injecté par le middleware.
    """
    project_id = getattr(request, 'project_id', None)  # Injecté par middleware
    if not project_id:
        return Response({"status": "success", "count": 0, "pieces": []}, status=200)

    # Récupérer les paramètres de date
    date_start = request.GET.get("date_start")
    date_end = request.GET.get("date_end")

    # Récupérer tous les FileSource DU PROJET
    file_sources = FileSource.objects.filter(project_id=project_id).order_by('-uploaded_at')

    # Récupérer tous les FormSource DU PROJET
    form_sources = FormSource.objects.filter(project_id=project_id).order_by('-created_at')

    # FILTRAGE PAR DATE
    if date_start:
        file_sources = file_sources.filter(date__gte=date_start)
        form_sources = form_sources.filter(date__gte=date_start)

    if date_end:
        file_sources = file_sources.filter(date__lte=date_end)
        form_sources = form_sources.filter(date__lte=date_end)

    pieces = []

    for fs in file_sources:
        ptype = fs.piece_type or "Autres"
        if ptype == "Bon de caisse":
            ptype = "Bon d'achat"

        piece_date = fs.date
        if piece_date and hasattr(piece_date, 'isoformat'):
            piece_date = piece_date.isoformat()
        elif piece_date:
            piece_date = str(piece_date)

        pieces.append({
            "id": f"file_{fs.id}",
            "source_type": "file",
            "source_label": "Via OCR",
            "piece_type": ptype,
            "nom": fs.file_name or "Sans nom",
            "description": fs.description or "",
            "ref": fs.ref_file or "",
            "date": piece_date,
            "created_at": fs.uploaded_at.isoformat() if fs.uploaded_at else None,
        })

    for fs in form_sources:
        ptype = fs.piece_type or "Autres"
        if ptype == "Bon de caisse":
            ptype = "Bon d'achat"

        piece_date = fs.date
        if piece_date and hasattr(piece_date, 'isoformat'):
            piece_date = piece_date.isoformat()
        elif piece_date:
            piece_date = str(piece_date)

        pieces.append({
            "id": f"form_{fs.id}",
            "source_type": "form",
            "source_label": "Saisie manuelle",
            "piece_type": ptype,
            "nom": fs.ref_file or f"Document {fs.id}",
            "description": fs.description or "",
            "ref": fs.ref_file or "",
            "date": piece_date,
            "created_at": fs.created_at.isoformat() if fs.created_at else None,
        })

    # Trier par date (plus récent en premier)
    pieces.sort(key=lambda x: x.get('date') or x.get('created_at') or '', reverse=True)

    return Response({
        "status": "success",
        "count": len(pieces),
        "pieces": pieces
    }, status=200)


# ============================================================================
# ENDPOINTS POUR L'IMPORTATION EXCEL AVANCÉE
# ============================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def excel_upload_and_analyze_view(request):
    """
    Endpoint 1: Upload et analyse initiale d'un fichier Excel.
    """
    project_id = getattr(request, 'project_id', None)
    if not project_id:
        return Response({"error": "Project non fourni."}, status=400)

    file = request.FILES.get("file")
    if not file:
        return Response({"error": "Aucun fichier envoyé."}, status=400)
    
    if not file.name.lower().endswith(('.xlsx', '.xls')):
        return Response({
            "error": "Format de fichier non supporté. Veuillez uploader un fichier Excel (.xlsx ou .xls)."
        }, status=400)
    
    use_ocr = request.data.get("use_ocr", "false").lower() == "true"
    
    try:
        print(f"\n[INFO] DEBUT ANALYSE EXCEL (Projet: {project_id}): {file.name}")
        
        if use_ocr:
            from ocr.excel_ocr_extractor import extract_excel_with_ocr
            result = extract_excel_with_ocr(file, client, settings.OPENAI_MODEL)
        else:
            from ocr.excel_parser import ExcelParser
            parser = ExcelParser(client, settings.OPENAI_MODEL)
            result = parser.parse_excel_file(file)
            result['extraction_method'] = 'DIRECT'
        
        response_data = {
            "status": "success",
            "message": "Fichier Excel analyse avec succes.",
            "file_name": result['file_name'],
            "total_rows": result['total_rows'],
            "extraction_method": result.get('extraction_method', 'DIRECT'),
            "sheets": []
        }
        
        for sheet in result['sheets']:
            sheet_data = {
                "sheet_name": sheet['sheet_name'],
                "detected_type": sheet['detected_type'],
                "confidence": sheet['confidence'],
                "columns_mapping": sheet['columns_mapping'],
                "data_preview": sheet['data_preview'],
                "unmapped_rows": sheet['unmapped_rows'],
                "total_rows": sheet['total_rows'],
                "extraction_method": sheet.get('extraction_method', result.get('extraction_method', 'DIRECT')),
                "structured_data": sheet.get('structured_data')
            }
            response_data['sheets'].append(sheet_data)
        
        return Response(response_data, status=200)
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] ERREUR ANALYSE EXCEL (Type: {type(e).__name__}): {str(e)}")
        print(error_trace)
        return Response({
            "error": f"Erreur lors de l'analyse du fichier Excel: {str(e)}",
            "detail": error_trace if settings.DEBUG else None
        }, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def excel_validate_mapping_view(request):
    """
    Endpoint 2: Validation et enrichissement du mapping des comptes.
    """
    data = request.data
    unmapped_rows = data.get('unmapped_rows', [])
    
    if not unmapped_rows:
        return Response({"status": "success", "message": "Aucune ligne à mapper.", "suggestions": []}, status=200)
    
    try:
        from ocr.pcg_loader import get_account_suggestions
        suggestions = []
        for row in unmapped_rows:
            libelle = row.get('libelle', '')
            account_suggestions = get_account_suggestions(libelle, top_n=5) if libelle else []
            suggestions.append({
                "row_index": row.get('row_index'),
                "libelle": libelle,
                "compte_detecte": row.get('compte_detecte', ''),
                "suggestions": account_suggestions
            })
        return Response({"status": "success", "suggestions": suggestions}, status=200)
    except Exception as e:
        return Response({"error": f"Erreur lors de la validation du mapping: {str(e)}"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasProjectAccess])
def excel_save_data_view(request):
    """
    Endpoint 3: Sauvegarde finale des données dans les modèles Bilan/CompteResultat.
    Crée également un FileSource pour déclencher le signal post_save.
    """
    project_id = getattr(request, 'project_id', None)
    if not project_id:
        return Response({"error": "Project non fourni."}, status=400)

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return Response({"error": "Project introuvable."}, status=404)

    # Récupérer les données
    if request.content_type and 'multipart/form-data' in request.content_type:
        # Données envoyées via FormData
        sheets_data_str = request.POST.get('sheets')
        company_metadata_str = request.POST.get('company_metadata')
        file = request.FILES.get('file')
        
        if not sheets_data_str:
            return Response({"error": "Aucune donnée à sauvegarder."}, status=400)
        
        try:
            sheets_data = json.loads(sheets_data_str)
            company_metadata = json.loads(company_metadata_str) if company_metadata_str else {}
        except json.JSONDecodeError as e:
            return Response({"error": f"Erreur de parsing JSON: {str(e)}"}, status=400)
    else:
        # Données envoyées via JSON (ancien format)
        data = request.data
        sheets_data = data.get('sheets', [])
        company_metadata = data.get('company_metadata', {})
        file = None
    
    if not sheets_data:
        return Response({"error": "Aucune donnée à sauvegarder."}, status=400)
    
    try:
        detected_types = set(s.get('detected_type') for s in sheets_data)
        created_bilans = 0
        created_cr = 0
        created_journals = 0  # NOUVEAU: Compteur pour les écritures Journal
        errors = []
        file_source = None
        
        # 1. CRÉER LE FILESOURCE SI UN FICHIER EST FOURNI
        if file:
            print(f"\n{'='*80}")
            print(f"[INFO] CREATION DU FILESOURCE POUR: {file.name}")
            print(f"{'='*80}\n")
            
            # Calculer le hash du fichier pour éviter les doublons
            file.seek(0)
            file_content = file.read()
            file_hash = hashlib.sha256(file_content).hexdigest()
            file.seek(0)
            
            # Vérifier si le fichier existe déjà
            existing_file = FileSource.objects.filter(
                hash_ocr=file_hash,
                project_id=project_id
            ).first()
            
            if existing_file:
                print(f"[WARNING] Fichier deja importe, utilisation de l'instance existante: {existing_file.id}")
                file_source = existing_file
            else:
                # Calculer le total de lignes
                total_rows = sum(len(s.get('rows', [])) for s in sheets_data)
                
                # Extraire la date d'exercice si disponible
                date_exercice = None
                for sheet in sheets_data:
                    rows = sheet.get('rows', [])
                    if rows and rows[0].get('date'):
                        try:
                            date_exercice = pd.to_datetime(rows[0]['date']).strftime('%d/%m/%Y')
                            break
                        except:
                            pass
                
                # Préparer un résumé enrichi pour GPT
                from datetime import datetime
                summary_data = {
                    "file_name": file.name,
                    "import_type": "Excel financier avancé",
                    "date_import": datetime.now().strftime('%d/%m/%Y'),
                    "date_exercice": date_exercice,
                    "total_lignes": total_rows,
                    "nombre_feuilles": len(sheets_data),
                    "sheets": [
                        {
                            "name": s.get('sheet_name', 'Unknown'),
                            "type": s.get('detected_type', 'Unknown'),
                            "rows_count": len(s.get('rows', []))
                        } for s in sheets_data
                    ],
                    "company_metadata": company_metadata
                }

                # Générer une description fluide via IA (même logique que import classique)
                try:
                    description = generate_description(
                        data=summary_data,
                        json=json,
                        client=client,
                        model=settings.OPENAI_MODEL
                    )
                except Exception as e:
                    print(f"[WARNING] Erreur generation description GPT: {e}")
                    description_parts = [f"{s.get('sheet_name')} ({s.get('detected_type')}): {len(s.get('rows', []))} lignes" for s in sheets_data]
                    description = f"Import Excel: {file.name}\n" + "\n".join(description_parts)
                
                # Extraire la date du premier sheet
                first_date = None
                for sheet in sheets_data:
                    rows = sheet.get('rows', [])
                    if rows and rows[0].get('date'):
                        try:
                            first_date = pd.to_datetime(rows[0]['date']).date()
                            break
                        except:
                            pass
                
                if not first_date:
                    first_date = date.today()
                
                sheet_names_upper = [s.get('sheet_name', '').upper() for s in sheets_data]
                
                if 'JOURNAL' in detected_types:
                    piece_type = "Grand Journal"
                else:
                    piece_type = "État financier"
                
                # Fallback pour la référence si vide
                ref_file = company_metadata.get('numero_facture') or company_metadata.get('reference')
                if not ref_file:
                    ref_file = file.name
                
                # Créer le FileSource
                file_source = FileSource.objects.create(
                    project=project,
                    file=file,
                    file_name=file.name,
                    ref_file=ref_file,
                    piece_type=piece_type,  # Type dynamique
                    description=description,
                    ocr_data={
                        "sheets": sheets_data,
                        "company_metadata": company_metadata
                    },
                    is_ocr_processed=True,
                    hash_ocr=file_hash,
                    date=first_date
                )
                
                print(f"[SUCCESS] FileSource cree avec succes: ID={file_source.id}")
                print(f"   - Fichier: {file_source.file_name}")
                print(f"   - Date: {file_source.date}")
                print(f"   - Nombre de feuilles: {len(sheets_data)}")
                print(f"\n{'='*80}\n")
        
        # 2. CRÉER LES BILANS ET COMPTES DE RÉSULTAT
        print(f"[INFO] CREATION DES BILANS ET COMPTES DE RESULTAT")
        print(f"{'='*80}\n")
        
        for sheet in sheets_data:
            sheet_name = sheet.get('sheet_name')
            sheet_type = sheet.get('detected_type')
            rows = sheet.get('rows', [])
            
            print(f"[INFO] Traitement de la feuille: {sheet_name} ({sheet_type})")
            print(f"   [DEBUG] Nombre de rows: {len(rows)}")
            if len(rows) > 0:
                print(f"   [DEBUG] Premier row: {rows[0]}")
                print(f"   [DEBUG] Clés disponibles: {list(rows[0].keys())}")
            
            if 'JOURNAL' in detected_types and sheet_type != 'JOURNAL':
                print(f"   [SKIP] Ignoré car un Journal est présent dans le fichier.")
                continue
            
            if sheet_type == 'BILAN':
                for row in rows:
                    try:
                        # Extraction du numéro de compte (DOIT ÊTRE FAIT EN PREMIER)
                        numero_compte = str(row.get('numero_compte') or '').strip()
                        
                        # Nettoyage et conversion du montant
                        raw_montant = row.get('montant_ar', 0)
                        if isinstance(raw_montant, str):
                            raw_montant = raw_montant.replace(',', '.').replace(' ', '')
                        try:
                            montant_ar = float(raw_montant)
                        except (ValueError, TypeError):
                            montant_ar = 0.0
                            
                        if not numero_compte or montant_ar == 0: continue
                        
                        first_digit = numero_compte[0] if numero_compte else '1'
                        prefix_2 = numero_compte[:2] if len(numero_compte) >= 2 else first_digit
                        
                        # LOGIQUE DE CLASSIFICATION PCG (CONFORME UTILISATEUR)
                        if first_digit == '1':
                            type_bilan = 'PASSIF'
                            # 15, 16, 17 -> Passifs non courants
                            if prefix_2 in ['15', '16', '17']:
                                categorie = 'PASSIFS_NON_COURANTS'
                            else:
                                categorie = 'CAPITAUX_PROPRES'
                                
                        elif first_digit == '2':
                            type_bilan = 'ACTIF'
                            categorie = 'ACTIF_NON_COURANTS'
                            
                        elif first_digit == '3':
                            type_bilan = 'ACTIF'
                            categorie = 'ACTIF_COURANTS'
                        
                        elif first_digit == '4':
                            # Classe 4 mixte
                            if prefix_2 in ['40', '42', '43', '44', '45', '47', '49']:
                                type_bilan = 'PASSIF'
                                categorie = 'PASSIFS_COURANTS'
                                # Exception 4456 (TVA Déductible) -> Actif ? 
                                # L'utilisateur a spécifié 44 -> Passif. 4456 -> Actif.
                                if numero_compte.startswith('4456') or numero_compte.startswith('46') or numero_compte.startswith('48'):
                                     type_bilan = 'ACTIF'
                                     categorie = 'ACTIF_COURANTS'
                            elif prefix_2 == '41':
                                type_bilan = 'ACTIF'
                                categorie = 'ACTIF_COURANTS'
                            elif prefix_2 == '46':
                                type_bilan = 'ACTIF'
                                categorie = 'ACTIF_COURANTS'
                            elif prefix_2 == '48':
                                type_bilan = 'ACTIF' 
                                categorie = 'ACTIF_COURANTS'
                            else:
                                # Défaut passif pour prudence ou Actif ? 41=Client=Actif. 40=Fourn=Passif.
                                type_bilan = 'PASSIF'
                                categorie = 'PASSIFS_COURANTS'

                        elif first_digit == '5':
                            # 519 = Découvert = Passif
                            if numero_compte.startswith('519'):
                                type_bilan = 'PASSIF'
                                categorie = 'PASSIFS_COURANTS'
                            else:
                                type_bilan = 'ACTIF'
                                categorie = 'ACTIF_COURANTS'
                        else:
                            # Par défaut (ex: erreur de classe)
                            type_bilan = 'PASSIF'
                            categorie = 'PASSIFS_COURANTS'
                        
                        
                        # Extraction et validation de la date avec gestion d'erreurs robuste
                        date_val = row.get('date')
                        
                        # Déterminer une date par défaut intelligente
                        default_date = date.today()
                        if company_metadata:
                             # Essayer d'extraire une année des métadonnées
                             for val in company_metadata.values():
                                 if val and isinstance(val, str):
                                     match = re.search(r'\b(20\d{2})\b', val)
                                     if match:
                                         default_date = date(int(match.group(1)), 12, 31)
                                         break
                        
                        date_obj = default_date  # Valeur par défaut corrigée
                        
                        if date_val and pd.notna(date_val):
                            try:
                                # Vérifier que ce n'est pas un nom de colonne ou une chaîne invalide
                                date_str = str(date_val).strip()
                                # Ignorer si ça ressemble à un nom de colonne
                                if '_' in date_str or any(c.isalpha() for c in date_str.replace('-', '').replace('/', '')):
                                    print(f"   [WARNING] Date invalide ignorée: {date_str}")
                                else:
                                    # Si c'est juste une année (ex: 2021 ou "2021")
                                    date_str_clean = date_str
                                    if date_str_clean.isdigit() and len(date_str_clean) == 4:
                                        year_int = int(date_str_clean)
                                        if 1900 <= year_int <= 2100:
                                            date_obj = date(year_int, 12, 31)
                                        else:
                                            if isinstance(date_val, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_val.strip()):
                                                parsed_date = pd.to_datetime(date_val)
                                            else:
                                                parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                            if pd.notna(parsed_date):
                                                date_obj = parsed_date.date()
                                    else:
                                        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                                            parsed_date = pd.to_datetime(date_val)
                                        else:
                                            if isinstance(date_val, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_val.strip()):
                                                parsed_date = pd.to_datetime(date_val)
                                            else:
                                                parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                        if pd.notna(parsed_date):
                                            date_obj = parsed_date.date()
                            except Exception as e:
                                print(f"   [WARNING] Erreur parsing date '{date_val}': {e}")
                        
                        libelle_row = str(row.get('libelle') or '').strip()

                        Bilan.objects.create(
                            project=project,
                            numero_compte=numero_compte,
                            libelle=libelle_row,
                            montant_ar=montant_ar,
                            date=date_obj,
                            type_bilan=type_bilan,
                            categorie=categorie
                        )
                        created_bilans += 1
                    except Exception as e:
                        print(f"   [ERROR] Erreur Bilan ligne {row.get('row_index')}: {e}")
                        print(f"      Row: {row}")
                        errors.append({'sheet': sheet_name, 'error': str(e)})

            elif sheet_type == 'COMPTE_RESULTAT':
                for row in rows:
                    try:
                        # Nettoyage montant
                        raw_montant = row.get('montant_ar', 0)
                        try:
                            if isinstance(raw_montant, str): raw_montant = raw_montant.replace(',', '.').replace(' ', '')
                            montant_ar = float(raw_montant)
                        except: montant_ar = 0.0
                        
                        numero_compte = str(row.get('numero_compte') or '').strip() # Moved here
                        if not numero_compte or montant_ar == 0: continue
                        
                        nature = 'CHARGE' if (numero_compte[0] if numero_compte else '6') == '6' else 'PRODUIT'
                        
                        # Extraction et validation de la date avec gestion d'erreurs robuste
                        date_val = row.get('date')
                        
                        # Déterminer une date par défaut intelligente
                        default_date = date.today()
                        if company_metadata:
                             # Essayer d'extraire une année des métadonnées
                             for val in company_metadata.values():
                                 if val and isinstance(val, str):
                                     match = re.search(r'\b(20\d{2})\b', val)
                                     if match:
                                         default_date = date(int(match.group(1)), 12, 31)
                                         break

                        date_obj = default_date  # Valeur par défaut corrigée
                        
                        if date_val and pd.notna(date_val):
                            try:
                                # Vérifier que ce n'est pas un nom de colonne ou une chaîne invalide
                                date_str = str(date_val).strip()
                                # Ignorer si ça ressemble à un nom de colonne
                                if '_' in date_str or any(c.isalpha() for c in date_str.replace('-', '').replace('/', '')):
                                    print(f"   [WARNING] Date invalide ignorée: {date_str}")
                                else:
                                    # Cas spécial: Année seule (ex: 2021) -> 31/12/2021
                                    date_str_clean = date_str
                                    if date_str_clean.isdigit() and len(date_str_clean) == 4:
                                        year_int = int(date_str_clean)
                                        if 1900 <= year_int <= 2100:
                                            date_obj = date(year_int, 12, 31)
                                        else:
                                            if isinstance(date_val, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_val.strip()):
                                                parsed_date = pd.to_datetime(date_val)
                                            else:
                                                parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                            if pd.notna(parsed_date):
                                                date_obj = parsed_date.date()
                                    else:
                                        parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                        if pd.notna(parsed_date):
                                            date_obj = parsed_date.date()
                            except Exception as e:
                                print(f"   [WARNING] Erreur parsing date '{date_val}': {e}")
                        
                        libelle_row = str(row.get('libelle') or '').strip()

                        CompteResultat.objects.create(
                            project=project,
                            numero_compte=numero_compte,
                            libelle=libelle_row,
                            montant_ar=montant_ar,
                            date=date_obj,
                            nature=nature
                        )
                        created_cr += 1
                    except Exception as e:
                        print(f"   [ERROR] Erreur CompteResultat ligne {row.get('row_index')}: {e}")
                        print(f"      Row: {row}")
                        errors.append({'sheet': sheet_name, 'error': str(e)})
            
            # NOUVEAU: Support pour JOURNAL
            elif sheet_type == 'JOURNAL':
                from compta.models import Journal
                from decimal import Decimal
                from ocr.financial_data_structurer import FinancialDataStructurer
                
                # Initialiser le structureur pour la détection du type_journal
                structurer = FinancialDataStructurer()
                
                # Charger les lignes depuis le nouveau format groupé si possible
                journal_rows = rows
                if not rows and sheet.get('structured_data'):
                    sd = sheet.get('structured_data')
                    if sd.get('donnees_par_annee'):
                        # Aplatir toutes les années pour la sauvegarde
                        journal_rows = []
                        for year_rows in sd.get('donnees_par_annee').values():
                            journal_rows.extend(year_rows)
                    elif sd.get('lignes'):
                        journal_rows = sd.get('lignes')

                for row in journal_rows:
                    try:
                        numero_compte = str(row.get('numero_compte') or '').strip()
                        if not numero_compte:
                            continue
                        
                        # Extraction et validation de la date avec gestion d'erreurs robuste
                        date_val = row.get('date')
                        date_obj = date.today()  # Valeur par défaut
                        
                        if date_val:
                            try:
                                date_str = str(date_val).strip()
                                # Ignorer si ça ressemble à un nom de colonne
                                if '_' in date_str or any(c.isalpha() for c in date_str.replace('-', '').replace('/', '')):
                                    print(f"   [WARNING] Date invalide ignorée (nom de colonne?): {date_str}")
                                else:
                                    # Cas spécial: Année seule (ex: 2021) -> 31/12/2021
                                    date_str_clean = date_str
                                    if date_str_clean.isdigit() and len(date_str_clean) == 4:
                                        year_int = int(date_str_clean)
                                        if 1900 <= year_int <= 2100:
                                            date_obj = date(year_int, 12, 31)
                                        else:
                                            if isinstance(date_val, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_val.strip()):
                                                parsed_date = pd.to_datetime(date_val)
                                            else:
                                                parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                            if pd.notna(parsed_date):
                                                date_obj = parsed_date.date()
                                    else:
                                        parsed_date = pd.to_datetime(date_val, errors='coerce', dayfirst=True)
                                        if pd.notna(parsed_date):
                                            date_obj = parsed_date.date()
                            except Exception as e:
                                print(f"   [WARNING] Erreur parsing date '{date_val}': {e}")
                                date_obj = date.today()
                        
                        numero_piece = row.get('numero_piece', None)
                        libelle = row.get('libelle', '').strip()
                        debit = Decimal(str(row.get('debit', 0) or 0))
                        credit = Decimal(str(row.get('credit', 0) or 0))
                        
                        # Validation: au moins débit ou crédit doit être non nul
                        if debit == 0 and credit == 0:
                            continue
                        
                        # DÉTECTION AUTOMATIQUE DU TYPE_JOURNAL
                        # Prioriser le type déjà envoyé par le front/structurer
                        type_journal = row.get('type_journal')
                        
                        if not type_journal:
                            # Utiliser la logique du FinancialDataStructurer en dernier recours
                            type_journal = structurer._detect_journal_type(libelle, numero_compte)
                            print(f"   [INFO] Type journal détecté (fallback): {type_journal} pour compte {numero_compte} ({libelle})")
                        else:
                            print(f"   [INFO] Type journal utilisé (reçu): {type_journal} pour pièce {numero_piece}")
                        
                        Journal.objects.create(
                            project=project,
                            date=date_obj,
                            numero_piece=numero_piece,
                            type_journal=type_journal,
                            numero_compte=numero_compte,
                            libelle=libelle,
                            debit_ar=debit,
                            credit_ar=credit
                        )
                        created_journals += 1
                        
                        # Lier le Journal au FileSource si disponible
                        if file_source and created_journals == 1:
                            # Récupérer la dernière écriture créée
                            last_journal = Journal.objects.filter(
                                project=project,
                                numero_compte=numero_compte,
                                date=date_obj
                            ).order_by('-id').first()
                            
                            if last_journal and not file_source.journal:
                                file_source.journal = last_journal
                                file_source.save(update_fields=['journal'])
                                print(f"   [INFO] FileSource lié au Journal ID={last_journal.id}")
                    except Exception as e:
                        errors.append({'sheet': sheet_name, 'row': row, 'error': str(e)})

        
        print(f"\n{'='*80}")
        print(f"[SUCCESS] SAUVEGARDE TERMINEE")
        print(f"   - Bilans crees: {created_bilans}")
        print(f"   - Comptes de resultat crees: {created_cr}")
        print(f"   - Ecritures Journal creees: {created_journals}")
        print(f"   - Erreurs: {len(errors)}")
        print(f"{'='*80}\n")

        response_data = {
            "status": "success",
            "created_bilans": created_bilans,
            "created_compte_resultat": created_cr,
            "created_journals": created_journals,  # NOUVEAU
            "errors": errors if errors else None
        }
        
        if file_source:
            response_data["file_source_id"] = file_source.id
            response_data["file_source_name"] = file_source.file_name
        
        return Response(response_data, status=201)
        
    except OperationalError as e:
        print(f"\n{'='*80}")
        print(f"[ERROR] ERREUR DE CONNEXION A LA BASE DE DONNEES")
        print(f"{'='*80}")
        print(f"Message: {str(e)}")
        print(f"{'='*80}\n")
        return Response({
            "error": "Impossible de se connecter a la base de donnees distante (Render). Le delai d'attente a expire. Veuillez verifier l'etat de votre base de donnees.",
            "details": str(e)
        }, status=503)
        
    except Exception as e:
        import traceback
        print(f"\n{'='*80}")
        print(f"[ERROR] ERREUR LORS DE LA SAUVEGARDE")
        print(f"{'='*80}")
        print(traceback.format_exc())
        print(f"{'='*80}\n")
        return Response({"error": f"Erreur lors de la sauvegarde: {str(e)}"}, status=500)


