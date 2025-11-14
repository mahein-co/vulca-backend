from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.shortcuts import render
from .models import User, Project, Task, ProjectAssignment
from .serializers import UserSerializer, ProjectSerializer, TaskSerializer, ProjectAssignmentSerializer
from .permissions import IsApprovedUser, IsAdminUser


@method_decorator(csrf_exempt, name='dispatch')
class SignupAPI(APIView):
    """API : Inscription utilisateur"""
    permission_classes = [AllowAny]
    
    def post(self, request):
        email = request.data.get('email', '').strip()
        password = request.data.get('password', '').strip()
        username = request.data.get('username', '').strip()
        name = request.data.get('name', '').strip()
        role = request.data.get('role', 'user').strip()

        if not email or not password or not username:
            return Response({"error": "Email, username et password sont obligatoires"}, status=400)

        if role not in ['admin', 'user']:
            role = 'user'

        try:
            if User.objects.filter(email=email).exists():
                return Response({"error": "Cet email existe déjà"}, status=400)
            
            if User.objects.filter(username=username).exists():
                return Response({"error": "Ce nom d'utilisateur existe déjà"}, status=400)
            
            user = User.objects.create_user(
                email=email,
                username=username,
                password=password,
                name=name,
                role=role
            )

            serializer = UserSerializer(user)
            return Response({
                "message": "Inscription réussie. En attente d'approbation.",
                "user": serializer.data
            }, status=201)

        except Exception as e:
            return Response({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class LoginAPI(APIView):
    """API : Connexion utilisateur"""
    permission_classes = [AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({"error": "Email et mot de passe requis"}, status=400)

        try:
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return Response({"error": "Identifiants incorrects"}, status=401)
            
            if not user.check_password(password):
                return Response({"error": "Identifiants incorrects"}, status=401)
            
            # Vérifier si approuvé
            if not user.can_access_system:
                return Response({
                    "error": "Votre compte est en attente d'approbation.",
                    "is_approved": False,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "username": user.username,
                        "name": user.name
                    }
                }, status=403)
            
            # Générer tokens JWT
            refresh = RefreshToken.for_user(user)
            serializer = UserSerializer(user)
            
            return Response({
                "message": "Connexion réussie",
                "user": serializer.data,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh)
                }
            }, status=200)

        except Exception as e:
            return Response({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class LogoutAPI(APIView):
    """API : Déconnexion"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        return Response({"message": "Déconnexion réussie"}, status=200)


class MeAPI(APIView):
    """API : Mon profil"""
    permission_classes = [IsAuthenticated, IsApprovedUser]
    
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class CheckApprovalAPI(APIView):
    """API : Vérifier le statut d'approbation"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        user = request.user
        return Response({
            "is_approved": user.is_approved,
            "can_access": user.can_access_system,
            "approved_at": user.approved_at,
            "message": "Approuvé" if user.can_access_system else "En attente d'approbation"
        })


class UserViewSet(viewsets.ModelViewSet):
    """Gestion des utilisateurs (admin)"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Liste des utilisateurs en attente"""
        pending_users = User.objects.filter(is_approved=False, role='user')
        serializer = self.get_serializer(pending_users, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approuver un utilisateur"""
        user = self.get_object()
        user.is_approved = True
        user.approved_by = request.user
        user.approved_at = timezone.now()
        user.save()
        
        serializer = self.get_serializer(user)
        return Response({
            "message": f"{user.username} a été approuvé",
            "user": serializer.data
        })
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Rejeter un utilisateur"""
        user = self.get_object()
        user.is_approved = False
        user.approved_by = None
        user.approved_at = None
        user.save()
        
        return Response({
            "message": f"Approbation retirée pour {user.username}"
        })


class ProjectViewSet(viewsets.ModelViewSet):
    """Gestion des projets"""
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_admin:
            return Project.objects.all()
        return Project.objects.filter(assigned_users=user)
    
    def perform_create(self, serializer):
        if not self.request.user.is_admin:
            raise PermissionError("Seuls les admins peuvent créer des projets")
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def assign_user(self, request, pk=None):
        """Assigner un user au projet"""
        if not request.user.is_admin:
            return Response({"error": "Action admin uniquement"}, status=403)
        
        project = self.get_object()
        user_id = request.data.get('user_id')
        can_edit = request.data.get('can_edit', False)
        can_delete = request.data.get('can_delete', False)
        
        try:
            user = User.objects.get(id=user_id)
            
            if ProjectAssignment.objects.filter(project=project, user=user).exists():
                return Response({"error": "Déjà assigné"}, status=400)
            
            assignment = ProjectAssignment.objects.create(
                project=project,
                user=user,
                assigned_by=request.user,
                can_edit=can_edit,
                can_delete=can_delete
            )
            
            serializer = ProjectAssignmentSerializer(assignment)
            return Response({
                "message": f"{user.name} assigné au projet",
                "assignment": serializer.data
            })
        
        except User.DoesNotExist:
            return Response({"error": "Utilisateur introuvable"}, status=404)
    
    @action(detail=True, methods=['post'])
    def remove_user(self, request, pk=None):
        """Retirer un user du projet"""
        if not request.user.is_admin:
            return Response({"error": "Action admin uniquement"}, status=403)
        
        project = self.get_object()
        user_id = request.data.get('user_id')
        
        try:
            assignment = ProjectAssignment.objects.get(project=project, user_id=user_id)
            user_name = assignment.user.name
            assignment.delete()
            
            return Response({"message": f"{user_name} retiré du projet"})
        
        except ProjectAssignment.DoesNotExist:
            return Response({"error": "Assignation introuvable"}, status=404)
    
    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        """Membres du projet"""
        project = self.get_object()
        assignments = ProjectAssignment.objects.filter(project=project)
        serializer = ProjectAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)


class TaskViewSet(viewsets.ModelViewSet):
    """Gestion des tâches"""
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_admin:
            return Task.objects.all()
        return Task.objects.filter(project__assigned_users=user)
    
    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        """Changer le statut"""
        task = self.get_object()
        new_status = request.data.get('status')
        
        if new_status in dict(Task.STATUS_CHOICES):
            task.status = new_status
            task.save()
            return Response({"message": "Statut mis à jour", "status": task.status})
        
        return Response({"error": "Statut invalide"}, status=400)


# Vue pour la page "En attente d'approbation"
def pending_approval_view(request):
    """Page d'attente d'approbation"""
    return render(request, 'app/pending_approval.html')