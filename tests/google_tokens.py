"""Offline Google-shaped tokens signed with a test-only RSA key."""
import json
import time
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt

CLIENT_ID = "123456-test.apps.googleusercontent.com"
KEY_ID = "test-google-key"


@lru_cache(maxsize=1)
def signing_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return crypt.RSASigner.from_string(private, key_id=KEY_ID), public.decode()


def google_token(subject="account-1", omit=(), **overrides):
    now = int(time.time())
    payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID,
               "sub": subject, "iat": now - 10, "exp": now + 3600}
    payload.update(overrides)
    for field in omit:
        payload.pop(field, None)
    return jwt.encode(signing_material()[0], payload).decode()


def certificate_response(url, **kwargs):
    # Verifier must always use Google's fixed endpoint, not token-supplied URLs.
    assert url == "https://www.googleapis.com/oauth2/v1/certs"
    class Response:
        status = 200
        data = json.dumps({KEY_ID: signing_material()[1]}).encode()
    return Response()
