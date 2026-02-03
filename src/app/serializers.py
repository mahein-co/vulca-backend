from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework.exceptions import AuthenticationFailed as DRFAuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from app.models import CustomUser

# U S E R   S E R I A L I Z E R
class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField(read_only=True)
    is_admin = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = CustomUser
        fields = ["id", "username", "email" , "full_name", "is_admin", "profile_picture", "is_active", "role"]
    

    def get_is_admin(self, obj):
        # User is admin ONLY if they have role='admin'
        # We don't check is_superuser because we use role-based access control
        return obj.role == 'admin'
        
    def get_full_name(self, obj):
        if obj.name:
            return obj.name
        full_name = f"{obj.first_name} {obj.last_name}".strip()
        return full_name if full_name else obj.username
        
# U S E R  S E R I A L I Z E R  W I T H  T O K E N
class UserSerializerWithToken(UserSerializer):
    token = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = CustomUser
        fields = ["id", "username", "email" , "full_name", "is_admin", "profile_picture", "token", "role"]
     
    def get_token(self, obj):
        token = AccessToken.for_user(obj)
        return str(token)

# R E G I S T E R    U S E R S
class RegisterUserSerializer(serializers.ModelSerializer):
    password2 = serializers.CharField(style={'input_type': 'password'}, write_only=True)
    token = serializers.SerializerMethodField(read_only=True)
    is_admin = serializers.SerializerMethodField(read_only=True)

    
    class Meta:
        model = CustomUser
        fields = ["email", "username", "name", "role",
                  "is_admin", "profile_picture", "token", "password", "password2"]
        extra_kwargs = {
            'password': {'write_only': True}
        }
    
    def get_is_admin(self, obj):
        return obj.role == 'admin'
    
    def get_token(self, obj):
        token = AccessToken.for_user(obj)
        return str(token)

    def save(self):
        user = CustomUser(
            email=self.validated_data['email'],
            username=self.validated_data['username'],
            name=self.validated_data.get('name', ''),
            role=self.validated_data.get('role', 'user')
        )
        password = self.validated_data['password']
        password2 = self.validated_data['password2']

        if len(password) < 8:
            raise serializers.ValidationError({"error": True, "message": "Ce mot de passe est trop court. Il doit contenir au minimum 8 caractères."})

        if password != password2:
            raise serializers.ValidationError({"error": True, 'message': 'Les mots de passe doivent être identiques.'})
        user.set_password(password)
        user.save()
        return user
    

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        token["email"] = user.email
        token["username"] = user.username
        return token 

    def validate(self, attrs):
        try:
            data = super().validate(attrs)
        except (AuthenticationFailed, DRFAuthenticationFailed):
            # Check if user exists to distinguish error
            User = CustomUser
            email = attrs.get("email")
            password = attrs.get("password")

            user = User.objects.filter(email=email).first()
            if user:
                if not user.is_active:
                    raise AuthenticationFailed({"detail": "Votre accès est bloqué, veuillez contacter l'admin.", "code": "account_blocked"})
                # User exists but auth failed -> Incorrect password
                raise AuthenticationFailed({"detail": "Mot de passe incorrect.", "code": "authorization_failed"})
            else:
                # User does not exist
                raise AuthenticationFailed({"detail": "Aucun compte associé à cet email.", "code": "authorization_failed"})

        if not self.user.is_verified:
            raise AuthenticationFailed("Votre email n'est pas vérifié.")

        obj_serializer = UserSerializerWithToken(self.user).data

        for k, v in obj_serializer.items():
            data[k] = v
        return data
