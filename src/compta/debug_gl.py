from compta.models import GrandLivre
from django.db.models import Sum
from django.db.models.functions import ExtractYear

print('--- BREAKDOWN BY YEAR ---')
qs = GrandLivre.objects.annotate(year=ExtractYear('date')).values('year').annotate(credit=Sum('credit'), debit=Sum('debit')).order_by('year')

for q in qs:
    balance = (q['credit'] or 0) - (q['debit'] or 0)
    print(f"Year {q['year']}: Credit={q['credit']}, Debit={q['debit']}, Balance={balance}")
