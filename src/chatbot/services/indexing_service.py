import json
from django.db import models
from django.utils import timezone
from compta.models import Journal, Bilan, CompteResultat
from ocr.models import FileSource, FormSource
from chatbot.models import AccountingIndex
from .embeddings import generate_embedding

class AccountingIndexer:
    """
    Service pour transformer les données comptables en texte structuré
    et générer des index sémantiques (embeddings).
    """

    @staticmethod
    def index_journal_entry(entry: Journal):
        """Transforme une ligne de journal en texte et l'indexe."""
        # ✅ SÉCURITÉ : Conversion date si string
        date_obj = entry.date
        if isinstance(date_obj, str):
            from dateutil import parser as date_parser
            date_obj = date_parser.parse(date_obj).date()

        text = (
            f"Écriture comptable du {date_obj.strftime('%d/%m/%Y') if date_obj else 'Inconnue'}. "
            f"Pièce n°{entry.numero_piece}. "
            f"Journal: {entry.get_type_journal_display()}. "
            f"Compte: {entry.numero_compte} ({entry.libelle}). "
            f"Débit: {entry.debit_ar} Ar. "
            f"Crédit: {entry.credit_ar} Ar. "
            f"Description: {entry.description or 'Aucune'}"
        )
        
        return AccountingIndexer._save_index(
            project=entry.project,
            model_name="Journal",
            source_id=entry.id,
            content=text,
            date=entry.date,
            metadata={
                "numero_piece": entry.numero_piece,
                "type_journal": entry.type_journal,
                "numero_compte": entry.numero_compte
            }
        )

    @staticmethod
    def index_bilan_entry(entry: Bilan):
        """Transforme une ligne de bilan en texte et l'indexe."""
        # ✅ SÉCURITÉ : Conversion date si string
        date_obj = entry.date
        if isinstance(date_obj, str):
            from dateutil import parser as date_parser
            date_obj = date_parser.parse(date_obj).date()

        text = (
            f"Ligne de Bilan au {date_obj.strftime('%d/%m/%Y') if date_obj else 'Inconnue'}. "
            f"Compte: {entry.numero_compte} ({entry.libelle}). "
            f"Type: {entry.get_type_bilan_display()}. "
            f"Catégorie: {entry.get_categorie_display()}. "
            f"Montant: {entry.montant_ar} Ar. "
            f"Description: {entry.description or 'Aucune'}"
        )
        
        return AccountingIndexer._save_index(
            project=entry.project,
            model_name="Bilan",
            source_id=entry.id,
            content=text,
            date=entry.date,
            metadata={
                "type_bilan": entry.type_bilan,
                "categorie": entry.categorie,
                "numero_compte": entry.numero_compte
            }
        )

    @staticmethod
    def index_resultat_entry(entry: CompteResultat):
        """Transforme une ligne de compte de résultat en texte et l'indexe."""
        # ✅ SÉCURITÉ : Conversion date si string
        date_obj = entry.date
        if isinstance(date_obj, str):
            from dateutil import parser as date_parser
            date_obj = date_parser.parse(date_obj).date()

        text = (
            f"Ligne de Compte de Résultat au {date_obj.strftime('%d/%m/%Y') if date_obj else 'Inconnue'}. "
            f"Compte: {entry.numero_compte} ({entry.libelle}). "
            f"Nature: {entry.get_nature_display()}. "
            f"Montant: {entry.montant_ar} Ar. "
            f"Description: {entry.description or 'Aucune'}"
        )
        
        return AccountingIndexer._save_index(
            project=entry.project,
            model_name="CompteResultat",
            source_id=entry.id,
            content=text,
            date=entry.date,
            metadata={
                "nature": entry.nature,
                "numero_compte": entry.numero_compte
            }
        )

    @staticmethod
    def index_file_source(source: FileSource):
        """Transforme un document source (OCR) en texte et l'indexe."""
        ocr_summary = ""
        if source.ocr_data:
            ocr_summary = f"Données identifiées par OCR: {json.dumps(source.ocr_data, ensure_ascii=False)}"

        # ✅ SÉCURITÉ : Conversion date si string
        date_obj = source.date
        if isinstance(date_obj, str):
            from dateutil import parser as date_parser
            date_obj = date_parser.parse(date_obj).date()

        text = (
            f"Document source importé le {source.uploaded_at.strftime('%d/%m/%Y') if source.uploaded_at else 'Inconnu'}. "
            f"Nom du fichier: {source.file_name or 'Inconnu'}. "
            f"Type de pièce: {source.piece_type or 'Autres'}. "
            f"Date du document: {date_obj.strftime('%d/%m/%Y') if date_obj else 'Inconnue'}. "
            f"Description: {source.description or 'Aucune'}. "
            f"{ocr_summary}"
        )
        
        return AccountingIndexer._save_index(
            project=source.project,
            model_name="FileSource",
            source_id=source.id,
            content=text,
            date=source.date,
            metadata={
                "piece_type": source.piece_type,
                "is_ocr_processed": source.is_ocr_processed
            }
        )

    @staticmethod
    def index_form_source(source: FormSource):
        """Transforme une saisie manuelle en texte et l'indexe."""
        data_summary = ""
        if source.data_json:
            data_summary = f"Détails de la saisie: {json.dumps(source.data_json, ensure_ascii=False)}"

        # ✅ SÉCURITÉ : Conversion date si string
        date_obj = source.date
        if isinstance(date_obj, str):
            from dateutil import parser as date_parser
            date_obj = date_parser.parse(date_obj).date()

        text = (
            f"Saisie manuelle effectuée le {source.created_at.strftime('%d/%m/%Y') if source.created_at else 'Inconnue'}. "
            f"Type de pièce: {source.piece_type or 'Autres'}. "
            f"Date: {date_obj.strftime('%d/%m/%Y') if date_obj else 'Inconnue'}. "
            f"Description: {source.description or 'Aucune'}. "
            f"{data_summary}"
        )
        
        return AccountingIndexer._save_index(
            project=source.project,
            model_name="FormSource",
            source_id=source.id,
            content=text,
            date=source.date,
            metadata={
                "piece_type": source.piece_type
            }
        )

    @staticmethod
    def _save_index(project, model_name, source_id, content, date, metadata):
        """Génère l'embedding et enregistre l'index."""
        if not project:
            return None
            
        try:
            # On vérifie si un index existe déjà pour mettre à jour ou créer
            obj, created = AccountingIndex.objects.get_or_create(
                project=project,
                source_model=model_name,
                source_id=source_id,
                defaults={
                    "content": content,
                    "date": date,
                    "metadata": metadata
                }
            )
            
            # Si déjà existant, on met à jour le contenu
            if not created:
                obj.content = content
                obj.date = date
                obj.metadata = metadata
            
            # Génération de l'embedding (via OpenAI)
            obj.embedding = generate_embedding(content)
            obj.save()
            return obj
            
        except Exception as e:
            print(f"[Error] Indexing {model_name} #{source_id}: {str(e)}")
            return None
