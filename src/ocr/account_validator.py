"""
Module de validation et correction intelligente des numéros de compte.

Ce module fournit des outils pour :
- Valider la cohérence entre numéro de compte et libellé
- Auto-corriger les erreurs évidentes d'OCR
- Suggérer des comptes basés sur le libellé
"""

import re
from typing import Dict, Optional, Tuple
from ocr.pcg_loader import get_pcg_label, get_account_suggestions


# Mapping de mots-clés vers classes de comptes (Fallback PCG 2005)
KEYWORD_TO_CLASS = {
    # Classe 1 - Capitaux propres & Passifs non courants
    'capital': '10',
    'reserve': '11',
    'report a nouveau': '11',
    'resultat': '12',
    'subvention d investissement': '13',
    'provision pour charge': '15',
    'emprunt': '16',
    'dette assimile': '16',
    'dette rattachee': '17',
    'compte de liaison': '18',
    'passif non courant': '1',
    'passif courant': '4',
    'capitaux propres': '1',
    
    # Classe 2 - Actifs non courants
    'immobilisation': '2',
    'immobilisation incorporelle': '20',
    'immobilisation corporelle': '21',
    'immobilisations corporelles': '21',
    'immobilisation mise en concession': '22',
    'immobilisation financiere': '26',
    'amortissement': '28',
    'depreciation': '29',
    
    # Classe 3 - Actifs courants (Stocks)
    'stock': '3',
    'stock de marchandise': '30',
    'stock de matiere premiere': '31',
    'approvisionnement': '32',
    'stock de produit': '35',
    
    # Classe 4 - Comptes de tiers (Courants)
    'fournisseur': '40',
    'client': '41',
    'personnel': '42',
    'organisme social': '43',
    'securite sociale': '43',
    'etat impots et taxes': '44',
    'tva deductible': '4456',
    'tva collectee': '4457',
    'associe et groupe': '45',
    'debiteur divers': '46',
    'crediteur divers': '46',
    'charge constate d avance': '48',
    'produit constate d avance': '48',
    
    # Classe 5 - Tresorerie
    'valeurs mobilieres de placement': '50',
    'banque': '51',
    'concours bancaire': '519',
    'decouvert': '519',
    'caisse': '53',
    
    # Classe 6 - Charges
    'achat de marchandise': '60',
    'variation de stock': '603',
    'service exterieur': '61',
    'autre service exterieur': '62',
    'impot et taxe': '63',
    'charge de personnel': '64',
    'salaire': '64',
    'autre charge de gestion courante': '65',
    'charge financiere': '66',
    'charge exceptionnelle': '67',
    'dotation': '68',
    'participation': '69',
    'impot sur benefice': '69',
    'ibs': '695',
    'ir': '695',
    'ibsir': '695',
    'lbsir': '695',
    
    # Classe 7 - Produits
    'vente': '70',
    'chiffre d affaires': '70',
    'variation de stock produit': '713',
    'production stockee': '71',
    'production immobilisee': '72',
    'subvention d exploitation': '74',
    'autre produit de gestion courante': '758',
    'produit divers de gestion courante': '758',
    'produit de gestion courante': '758',
    'produit financier': '76',
    'produit exceptionnel': '77',
    'reprise': '78',
}


def normalize_libelle(libelle: str) -> str:
    """Normalise un libellé pour la comparaison, avec gestion des erreurs OCR."""
    if not libelle:
        return ""
        
    # Minuscules
    libelle = libelle.lower()
    
    # Remplacements spécifiques pour erreurs OCR fréquentes
    # "Caïtal" -> "capital", "Emprnts" -> "emprunts"
    libelle = libelle.replace('caïtal', 'capital')
    libelle = libelle.replace('caital', 'capital')
    libelle = libelle.replace('caputal', 'capital')
    libelle = libelle.replace('emprnts', 'emprunt')
    libelle = libelle.replace('emprunt', 'emprunt')
    libelle = libelle.replace('repot', 'report') # "Repot a nouveau" -> "Report a nouveau"
    
    # Nettoyage standard des accents
    libelle = libelle.replace('é', 'e').replace('è', 'e').replace('ê', 'e')
    libelle = libelle.replace('à', 'a').replace('â', 'a')
    libelle = libelle.replace('ô', 'o').replace('ö', 'o')
    libelle = libelle.replace('û', 'u').replace('ü', 'u')
    libelle = libelle.replace('ç', 'c')
    libelle = libelle.replace('ï', 'i').replace('î', 'i')
    
    # Supprimer les caractères spéciaux (garder seulement lettres et espaces)
    libelle = re.sub(r'[^a-z\s]', ' ', libelle)
    
    # Gestion simple des pluriels
    words = libelle.split()
    normalized_words = []
    for w in words:
        # Cas spécifiques d'erreurs OCR sur des mots courts
        if w == 'caital' or w == 'caïtal': w = 'capital'
        if w == 'emprnt' or w == 'emprnts': w = 'emprunt'
        
        if len(w) > 3 and w.endswith('s'):
            normalized_words.append(w[:-1])
        else:
            normalized_words.append(w)
            
    return " ".join(normalized_words).strip()


