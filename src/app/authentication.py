from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed

class JWTAuthenticationFromCookie(JWTAuthentication):
    def authenticate(self, request):
        # DEBUGGING: Print all cookies to see what we are receiving
        print(f"DEBUG AUTH: Cookies received: {request.COOKIES.keys()}")
        
        header = self.get_header(request)
        if header is None:
            raw_token = request.COOKIES.get("access")
            if not raw_token:
                print("DEBUG AUTH: No 'access' cookie found.")
                # Try refresh token just in case
                if request.COOKIES.get("refresh"):
                    print("DEBUG AUTH: 'refresh' cookie found but no 'access' cookie.")
                return None

            try:
                validated_token = self.get_validated_token(raw_token)
                # print(f"DEBUG AUTH: Access cookie found and validated for user: {self.get_user(validated_token)}")
                return self.get_user(validated_token), validated_token
            except Exception as e:
                print(f"DEBUG AUTH: Token validation failed: {str(e)}")
                return None

        return super().authenticate(request)




