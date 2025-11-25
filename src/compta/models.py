from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from decimal import Decimal
# --------------------------------------------------------
# JOURNAL COMPTABLE (Source de vérité)
# --------------------------------------------------------
class Journal(models.Model):
    TYPE_JOURNAL_CHOICES = [
        ('ACHAT', 'Journal des achats'),
        ('VENTE', 'Journal des ventes'),
        ('BANQUE', 'Journal de banque'),
        ('CAISSE', 'Journal de caisse'),
        ('OD', 'Journal des opérations diverses'),
        ('AN', 'Journal des à-nouveaux'),
    ]
    
    date = models.DateField(db_index=True)
    numero_piece = models.CharField(max_length=50)
    type_journal = models.CharField(max_length=20, choices=TYPE_JOURNAL_CHOICES)
    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    debit_ar = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    credit_ar = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )

    form_source = models.ManyToManyField(
        'ocr.FormSource',
        blank=True,
        null=True,
        related_name='form_source_journals'
    )
    file_source = models.ManyToManyField(
        'ocr.FileSource',
        blank=True,
        null=True,
        related_name='file_source_journals'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'journal'
        ordering = ['date', 'numero_piece']
        indexes = [
            models.Index(fields=['date', 'type_journal']),
            models.Index(fields=['numero_compte', 'date']),
        ]

    def clean(self):
        if self.debit_ar == 0 and self.credit_ar == 0:
            raise ValidationError("Une écriture comptable doit avoir un débit ou un crédit non nul")

    def __str__(self):
        return f"{self.date} - {self.numero_piece} - {self.libelle}"



# GRAND LIVRE (Dérivé du Journal)

class GrandLivre(models.Model):
    journal = models.ForeignKey(
        Journal, on_delete=models.CASCADE,
        related_name='grand_livre_entries', verbose_name='Écriture journal',
        null=True, blank=True
    )
    numero_compte = models.CharField(max_length=20, db_index=True, null=True, blank=True)
    date = models.DateField(db_index=True)
    numero_piece = models.CharField(max_length=50)
    libelle = models.CharField(max_length=255)
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                validators=[MinValueValidator(Decimal('0.00'))])
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                 validators=[MinValueValidator(Decimal('0.00'))])
    solde = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'grand_livre'
        ordering = ['numero_compte', 'date', 'numero_piece']
        indexes = [
            models.Index(fields=['numero_compte', 'date']),
            models.Index(fields=['date']),
        ]

    def save(self, *args, **kwargs):
        # Remplir automatiquement depuis le Journal
        if self.journal_id:
            self.numero_compte = self.journal.numero_compte
            self.date = self.journal.date
            self.numero_piece = self.journal.numero_piece
            self.libelle = self.journal.libelle
            self.debit = self.journal.debit_ar
            self.credit = self.journal.credit_ar

        # Calcul du solde cumulatif
        super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.numero_compte} - {self.date} - {self.libelle}"



# BALANCE (Synthèse par compte depuis Grand Livre)

class Balance(models.Model):
    numero_compte = models.CharField(max_length=20, unique=True, db_index=True)
    intitule_du_compte = models.CharField(max_length=255)
    total_debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                      validators=[MinValueValidator(Decimal('0.00'))])
    total_credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                       validators=[MinValueValidator(Decimal('0.00'))])
    solde_debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    solde_credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    date = models.DateField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'balance'
        ordering = ['numero_compte']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['numero_compte', 'date']),
        ]

    def calculate_from_grand_livre(self):
        """Calculer la Balance à partir du Grand Livre"""
        data = GrandLivre.objects.filter(numero_compte=self.numero_compte).aggregate(
            total_debit=models.Sum('debit'),
            total_credit=models.Sum('credit')
        )
        self.total_debit = data['total_debit'] or Decimal('0.00')
        self.total_credit = data['total_credit'] or Decimal('0.00')
        diff = self.total_debit - self.total_credit
        self.solde_debit = max(diff, Decimal('0.00'))
        self.solde_credit = max(-diff, Decimal('0.00'))
        self.save()

    def __str__(self):
        return f"{self.numero_compte} - {self.intitule_du_compte} au {self.date}"



# COMPTE DE RESULTAT (Dérivé de la Balance)

class CompteResultat(models.Model):
    NATURE_CHOICES = [('CHARGE', 'Charge'), ('PRODUIT', 'Produit')]

    balance = models.ForeignKey(Balance, on_delete=models.CASCADE,
                                related_name='comptes_resultat', verbose_name='Balance source',
                                null=True, blank=True)

    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(max_digits=15, decimal_places=2)
    nature = models.CharField(max_length=10, choices=NATURE_CHOICES, db_index=True)
    date = models.DateField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compte_resultat'
        ordering = ['date', 'nature', 'numero_compte']
        indexes = [
            models.Index(fields=['date', 'nature']),
            models.Index(fields=['numero_compte']),
        ]

    def save(self, *args, **kwargs):
        if self.balance_id:
            self.numero_compte = self.balance.numero_compte
            self.libelle = self.balance.intitule_du_compte
            self.date = self.balance.date
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.nature}"


# BILAN (Dérivé de la Balance)

class Bilan(models.Model):
    TYPE_CHOICES = [('ACTIF', 'Actif'), ('PASSIF', 'Passif')]
    CATEGORIE_CHOICES = [
        ('ACTIF_IMMOBILISE', 'Actif immobilisé'),
        ('ACTIF_CIRCULANT', 'Actif circulant'),
        ('TRESORERIE_ACTIF', 'Trésorerie - Actif'),
        ('CAPITAUX_PROPRES', 'Capitaux propres'),
        ('PROVISIONS', 'Provisions'),
        ('DETTES', 'Dettes'),
        ('TRESORERIE_PASSIF', 'Trésorerie - Passif'),
    ]

    balance = models.ForeignKey(Balance, on_delete=models.CASCADE,
                                related_name='bilans', verbose_name='Balance source',
                                null=True, blank=True)

    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(max_digits=15, decimal_places=2)
    nature = models.CharField(max_length=50, blank=True, null=True)
    date = models.DateField(db_index=True)
    type_bilan = models.CharField(max_length=10, choices=TYPE_CHOICES, db_index=True)
    categorie = models.CharField(max_length=30, choices=CATEGORIE_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bilan'
        ordering = ['date', 'type_bilan', 'categorie', 'numero_compte']
        indexes = [
            models.Index(fields=['date', 'type_bilan']),
            models.Index(fields=['type_bilan', 'categorie']),
        ]

    def save(self, *args, **kwargs):
        if self.balance_id:
            self.numero_compte = self.balance.numero_compte
            self.libelle = self.balance.intitule_du_compte
            self.date = self.balance.date
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.type_bilan}"



# ANNEXE COMPTABLE (Lié au Bilan)

class AnnexeComptable(models.Model):
    bilan = models.ForeignKey(Bilan, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name='annexes',
                              verbose_name='Bilan associé')

    date = models.DateField(db_index=True)
    section = models.CharField(max_length=100)
    rubrique = models.CharField(max_length=200)
    commentaire_donnee = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'annexe_comptable'
        ordering = ['date', 'section', 'rubrique']
        indexes = [
            models.Index(fields=['date', 'section']),
        ]

    def __str__(self):
        return f"{self.date} - {self.section} - {self.rubrique}"
