"""
Compatibility adapter for apps.submissions.models blueprint imports.
Re-exports authoritative models from users.models.
"""

from users.models import (
    Submission,
    Category,
    SubCategory,
    SubCategory as Subcategory,
    ClassLedger,
)

__all__ = [
    'Submission',
    'Category',
    'SubCategory',
    'Subcategory',
    'ClassLedger',
]
