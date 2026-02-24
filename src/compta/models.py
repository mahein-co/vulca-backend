from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.conf import settings
from decimal import Decimal

# --------------------------------------------------------
# PROJET (Multi-tenant accounting)
# --------------------------------------------------------

class Project(models.Model):
    """
    Représente un projet comptable indépendant.
    Chaque projet a ses propres données comptables isolées.
    """
    name = models.CharField(max_length=255, unique=True, verbose_name="Nom du projet")
    description = models.TextField(blank=True, null=True, verbose_name="Description")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_projects',
        verbose_name="Créé par"
    )
    is_active = models.BooleanField(default=True, verbose_name="Actif")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'project'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name


class ProjectAccess(models.Model):
    """
    Gère les accès utilisateurs aux projets.
    Status: pending (en attente), approved (approuvé), rejected (rejeté)
    """
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('approved', 'Approuvé'),
        ('rejected', 'Rejeté'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_accesses',
        verbose_name="Utilisateur"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='user_accesses',
        verbose_name="Projet"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name="Statut"
    )
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name="Demandé le")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Approuvé le")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_accesses',
        verbose_name="Approuvé par"
    )

    class Meta:
        db_table = 'project_access'
        ordering = ['-requested_at']
        unique_together = ('user', 'project')  # Un utilisateur ne peut demander qu'une fois par projet
        indexes = [
            models.Index(fields=['user', 'project']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.project.name} ({self.status})"


# --------------------------------------------------------
# JOURNAL COMPTABLE (Source de vérité)
# --------------------------------------------------------
class Journal(models.Model):
    # Relations vers les sources (fichier ou formulaire)
    # file_source = models.ForeignKey(
        # 'ocr.FileSource',
        # on_delete=models.SET_NULL,
        # null=True,
        # blank=True,
        # related_name='journal_entries'
    # )
    # form_source = models.ForeignKey(
        # 'ocr.FormSource',
        # on_delete=models.SET_NULL,
        # null=True,
        # blank=True,
        # related_name='journal_entries'
    # )
    
    # Projet auquel appartient cette écriture
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='journals',
        verbose_name="Projet",
        null=True,
        blank=True
    )
    
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
    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'journal'
        ordering = ['project', 'date', 'numero_piece']
        indexes = [
            models.Index(fields=['project', 'date', 'type_journal']),
            models.Index(fields=['project', 'numero_compte', 'date']),
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
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='grand_livres',
        verbose_name="Projet",
        null=True,
        blank=True
    )
    journal = models.ForeignKey(
        'Journal', on_delete=models.CASCADE,
        related_name='grand_livre_entries',
        verbose_name='Écriture journal'
    )
    numero_compte = models.CharField(max_length=20, db_index=True)
    date = models.DateField(db_index=True)
    numero_piece = models.CharField(max_length=50)
    libelle = models.CharField(max_length=255)
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                validators=[MinValueValidator(Decimal('0.00'))])
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                 validators=[MinValueValidator(Decimal('0.00'))])
    solde = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # resume_mouvement = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'grand_livre'
        ordering = ['project', 'numero_compte', 'date', 'numero_piece']
        indexes = [
            models.Index(fields=['project', 'numero_compte', 'date']),
            models.Index(fields=['project', 'date']),
            models.Index(fields=['numero_compte', 'date']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"{self.numero_compte} - {self.date} - {self.libelle}"



# BALANCE (Synthèse par compte depuis Grand Livre)

class Balance(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='balances',
        verbose_name="Projet",
        null=True,
        blank=True
    )
    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255, blank=True, null=True)
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
        ordering = ['project', 'numero_compte', 'date']
        unique_together = ('project', 'numero_compte', 'date') 
        indexes = [
            models.Index(fields=['project', 'date']),
            models.Index(fields=['project', 'numero_compte', 'date']),
            models.Index(fields=['date']),
            models.Index(fields=['numero_compte', 'date']),
        ]

    def calculate_from_grand_livre(self):
        """Calculer la Balance à partir du Grand Livre"""

        from .models import GrandLivre

        data = GrandLivre.objects.filter(
            project=self.project,
            numero_compte=self.numero_compte,
            date=self.date   # ✅ FILTRE PAR DATE OBLIGATOIRE
        ).aggregate(
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
        return f"{self.numero_compte} - {self.libelle} au {self.date}"



# COMPTE DE RESULTAT (Dérivé de la Balance)

class CompteResultat(models.Model):
    NATURE_CHOICES = [('CHARGE', 'Charge'), ('PRODUIT', 'Produit')]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='comptes_resultat',
        verbose_name="Projet",
        null=True,
        blank=True
    )
    balance = models.ForeignKey(Balance, on_delete=models.CASCADE,
                                related_name='comptes_resultat', verbose_name='Balance source',
                                null=True, blank=True)

    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(max_digits=15, decimal_places=2)
    nature = models.CharField(max_length=10, choices=NATURE_CHOICES, db_index=True)
    date = models.DateField(db_index=True)
    description = models.TextField(blank=True, null=True)
    hash = models.CharField(max_length=64, blank=True, null=True)  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compte_resultat'
        ordering = ['project', 'date', 'nature', 'numero_compte']
        indexes = [
            models.Index(fields=['project', 'date', 'nature']),
            models.Index(fields=['project', 'numero_compte']),
            models.Index(fields=['date', 'nature']),
            models.Index(fields=['numero_compte']),
        ]

    def save(self, *args, **kwargs):
        if self.balance_id:
            if not self.numero_compte:
                self.numero_compte = self.balance.numero_compte
            if not self.libelle:
                self.libelle = self.balance.libelle
            if not self.date:
                self.date = self.balance.date
            # ✅ COPIE DU PROJET DEPUIS LA BALANCE
            if not self.project_id:
                self.project_id = self.balance.project_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.nature}"