def guess_account_from_libelle(libelle: str, document_type: str = None) -> Optional[str]:
    """
    Devine le numéro de compte basé sur le libellé et le type de document.
    """
    if not libelle:
        return None
        
    libelle_norm = normalize_libelle(libelle)
    
    # 1. Tenter d'utiliser le PCG Loader (beaucoup plus précis)
    pcg_suggestions = get_account_suggestions(libelle)
    if pcg_suggestions:
        # Filtrer par type de document si spécifié
        valid_prefixes = []
        if document_type == 'COMPTE_RESULTAT':
            valid_prefixes = ['6', '7']
        elif document_type == 'BILAN':
            valid_prefixes = ['1', '2', '3', '4', '5']
            
        if valid_prefixes:
            # Chercher la première suggestion qui correspond au type de document
            for sugg in pcg_suggestions:
                num = str(sugg['numero_compte'])
                if any(num.startswith(p) for p in valid_prefixes):
                    print(f"   [PCG-MATCH] '{libelle}' -> Suggestion: {num} ({sugg['libelle']})")
                    return num
        else:
            # Sans filtre, prendre la meilleure suggestion
            return pcg_suggestions[0]['numero_compte']

    # 2. Fallback sur le mapping de mots-clés hardcodé
    best_match = None
    best_score = 0
    
    for keyword, account_prefix in KEYWORD_TO_CLASS.items():
        keyword_norm = normalize_libelle(keyword)
        if keyword_norm in libelle_norm:
            score = len(keyword_norm)
            
            # Vérifier si le préfixe correspond au document_type
            is_valid_type = True
            if document_type == 'COMPTE_RESULTAT' and not any(account_prefix.startswith(p) for p in ['6', '7']):
                is_valid_type = False
            elif document_type == 'BILAN' and not any(account_prefix.startswith(p) for p in ['1', '2', '3', '4', '5']):
                is_valid_type = False
                
            if is_valid_type and score > best_score:
                best_score = score
                best_match = account_prefix
    
    return best_match


