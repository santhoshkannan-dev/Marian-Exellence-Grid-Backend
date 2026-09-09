"""
File Security & Validation Module for Marian Best Class.

Enforces:
1. Strict file size limits (e.g. 10MB for evidence, 5MB for images).
2. Extension whitelisting and executable blocking.
3. Content inspection via magic bytes sniffing (prevents renamed executables).
4. Filename sanitization and UUID-based obfuscation (prevents path traversal and predictable URLs).
5. Canonical path verification ensuring files stay strictly within PRIVATE_MEDIA_ROOT.
"""

import os
import re
import uuid
import hashlib
from typing import Tuple, Optional, Set
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

# File size limits
MAX_EVIDENCE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024       # 5 MB

# Allowed extensions
ALLOWED_EVIDENCE_EXTENSIONS: Set[str] = {
    '.pdf', '.png', '.jpg', '.jpeg', '.webp',
    '.doc', '.docx', '.csv', '.txt'
}

ALLOWED_IMAGE_EXTENSIONS: Set[str] = {
    '.png', '.jpg', '.jpeg', '.webp'
}

# Explicitly forbidden dangerous executable extensions
BLOCKED_EXTENSIONS: Set[str] = {
    '.exe', '.bat', '.cmd', '.sh', '.bash', '.py', '.js', '.vbs',
    '.php', '.phtml', '.cgi', '.pl', '.jar', '.war', '.msi', '.dll',
    '.com', '.scr', '.ps1', '.app', '.hta'
}


def get_private_media_root() -> str:
    """Returns the absolute root directory for private evidence storage."""
    root = getattr(settings, 'PRIVATE_MEDIA_ROOT', None)
    if not root:
        root = os.path.join(settings.BASE_DIR, 'private_media')
    os.makedirs(root, exist_ok=True)
    return os.path.abspath(root)


def sniff_mime_type(header: bytes) -> str:
    """
    Inspects leading magic bytes to identify real file MIME type independently of extension.
    """
    if not header:
        return 'application/octet-stream'

    # 1. Executable detection (reject early)
    if header.startswith(b'MZ') or header.startswith(b'\x7fELF') or header.startswith(b'#!'):
        return 'application/x-executable'
    if header.startswith(b'\xca\xfe\xba\xbe'):  # Java class
        return 'application/x-java-applet'
    if header.lower().startswith(b'<!doctype html') or header.lower().startswith(b'<html') or header.lower().startswith(b'<script'):
        return 'text/html'
    if header.lower().startswith(b'<?php'):
        return 'application/x-php'

    # 2. Documents & Images
    if header.startswith(b'%PDF'):
        return 'application/pdf'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if header.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if header.startswith(b'RIFF') and len(header) >= 12 and header[8:12] == b'WEBP':
        return 'image/webp'
    if header.startswith(b'PK\x03\x04'):
        # ZIP-based container: could be DOCX
        return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    if header.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        # Legacy MS-CFBF container: DOC
        return 'application/msword'

    # 3. Plain text / CSV inspection: Must not contain binary nulls
    if b'\x00' not in header[:512]:
        try:
            header[:512].decode('utf-8')
            return 'text/plain'
        except UnicodeDecodeError:
            pass

    return 'application/octet-stream'


def sanitize_filename(filename: str) -> str:
    r"""
    Sanitizes an untrusted filename:
    1. Removes path traversal sequences (../, ..\, /, \).
    2. Strips control and non-alphanumeric characters.
    3. Prefixes a secure UUID hex to eliminate URL predictability.
    """
    if not filename:
        return f"{uuid.uuid4().hex}.dat"

    # Strip paths
    base = os.path.basename(filename)
    # Remove null bytes and traversal tokens
    base = base.replace('\x00', '').replace('..', '').strip()

    name, ext = os.path.splitext(base)
    ext = ext.lower().strip()

    # Clean name: keep alphanumeric, underscore, hyphen
    clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    if not clean_name:
        clean_name = "file"
    clean_name = clean_name[:40]  # truncate long names

    unique_token = uuid.uuid4().hex[:12]
    return f"{unique_token}_{clean_name}{ext}"


