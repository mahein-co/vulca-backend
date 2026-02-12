"""
Module d'analyse IA pour le Compte de Résultat et le Bilan.
Utilise GPT-4 pour générer des analyses d'expert-comptable basées sur les transactions et KPIs.
"""

import json
from openai import OpenAI
from django.conf import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)

def analyze_compte_resultat_with_ai(data, view_type='compte_resultat', project_id=None):
    """
    Analyse les données du Compte de Résultat ou du Bilan avec l'IA.
    """
    
    # Préparer le résumé des données
    data_summary = _prepare_data_summary(data, view_type)
    
    filters = data.get('filters', {})
    year = filters.get('year', 'N/A')
    period_mode = filters.get('period_mode', 'ANNUAL')
    sub_period = filters.get('sub_period', '')
    
    period_label = f"Année {year}"
    if period_mode == 'QUARTERLY' and sub_period:
        period_label += f" - Trimestre {sub_period}"
    elif period_mode == 'MONTHLY' and sub_period:
        # On pourrait mapper M1 à Janvier ici mais restons simple
        period_label += f" - Mois {sub_period}"

    view_label = "Compte de Résultat" if view_type == 'compteResultat' else "Bilan"

    prompt = f"""
Tu es un expert-comptable certifié et analyste financier senior. Tu analyses le {view_label} d'une entreprise malgache.

PÉRIODE ANALYSÉE: {period_label}

DONNÉES:
{data_summary}

MISSION:
Réalise une analyse approfondie de ce {view_label} comme si tu présentais ton rapport au chef d'entreprise ou à un partenaire financier. Ton ton doit être professionnel, expert mais actionnable.

Instructions spécifiques pour le {view_label}:
1. **Vue d'ensemble**: Résume la situation financière sur la période sélectionnée en 2-3 phrases.
2. **Analyse des postes**: 
   - Si c'est un Compte de Résultat, analyse la structure des produits et des charges, la marge et le résultat net.
   - Si c'est un Bilan, analyse l'équilibre financier (Actifs vs Passifs) et la structure du patrimoine.
3. **Analyse des transactions**: Commente les transactions les plus significatives fournies.
4. **Points d'attention**: Dépendamment des chiffres, identifie les zones de risque ou les anomalies.
5. **Recommandations**: Propose 3-4 actions concrètes pour améliorer la santé financière.

FORMAT DE SORTIE (JSON):
{{
    "vue_ensemble": "Résumé global",
    "analyse_detaillee": {{
        "produits_ou_actifs": "Analyse de la partie haute (Produits ou Actifs)",
        "charges_ou_passifs": "Analyse de la partie basse (Charges ou Passifs)",
        "performance_ou_equilibre": "Analyse de la rentabilité ou de l'équilibre financier"
    }},
    "transactions_remarquables": "Analyse des écritures comptables les plus impactantes",
    "points_forts": ["Point 1", "Point 2"],
    "points_faibles": ["Point 1", "Point 2"],
    "recommandations": [
        {{
            "action": "Description de l'action",
            "priorite": "URGENT|IMPORTANT|SOUHAITABLE",
            "justification": "Pourquoi faire cela"
        }}
    ]
}}

IMPORTANT: Retourne UNIQUEMENT le JSON, sans texte explicatif ni balises markdown.
"""

    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=3000
        )
        
        ai_response = response.choices[0].message.content
        cleaned_response = _clean_json_response(ai_response)
        analysis = json.loads(cleaned_response)
        
        return {
            "success": True,
            "analysis": analysis
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": "Erreur lors de l'analyse IA",
            "details": str(e)
        }

def _prepare_data_summary(data, view_type):
    kpis = data.get('kpis', {})
    transactions = data.get('transactions', [])
    
    summary = []
    
    if view_type == 'compteResultat':
        summary.append("=== INDICATEURS CLÉS (COMPTE DE RÉSULTAT) ===")
        summary.append(f"Chiffre d'Affaires (Produits): {_format_amount(kpis.get('produits', 0))} Ar")
        summary.append(f"Total des Charges: {_format_amount(kpis.get('charges', 0))} Ar")
        summary.append(f"Résultat Net: {_format_amount(kpis.get('resultatNet', 0))} Ar")
    else:
        summary.append("=== INDICATEURS CLÉS (BILAN) ===")
        summary.append(f"Total Actif: {_format_amount(kpis.get('totalActif', 0))} Ar")
        summary.append(f"Total Passif: {_format_amount(kpis.get('totalPassif', 0))} Ar")
        summary.append(f"Capitaux Propres: {_format_amount(kpis.get('capitauxPropres', 0))} Ar")
        summary.append(f"Ratio d'Endettement: {kpis.get('endettementRatio', 0):.2f}%")

    if transactions:
        summary.append("\n=== EXTRAIT DES TRANSACTIONS (TOP IMPACT) ===")
        # On limite aux 20 plus grosses transactions pour ne pas exploser les tokens
        sorted_tx = sorted(transactions, key=lambda x: abs(float(x.get('montant_ar', 0))), reverse=True)[:20]
        for tx in sorted_tx:
            summary.append(f"- {tx.get('date', '')} | {tx.get('numero_compte', '')} | {tx.get('libelle', '')} | {_format_amount(tx.get('montant_ar', 0))} Ar | ({tx.get('nature', tx.get('categorie', ''))})")
            
    return "\n".join(summary)

def _format_amount(value):
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except:
        return str(value)

def _clean_json_response(response):
    response = response.strip()
    if response.startswith("```json"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]
    return response.strip()
