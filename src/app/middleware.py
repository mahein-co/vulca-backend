class DisableCSRFMiddleware:
    """Désactive le CSRF pour toutes les routes /app/"""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/app/') or request.path.startswith('/users/'):
            setattr(request, '_dont_enforce_csrf_checks', True)
        return self.get_response(request)