"""Simulate a WeChat Work (企业微信) callback event for local testing.

Usage:
  # Send a text message
  python scripts/simulate_wecom_event.py "帮我查一下2024年人机协同的论文"

  # Print the XML payload without sending (for debugging)
  python scripts/simulate_wecom_event.py "hello" --print-only

  # Custom URL
  python scripts/simulate_wecom_event.py "hello" --url http://127.0.0.1:8080/integrations/wecom/events

Environment variables (for signature verification when WECOM_TOKEN is set):
  WECOM_TOKEN           - callback token (also set WECOM_ENCODING_AES_KEY for encryption)
  WECOM_ENCODING_AES_KEY - 43-char Base64 AES key for encrypting the payload
  WECOM_CORP_ID          - corp_id for encrypted payload verification
"""

import argparse
import base64
import hashlib
import os
import struct
import time
import urllib.request
import xml.etree.ElementTree as ET


def _wecom_aes_key(key_b64: str) -> bytes:
    return base64.b64decode(key_b64 + "=")


def _wecom_encrypt(plaintext: str, encoding_aes_key: str, corp_id: str) -> str:
    """AES-256-CBC encrypt with PKCS#7 padding (WeChat Work format)."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _wecom_aes_key(encoding_aes_key)
    iv = key[:16]
    random_prefix = os.urandom(16)
    msg_bytes = plaintext.encode("utf-8")
    corp_bytes = corp_id.encode("utf-8")
    length_prefix = struct.pack("!I", len(msg_bytes))
    plain = random_prefix + length_prefix + msg_bytes + corp_bytes
    pad_len = 32 - (len(plain) % 32)
    plain += bytes([pad_len] * pad_len)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).encryptor()
    return base64.b64encode(encryptor.update(plain) + encryptor.finalize()).decode("ascii")


def build_xml(text: str, encrypt: bool = False) -> tuple[str, dict]:
    """Build a WeChat Work text message XML payload.

    Returns (xml_body, query_params) where query_params contains
    msg_signature, timestamp, nonce, and optionally echostr for URL verification.
    """
    timestamp = str(int(time.time()))
    nonce = "test_nonce_" + str(int(time.time() * 1000) % 100000)
    msg_id = "test_msg_" + str(int(time.time() * 1000))

    inner_xml = (
        "<xml>"
        f"<ToUserName><![CDATA[test_corp]]></ToUserName>"
        f"<FromUserName><![CDATA[test_user]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        f"<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content>"
        f"<MsgId>{msg_id}</MsgId>"
        f"<AgentID>1000001</AgentID>"
        "</xml>"
    )

    token = os.environ.get("WECOM_TOKEN", "")
    encoding_aes_key = os.environ.get("WECOM_ENCODING_AES_KEY", "")
    corp_id = os.environ.get("WECOM_CORP_ID", "")

    if encrypt and encoding_aes_key and corp_id:
        encrypted = _wecom_encrypt(inner_xml, encoding_aes_key, corp_id)
        outer_xml = (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<ToUserName><![CDATA[test_corp]]></ToUserName>"
            f"<AgentID>1000001</AgentID>"
            "</xml>"
        )
        if token:
            params_list = sorted([token, timestamp, nonce, encrypted])
            msg_signature = hashlib.sha1("".join(params_list).encode("utf-8")).hexdigest()
        else:
            msg_signature = ""
        return outer_xml, {
            "msg_signature": msg_signature,
            "timestamp": timestamp,
            "nonce": nonce,
        }

    # Plaintext (no encryption)
    return inner_xml, {}


def build_url_verification() -> tuple[str, dict]:
    """Build URL verification request parameters (echostr)."""
    timestamp = str(int(time.time()))
    nonce = "test_nonce_" + str(int(time.time() * 1000) % 100000)
    echostr = "test_echo_" + str(int(time.time() * 1000))

    token = os.environ.get("WECOM_TOKEN", "")
    encoding_aes_key = os.environ.get("WECOM_ENCODING_AES_KEY", "")
    corp_id = os.environ.get("WECOM_CORP_ID", "")

    if encoding_aes_key and corp_id:
        encrypted_echo = _wecom_encrypt(echostr, encoding_aes_key, corp_id)
        if token:
            params_list = sorted([token, timestamp, nonce, encrypted_echo])
            msg_signature = hashlib.sha1("".join(params_list).encode("utf-8")).hexdigest()
        else:
            msg_signature = ""
        return "", {
            "msg_signature": msg_signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": encrypted_echo,
        }

    # Plaintext echostr (no encryption)
    return "", {
        "msg_signature": "",
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": echostr,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Send a simulated WeChat Work callback event to the gateway."
    )
    parser.add_argument(
        "text", nargs="?",
        default="帮我查一下2024年以来人机协同的论文",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8080/integrations/wecom/events")
    parser.add_argument("--encrypt", action="store_true",
                        help="Encrypt the payload using WECOM_ENCODING_AES_KEY")
    parser.add_argument("--url-verify", action="store_true",
                        help="Simulate URL verification (echostr) instead of a message")
    parser.add_argument("--print-only", action="store_true",
                        help="Print the request details without sending")
    args = parser.parse_args()

    if args.url_verify:
        body, params = build_url_verification()
        query = "&".join(f"{k}={v}" for k, v in params.items() if v)
        full_url = f"{args.url}?{query}" if query else args.url
        if args.print_only:
            print(f"URL: {full_url}")
            print("(URL verification — no body)")
            return
        req = urllib.request.Request(full_url, data=b"", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"Status: {resp.status}")
                print(f"Body: {resp.read().decode('utf-8')}")
        except urllib.error.HTTPError as e:
            print(f"Status: {e.code}")
            print(f"Body: {e.read().decode('utf-8')}")
        return

    body, params = build_xml(args.text, encrypt=args.encrypt)
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    full_url = f"{args.url}?{query}" if query else args.url
    data = body.encode("utf-8")

    if args.print_only:
        print(f"URL: {full_url}")
        print(f"Body:\n{body}")
        return

    req = urllib.request.Request(
        full_url, data=data,
        headers={"Content-Type": "application/xml; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Status: {resp.status}")
            print(f"Body: {resp.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"Status: {e.code}")
        print(f"Body: {e.read().decode('utf-8')}")


if __name__ == "__main__":
    main()
