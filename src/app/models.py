from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.auth.base_user import BaseUserManager
from django.utils import timezone
from .managers import UserManager


class UserManager(BaseUserManager):
    """Manager pour le modèle User personnalisé"""
    
    def create_user(self, email, username, password=None, **extra_fields):
        if not email:
            raise ValueError("L'utilisateur doit avoir un email")
        
        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_approved', True)  # Superuser auto-approuvé

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Le superutilisateur doit avoir is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Le superutilisateur doit avoir is_superuser=True.')

        return self.create_user(email, username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Modèle User avec système d'approbation"""
    
    ROLE_CHOICES = [
        ('admin', 'Administrateur'),
        ('user', 'Utilisateur'),
    ]
    
    email = models.EmailField(unique=True, verbose_name='Email')
    username = models.CharField(max_length=150, unique=True, verbose_name='Nom d\'utilisateur')
    name = models.CharField(max_length=255, blank=True, verbose_name='Nom complet')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user', verbose_name='Rôle')
    
    # Système d'approbation
    is_approved = models.BooleanField(default=False, verbose_name='Approuvé')
    approved_by = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='approved_users',
        verbose_name='Approuvé par'
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='Approuvé le')
    
    # Permissions Django
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']
    
    class Meta:
        db_table = 'users'
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
    
    def __str__(self):
        return self.email
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def can_access_system(self):
        """L'utilisateur peut accéder au système s'il est approuvé ou admin"""
        return self.is_approved or self.is_admin


class Project(models.Model):
    """Projets créés par les admins"""
    title = models.CharField(max_length=200, verbose_name='Titre')
    description = models.TextField(verbose_name='Description')
    
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='created_projects',
        limit_choices_to={'role': 'admin'},
        verbose_name='Créé par'
    )
    
    assigned_users = models.ManyToManyField(
        User,
        through='ProjectAssignment',
        related_name='assigned_projects',
        through_fields=('project', 'user'),
        blank=True,
        verbose_name='Utilisateurs assignés'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, verbose_name='Actif')
    
    class Meta:
        db_table = 'projects'
        ordering = ['-created_at']
        verbose_name = 'Projet'
        verbose_name_plural = 'Projets'
    
    def __str__(self):
        return self.title


class ProjectAssignment(models.Model):
    """Table intermédiaire pour l'assignation des projets"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Utilisateur')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name='Projet')
    
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='assignments_made',
        verbose_name='Assigné par'
    )
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name='Assigné le')
    
    # Permissions spécifiques
    can_edit = models.BooleanField(default=False, verbose_name='Peut modifier')
    can_delete = models.BooleanField(default=False, verbose_name='Peut supprimer')
    
    class Meta:
        db_table = 'project_assignments'
        unique_together = ['user', 'project']
        verbose_name = 'Assignation de projet'
        verbose_name_plural = 'Assignations de projets'
    
    def __str__(self):
        return f"{self.user.username} → {self.project.title}"


class Task(models.Model):
    """Tâches liées aux projets"""
    STATUS_CHOICES = [
        ('todo', 'À faire'),
        ('in_progress', 'En cours'),
        ('done', 'Terminé'),
    ]
    
    PRIORITY_CHOICES = [
        ('low', 'Basse'),
        ('medium', 'Moyenne'),
        ('high', 'Haute'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks', verbose_name='Projet')
    title = models.CharField(max_length=200, verbose_name='Titre')
    description = models.TextField(blank=True, verbose_name='Description')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='todo', verbose_name='Statut')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium', verbose_name='Priorité')
    assigned_to = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='assigned_tasks',
        verbose_name='Assigné à'
    )
    due_date = models.DateField(null=True, blank=True, verbose_name='Date d\'échéance')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'tasks'
        ordering = ['-created_at']
        verbose_name = 'Tâche'
        verbose_name_plural = 'Tâches'
    
    def __str__(self):
        return self.title