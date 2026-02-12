"""
Module d'analyse IA pour le dashboard financier.
Utilise GPT-4 pour générer des analyses contextuelles intelligentes des indicateurs financiers.
"""

import json
from decimal import Decimal
from openai import OpenAI
from django.conf import settings


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def analyze_dashboard_with_ai(indicators_data, start_date=None, end_date=None, project_id=None):
    """
    Analyse les indicateurs du dashboard avec l'IA et génère des explications contextuelles.
    
    Args:
        indicators_data: Dictionnaire contenant tous les indicateurs du dashboard
        start_date: Date de début de la période analysée (optionnel)
        end_date: Date de fin de la période analysée (optionnel)
        project_id: ID du projet (optionnel, pour contexte futur)
    
    Returns:
        dict: Analyse structurée avec insights, tendances, et recommandations
    """
    
    # Préparer les données pour le prompt
    indicators_summary = _prepare_indicators_summary(indicators_data, start_date, end_date)
    
    # Préparer l'en-tête de période si les dates sont fournies
    period_header = ""
    if start_date and end_date:
        period_header = f"\nPÉRIODE ANALYSÉE: Du {start_date} au {end_date}\n"
    elif start_date:
        period_header = f"\nPÉRIODE ANALYSÉE: À partir du {start_date}\n"
    elif end_date:
        period_header = f"\nPÉRIODE ANALYSÉE: Jusqu'au {end_date}\n"
    
    prompt = f"""
Tu es un expert-comptable certifié et analyste financier senior. Tu analyses le dashboard financier d'une entreprise malgache.
{period_header}
DONNÉES DU DASHBOARD:
{indicators_summary}

MISSION:
Analyse ces indicateurs financiers de manière approfondie et génère un rapport d'analyse intelligent qui explique:

1. **Vue d'ensemble**: Résumé de la santé financière globale de l'entreprise

2. **Analyse des indicateurs principaux**:
   - Chiffre d'Affaires: niveau, tendance, commentaire
   - CAF (Capacité d'Autofinancement): niveau, signification
   - EBE (Excédent Brut d'Exploitation): performance opérationnelle
   - BFR (Besoin en Fonds de Roulement): impact sur la trésorerie
   - Trésorerie: situation de liquidité

3. **Analyse des ratios**:
   - Ratios de rentabilité (ROE, ROA, marges): que révèlent-ils?
   - Ratios de liquidité (Current Ratio, Quick Ratio): capacité à honorer les dettes
   - Ratios d'endettement: niveau de risque financier
   - Ratios d'activité: efficacité opérationnelle

4. **Analyse des graphiques et visualisations**:
   - Quelles tendances observes-tu dans l'évolution du CA et des métriques?
   - Que révèle la répartition des comptes, de la TVA, des produits/charges?
   - Y a-t-il des patterns saisonniers ou des anomalies visibles?
   - Quels insights visuels peuvent aider à la prise de décision?

5. **Corrélations et insights**:
   - Quelles relations observes-tu entre les différents indicateurs?
   - Y a-t-il des incohérences ou des signaux d'alerte?
   - Quels sont les points forts de l'entreprise?
   - Quels sont les points faibles ou risques?

6. **Recommandations actionnables**:
   - 3-5 actions concrètes que l'entreprise devrait entreprendre
   - Priorisation des actions (urgent, important, souhaitable)

RÈGLES D'ANALYSE:
- Sois précis et factuel, base-toi sur les chiffres fournis
- Explique POURQUOI un indicateur est bon ou mauvais (ne te contente pas de dire "c'est bien")
- Si un ratio est en alerte, explique les causes possibles et les conséquences
- Utilise un langage professionnel mais accessible
- Fournis des comparaisons avec les seuils standards quand pertinent
- Sois constructif: même pour les points négatifs, propose des pistes d'amélioration

FORMAT DE SORTIE (JSON):
{{
    "vue_ensemble": "Résumé global en 2-3 phrases",
    "indicateurs_principaux": {{
        "ca": "Analyse du CA",
        "caf": "Analyse de la CAF",
        "ebe": "Analyse de l'EBE",
        "bfr": "Analyse du BFR",
        "tresorerie": "Analyse de la trésorerie"
    }},
    "ratios": {{
        "rentabilite": "Analyse des ratios de rentabilité",
        "liquidite": "Analyse des ratios de liquidité",
        "endettement": "Analyse des ratios d'endettement",
        "activite": "Analyse des ratios d'activité"
    }},
    "graphiques": {{
        "tendances": "Analyse des tendances visibles dans les graphiques d'évolution (CA, métriques financières)",
        "repartition": "Analyse de la répartition (top comptes, TVA, produits/charges, journaux)",
        "insights_visuels": "Insights tirés des visualisations et ce qu'elles révèlent sur l'activité"
    }},
    "correlations_insights": [
        "Insight 1: explication d'une corrélation ou observation importante",
        "Insight 2: ...",
        "Insight 3: ..."
    ],
    "points_forts": [
        "Point fort 1",
        "Point fort 2",
        "Point fort 3"
    ],
    "points_faibles": [
        "Point faible 1",
        "Point faible 2"
    ],
    "recommandations": [
        {{
            "action": "Description de l'action recommandée",
            "priorite": "URGENT|IMPORTANT|SOUHAITABLE",
            "justification": "Pourquoi cette action est importante"
        }}
    ]
}}

IMPORTANT: Retourne UNIQUEMENT le JSON, sans texte explicatif ni balises markdown.
"""

    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,  # Un peu de créativité mais reste factuel
            max_tokens=3000
        )
        
        ai_response = response.choices[0].message.content
        
        # Nettoyer la réponse (enlever les balises markdown si présentes)
        cleaned_response = ai_response.strip()
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[7:]
        if cleaned_response.startswith("```"):
            cleaned_response = cleaned_response[3:]
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3]
        cleaned_response = cleaned_response.strip()
        
        # Parser le JSON
        analysis = json.loads(cleaned_response)
        
        return {
            "success": True,
            "analysis": analysis
        }
        
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": "Erreur de parsing de la réponse IA",
            "details": str(e),
            "raw_response": ai_response if 'ai_response' in locals() else None
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Erreur lors de l'analyse IA",
            "details": str(e)
        }


