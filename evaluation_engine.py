"""
evaluation_engine.py
====================
Authoritative Round 3 Evaluator Window Marking Engine & Verification Controller.

Implements the specification for:
- Category 1: Formula-Driven dynamic calculation (Academics pass bonus + grade breakdown)
- Categories 2-11: Subcategory Lookup Table (Predefined default_marks, verified for integrity)
- Category 12: Manual Input (Evaluator explicit entry with non-negative validation)
"""

from decimal import Decimal
from django.core.exceptions import ValidationError


def calculate_pass_bonus(pass_percentage: float) -> Decimal:
    """Calculates pass bonus tier for Category 1 Academics."""
    if pass_percentage > 90.0:
        return Decimal("5.00")
    elif pass_percentage > 80.0:
        return Decimal("4.00")
    elif pass_percentage > 70.0:
        return Decimal("3.00")
    elif pass_percentage > 60.0:
        return Decimal("2.00")
    elif pass_percentage > 50.0:
        return Decimal("1.00")
    return Decimal("0.00")


def calculate_academics_marks(metadata: dict) -> Decimal:
    """
    Formula: (N_>=90% * 5) + (N_80-90% * 4) + (N_70-80% * 3) + PassBonus - N_Fail
    """
    meta = dict(metadata or {})
    if 'grades' in meta and isinstance(meta['grades'], dict):
        g = meta['grades']
        if 'count_90_above' not in meta:
            meta['count_90_above'] = g.get('count_90_above', g.get('count90Above', g.get('90_above', g.get('S', g.get('s_grade_count', 0)))))
        if 'count_80_90' not in meta:
            meta['count_80_90'] = g.get('count_80_90', g.get('count80to90', g.get('80_90', g.get('APlus', g.get('a_plus_grade_count', 0)))))
        if 'count_70_80' not in meta:
            meta['count_70_80'] = g.get('count_70_80', g.get('count70to80', g.get('70_80', g.get('A', g.get('a_grade_count', 0)))))
        if 'count_fail' not in meta:
            meta['count_fail'] = g.get('count_fail', g.get('failCount', g.get('failed_count', g.get('Fail', 0))))
        if 'pass_percentage' not in meta:
            tot = int(meta.get('totalStudents', meta.get('total_students', 0)) or 0)
            if tot > 0:
                fails = int(meta.get('count_fail', 0) or 0)
                meta['pass_percentage'] = round(((tot - fails) / float(tot)) * 100.0, 2)

    c_90 = Decimal(str(meta.get('count_90_above', 0)))
    c_80 = Decimal(str(meta.get('count_80_90', 0)))
    c_70 = Decimal(str(meta.get('count_70_80', 0)))
    c_fail = Decimal(str(meta.get('count_fail', 0)))
    pass_pct = float(meta.get('pass_percentage', 0.0))

    bonus = calculate_pass_bonus(pass_pct)
    calculated = (c_90 * 5) + (c_80 * 4) + (c_70 * 3) + bonus - c_fail
    return max(Decimal("0.00"), calculated)


def resolve_evaluator_marks(submission, evaluator_manual_input: Decimal = None) -> Decimal:
    """
    Resolves final marks to credit during Round 3 Evaluator Approval.
    """
    cat_id = getattr(submission, 'category_id', None)

    # If cat_id is not 1 or 12 directly, check category object code if attached
    if cat_id not in (1, 12):
        cat_obj = getattr(submission, 'category', None)
        if cat_obj:
            code = (getattr(cat_obj, 'code', '') or '').strip().lower()
            name = (getattr(cat_obj, 'name', '') or getattr(cat_obj, 'category', '') or '').strip().lower()
            if code == 'cat-academics' or 'academic' in name:
                cat_id = 1
            elif code == 'cat-career-advancement' or 'career' in name:
                cat_id = 12

    # 1. Category 1: Academics (Formula)
    if cat_id == 1:
        meta = getattr(submission, 'metadata', None)
        if meta is None:
            meta = getattr(submission, 'submission_metadata', None) or getattr(submission, 'evidence', None) or {}
        return calculate_academics_marks(meta)

    # 2. Category 12: Career Advancement (Manual Entry)
    if cat_id == 12:
        if evaluator_manual_input is None:
            raise ValidationError("Category 12 requires a valid manual mark input.")
        try:
            val = Decimal(str(evaluator_manual_input))
        except Exception:
            raise ValidationError("Category 12 requires a valid manual mark input.")
        if val < Decimal("0.00"):
            raise ValidationError("Category 12 requires a valid manual mark input.")
        return val

    # 3. Categories 2-11: Subcategory Lookup Table
    subcat = getattr(submission, 'subcategory', None)
    if subcat:
        return Decimal(str(subcat.default_marks))

    # If subcategory attribute is not populated, attempt DB lookup if subcategory_id present
    if getattr(submission, 'subcategory_id', None):
        try:
            from users.models import SubCategory
            subcat = SubCategory.objects.filter(id=submission.subcategory_id).first()
            if subcat:
                return Decimal(str(subcat.default_marks))
        except Exception:
            pass

    # Fallback for non-master legacy test categories with explicit manual input
    if evaluator_manual_input is not None:
        try:
            val = Decimal(str(evaluator_manual_input))
            if val >= Decimal("0.00"):
                return val
        except Exception:
            pass

    raise ValidationError("Invalid submission: Subcategory required for calculation.")