def validate_file_upload(
    file_obj,
    allowed_extensions: Set[str] = ALLOWED_EVIDENCE_EXTENSIONS,
    max_size_bytes: int = MAX_EVIDENCE_SIZE_BYTES
) -> Tuple[bool, str, str]:
    """
    Validates an uploaded file object.
    Returns: (is_valid: bool, error_message: str, detected_mime: str)
    """
    if not file_obj:
        return False, "No file provided.", ""

    # 1. Size check
    size = getattr(file_obj, 'size', None)
    if size is None:
        try:
            file_obj.seek(0, os.SEEK_END)
            size = file_obj.tell()
            file_obj.seek(0)
        except Exception:
            size = 0

    if size > max_size_bytes:
        max_mb = max_size_bytes / (1024 * 1024)
        return False, f"File size ({size / (1024 * 1024):.2f} MB) exceeds maximum allowed limit of {max_mb:.1f} MB.", ""

    if size == 0:
        return False, "File is empty (0 bytes).", ""

    # 2. Extension check
    orig_name = getattr(file_obj, 'name', '') or ''
    _, ext = os.path.splitext(orig_name)
    ext = ext.lower().strip()

    if ext in BLOCKED_EXTENSIONS:
        return False, f"Dangerous executable extension '{ext}' is strictly prohibited.", ""

    if ext not in allowed_extensions:
        allowed_list = ", ".join(sorted(allowed_extensions))
        return False, f"Unsupported file extension '{ext}'. Allowed extensions: {allowed_list}.", ""

    # 3. Magic bytes inspection
    try:
        header = file_obj.read(1024)
        file_obj.seek(0)
    except Exception as e:
        return False, f"Unable to read file content: {str(e)}", ""

    detected_mime = sniff_mime_type(header)

    # Reject executable payloads regardless of extension
    if detected_mime in ('application/x-executable', 'application/x-java-applet', 'application/x-php', 'text/html'):
        return False, "File content contains an executable, script, or HTML payload and was rejected.", detected_mime

    # Mismatched MIME check: If extension claims to be PDF or image, verify magic bytes
    if ext == '.pdf' and detected_mime != 'application/pdf':
        return False, f"File extension is '.pdf' but content does not match PDF format (detected {detected_mime}).", detected_mime

    if ext in ('.png', '.jpg', '.jpeg', '.webp') and not detected_mime.startswith('image/'):
        return False, f"File extension is image '{ext}' but content does not match an image format.", detected_mime

    return True, "", detected_mime


def save_private_evidence_file(file_obj, academic_year: str = 'general') -> Tuple[str, str, int, str]:
    """
    Securely saves an evidence file into PRIVATE_MEDIA_ROOT.
    Returns: (relative_path: str, sha256_hash: str, file_size: int, detected_mime: str)
    """
    is_valid, err, detected_mime = validate_file_upload(file_obj, ALLOWED_EVIDENCE_EXTENSIONS, MAX_EVIDENCE_SIZE_BYTES)
    if not is_valid:
        raise ValueError(err)

    clean_name = sanitize_filename(getattr(file_obj, 'name', 'evidence.dat'))
    safe_year = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(academic_year or 'general'))

    private_root = get_private_media_root()
    dest_dir = os.path.join(private_root, 'evidence', safe_year)
    os.makedirs(dest_dir, exist_ok=True)

    dest_path = os.path.abspath(os.path.join(dest_dir, clean_name))

    # Path traversal assertion: Ensure canonical path is strictly inside private_root
    common = os.path.commonpath([private_root, dest_path])
    if common != private_root:
        raise SecurityError("Path traversal attempt detected during evidence file storage.")

    hasher = hashlib.sha256()
    total_bytes = 0

    file_obj.seek(0)
    with open(dest_path, 'wb') as dest_file:
        for chunk in file_obj.chunks() if hasattr(file_obj, 'chunks') else [file_obj.read()]:
            hasher.update(chunk)
            dest_file.write(chunk)
            total_bytes += len(chunk)

    sha256_hash = hasher.hexdigest()
    relative_path = os.path.relpath(dest_path, private_root).replace('\\', '/')

    logger.info(f"Stored private evidence '{relative_path}' ({total_bytes} bytes, hash={sha256_hash[:12]}...)")
    return relative_path, sha256_hash, total_bytes, detected_mime


def resolve_safe_private_path(relative_path: str) -> Optional[str]:
    """
    Safely resolves a relative evidence path into an absolute file path.
    Guarantees no traversal outside PRIVATE_MEDIA_ROOT.
    Returns None if file does not exist or traversal detected.
    """
    if not relative_path or '..' in relative_path:
        return None

    private_root = get_private_media_root()
    # Normalize forward slashes
    norm_rel = relative_path.replace('/', os.sep).replace('\\', os.sep).lstrip(os.sep)
    abs_path = os.path.abspath(os.path.join(private_root, norm_rel))

    try:
        common = os.path.commonpath([private_root, abs_path])
        if common != private_root:
            logger.warning(f"Path traversal detected: {relative_path} -> {abs_path}")
            return None
    except ValueError:
        return None

    if not os.path.isfile(abs_path):
        return None

    return abs_path


class EvidenceSecurityManager:
    """
    Focused service for evidence and file validation, security, and storage.
    """
    validate_file_upload = staticmethod(validate_file_upload)
    save_private_evidence_file = staticmethod(save_private_evidence_file)
    resolve_safe_private_path = staticmethod(resolve_safe_private_path)
    sniff_mime_type = staticmethod(sniff_mime_type)
    get_private_media_root = staticmethod(get_private_media_root)