# BILAN (Dérivé de la Balance)

class Bilan(models.Model):
    TYPE_CHOICES = [('ACTIF', 'Actif'), ('PASSIF', 'Passif')]
    CATEGORIE_CHOICES = [
        ('ACTIF_COURANTS', 'Actif courants'),
        ('ACTIF_NON_COURANTS', 'Actif non courants'),
        ('CAPITAUX_PROPRES', 'Capitaux propres'),
        ('PASSIFS_COURANTS', 'Passifs courants'),
        ('PASSIFS_NON_COURANTS', 'Passifs non courants'),
        
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='bilans',
        verbose_name="Projet",
        null=True,
        blank=True
    )
    balance = models.ForeignKey(Balance, on_delete=models.CASCADE,
                                related_name='bilans', verbose_name='Balance source',
                                null=True, blank=True)

    numero_compte = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=255)
    montant_ar = models.DecimalField(max_digits=15, decimal_places=2)
    date = models.DateField(db_index=True)
    type_bilan = models.CharField(max_length=10, choices=TYPE_CHOICES, db_index=True)
    categorie = models.CharField(max_length=30, choices=CATEGORIE_CHOICES)
    description = models.TextField(blank=True, null=True)
    hash = models.CharField(max_length=64, blank=True, null=True)  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bilan'
        ordering = ['project', 'date', 'type_bilan', 'categorie', 'numero_compte']
        indexes = [
            models.Index(fields=['project', 'date', 'type_bilan']),
            models.Index(fields=['project', 'type_bilan', 'categorie']),
            models.Index(fields=['date', 'type_bilan']),
            models.Index(fields=['type_bilan', 'categorie']),
        ]

    def save(self, *args, **kwargs):
        if self.balance_id:
            if not self.numero_compte:
                self.numero_compte = self.balance.numero_compte
            if not self.libelle:
                self.libelle = self.balance.libelle
            if not self.date:
                self.date = self.balance.date
            # ✅ COPIE DU PROJET DEPUIS LA BALANCE
            if not self.project_id:
                self.project_id = self.balance.project_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_compte} - {self.libelle} - {self.type_bilan}"

  

# ANNEXE COMPTABLE (Lié au Bilan)

class AnnexeComptable(models.Model):
    bilan = models.ForeignKey(Bilan, on_delete=models.CASCADE,
                              null=True, blank=True, related_name='annexes',
                              verbose_name='Bilan associé')

    date = models.DateField(db_index=True)
    section = models.CharField(max_length=100)
    rubrique = models.CharField(max_length=200)
    commentaire_donnee = models.TextField()
    description = models.TextField(blank=True, null=True)
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
