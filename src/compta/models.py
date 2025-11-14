from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class Journal(models.Model):
    """Journal comptable"""
    
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
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Débit (Ar)'
    )
    credit_ar = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Crédit (Ar)'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'journal'
        ordering = ['date', 'numero_piece']
        verbose_name = 'Journal'
        verbose_name_plural = 'Journaux'
        indexes = [
            models.Index(fields=['date', 'type_journal']),
            models.Index(fields=['numero_compte', 'date']),
        ]
    
    def __str__(self):
        return f"{self.date} - {self.numero_piece} - {self.libelle}"


class GrandLivre(models.Model):
    """Grand livre - Historique des mouvements par compte"""
    
    date = models.DateField(db_index=True)
    numero_piece = models.CharField(max_length=50)
    libelle = models.CharField(max_length=255)
    debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    solde = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'grand_livre'
        ordering = ['date', 'numero_piece']
        verbose_name = 'Grand Livre'
        verbose_name_plural = 'Grand Livre'
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['numero_piece']),
        ]
    
    def __str__(self):
        return f"{self.date} - {self.numero_piece} - {self.libelle}"


class Balance(models.Model):
    """Balance comptable - Soldes des comptes"""
    
    numero_compte = models.CharField(max_length=20, db_index=True)
    intitule_du_compte = models.CharField(max_length=255)
    total_debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    total_credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    solde_debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    solde_credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    date = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'balance'
        ordering = ['numero_compte', 'date']
        verbose_name = 'Balance'
        verbose_name_plural = 'Balances'
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['numero_compte', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['numero_compte', 'date'],
                name='unique_balance_compte_date'
            )
        ]
    
    def __str__(self):
        return f"{self.numero_compte} - {self.intitule_du_compte} au {self.date}"


class CompteResultat(models.Model):
    """Compte de résultat - Charges et Produits"""
    
    NATURE_CHOICES = [
        ('CHARGE', 'Charge'),
        ('PRODUIT', 'Produit'),
    ]
    
    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name='Montant (Ar)'
    )
    nature = models.CharField(max_length=10, choices=NATURE_CHOICES, db_index=True)
    date = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'compte_resultat'
        ordering = ['date', 'nature', 'numero_compte']
        verbose_name = 'Compte de Résultat'
        verbose_name_plural = 'Comptes de Résultat'
        indexes = [
            models.Index(fields=['date', 'nature']),
            models.Index(fields=['numero_compte']),
        ]
    
    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.nature}"


class Bilan(models.Model):
    """Bilan comptable - Actif et Passif"""
    
    TYPE_CHOICES = [
        ('ACTIF', 'Actif'),
        ('PASSIF', 'Passif'),
    ]
    
    CATEGORIE_CHOICES = [
        ('ACTIF_IMMOBILISE', 'Actif immobilisé'),
        ('ACTIF_CIRCULANT', 'Actif circulant'),
        ('TRESORERIE_ACTIF', 'Trésorerie - Actif'),
        ('CAPITAUX_PROPRES', 'Capitaux propres'),
        ('PROVISIONS', 'Provisions'),
        ('DETTES', 'Dettes'),
        ('TRESORERIE_PASSIF', 'Trésorerie - Passif'),
    ]
    
    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name='Montant (Ar)'
    )
    nature = models.CharField(max_length=50, blank=True, null=True)
    date = models.DateField(db_index=True)
    type_bilan = models.CharField(max_length=10, choices=TYPE_CHOICES, db_index=True)
    categorie = models.CharField(max_length=30, choices=CATEGORIE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'bilan'
        ordering = ['date', 'type_bilan', 'categorie', 'numero_compte']
        verbose_name = 'Bilan'
        verbose_name_plural = 'Bilans'
        indexes = [
            models.Index(fields=['date', 'type_bilan']),
            models.Index(fields=['type_bilan', 'categorie']),
        ]
    
    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.type_bilan}"


class AnnexeComptable(models.Model):
    """Annexe comptable - Informations complémentaires"""
    
    date = models.DateField(db_index=True)
    section = models.CharField(max_length=100)
    rubrique = models.CharField(max_length=200)
    commentaire_donnee = models.TextField(verbose_name='Commentaire / Donnée')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'annexe_comptable'
        ordering = ['date', 'section', 'rubrique']
        verbose_name = 'Annexe Comptable'
        verbose_name_plural = 'Annexes Comptables'
        indexes = [
            models.Index(fields=['date', 'section']),
        ]
    
    def __str__(self):
        return f"{self.date} - {self.section} - {self.rubrique}"