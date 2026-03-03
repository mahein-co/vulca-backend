import os
import django
import sys

# Setup Django
sys.path.append('d:/mahein-co/vulca-backend/src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vulca_backend.settings')
django.setup()

from compta.kpi_utils import get_latest_bilan_sum, get_cr_sum, get_resultat_net
from decimal import Decimal

project_id = 4
ds = '2025-01-01'
de = '2025-12-31'

print("--- KPI Verification ---")
try:
    ca = get_cr_sum(project_id, ds, de, prefix_list=["70"])
    total_actif = get_latest_bilan_sum(project_id, ds, de, type_bilan="ACTIF")
    res_net = get_resultat_net(project_id, ds, de)
    
    print(f"Project ID: {project_id}")
    print(f"Period: {ds} to {de}")
    print(f"CA (70): {ca}")
    print(f"Total Actif: {total_actif}")
    print(f"Resultat Net: {res_net}")
    
    # Test specific ratio components
    stocks = get_latest_bilan_sum(project_id, ds, de, prefix_list=["3"], type_bilan="ACTIF")
    print(f"Stocks: {stocks}")
    
    dettes_fi = get_latest_bilan_sum(project_id, ds, de, prefix_list=["16"], type_bilan="PASSIF")
    print(f"Dettes FI (16): {dettes_fi}")
    
    print("Verification completed successfully.")
except Exception as e:
    print(f"Verification failed with error: {e}")
    import traceback
    traceback.print_exc()