def _prepare_indicators_summary(indicators_data, start_date=None, end_date=None):
    """
    Prépare un résumé textuel des indicateurs pour le prompt.
    """
    summary_lines = []
    
    # En-tête avec la période si disponible
    if start_date and end_date:
        summary_lines.append(f"=== PÉRIODE: {start_date} au {end_date} ===")
        summary_lines.append("")
    
    # Indicateurs principaux
    if "indicators" in indicators_data:
        ind = indicators_data["indicators"]
        summary_lines.append("=== INDICATEURS PRINCIPAUX ===")
        summary_lines.append(f"Chiffre d'Affaires: {_format_amount(ind.get('ca', 0))} Ar")
        summary_lines.append(f"CAF (Capacité d'Autofinancement): {_format_amount(ind.get('caf', 0))} Ar")
        summary_lines.append(f"EBE (Excédent Brut d'Exploitation): {_format_amount(ind.get('ebe', 0))} Ar")
        summary_lines.append(f"Marge Brute: {_format_amount(ind.get('marge_brute', 0))} Ar")
        summary_lines.append(f"BFR (Besoin en Fonds de Roulement): {_format_amount(ind.get('bfr', 0))} Ar")
        summary_lines.append(f"Trésorerie: {_format_amount(ind.get('tresorerie', 0))} Ar")
        summary_lines.append("")
    
    # Ratios
    if "ratios" in indicators_data:
        ratios = indicators_data["ratios"]
        summary_lines.append("=== RATIOS FINANCIERS ===")
        
        # Rentabilité
        summary_lines.append("Rentabilité:")
        summary_lines.append(f"  - ROE: {_format_ratio(ratios.get('roe'))}%")
        summary_lines.append(f"  - ROA: {_format_ratio(ratios.get('roa'))}%")
        summary_lines.append(f"  - Marge Nette: {_format_ratio(ratios.get('marge_nette'))}%")
        summary_lines.append(f"  - Marge Opérationnelle: {_format_ratio(ratios.get('marge_operationnelle'))}%")
        
        # Liquidité
        summary_lines.append("Liquidité:")
        summary_lines.append(f"  - Current Ratio: {_format_ratio(ratios.get('current_ratio'))}")
        summary_lines.append(f"  - Quick Ratio: {_format_ratio(ratios.get('quick_ratio'))}")
        
        # Endettement
        summary_lines.append("Endettement:")
        summary_lines.append(f"  - Annuité/CAF: {_format_ratio(ratios.get('annuite_caf'))}")
        summary_lines.append(f"  - Dette LMT/CAF: {_format_ratio(ratios.get('dette_caf'))}")
        summary_lines.append(f"  - Charges Financières/EBE: {_format_ratio(ratios.get('fi_ebe'))}")
        summary_lines.append(f"  - Gearing: {_format_ratio(ratios.get('gearing'))}")
        summary_lines.append(f"  - Leverage: {_format_ratio(ratios.get('leverage'))}")
        
        # Activité
        summary_lines.append("Activité:")
        summary_lines.append(f"  - Rotation Stock: {_format_ratio(ratios.get('rotation_stock'))}x")
        summary_lines.append("")
    
    # Statuts des ratios (OK/Alerte)
    if "ratios" in indicators_data:
        summary_lines.append("=== ALERTES ===")
        alerts = []
        for key, value in indicators_data["ratios"].items():
            if isinstance(value, dict) and value.get("status") == "Alerte":
                alerts.append(f"⚠️ {key}: {value.get('value')} (Seuil: {value.get('threshold')})")
        
        if alerts:
            summary_lines.extend(alerts)
        else:
            summary_lines.append("Aucune alerte détectée")
    
    return "\n".join(summary_lines)


def _format_amount(value):
    """Formate un montant avec séparateurs de milliers."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.0f}".replace(",", " ")
    except (ValueError, TypeError):
        return str(value)


def _format_ratio(value):
    """Formate un ratio (peut être un nombre ou un dict avec 'value')."""
    if value is None:
        return "N/A"
    
    # Si c'est un dict (avec status, value, threshold)
    if isinstance(value, dict):
        val = value.get("value")
        if val is None:
            return "N/A"
        try:
            return f"{float(val):.2f}"
        except (ValueError, TypeError):
            return str(val)
    
    # Si c'est un nombre direct
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return str(value)
