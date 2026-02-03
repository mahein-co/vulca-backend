from django.http import JsonResponse
from compta.models import ProjectAccess


class ProjectMiddleware:
    """
    Middleware qui injecte automatiquement project_id dans request.
    Vérifie l'accès utilisateur au projet selon les règles:
    - ADMIN: accès à tous les projets
    - Non-admin: accès uniquement si ProjectAccess.status = 'approved'
    """
    
    # Routes exclues de la vérification project_id
    EXCLUDED_PATHS = [
        '/users/login/',
        '/users/register/',
        '/users/verify-otp/',
        '/users/forgot-password/',
        '/users/reset-password/',
        '/users/token/refresh/',  # ✅ Exclure le rafraîchissement
        '/admin/',
        '/api/projects/',  # Liste des projets accessible sans project_id
        '/api/project-access/',  # Demandes d'accès
        '/static/',
        '/media/',
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Ignorer les routes exclues
        if any(request.path.startswith(path) for path in self.EXCLUDED_PATHS):
            return self.get_response(request)
        
        # Ignorer si utilisateur non authentifié (géré par IsAuthenticated)
        if not request.user.is_authenticated:
            # ⚡ [BUGFIX] : JWTAuthenticationFromCookie s'exécute dans DRF, après le middleware.
            # On tente une authentification manuelle pour que le middleware connaisse l'utilisateur.
            from app.authentication import JWTAuthenticationFromCookie
            try:
                user_auth = JWTAuthenticationFromCookie().authenticate(request)
                if user_auth:
                    request.user = user_auth[0]
            except Exception:
                pass

        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Récupérer project_id depuis header ou query params
        project_id = request.headers.get('X-Project-ID') or request.GET.get('project_id')
        
        # Admin/Superuser: accès à tous les projets
        if request.user.is_superuser or getattr(request.user, 'role', None) == 'admin':
            if project_id:
                request.project_id = project_id
            else:
                # Si pas de project_id fourni par un admin, on ne peut pas injecter request.project_id
                # La vue devra gérer le cas ou filtrer par défaut (ex: dernier projet)
                pass
            return self.get_response(request)
        
        # Vérifier que project_id est fourni pour les utilisateurs non-admin
        if not project_id:
            return JsonResponse({
                'error': 'project_id requis',
                'detail': 'Veuillez fournir X-Project-ID dans le header ou project_id en paramètre'
            }, status=400)
        
        # Vérifier l'accès utilisateur au projet (status = 'approved')
        has_access = ProjectAccess.objects.filter(
            user=request.user,
            project_id=project_id,
            status='approved'
        ).exists()
        
        if not has_access:
            return JsonResponse({
                'error': 'Accès refusé',
                'detail': f'Vous n\'avez pas accès au projet {project_id}. Veuillez soumettre une demande d\'accès.'
            }, status=403)
        
        # Injecter project_id dans request
        request.project_id = project_id
        
        return self.get_response(request)
