"""
users/access_rules.py
=====================
Authoritative Subcategory-Level Permission & Mark Registry
----------------------------------------------------------
Single source of truth for:
  1. Category-level access classification (DQC_ONLY / ALL_STUDENTS / HYBRID)
  2. Subcategory-level DQC restrictions within Hybrid categories (8 & 12)
  3. Per-subcategory mark values (used to cross-check scoring_engine)
  4. Per-subcategory submission cycle limits

Open-question resolutions (approved 2026-09-11):
  - Participation (Individual) prizes → ALL_STUDENTS  (not DQC-only)
  - Cat 12 LinkedIn extras (1104/1105) → ALL_STUDENTS, not in scope of DQC rules
  - Cat 9 Programs Organised → NO cycle limit

Category number ↔ code mapping
  1  Academics                 cat-academics
  2  Online Courses            cat-online-courses
  3  Competitive Exams         cat-competitive-exams
  4  Internships               cat-internships
  5  Scholarships              cat-scholarships
  6  Research                  cat-research
  7  Startups                  cat-startups
  8  Prizes Won                cat-prizes
  9  Programs Organised        cat-programs-organized
  10 Leaderships               cat-leadership
  11 Social Responsibilities   cat-social-responsibility
  12 Career Advancement        cat-career-advancement
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1.  Category-level Access Classification
# ---------------------------------------------------------------------------

#: Categories where every CriteriaItem is DQC-only.
DQC_ONLY_CATEGORY_CODES: frozenset[str] = frozenset({
    "cat-academics",
    "cat-programs-organized",
    "cat-leadership",
    "cat-leaderships",          # alias used in some DB rows
    "cat-social-responsibility",
    "cat-social-responsibilities",
    "cat-documentation",        # legacy alias
})

#: Human-readable display names that also map to DQC-only (lowercase, used for
#: fallback matching when category.code is absent or inconsistent).
DQC_ONLY_CATEGORY_NAMES: frozenset[str] = frozenset({
    "academics",
    "programs organized",
    "leaderships",
    "leadership",
    "social responsibilities",
    "social responsibility",
    "documentation",
})

#: Categories where every CriteriaItem is open to all students.
ALL_STUDENTS_CATEGORY_CODES: frozenset[str] = frozenset({
    "cat-online-courses",
    "cat-competitive-exams",
    "cat-internships",
    "cat-scholarships",
    "cat-research",
    "cat-startups",
})

#: Hybrid categories — access is determined at the subcategory (CriteriaItem) level.
HYBRID_CATEGORY_CODES: frozenset[str] = frozenset({
    "cat-prizes",
    "cat-career-advancement",
})

# ---------------------------------------------------------------------------
# 2.  Subcategory DQC Restrictions — Cat 8 (Prizes Won)
# ---------------------------------------------------------------------------

#: Exact subcategory name fragments that flag a Prize submission as DQC-only.
#: Matching is case-insensitive substring check on the submitted subItem name
#: OR on the CriteriaItem.title.
#: NOTE: "(Group)" is DQC-only; "Participation (Individual)" is ALL_STUDENTS.
PRIZE_DQC_SUBCATEGORY_FRAGMENTS: Tuple[str, ...] = (
    "(group)",          # "1st Prize (Group)", "2nd Prize (Group)", etc.
    "(group).",         # defensive variant
)

#: Subcategory names that are explicitly ALL_STUDENTS for prizes (never DQC-only).
PRIZE_ALL_STUDENTS_SUBCATEGORY_FRAGMENTS: Tuple[str, ...] = (
    "(individual)",
    "participation (individual)",
)

# ---------------------------------------------------------------------------
# 3.  Subcategory DQC Restrictions — Cat 12 (Career Advancement)
# ---------------------------------------------------------------------------

#: Exact CriteriaItem titles (case-insensitive) that are DQC-only in Cat 12.
CAREER_DQC_SUBCATEGORY_TITLES: frozenset[str] = frozenset({
    "library - regular footfall (biometric / entry)",
    "library - academic & career books issued/read",
    "library - academic and career books issued/read",   # spelling variant
    "repository creation (drive / github / lms / website)",
})

#: Cat 12 items that are ALL_STUDENTS.
CAREER_ALL_STUDENTS_SUBCATEGORY_TITLES: frozenset[str] = frozenset({
    "linkedin - profile completion (active profile)",
})

# ---------------------------------------------------------------------------
# 4.  Authoritative Mark Table
#     Keyed by category_code → subcategory_title → marks (float)
#     Used both for access_rules validation and scoring_engine cross-checks.
# ---------------------------------------------------------------------------

CATEGORY_MARK_TABLE: dict[str, dict[str, float]] = {
    # Cat 1 — Academics (formula-based; listed for reference only)
    "cat-academics": {
        "sem result (end semester examination)": 0.0,   # computed by formula
    },

    # Cat 2 — Online Courses
    "cat-online-courses": {
        "swayam / nptel course": 5.0,
        "mooc course": 2.0,
    },

    # Cat 3 — Competitive Exams
    "cat-competitive-exams": {
        "jrf passed": 20.0,
        "net passed": 10.0,
        "any other relevant exam (ielts, pet, language specific, etc.)": 3.0,
        "participation in relevant exam (upsc / psc exams)": 1.0,
    },

    # Cat 4 — Internships
    "cat-internships": {
        "offline internship (min. 1 month)": 5.0,
        "online internship (min. 1 month)": 3.0,
    },

    # Cat 5 — Scholarships
    "cat-scholarships": {
        "international level scholarship": 20.0,
        "national level scholarship": 10.0,
        "state level scholarship": 5.0,
        "district level scholarship": 2.0,
    },

    # Cat 6 — Research
    "cat-research": {
        # Publications
        "scopus / web of science": 10.0,
        "conference proceeding / peer reviewed article": 5.0,
        # Paper Presentation
        "outside marian college": 5.0,
        "inside marian college": 3.0,
        # Patents
        "utility": 10.0,
        "design": 5.0,
        # Book Publications
        "book": 10.0,
        "book chapter": 5.0,
        "article": 2.0,
        # Funded Projects
        "international": 20.0,
        "national": 10.0,
        "state": 5.0,
        "any other": 3.0,
    },

    # Cat 7 — Startups
    "cat-startups": {
        "government-registered start-up": 10.0,
    },

    # Cat 8 — Prizes Won (Hybrid)
    "cat-prizes": {
        # From Marian College — ALL_STUDENTS
        "1st prize (individual)": 10.0,
        "2nd prize (individual)": 5.0,
        "3rd prize (individual)": 3.0,
        # From Marian College — DQC ONLY
        "1st prize (group)": 5.0,
        "2nd prize (group)": 3.0,
        "3rd prize (group)": 2.0,
        # Outside Marian College — ALL_STUDENTS
        "outside - 1st prize (individual)": 15.0,
        "outside - 2nd prize (individual)": 10.0,
        "outside - 3rd prize (individual)": 5.0,
        "participation (individual)": 3.0,
        # Outside Marian College — DQC ONLY
        "outside - 1st prize (group)": 10.0,
        "outside - 2nd prize (group)": 5.0,
        "outside - 3rd prize (group)": 3.0,
        "participation (group)": 2.0,
    },

    # Cat 9 — Programs Organised (DQC-only, no cycle limit)
    "cat-programs-organized": {
        "intercollegiate": 5.0,
        "intra - collegiate": 3.0,
        "intracollegiate": 3.0,
        "class magazine": 5.0,
    },

    # Cat 10 — Leaderships (DQC-only)
    "cat-leadership": {
        "mcsc executive body position": 5.0,
        "sahya executive body position": 5.0,
        "clubs & associations leadership position": 5.0,
        "innovative / sustainable suggestion": 5.0,
    },

    # Cat 11 — Social Responsibilities (DQC-only)
    "cat-social-responsibility": {
        "coordination of event (community action / outreach)": 5.0,
        "participation in event": 3.0,
        "news media coverage (excluding social media)": 3.0,
    },

    # Cat 12 — Career Advancement (Hybrid, manual eval)
    "cat-career-advancement": {
        # DQC-only
        "library - regular footfall (biometric / entry)": 0.0,   # manual eval
        "library - academic & career books issued/read": 0.0,
        "repository creation (drive / github / lms / website)": 0.0,
        # ALL_STUDENTS
        "linkedin - profile completion (active profile)": 0.0,
    },
}

# ---------------------------------------------------------------------------
# 5.  Submission Cycle Limits
#     Format: { (category_code, limit_key) -> max_count }
#     limit_key is a normalised lowercase string derived from the item title.
#     A value of None means no limit enforced.
# ---------------------------------------------------------------------------

#: Max-per-cycle rules keyed by a (category_code, limit_key) tuple.
#: limit_key = "" means the limit applies to the WHOLE category uniformly.
CYCLE_LIMITS: dict[tuple[str, str], int] = {
    # Cat 1 — Academics: max 1 per class per cycle (checked at category level)
    ("cat-academics", ""): 1,

    # Cat 2 — Online Courses
    ("cat-online-courses", "swayam"): 3,   # Swayam / NPTEL
    ("cat-online-courses", "nptel"): 3,
    ("cat-online-courses", "mooc"): 3,

    # Cat 3 — Competitive Exams
    ("cat-competitive-exams", "jrf"): 1,
    ("cat-competitive-exams", "net"): 1,
    ("cat-competitive-exams", "upsc"): 3,
    ("cat-competitive-exams", "psc"): 3,
}

# ---------------------------------------------------------------------------
# 6.  Core Validation Functions
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    """Normalise to lowercase stripped string."""
    return str(text or "").strip().lower()


def _is_dqc_only_category(cat_code: str, cat_name: str) -> bool:
    """Return True if the entire category is DQC-only (not hybrid)."""
    return (
        _norm(cat_code) in DQC_ONLY_CATEGORY_CODES
        or _norm(cat_name) in DQC_ONLY_CATEGORY_NAMES
    )


def _is_dqc_prize_subcategory(sub_item_name: str, item_title: str, prize_scope: str) -> bool:
    """
    Return True iff a Prizes Won (Cat 8) submission is DQC-only.
    Rule:
      - Any group prize is DQC-only.
      - Any participation entry in Outside Marian College is DQC-only.
      - Individual prizes (1st, 2nd, 3rd) are ALL_STUDENTS.
    """
    combined = _norm(sub_item_name) + " " + _norm(item_title) + " " + _norm(prize_scope)
    # Group keyword makes it DQC-only
    if "group" in combined or "(group)" in combined:
        return True
    # Participation in Outside Marian College is DQC-only
    if "participation" in combined and "outside" in combined:
        return True
    return False


def _is_dqc_career_subcategory(sub_item_name: str, item_title: str) -> bool:
    """
    Return True iff a Career Advancement (Cat 12) submission is DQC-only.
    Library Footfall, Library Books, Repository Creation → DQC-only.
    LinkedIn Profile Completion → ALL_STUDENTS.

    NOTE: Uses exact-title matching only — no broad keyword fallback,
    to prevent false positives where proof URLs or descriptions contain
    words like 'library' or 'repository'.
    """
    check_item = _norm(item_title)
    check_sub = _norm(sub_item_name)

    # Explicit allowlist — never DQC-restricted
    if "linkedin" in check_item or "linkedin" in check_sub:
        return False

    # Exact DQC title match on the item title
    for title in CAREER_DQC_SUBCATEGORY_TITLES:
        if title == check_item or title in check_item:
            return True

    # Exact DQC title match on the submitted subItem name (if present)
    if check_sub:
        for title in CAREER_DQC_SUBCATEGORY_TITLES:
            if title == check_sub or title in check_sub:
                return True

    return False


def validate_subcategory_access(user, criteria_item, evidence: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Authoritative subcategory-level access check.

    Returns:
        (True, "")                   — access is allowed
        (False, "<error message>")   — access is denied; error message is human-readable

    Logic:
      0. Trust CriteriaItem.access_level from DB (set by migration 0030) as primary gate.
         If item is explicitly 'all_students', skip heuristic keyword checks entirely.
      1. Staff / admin / superuser always pass.
      2. DQC-only categories: reject non-DQC students.
      3. Hybrid Cat 8 (Prizes): reject non-DQC for group/participation(group) subcategories.
      4. Hybrid Cat 12 (Career): reject non-DQC for library/repository subcategories.
      5. All-students categories: always allow.
    """
    from users.services.user_service import UserService

    if not user or not getattr(user, "is_authenticated", False):
        return False, "Authentication required."

    user_role = getattr(user, "role", None)

    # Staff / admin always bypass
    if user_role in ("admin", "faculty", "evaluation") or getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True, ""

    # Only apply student rules below
    if user_role != "student":
        return True, ""

    if not criteria_item:
        return True, ""  # Can't determine; let other validators catch it

    cat = getattr(criteria_item, "category", None)
    cat_code = _norm(getattr(cat, "code", "") or "")
    cat_name = _norm(getattr(cat, "category", "") or "")
    item_title = _norm(getattr(criteria_item, "title", "") or "")

    # Resolve evidence dict
    ev = evidence if isinstance(evidence, dict) else {}
    sub_item_name = _norm(
        ev.get("subItem") or ev.get("prizesSubItem") or ev.get("researchSubItem") or ""
    )
    prize_scope = _norm(ev.get("prizeScope", "") or "")

    # --- Rule 1: Wholly DQC-only category (Academics, Documentation, Programs Organized) ---
    if _is_dqc_only_category(cat_code, cat_name):
        is_dqc = UserService.is_user_dqc_rep(user)
        if not is_dqc:
            display = getattr(cat, "category", cat_name.title()) or "this category"
            return False, (
                f"Access to '{display}' is restricted to DQC members only (DQC Student Representatives only). "
                "Normal students can only submit individual student activities."
            )
        return True, ""

    # --- Rule 2: Hybrid Cat 8 — Prizes ---
    if "prize" in cat_code or "prize" in cat_name or cat_code == "cat-prizes":
        if _is_dqc_prize_subcategory(sub_item_name, item_title, prize_scope):
            is_dqc = UserService.is_user_dqc_rep(user)
            if not is_dqc:
                return False, (
                    "Group prize submissions are restricted to DQC members only (DQC Student Representatives only). "
                    "Normal students may submit Individual prize or Participation (Individual) entries."
                )

    # --- Rule 3: Hybrid Cat 12 — Career Advancement ---
    if "career" in cat_code or "career" in cat_name or cat_code == "cat-career-advancement":
        if _is_dqc_career_subcategory(sub_item_name, item_title):
            is_dqc = UserService.is_user_dqc_rep(user)
            if not is_dqc:
                return False, (
                    "This Career Advancement item (Library/Repository) is restricted to DQC members only (DQC Student Representatives only). "
                    "Normal students can submit 'LinkedIn - Profile Completion (Active Profile)'."
                )

    # --- Rule 4: Explicit DB access_level gate ---
    item_raw_level = (getattr(criteria_item, "access_level", "") or "").strip()
    cat_raw_level = (getattr(cat, "access_level", "") or "").strip() if cat else ""
    effective_level = item_raw_level or cat_raw_level
    if effective_level in ("dqc_only", "student_rep_only"):
        is_dqc = UserService.is_user_dqc_rep(user)
        if not is_dqc:
            display = getattr(cat, "category", "this category") or "this category"
            return False, (
                f"Access to '{display} — {criteria_item.title}' is restricted to "
                "DQC members only (DQC Student Representatives only)."
            )
        return True, ""

    return True, ""


