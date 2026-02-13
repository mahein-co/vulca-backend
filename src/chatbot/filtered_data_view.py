from django.db.models import Sum, Q
from decimal import Decimal
from datetime import datetime, date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from compta.permissions import HasProjectAccess
from chatbot.services.accounting_queries import AccountingQueryService


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasProjectAccess])
def get_filtered_accounting_data(request):
    """
    Retourne toutes les données financières filtrées par date pour le chatbot.
    Endpoint: /api/chatbot/filtered-data/?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD
    """
    project_id = getattr(request, 'project_id', None)
    if not project_id:
        return Response({"error": "Project non fourni."}, status=400)
    
    date_start = request.GET.get('date_start')
    date_end = request.GET.get('date_end')
    
    if not date_start or not date_end:
        return Response({
            "error": "Les paramètres date_start et date_end sont requis."
        }, status=400)
    
    try:
        # Convertir les dates string en objets date
        start_date = datetime.strptime(date_start, '%Y-%m-%d').date()
        end_date = datetime.strptime(date_end, '%Y-%m-%d').date()
    except ValueError:
        return Response({
            "error": "Format de date invalide. Utilisez YYYY-MM-DD."
        }, status=400)
    
    # Initialiser le service de requêtes comptables
    service = AccountingQueryService(project_id=project_id)
    
    # Récupérer toutes les données pertinentes pour la période
    try:
        data = {
            'filter': {
                'date_start': date_start,
                'date_end': date_end,
                'applied_at': datetime.now().isoformat(),
                'project_id': project_id
            },
            'chiffre_affaires': service.get_chiffre_affaires(
                start_date=start_date,
                end_date=end_date
            ),
            'charges': service.get_charges(
                start_date=start_date,
                end_date=end_date
            ),
            'resultat_net': service.get_resultat_net(
                start_date=start_date,
                end_date=end_date
            ),
            'tresorerie': service.get_tresorerie(date_fin=end_date),
            'bilan': service.get_bilan_summary(date_bilan=end_date),
        }
        
        print(f"\n{'='*80}")
        print(f"[INFO] DONNÉES FILTRÉES GÉNÉRÉES")
        print(f"{'='*80}")
        print(f"Projet: {project_id}")
        print(f"Période: {date_start} → {date_end}")
        print(f"CA: {data['chiffre_affaires'].get('montant', 0):,.2f} AR")
        print(f"Charges: {data['charges'].get('montant', 0):,.2f} AR")
        print(f"Résultat: {data['resultat_net'].get('montant', 0):,.2f} AR")
        print(f"{'='*80}\n")
        
        return Response(data, status=200)
        
    except Exception as e:
        import traceback
        print(f"\n[ERROR] Erreur lors de la génération des données filtrées:")
        print(traceback.format_exc())
        return Response({
            "error": f"Erreur lors de la récupération des données: {str(e)}"
        }, status=500)
