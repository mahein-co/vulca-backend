from rest_framework import permissions


class IsApprovedUser(permissions.BasePermission):
    """Seuls les utilisateurs approuvés peuvent accéder"""
    message = "Votre compte est en attente d'approbation par un administrateur."
    
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.can_access_system


class IsAdminUser(permissions.BasePermission):
    """Seuls les admins peuvent accéder"""
    message = "Vous devez être administrateur pour effectuer cette action."
    
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_admin
