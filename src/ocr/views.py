import json
import re
import unicodedata
import hashlib
from vulca_backend import settings

from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from ocr.models import FileSource, FormSource
from ocr.serializers import FileSourceSerializer, FormSourceSerializer
from ocr.utils import detect_file_type, clean_ai_json, generate_description
from ocr.openai_vision_ocr import extract_content_with_vision
from ocr.constants import EXTRACTION_FIELDS_PROMPT
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
        print("❌ Erreur génération journal:", e)
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
    if file_type == "unknown":
        return Response({"error": "Type de fichier non supporté."}, status=400)

    # OCR avec OpenAI Vision API
    content = extract_content_with_vision(file, file_type, client, settings.OPENAI_MODEL)
    if not content:
        return Response({"error": "Impossible d'extraire le texte."}, status=400)

    print("\n" + "=" * 80)
    print("📄 TEXTE OCR BRUT :")
    print("=" * 80)
    print(content[:2000])  # Limité pour logs
    print("=" * 80 + "\n")

    # ÉTAPE 1 : Vérification pièce comptable
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Tu es expert comptable. Réponds uniquement OUI ou NON."},
                {"role": "user", "content": f"Voici un document : {content[:5000]}\n\nEst-ce un document professionnel ou administratif valide (lié à une activité d'entreprise : facture, reçu, document bancaire, RH, fiscal, juridique, etc.) ? Réponds OUI sauf s'il s'agit manifestement d'un document personnel sans lien (ex: photo de vacances, poème) ou illisible."}
            ],
            temperature=0
        )
        decision = response.choices[0].message.content.strip().lower()
    except Exception as e:
        return Response({"error": f"Erreur OpenAI vérification : {str(e)}"}, status=500)

    if "oui" not in decision and "yes" not in decision:
        return Response({"error": "Document non reconnu comme pièce comptable."}, status=400)

    # ÉTAPE 2 : Type de document
    try:
        type_response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Tu dois répondre STRICTEMENT par un seul mot parmi : ACHAT, VENTE, BANQUE, CAISSE, OD, PAIE.\n\nRÈGLES IMPÉRATIVES :\n1. Si le document est un Relevé Bancaire, un Reçu de Virement ou un Avis de Virement -> C'EST 'BANQUE'.\n2. Si c'est une Facture Client, un Bon de commande ou un Bon de livraison -> 'VENTE'.\n3. Si c'est une Facture Fournisseur ou un Bon d'achat -> 'ACHAT'.\n4. Si c'est un ticket de caisse ou espèces -> 'CAISSE'.\n5. Si c'est une Fiche de Paie ou Bulletin de Salaire -> 'PAIE'.\n6. Pour tout autre document (juridique, fiscal, divers) -> 'OD'."
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

    # ÉTAPE 3 : Extraction avec prompt unifié
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

    # Conversion JSON
    try:
        extracted_json = json.loads(extracted_json_str)
    except json.JSONDecodeError:
        return Response({
            "error": "JSON IA invalide",
            "raw": extracted_json_str
        }, status=500)

    # POST-TRAITEMENT avec normalisation
    extracted_json = normalize_extracted_json(extracted_json, content)

    # Ajouter type_document
    content_lower = content.lower()
    is_bank = any(k in content_lower for k in ["banque", "relev", "virement", "statement"])
    
    if is_bank:
        extracted_json["type_document"] = "BANQUE"
    else:
        extracted_json["type_document"] = type_document

    # ⚠️ FALLBACK : Extraction du numéro de facture si l'IA ne l'a pas trouvé
    if not extracted_json.get("numero_facture"):
        print("⚠️ Numéro de facture manquant, tentative d'extraction par regex...")
        
        # Patterns de recherche pour numéro de facture (PAR ORDRE DE PRIORITÉ)
        # Les patterns plus spécifiques en premier pour éviter les faux positifs
        patterns = [
            # Patterns très spécifiques (haute priorité)
            r'(?:N[°o]|Num[ée]ro|Number)[\s:]*(?:Facture|Invoice|Bill)[\s:]*(\w+[-/]?\w+)',  # N° Facture: XXX
            r'(?:Facture|Invoice|Bill)[\s:]*(?:N[°o]|Num[ée]ro|#)[\s:]*(\w+[-/]?\w+)',  # Facture N°: XXX
            r'(?:R[ée]f[ée]rence|Ref)[\s:]*(?:Facture|Invoice)?[\s:]*(\w+[-/]?\w+)',  # Référence: XXX
            
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
                    print(f"✅ Numéro de facture extrait par regex : {numero}")
                    break
        
        if not extracted_json.get("numero_facture"):
            print("❌ Aucun numéro de facture trouvé, même avec regex")
            # Générer un numéro temporaire basé sur la date
            from datetime import datetime
            temp_num = f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            extracted_json["numero_facture"] = temp_num
            print(f"⚠️ Numéro temporaire généré : {temp_num}")

    print("\n✅ JSON FINAL NORMALISÉ :")
    print("=" * 80)
    print(json.dumps(extracted_json, indent=2, ensure_ascii=False))
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


