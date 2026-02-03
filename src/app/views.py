import json
from django.utils import timezone

from django.contrib.auth import authenticate
from django.http import JsonResponse
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model

from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status 
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny

from app.serializers import RegisterUserSerializer, UserSerializer, MyTokenObtainPairSerializer
from app.models import CustomUser, OtpToken
from app.utils import get_verification_token

from app.tasks import send_registration_email

User = get_user_model()

# 2. U S E R S
# 2.1. users
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_users(request):
    if request.method == "GET":
        users = CustomUser.objects.all()

        obj_serializers = UserSerializer(users, many=True)

        context = {"users": obj_serializers.data,}
        return Response(context, status=status.HTTP_200_OK)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_user(request, pk):
    try:
        user = CustomUser.objects.get(pk=pk)
    except CustomUser.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    
    # Check admin quota if changing role to admin
    new_role = data.get("role", user.role)
    if new_role == "admin" and user.role != "admin":
        admin_count = CustomUser.objects.filter(role="admin").count()
        if admin_count >= 3:
            return Response({"error": "Le quota de 3 administrateurs est atteint"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Update fields
    user.name = data.get("name", user.name)
    user.email = data.get("email", user.email)
    user.role = data.get("role", user.role)
    
    # Handle status update
    if "is_active" in data:
        user.is_active = data["is_active"]
        
    user.save()
    serializer = UserSerializer(user, many=False)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_user(request, pk):
    try:
        user = CustomUser.objects.get(pk=pk)
    except CustomUser.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        
    if user.id == request.user.id:
         return Response({"error": "Vous ne pouvez pas supprimer votre propre compte."}, status=status.HTTP_400_BAD_REQUEST)

    user.delete()
    return Response({"message": "Utilisateur supprimé avec succès"}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_admin_count(request):
    """
    Returns the current number of admin users
    """
    admin_count = CustomUser.objects.filter(role="admin").count()
    return Response({"admin_count": admin_count}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_user_by_admin(request):
    """
    Create a new user by admin - auto-activated without OTP
    """
    data = request.data
    
    # Validate required fields
    if not all([data.get("username"), data.get("name"), data.get("email"), data.get("role")]):
        return Response({"error": "Nom d'utilisateur, nom, email et rôle sont requis"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Check admin quota if role is admin
    if data.get("role") == "admin":
        admin_count = CustomUser.objects.filter(role="admin").count()
        if admin_count >= 3:
            return Response({"error": "Le quota de 3 administrateurs est atteint"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Get username from request
    username = data.get("username")
    email = data.get("email")
    
    # Check if username already exists
    if CustomUser.objects.filter(username=username).exists():
        return Response({"error": "Ce nom d'utilisateur existe déjà"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Check if email already exists
    if CustomUser.objects.filter(email=email).exists():
        return Response({"error": "Un utilisateur avec cet email existe déjà"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Create user with auto-activation
        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            name=data.get("name"),
            role=data.get("role"),
            is_active=True,
            is_verified=True,
            password=CustomUser.objects.make_random_password()  # Generate random password
        )
        
        serializer = UserSerializer(user, many=False)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)



# 2.2. get user profile
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_user_profile(request):
    if request.method == "GET":
        user = request.user
        obj_serializer = UserSerializer(user, many=False)
        context= {"user": obj_serializer.data}
        return Response(context)

class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    def put(self, request):
        user = request.user
        data = request.data
        user.name = data.get("name", user.name)
        user.save()
        serializer = UserSerializer(user)
        return Response(serializer.data)


# 3. A C C O U N T S 
# 3.1. authentication
class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        # Appel à la logique de SimpleJWT
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        access_token = data.get("access")
        refresh_token = data.get("refresh")

        # JSON: USER INFO 
        user_info = {
            "id": data.get("id"),
            "username": data.get("username"),
            "email": data.get("email"),
            "full_name": data.get("full_name"),
            "profile_picture": data.get("profile_picture"),
            "is_admin": data.get("is_admin"),
        }

        response = Response(user_info, status=status.HTTP_200_OK)

        # Cookie settings based on environment
        cookie_params = {
            "httponly": True,
            "secure": not settings.DEBUG,
            "samesite": "Lax" if settings.DEBUG else "None",
            "path": "/",
        }

        # Stockage des tokens en cookies HttpOnly (invisible côté JS)
        response.set_cookie(key="access", value=access_token, **cookie_params)
        response.set_cookie(key="refresh", value=refresh_token, **cookie_params)

        return response


# 3.2. register 
@api_view(["POST"])
@permission_classes([AllowAny])
def register_user(request):
    """
    Enregistre un nouvel utilisateur, crée un OTP et envoie un e-mail de vérification.
    """
    serializer = RegisterUserSerializer(data=request.data)
    data = {}

    if serializer.is_valid():
        # Sauvegarde de l'utilisateur
        user = serializer.save()
        user.is_active = False  # L'utilisateur doit d'abord vérifier son e-mail
        user.save()

        # Création d'un OTP valide 5 minutes
        otp_token = OtpToken.objects.create(
            user=user,
            otp_expires_at=timezone.now() + timezone.timedelta(minutes=5)
        )
        # Force refresh to ensure default value is available
        otp_token.refresh_from_db()

        # Détermination du lien frontend
        # frontend_url = getattr(settings, "FRONTEND_URL", "https://www.lexaiq.com")
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        verification_url = f"{frontend_url}/auth/verify-email/{user.username}"

        # Contenu de l’e-mail
        subject = "Vérification du compte email"
        message = f"""
Bienvenue  {user.username},

Pour terminer votre inscription, veuillez valider votre adresse email.

Voici votre code de vérification : {otp_token.otp_code}

Ce code expire dans 5 minutes.

Sinon, cliquez sur le lien ci-dessous :
{verification_url}

Merci,

        """
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "contact@mahein.co")
        recipient = [user.email]

        # Envoi du mail
        try:
            # send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient)
            msg = EmailMultiAlternatives(subject, message, settings.DEFAULT_FROM_EMAIL, recipient)
            # Ajout d'une version HTML (optionnel mais recommandé)
            html_content = f"""
            <h3>Bienvenue {user.username}</h3>
            <p>Pour terminer votre inscription, veuillez valider votre adresse email.</p>
            <p>Voici votre code de vérification : <strong>{otp_token.otp_code}</strong></p>
            <p>Ce code expire dans 5 minutes.</p>
            <p>Sinon, cliquez sur le lien ci-dessous :<br>
            <a href="{verification_url}">{verification_url}</a></p>
            <p>Merci</p>
            """
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            
            # send_registration_email.delay(subject, message, sender, recipient)
            data["message"] = "User registered successfully! OTP sent to your email."
            data["error"] = False
            data["id"] = user.pk
            data["email"] = user.email
            # data["full_name"] = user.get_full_name()

        except Exception as e:
            data["message"] = f"User created but failed to send email: {str(e)}"
            data["error"] = True

        return Response({"data": data}, status=201)

    else:
        # Erreur de validation du serializer
        errors = serializer.errors
        data["error"] = True
        first_error = [v[0] for v in errors.values()][0] if errors else "Invalid data"
        data["message"] = first_error

        return Response({"data": data}, status=400)

# REFRESH TOKEN
class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        # Lire le cookie HttpOnly refresh
        refresh = request.COOKIES.get("refresh")
        if refresh is None:
            return Response({"error": "No refresh token"}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = self.get_serializer(data={"refresh": refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response({"error": "Invalid refresh token"}, status=status.HTTP_401_UNAUTHORIZED)

        # Récupérer le nouvel access token
        access_token = serializer.validated_data["access"]

        # Créer une réponse vide ou avec un message
        response = Response({"message": "Access token refreshed"}, status=status.HTTP_200_OK)

        # Cookie settings based on environment
        cookie_params = {
            "httponly": True,
            "secure": not settings.DEBUG,
            "samesite": "Lax" if settings.DEBUG else "None",
            "path": "/",
        }

        # Réinjecter le nouvel access token dans un cookie HttpOnly
        response.set_cookie(key="access", value=access_token, **cookie_params)

        return response


# # EMAIL VERIFICATION
# class VerifyEmailAPIView(APIView):
#     """
#     Vérification du code OTP et activation du compte
#     """
#     def post(self, request):
#         username = request.data.get("username")
#         otp_code = request.data.get("otp_code")

#         try:
#             user = User.objects.get(username=username)
#             otp_token = OtpToken.objects.filter(user=user).last()

#             if not otp_token:
#                 return Response({"error": "Aucun OTP trouvé pour cet utilisateur."}, status=404)

#             if otp_token.otp_code != otp_code:
#                 return Response({"error": "OTP invalide."}, status=400)

#             if otp_token.otp_expires_at < timezone.now():
#                 return Response({"error": "OTP expiré. Veuillez en redemander un."}, status=400)

#             user.is_active = True
#             user.save()
#             return Response({"message": "Email vérifié avec succès. Compte activé."}, status=200)

#         except User.DoesNotExist:
#             return Response({"error": "Utilisateur introuvable."}, status=404)

# RESENT OTP
class ResendOtpAPIView(APIView):
    """
    Renvoyer un OTP par e-mail
    """
    def post(self, request):
        email = request.data.get("email")

        try:
            user = User.objects.get(email=email)
            otp = OtpToken.objects.create(
                user=user,
                otp_expires_at=timezone.now() + timezone.timedelta(minutes=5)
            )

            frontend_url = getattr(settings, "FRONTEND_URL", "https://www.lexaiq.com")
            verification_url = f"{frontend_url}/auth/verify-email/{user.username}"

            sender = settings.EMAIL_HOST_USER
            recipient = user.email

            subject = "Renvoi de votre code OTP"
            message = (
                f"Bienvenue {user.username},\n\n"
                f"Voici votre nouveau code de vérification : {otp.otp_code}\n"
                f"Il expire dans 5 minutes.\n\n"
                f"Cliquez sur le lien ci-dessous pour vérifier votre e-mail :\n{verification_url}\n\n"
                f"Merci, \n"
                
            )

            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
            # send_registration_email.delay(subject, message, sender, recipient)
            return Response({"message": "Un nouvel OTP a été envoyé à votre adresse e-mail."}, status=200)

        except User.DoesNotExist:
            return Response({"error": "Aucun compte associé à cet e-mail."}, status=404)

@api_view(["POST"])
@permission_classes([AllowAny])
def verify_otp(request):
    """
    Vérifie le OTP et active l'utilisateur.
    """
    username = request.data.get("username")
    otp_code = request.data.get("otp_code")
    data = {}

    if not username or not otp_code:
        data["error"] = True
        data["message"] = "Le nom d'utilisateur et le code OTP sont requis."
        return Response({"data": data}, status=400)

    User = get_user_model()
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        data["error"] = True
        data["message"] = "L'utilisateur n'existe pas."
        return Response({"data": data}, status=404)

    # Récupérer le dernier OTP
    otp_token = OtpToken.objects.filter(user=user).order_by('-otp_expires_at').first()
    if not otp_token:
        data["error"] = True
        data["message"] = "Code OTP introuvable. Veuillez en demander un nouveau."
        return Response({"data": data}, status=404)

    # Vérifier l'OTP
    if otp_token.otp_code != otp_code:
        data["error"] = True
        data["message"] = "Code OTP invalide."
        return Response({"data": data}, status=400)

    # Vérifier l'expiration
    if otp_token.otp_expires_at < timezone.now():
        data["error"] = True
        data["message"] = "Le code OTP a expiré. Veuillez en demander un nouveau."
        return Response({"data": data}, status=400)

    # Activer l'utilisateur
    user.is_verified = True
    user.is_active = True
    user.save()

    data["error"] = False
    data["message"] = "Compte vérifié avec succès ! Vous pouvez maintenant vous connecter."
    return Response({"data": data}, status=200)


# RESET PASSWORD
@api_view(["POST"])
@permission_classes([AllowAny])
def request_password_reset(request):
    """
    Étape 1 : L'utilisateur entre son email -> on envoie un OTP pour réinitialiser le mot de passe.
    """
    email = request.data.get("email")
    if not email:
        return Response({"error": True, "message": "L'email est requis."}, status=400)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": True, "message": "Aucun compte associé à cet email."}, status=404)

    # Crée un OTP valide 10 minutes
    otp_token = OtpToken.objects.create(
        user=user,
        otp_expires_at=timezone.now() + timezone.timedelta(minutes=10)
    )

    subject = "Réinitialisation de votre mot de passe "
    
    message = f"""
    Bonjour {user.username},

    Voici votre code OTP pour réinitialiser votre mot de passe : {otp_token.otp_code}

    Ce code expirera dans 10 minutes.

    Si vous n'avez pas demandé cette action, ignorez simplement cet e-mail.

   
    """

    try:
        # send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
        msg = EmailMultiAlternatives(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
        
        # HTML Content
        html_content = f"""
        <h3>Bonjour {user.username},</h3>
        <p>Voici votre code OTP pour réinitialiser votre mot de passe :</p>
        <h2 style="color: #4F46E5;">{otp_token.otp_code}</h2>
        <p>Ce code expirera dans 10 minutes.</p>
        <p>Si vous n'avez pas demandé cette action, ignorez simplement cet e-mail.</p>
        """
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        return Response({
            "error": False,
            "message": "Un code OTP a été envoyé à votre adresse e-mail."
        }, status=200)
    except Exception as e:
        print(f"ERROR_SENDING_EMAIL: {str(e)}")
        return Response({
            "error": True,
            "message": f"Erreur lors de l’envoi de l’e-mail : {str(e)}"
        }, status=500)



@api_view(["POST"])
@permission_classes([AllowAny])
def verify_reset_otp(request):
    """
    Étape 2 : L'utilisateur entre son email + otp pour vérification avant de changer le mot de passe.
    """
    email = request.data.get("email")
    otp_code = request.data.get("otp_code")

    if not email or not otp_code:
        return Response({"error": True, "message": "Email et OTP requis."}, status=400)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": True, "message": "Utilisateur introuvable."}, status=404)

    otp_token = OtpToken.objects.filter(user=user).order_by('-otp_expires_at').first()
    if not otp_token:
        return Response({"error": True, "message": "Aucun OTP trouvé."}, status=404)

    if otp_token.otp_code != otp_code:
        return Response({"error": True, "message": "OTP invalide."}, status=400)

    if otp_token.otp_expires_at < timezone.now():
        return Response({"error": True, "message": "OTP expiré. Veuillez en redemander un."}, status=400)

    return Response({
        "error": False,
        "message": "OTP vérifié avec succès. Vous pouvez maintenant réinitialiser votre mot de passe."
    }, status=200)


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password(request):
    """
    Étape 3 : L'utilisateur définit son nouveau mot de passe après vérification OTP.
    """
    email = request.data.get("email")
    new_password = request.data.get("new_password")
    confirm_password = request.data.get("confirm_password")

    if not all([email, new_password, confirm_password]):
        return Response({"error": True, "message": "Tous les champs sont requis."}, status=400)

    if new_password != confirm_password:
        return Response({"error": True, "message": "Les mots de passe ne correspondent pas."}, status=400)

    if len(new_password) < 8:
        return Response({"error": True, "message": "Ce mot de passe est trop court. Il doit contenir au minimum 8 caractères."}, status=400)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": True, "message": "Utilisateur introuvable."}, status=404)

    user.set_password(new_password)
    user.save()

    # Nettoyage des anciens OTP
    OtpToken.objects.filter(user=user).delete()

    return Response({
        "error": False,
        "message": "Mot de passe réinitialisé avec succès. Vous pouvez maintenant vous connecter."
    }, status=200)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    user = request.user
    data = request.data
    
    old_password = data.get("old_password")
    new_password = data.get("new_password")
    confirm_password = data.get("confirm_password")
    
    if not user.check_password(old_password):
        return Response({"error": True, "message": "L'ancien mot de passe est incorrect."}, status=400)
        
    if new_password != confirm_password:
        return Response({"error": True, "message": "Les nouveaux mots de passe ne correspondent pas."}, status=400)
        
    if len(new_password) < 8:
        return Response({"error": True, "message": "Ce mot de passe est trop court. Il doit contenir au minimum 8 caractères."}, status=400)
        
    user.set_password(new_password)
    user.save()
    
    return Response({"error": False, "message": "Mot de passe modifié avec succès."}, status=200)