def validate_account_coherence(numero_compte: str, libelle: str, document_type: str = None) -> Tuple[bool, Optional[str], str]:
    """
    Valide la cohérence entre un numéro de compte et son libellé.
    
    Args:
        numero_compte: Numéro de compte extrait
        libelle: Libellé du compte
        document_type: Type de document (BILAN, COMPTE_RESULTAT, etc.)
        
    Returns:
        Tuple (is_valid, suggested_account, reason)
        - is_valid: True si le compte est cohérent
        - suggested_account: Compte suggéré si incohérent (ou None)
        - reason: Raison de l'incohérence
    """
    if not numero_compte or not libelle:
        return True, None, ""
    
    # Extraire la classe du compte (premier chiffre)
    try:
        classe = int(numero_compte[0])
    except (ValueError, IndexError):
        return False, None, "Numéro de compte invalide"
    
    libelle_norm = normalize_libelle(libelle)
    
    # Règles de validation spécifiques
    
    # 1. Stocks (classe 3) ne peuvent pas être en classe 7 (Produits)
    if 'stock' in libelle_norm and classe == 7:
        if not 'variation' in libelle_norm:
            return False, '31', "Stocks détecté en classe 7 (Produits) au lieu de classe 3"
    
    # 2. Variation des stocks (classe 6 ou 7)
    if 'variation' in libelle_norm and 'stock' in libelle_norm and classe not in [6, 7]:
        return False, '603', "Variation de stock détecté hors classe 6 ou 7"
    
    # 2. Trésorerie (classe 5) ne peut pas être en classe 1 (Capitaux propres)
    if any(kw in libelle_norm for kw in ['tresorerie', 'banque', 'caisse']) and classe == 1:
        if 'banque' in libelle_norm:
            return False, '512', "Banque détecté en classe 1 au lieu de classe 5"
        elif 'caisse' in libelle_norm:
            return False, '53', "Caisse détecté en classe 1 au lieu de classe 5"
        else:
            return False, '5', "Trésorerie détecté en classe 1 au lieu de classe 5"
    
    # 3. Créances (classe 4) ne peuvent pas être en classe 5
    if any(kw in libelle_norm for kw in ['creance', 'client']) and classe == 5:
        return False, '41', "Créances détecté en classe 5 au lieu de classe 4"
    
    # 4. Fournisseurs (classe 4) ne peuvent pas être en classe 1
    if 'fournisseur' in libelle_norm and classe == 1:
        return False, '40', "Fournisseurs détecté en classe 1 au lieu de classe 4"
    
    # 5. Dettes fiscales et sociales (classe 4) ne peuvent pas être en classe 1
    if any(kw in libelle_norm for kw in ['etat', 'fiscal', 'social', 'taxe', 'impot']) and classe == 1:
        suggested = guess_account_from_libelle(libelle, document_type)
        return False, suggested or '44', "Dettes fiscales/sociales détecté en classe 1 au lieu de classe 4"
    
    # 6. Capital (classe 1) ne peut pas être en classe 4 ou 5
    if 'capital' in libelle_norm and classe not in [1]:
        return False, '101', "Capital détecté hors classe 1"
    
    # 6. Chiffre d'affaires / Production / Produits (classe 7) ne peut pas être en classe 1
    if any(kw in libelle_norm for kw in ['chiffre', 'production', 'vente', 'produit']) and classe == 1:
        # Exception pour "produit constaté d'avance" qui est souvent en classe 4 mais peut être vu comme passif
        if 'constate d avance' not in libelle_norm:
            suggested = guess_account_from_libelle(libelle, document_type)
            return False, suggested or '7', "Produits détecté en classe 1 (Capitaux propres) au lieu de classe 7 (Produits)"
    
    # 7. Charges (classe 6) ne peuvent pas être en classe 1, 2, 7
    if any(kw in libelle_norm for kw in ['charge', 'achat', 'service', 'dotation']) and classe in [1, 2, 7]:
        suggested = guess_account_from_libelle(libelle, document_type)
        return False, suggested or '6', "Charges détecté hors classe 6"
    
    # 8. Immobilisations (classe 2) ne peuvent pas être en classe 7
    if 'immobilisation' in libelle_norm and classe == 7:
        return False, '2', "Immobilisations détecté en classe 7 au lieu de classe 2"
    
    # Si aucune incohérence détectée, c'est valide
    return True, None, ""


def auto_correct_account(numero_compte: str, libelle: str, document_type: str = None) -> Tuple[str, bool, str]:
    """
    Corrige automatiquement un numéro de compte si nécessaire.
    
    Args:
        numero_compte: Numéro de compte extrait
        libelle: Libellé du compte
        document_type: Type de document
        
    Returns:
        Tuple (corrected_account, was_corrected, correction_reason)
    """
    # Valider la cohérence
    is_valid, suggested, reason = validate_account_coherence(numero_compte, libelle, document_type)
    
    if is_valid:
        return numero_compte, False, ""
    
    # Si incohérent, utiliser le compte suggéré
    if suggested:
        return suggested, True, reason
    
    # Sinon, essayer de deviner basé sur le libellé
    guessed = guess_account_from_libelle(libelle, document_type)
    if guessed:
        return guessed, True, f"Compte deviné basé sur le libellé: {reason}"
    
    # En dernier recours, garder le compte original
    return numero_compte, False, f"Incohérence détectée mais pas de suggestion: {reason}"


class AccountValidator:
    """
    Interface objet pour la validation et correction des comptes.
    """
    
    def validate_account(self, numero_compte: str, libelle: str, document_type: str = None) -> Dict:
        """
        Valide un compte et retourne un dictionnaire de résultat.
        """
        is_valid, suggested, reason = validate_account_coherence(numero_compte, libelle, document_type)
        
        # Déterminer la classe suggérée si incohérent
        suggested_class = None
        if suggested:
            suggested_class = suggested[0]
        elif not is_valid:
            # Essayer de deviner la classe
            guessed = guess_account_from_libelle(libelle, document_type)
            if guessed:
                suggested_class = guessed[0]
                
        return {
            'is_valid': is_valid,
            'is_corrected': not is_valid and suggested is not None,
            'suggested_account': suggested,
            'suggested_class': suggested_class,
            'reason': reason
        }
        
    def suggest_account_from_label(self, libelle: str, document_type: str = None) -> Optional[str]:
        """
        Suggère un numéro de compte (ou préfixe) basé sur le libellé.
        """
        return guess_account_from_libelle(libelle, document_type)
        
    def suggest_class_from_label(self, libelle: str, document_type: str = None) -> Optional[str]:
        """
        Suggère une classe de compte (1-7) basée sur le libellé.
        """
        guessed = guess_account_from_libelle(libelle, document_type)
        if guessed:
            return guessed[0]
        return None
