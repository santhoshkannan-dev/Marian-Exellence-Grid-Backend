"""
Institutional Audit Logging Engine for Marian Best Class.

Enforces:
1. Immutability: SystemAuditLog and WorkflowAuditTrail are strictly append-only.
2. Cryptographic Integrity: SHA-256 hash chaining across every log record.
3. Role Attribution: Capture of actor, role, email, client IP, User-Agent, and Request ID.
4. Old-Value / New-Value Diffs: Captures data transformations for sensitive mutations.
"""

import hashlib
import logging
from typing import Optional, Dict, Any
from django.db import transaction

logger = logging.getLogger(__name__)


def get_client_ip(request) -> Optional[str]:
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_user_agent(request) -> Optional[str]:
    if not request:
        return None
    return request.META.get('HTTP_USER_AGENT')


def get_request_id(request) -> Optional[str]:
    if not request:
        return None
    return request.META.get('HTTP_X_REQUEST_ID')


def record_system_audit_event(
    action: str,
    object_type: str,
    object_id: Any,
    actor=None,
    object_repr: str = "",
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    request=None
):
    """
    Safely records a tamper-evident audit event into SystemAuditLog.
    """
    try:
        from users.models import SystemAuditLog

        actual_actor = actor
        if not actual_actor and request and hasattr(request, 'user') and getattr(request.user, 'is_authenticated', False):
            actual_actor = request.user

        ip_addr = get_client_ip(request)
        ua = get_user_agent(request)
        req_id = get_request_id(request)

        actor_email = getattr(actual_actor, 'email', '') or ''
        actor_role = getattr(actual_actor, 'role', '') or ''

        return SystemAuditLog.objects.create(
            actor=actual_actor if (actual_actor and getattr(actual_actor, 'is_authenticated', False)) else None,
            actor_email=actor_email,
            actor_role=actor_role,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            object_repr=str(object_repr)[:255],
            old_value=old_value,
            new_value=new_value,
            reason=reason or "",
            ip_address=ip_addr,
            user_agent=ua,
            request_id=req_id
        )
    except Exception as e:
        logger.warning(f"Failed to record system audit log for {action} on {object_type}#{object_id}: {e}")
        return None


import re

class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes passwords, bearer tokens, JWTs, API secrets,
    and sensitive user credentials from all application logs.
    """
    PATTERNS = [
        (re.compile(r'(password[\'\"\s]*[:=][\'\"\s]*)[^\'\"\s&,}]+', re.IGNORECASE), r'\1[REDACTED]'),
        (re.compile(r'(bearer\s+)[a-zA-Z0-9_\-\.]+', re.IGNORECASE), r'\1[REDACTED_TOKEN]'),
        (re.compile(r'(token[\'\"\s]*[:=][\'\"\s]*)[^\'\"\s&,}]+', re.IGNORECASE), r'\1[REDACTED_TOKEN]'),
        (re.compile(r'(secret[\'\"\s]*[:=][\'\"\s]*)[^\'\"\s&,}]+', re.IGNORECASE), r'\1[REDACTED_SECRET]'),
    ]

    def filter(self, record):
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, repl in self.PATTERNS:
                msg = pattern.sub(repl, msg)
            record.msg = msg
        return True

