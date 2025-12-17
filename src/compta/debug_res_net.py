from compta.models import GrandLivre
from django.db.models import Sum
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal

def solde(prefix, start, end):
    qs = GrandLivre.objects.filter(
        numero_compte__startswith=prefix,
        date__range=[start, end]
    )
    data = qs.aggregate(credit=Sum("credit"), debit=Sum("debit"))
    return (data["credit"] or Decimal("0.00")) - (data["debit"] or Decimal("0.00"))

def calc_res(start, end):
    return sum(solde(str(c), start, end) for c in ['7', '76', '77']) - \
           sum(solde(str(c), start, end) for c in ['60','61','62','63','64','65', '66', '67', '69'])  # Using subtraction like formula (wait, formula IS ADDITION of signed values)

def calc_res_corrected(start, end):
    # Using the NEW formula (addition of signed values)
    p = solde("7", start, end) + solde("76", start, end) + solde("77", start, end)
    c = sum(solde(str(i), start, end) for i in range(60, 70) if i not in [66, 67, 69]) # 60-65
    c_fin = solde("66", start, end)
    c_exc = solde("67", start, end)
    impot = solde("69", start, end)
    return p + c + c_fin + c_exc + impot

# Simulate Default (2025 vs 2024)
today = date.today()
cur_s = date(today.year, 1, 1)
cur_e = date(today.year, 12, 31)
prev_s = date(today.year - 1, 1, 1)
prev_e = date(today.year - 1, 12, 31)

print(f"Default Current ({cur_s} - {cur_e}): {calc_res_corrected(cur_s, cur_e)}")
print(f"Default Previous ({prev_s} - {prev_e}): {calc_res_corrected(prev_s, prev_e)}")

# Simulate User View (2019-2020?)
# User result is 3,390,101. let's find the range.
# 2019 + 2020 range?
user_s = date(2019, 1, 1)
user_e = date(2020, 12, 31)
print(f"User Range ({user_s} - {user_e}): {calc_res_corrected(user_s, user_e)}")

# If user range is 2 years, previous is 2017-2018
user_delta = (user_e - user_s).days
prev_user_eff_end = user_s - relativedelta(days=1)
prev_user_eff_start = prev_user_eff_end - (user_e - user_s)
print(f"User Previous ({prev_user_eff_start} - {prev_user_eff_end}): {calc_res_corrected(prev_user_eff_start, prev_user_eff_end)}")

# Try to find a previous period that gives 150M
print("Searching for 150M result...")
# Check all years
for y in range(2015, 2026):
    r = calc_res_corrected(date(y,1,1), date(y,12,31))
    print(f"{y}: {r}")
