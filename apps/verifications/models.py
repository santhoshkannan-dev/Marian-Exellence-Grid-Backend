"""
Compatibility adapter for apps.verifications.models blueprint imports.
Re-exports authoritative models from users.models.
"""

from users.models import VerificationLog

__all__ = [
    'VerificationLog',
]
