import unittest
from datetime import date
import sys
import os

# Add parent directory to sys.path to allow imports from sibling modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from views import detect_financial_query

class TestChatbotLogic(unittest.TestCase):
    def test_detect_ca_with_french_dates(self):
        # Cas: "chiffre d'affaires entre juin 2023 et décembre 2023"
        user_input = "chiffre d'affaires entre juin 2023 et décembre 2023"
        result = detect_financial_query(user_input)
        
        self.assertEqual(result['type'], 'ca')
        self.assertTrue(result['has_explicit_dates'])
        self.assertEqual(result['params']['start_date'], date(2023, 6, 1))
        self.assertEqual(result['params']['end_date'], date(2023, 12, 31))

    def test_detect_charges_single_month(self):
        # Cas: "mes charges en janvier"
        user_input = "mes charges en janvier"
        # On assume l'année en cours pour le test si pas d'année
        import datetime
        current_year = datetime.datetime.now().year
        
        result = detect_financial_query(user_input)
        
        self.assertEqual(result['type'], 'charges')
        self.assertTrue(result['has_explicit_dates'])
        self.assertEqual(result['params']['start_date'], date(current_year, 1, 1))
        self.assertEqual(result['params']['end_date'], date(current_year, 1, 31))

    def test_detect_comparaison_years(self):
        # Cas: "comparaison 2023 et 2024"
        user_input = "comparaison 2023 et 2024"
        result = detect_financial_query(user_input)
        
        self.assertEqual(result['type'], 'comparaison')
        self.assertTrue(result['has_explicit_dates'])
        self.assertEqual(result['params']['annee1'], 2023)
        self.assertEqual(result['params']['annee2'], 2024)

    def test_detect_details_flag(self):
        # Cas: "donne moi les détails de mon CA"
        user_input = "donne moi les détails de mon CA"
        result = detect_financial_query(user_input)
        
        self.assertEqual(result['type'], 'ca')
        self.assertTrue(result['include_details'])

    def test_detect_global_analysis_with_dates(self):
        # Cas: "analyse ma situation en 2023"
        user_input = "analyse ma situation en 2023"
        result = detect_financial_query(user_input)
        
        self.assertEqual(result['type'], 'analyse_globale')
        self.assertEqual(result['params']['annee'], 2023)

if __name__ == '__main__':
    unittest.main()
