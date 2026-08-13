import base64, hashlib, hmac, json, time
from functools import lru_cache
import boto3
from botocore.client import Config
from .settings import settings


@lru_cache(maxsize=4)
def _client(endpoint_url: str | None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def client():
    return _client(settings.s3_endpoint_url)


def public_client():
    return _client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def presign_get(bucket: str, object_key: str, ttl: int | None = None) -> str:
    return public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key},
        ExpiresIn=ttl or settings.media_token_ttl_seconds,
    )


def ensure_bucket():
    s3=client()
    try: s3.head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        try: s3.create_bucket(Bucket=settings.s3_bucket)
        except Exception:
            s3.head_bucket(Bucket=settings.s3_bucket)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value+"="*((4-len(value)%4)%4))


def sign_media_token(media_id: str,workspace_id: str,ttl: int|None=None) -> str:
    payload={"mid":media_id,"wid":workspace_id,"exp":int(time.time())+(ttl or settings.media_token_ttl_seconds)}
    raw=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
    sig=hmac.new(settings.media_signing_secret.encode(),raw,hashlib.sha256).digest()
    return _b64(raw)+"."+_b64(sig)


def verify_media_token(token: str,media_id: str) -> dict:
    try:
        a,b=token.split(".",1); raw=_unb64(a); sig=_unb64(b)
        expected=hmac.new(settings.media_signing_secret.encode(),raw,hashlib.sha256).digest()
        if not hmac.compare_digest(sig,expected): raise ValueError("signature")
        payload=json.loads(raw)
        if payload.get("mid")!=media_id or int(payload.get("exp",0))<int(time.time()): raise ValueError("expired")
        return payload
    except Exception:
        raise ValueError("invalid media token")
