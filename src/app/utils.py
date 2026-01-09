from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from rest_framework_simplejwt.tokens import RefreshToken

signer = TimestampSigner()

def generate_email_token(email: str) -> str:
    return signer.sign(email)

def verify_email_token(token: str, max_age=60*60*24) -> str | None:
    """
    Vérifie le token. Retourne l'email si valide, sinon None.
    max_age = 24h par défaut
    """
    try:
        return signer.unsign(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None



def get_verification_token(user):
    token = RefreshToken.for_user(user)
    return str(token.access_token)
