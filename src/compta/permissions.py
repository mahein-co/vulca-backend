from rest_framework import permissions
from .models import ProjectAccess

class HasProjectAccess(permissions.BasePermission):
    """
    Permission personnalisée pour vérifier si l'utilisateur a accès au projet spécifié.
    L'ID du projet est attendu dans le header 'X-Project-ID' ou en paramètre de requête 'project_id'.
    Les superusers et admins ont accès à tout.
    """

    def has_permission(self, request, view):
        # Admin access
        if not request.user or not request.user.is_authenticated:
            return False
            
        if request.user.is_superuser or getattr(request.user, 'role', None) == 'admin':
            return True

        # Récupérer l'ID du projet
        project_id = request.headers.get('X-Project-ID') or request.query_params.get('project_id')

        # Si pas de projet spécifié, laisser passer (la vue devra filtrer ou rejeter si obligatoire)
        # Certaines vues globales (liste des projets) ne nécessitent pas de projet cible
        if not project_id:
            return True

        # Vérifier l'accès
        return ProjectAccess.objects.filter(
            user=request.user,
            project_id=project_id,
            status='approved'
        ).exists()

    def has_object_permission(self, request, view, obj):
        # Admin access
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser or getattr(request.user, 'role', None) == 'admin':
            return True

        # Si l'objet a un attribut 'project', vérifier l'accès à ce projet
        if hasattr(obj, 'project'):
            if not obj.project: # Si l'objet n'a pas de projet assigné (cas rares/migration)
                return True
                
            return ProjectAccess.objects.filter(
                user=request.user,
                project=obj.project,
                status='approved'
            ).exists()
            
        return True