def get_submission_cycle_limit(criteria_item, evidence: Optional[dict] = None) -> Tuple[Optional[int], str]:
    """
    Return (max_submissions_per_cycle, limit_key_label) for the given criteria item.

    Returns (None, "") if no cycle limit applies.
    The limit_key_label is a human-readable description used in error messages.

    Decisions:
      - Academics (Cat 1): 1 per cycle (whole category)
      - Online Courses (Cat 2): 3 per Swayam/NPTEL; 3 per MOOC
      - Competitive Exams (Cat 3): 1 for JRF; 1 for NET; 3 for UPSC/PSC
      - Programs Organised (Cat 9): NO limit
      - All others: no limit
    """
    if not criteria_item:
        return None, ""

    cat = getattr(criteria_item, "category", None)
    cat_code = _norm(getattr(cat, "code", "") or "")
    cat_name = _norm(getattr(cat, "category", "") or "")
    item_title = _norm(getattr(criteria_item, "title", "") or "")

    ev = evidence if isinstance(evidence, dict) else {}
    sub_item_name = _norm(
        ev.get("subItem") or ev.get("prizesSubItem") or ev.get("researchSubItem") or ""
    )

    # --- Cat 1: Academics ---
    if cat_code in ("cat-academics", "academics") or cat_name == "academics":
        return 1, "Academics"

    # --- Cat 2: Online Courses ---
    if cat_code == "cat-online-courses" or "online course" in cat_name:
        combined = item_title + " " + sub_item_name
        if "swayam" in combined or "nptel" in combined:
            return 3, "Swayam / NPTEL Course"
        if "mooc" in combined:
            return 3, "MOOC Course"
        # Generic fallback for other online course items
        return 3, "Online Course"

    # --- Cat 3: Competitive Exams ---
    if cat_code == "cat-competitive-exams" or "competitive" in cat_name or "exam" in cat_name:
        combined = item_title + " " + sub_item_name
        if "jrf" in combined:
            return 1, "JRF Passed"
        if "net" in combined and "jrf" not in combined:
            return 1, "NET Passed"
        if "upsc" in combined or "psc" in combined or "participation in relevant exam" in combined:
            return 3, "UPSC / PSC Exam Participation"
        # Any Other Relevant Exam — no cap mentioned in spec
        return None, ""

    # No limit for all other categories
    return None, ""


def get_expected_access_level_for_item(criteria_item) -> str:
    """
    Returns the expected access_level string for a CriteriaItem based on
    the authoritative access-control matrix.

    Used by the audit management command and the data migration.

    Returns:
        'dqc_only'    — item should only be accessible to DQC reps
        'all_students' — item is open to all students
    """
    if not criteria_item:
        return "all_students"

    cat = getattr(criteria_item, "category", None)
    cat_code = _norm(getattr(cat, "code", "") or "")
    item_title = _norm(getattr(criteria_item, "title", "") or "")

    # Wholly DQC categories
    if cat_code in DQC_ONLY_CATEGORY_CODES:
        return "dqc_only"

    # Cat 8: Prizes — group subcategories are DQC-only
    if cat_code == "cat-prizes":
        if "(group)" in item_title and "participation (individual)" not in item_title:
            return "dqc_only"
        if "participation (group)" in item_title:
            return "dqc_only"
        return "all_students"

    # Cat 12: Career Advancement — library/repository items are DQC-only
    if cat_code == "cat-career-advancement":
        for dqc_title in CAREER_DQC_SUBCATEGORY_TITLES:
            if dqc_title in item_title:
                return "dqc_only"
        return "all_students"

    return "all_students"
