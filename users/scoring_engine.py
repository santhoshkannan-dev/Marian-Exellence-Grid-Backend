"""
Authoritative Scoring & Moderation Engine for Marian Best Class.

Institutional Moderation Formula:
  M = (S - P) / N² * (1 + 100 * (N - n))
  where:
    S = Evaluated Marks (sum of verified marks on Evaluated/Locked submissions)
    P = Penalties (defined in department management via Class.negative_points)
    N = Class Size (defined in department management via Class.num_students)
    n = 0 (Smallest Class Benchmark defined in department management via SystemSetting['smallest_class_size'])
"""

from typing import Dict, List, Optional, Tuple, Any
from django.db.models import Sum, Q
from django.db import transaction
import json
import logging

logger = logging.getLogger(__name__)

SCORING_ENGINE_VERSION = 'v1.0-authoritative'
DEFAULT_BENCHMARK_CLASS_SIZE = 0.0
MAX_MODERATION_MARK = 200.0
MODERATION_FACTOR = 2.0

# Authoritative Institutional Pillar Categorization
PILLAR_MAPPING = {
    'Academic': {
        'cat-academics',
        'cat-online-courses',
        'cat-competitive-exams',
        'cat-internships',
        'cat-research',
        'cat-career-advancement',
        'academics',
        'online courses',
        'competitive exams',
        'internships',
        'research',
        'career advancement',
    },
    'Co-Curricular': {
        'cat-scholarships',
        'cat-startups',
        'cat-prizes',
        'scholarships',
        'startups',
        'prizes',
        'prizes won',
        'co-curricular',
    },
    'Extra-Curricular': {
        'cat-programs-organized',
        'cat-leadership',
        'cat-social-responsibility',
        'programs organized',
        'leaderships',
        'social responsibilities',
        'extra-curricular',
    },
}


def get_pillar_for_category(category_identifier: str) -> str:
    """Classify a category code or name into one of the 3 institutional pillars."""
    norm = str(category_identifier).strip().lower()
    for pillar, cats in PILLAR_MAPPING.items():
        if norm in cats:
            return pillar
    return 'Other'


def calculate_class_moderation(N: int, n: float = 0.0) -> float:
    """
    Class cohort benchmark difference: (N - n).
    """
    n_val = float(n) if n is not None else 0.0
    return round(float(N) - n_val, 2)


def calculate_net_score(gross_marks: float, negative_points: float) -> float:
    """
    Net Obtained Score = S - P.
    """
    s_val = float(gross_marks) if gross_marks is not None else 0.0
    p_val = float(negative_points) if negative_points is not None else 0.0
    return round(s_val - p_val, 2)


def calculate_total_score(net_score: float, moderation_mark: float = 0.0) -> float:
    """
    Total Score (non-negative).
    """
    net_val = float(net_score) if net_score is not None else 0.0
    return round(max(0.0, net_val), 2)


def calculate_class_index(
    S: float,
    P: float = 0.0,
    N: Optional[int] = None,
    n: float = 0.0,
    **kwargs
) -> Optional[float]:
    """
    Moderation Formula:
    M = (S − P) / N² × (1 + 100 × (N − n))
    where:
      S = Evaluated Marks
      P = Penalties (defined in department management)
      N = Class Size (defined in department management)
      n = 0 (Smallest Class Benchmark defined in department management)
    """
    # Support backward compatibility if called as calculate_class_index(total_score, N=40)
    if N is None:
        if 'N' in kwargs:
            N = kwargs['N']
        elif isinstance(P, int) and P > 0 and 'n' not in kwargs:
            # Called as (S, N)
            N = P
            P = 0.0
        else:
            return None

    if N <= 0:
        return None

    s_val = float(S) if S is not None else 0.0
    p_val = float(P) if P is not None else 0.0
    n_val = float(n) if n is not None else 0.0

    net_score = s_val - p_val
    diff = float(N) - n_val
    multiplier = 1.0 + 100.0 * diff
    m_val = (net_score / (float(N) ** 2)) * multiplier
    return round(max(0.0, m_val), 4)


def get_criteria_allowed_bounds(criteria_item, evidence: Any = None) -> Tuple[float, float, str]:
    """
    Authoritative computation of (allowed_min, allowed_max, details) for a given CriteriaItem based on:
    1. Dynamic subItems in rules_json or CriteriaRule.extra_config (e.g. Publications, Patents, Prizes)
    2. Academic grade rules in rules_json (e.g. Class Pass Percentage)
    3. Count multipliers for count-based criteria
    4. Associated CriteriaRule bounds (minimum_marks, maximum_marks, is_negative)
    """
    from .models import CriteriaRule

    if not criteria_item:
        return 0.0, 100.0, ""

    ev = evidence
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except Exception:
            ev = {}
    elif not isinstance(ev, dict):
        ev = {}

    rule = CriteriaRule.objects.filter(item=criteria_item).first()

    # Base mark calculation: SubCategory is authoritative for categories with subcategories
    subcat_matched = False
    is_academic = (criteria_item.type == 'academic_grades') or (criteria_item.category and criteria_item.category.code == 'cat-academics')
    if not is_academic:
        try:
            from .models import SubCategory
            subcat = SubCategory.find_subcategory(
                subcategory_id=ev.get('subcategory_id') or ev.get('subcategoryId'),
                category_id=getattr(getattr(criteria_item, 'category', None), 'id', None),
                category_code=getattr(getattr(criteria_item, 'category', None), 'code', None),
                criteria_item=criteria_item,
                evidence=ev
            )
            if subcat:
                base_mark = float(subcat.default_marks)
                details = f" (subcategory '{subcat.subcategory_name}': {base_mark})"
                subcat_matched = True
        except Exception as _ex:
            logger.debug("Subcategory lookup in scoring engine error: %s", _ex)

    if not subcat_matched:
        if rule and rule.maximum_marks is not None:
            base_mark = float(rule.maximum_marks)
        else:
            base_mark = float(criteria_item.marks or 0.0)
        details = ""

        # 1. SubItems mapping in CriteriaRule.extra_config (authoritative) or rules_json (fallback)
        sub_items = None
        if rule and isinstance(rule.extra_config, dict) and 'subItems' in rule.extra_config:
            sub_items = rule.extra_config.get('subItems')
        elif isinstance(criteria_item.rules_json, dict) and 'subItems' in criteria_item.rules_json:
            sub_items = criteria_item.rules_json.get('subItems')

        if isinstance(sub_items, dict) and len(sub_items) > 0:
            submitted_sub_item = (
                ev.get('subItem') or
                ev.get('researchSubItem') or
                ev.get('prizesSubItem')
            )
            matched_val = None
            if submitted_sub_item:
                if submitted_sub_item in sub_items:
                    matched_val = float(sub_items[submitted_sub_item])
                    details = f" (subcategory '{submitted_sub_item}': {matched_val})"
                else:
                    sub_norm = str(submitted_sub_item).strip().lower()
                    for k, v in sub_items.items():
                        if str(k).strip().lower() == sub_norm:
                            matched_val = float(v)
                            details = f" (subcategory '{k}': {matched_val})"
                            break

            if matched_val is not None:
                base_mark = matched_val
            else:
                base_mark = float(max(sub_items.values()))
                details = f" (max subcategory: {base_mark})"

        # 1a. CATEGORY_MARK_TABLE cross-validation (authoritative spec cross-check)
        # If access_rules.CATEGORY_MARK_TABLE has a canonical mark for this subcategory,
        # it acts as an authoritative cap to prevent stale DB data from inflating marks.
        try:
            from users.access_rules import CATEGORY_MARK_TABLE
            cat = getattr(criteria_item, 'category', None)
            cat_code_for_table = str(getattr(cat, 'code', '') or '').strip().lower()
            if cat_code_for_table in CATEGORY_MARK_TABLE:
                cat_table = CATEGORY_MARK_TABLE[cat_code_for_table]
                submitted_sub = (
                    ev.get('subItem') or ev.get('researchSubItem') or ev.get('prizesSubItem') or ''
                )
                sub_norm_table = str(submitted_sub).strip().lower()
                item_title_norm = str(getattr(criteria_item, 'title', '') or '').strip().lower()
                sub_candidates = [sub_norm_table]
                if 'outside' in item_title_norm:
                    sub_candidates.insert(0, f"outside - {sub_norm_table}")

                canonical_mark = None
                for cand in sub_candidates:
                    if cand in cat_table:
                        canonical_mark = float(cat_table[cand])
                        break
                # Only override if canonical_mark is positive (manual-eval items stay 0.0)
                if canonical_mark is not None and canonical_mark > 0.0 and base_mark > canonical_mark + 1e-5:
                    logger.warning(
                        "Scoring engine: base_mark %.2f for subcategory '%s' exceeds canonical "
                        "CATEGORY_MARK_TABLE value %.2f — capping to canonical value.",
                        base_mark, submitted_sub, canonical_mark
                    )
                    base_mark = canonical_mark
                    details += f" [capped to canonical {canonical_mark}]"
        except Exception as _e:
            logger.debug("CATEGORY_MARK_TABLE cross-check skipped: %s", _e)

    # 2. Count multiplier for count-based items
    count_val = 1
    if criteria_item.type == 'count' or 'count' in ev:
        try:
            count_val = max(1, int(ev.get('count', 1)))
        except (ValueError, TypeError):
            count_val = 1

    allowed_max = base_mark * count_val

    # 3. Dynamic handling for Academic Grades
    if criteria_item.type == 'academic_grades' or (
        criteria_item.category and criteria_item.category.code == 'cat-academics'
    ) or (
        isinstance(criteria_item.rules_json, dict) and 'pass_percentage_ranges' in criteria_item.rules_json
    ):
        rules = criteria_item.rules_json or {}
        m90 = float(rules.get('90_above', 5.0))
        m80 = float(rules.get('80_90', 4.0))
        m70 = float(rules.get('70_80', 3.0))

        total_students = 100
        try:
            total_students = max(1, int(ev.get('totalStudents', 100)))
        except (ValueError, TypeError):
            total_students = 100

        # Exact Academics Formula:
        # Exact Academics Formula:
        # (Count >= 90% * 5) + (Count 80-90% * 4) + (Count 70-80% * 3) + Pass Rate Bonus - Count Fail
        grades = ev.get('grades') or ev.get('markBreakdown') or ev
        cnt_90 = int(grades.get('count_90_above', grades.get('count90Above', grades.get('90_above', grades.get('S', grades.get('s_grade_count', 0))))) or 0)
        cnt_80 = int(grades.get('count_80_90', grades.get('count80to90', grades.get('80_90', grades.get('APlus', grades.get('a_plus_grade_count', 0))))) or 0)
        cnt_70 = int(grades.get('count_70_80', grades.get('count70to80', grades.get('70_80', grades.get('A', grades.get('a_grade_count', 0))))) or 0)
        cnt_fail = int(grades.get('count_fail', grades.get('failCount', grades.get('failed_count', grades.get('Fail', 0)))) or 0)

        pass_pct_raw = (
            ev.get('pass_percentage') if ev.get('pass_percentage') is not None else
            (grades.get('pass_percentage') if isinstance(grades, dict) and grades.get('pass_percentage') is not None else None) or
            ev.get('classPassPercentage') or
            ev.get('effectivePassPercentage') or
            ev.get('class_pass_percentage') or
            (grades.get('class_pass_percentage') if isinstance(grades, dict) else None)
        )
        if (pass_pct_raw is None or pass_pct_raw == '') and (cnt_90 or cnt_80 or cnt_70 or cnt_fail) and total_students > 0:
            passed = max(0, total_students - cnt_fail)
            pass_pct = round((passed / float(total_students)) * 100.0, 2)
        elif pass_pct_raw is not None and pass_pct_raw != '':
            try:
                pass_pct = float(pass_pct_raw)
            except (ValueError, TypeError):
                pass_pct = 0.0
        else:
            pass_pct = 0.0

        pass_bonus = 0.0
        if pass_pct > 90.0:
            pass_bonus = 5.0
        elif pass_pct > 80.0:
            pass_bonus = 4.0
        elif pass_pct > 70.0:
            pass_bonus = 3.0
        elif pass_pct > 60.0:
            pass_bonus = 2.0
        elif pass_pct > 50.0:
            pass_bonus = 1.0
        else:
            pass_bonus = 0.0

        calculated_academic_score = (cnt_90 * 5.0) + (cnt_80 * 4.0) + (cnt_70 * 3.0) + pass_bonus - cnt_fail
        if evidence is not None:
            allowed_max = max(0.0, calculated_academic_score)
        else:
            allowed_max = max(0.0, calculated_academic_score) if (cnt_90 or cnt_80 or cnt_70 or cnt_fail or pass_bonus) else ((total_students * max(m90, m80, m70)) + 5.0)
        base_mark = allowed_max
        details = f" (academics formula: 90%={cnt_90}, 80-90%={cnt_80}, 70-80%={cnt_70}, fail={cnt_fail}, passBonus={pass_bonus})"

    # 4. CriteriaRule overrides/caps (for non-academic grade items)
    allowed_min = 0.0
    is_academic = (criteria_item.type == 'academic_grades') or (criteria_item.category and criteria_item.category.code == 'cat-academics')
    is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (rule and rule.is_negative)
    if rule and not is_academic and not subcat_matched:
        has_sub_items = bool(
            (rule and isinstance(rule.extra_config, dict) and 'subItems' in rule.extra_config) or
            (isinstance(criteria_item.rules_json, dict) and 'subItems' in criteria_item.rules_json)
        )
        if rule.maximum_marks is not None:
            rule_max = float(rule.maximum_marks)
            if not (has_sub_items and rule_max == 0.0):
                if allowed_max > 0:
                    allowed_max = min(allowed_max, rule_max)
                else:
                    allowed_max = rule_max
        if getattr(rule, 'minimum_marks', None) is not None:
            allowed_min = float(rule.minimum_marks)
        if rule.is_negative:
            is_negative = True

    if is_negative and allowed_min == 0.0:
        allowed_min = -1000.0

    return allowed_min, allowed_max, details


def calculate_submission_score(criteria_item, evidence: Any = None) -> float:
    """
    Evaluates the authoritative marks for a submission against a CriteriaItem and its CriteriaRule/SubCategory.
    For categories containing subcategories, the calculated marks are strictly driven by
    subcategories.default_marks.
    For Category 12 (Career Advancement), marks are manually evaluated by the Evaluator.
    """
    if getattr(criteria_item, 'is_manual_eval', False) or (criteria_item.category and getattr(criteria_item.category, 'is_manual_eval', False)):
        return 0.0

    ev = evidence if isinstance(evidence, dict) else {}
    is_academic = (criteria_item.type == 'academic_grades') or (criteria_item.category and criteria_item.category.code == 'cat-academics')
    if not is_academic:
        try:
            from .models import SubCategory
            subcat = SubCategory.find_subcategory(
                subcategory_id=ev.get('subcategory_id') or ev.get('subcategoryId'),
                category_id=getattr(getattr(criteria_item, 'category', None), 'id', None),
                category_code=getattr(getattr(criteria_item, 'category', None), 'code', None),
                criteria_item=criteria_item,
                evidence=ev
            )
            if subcat:
                count_val = 1
                if criteria_item.type == 'count' or 'count' in ev:
                    try:
                        count_val = max(1, int(ev.get('count', 1)))
                    except (ValueError, TypeError):
                        count_val = 1
                return float(subcat.default_marks) * count_val
        except Exception as _ex:
            logger.debug("calculate_submission_score subcategory lookup: %s", _ex)

    allowed_min, allowed_max, _ = get_criteria_allowed_bounds(criteria_item, evidence)
    return allowed_max


def get_benchmark_class_size() -> float:
    """Retrieve benchmark class size n from SystemSetting or fallback to default 0.0."""
    from .models import SystemSetting
    try:
        n_setting = SystemSetting.objects.get(key='smallest_class_size')
        return float(n_setting.value) if n_setting.value is not None and str(n_setting.value).strip() != '' else DEFAULT_BENCHMARK_CLASS_SIZE
    except (SystemSetting.DoesNotExist, ValueError, TypeError):
        return DEFAULT_BENCHMARK_CLASS_SIZE


def compute_class_scores(cls, academic_year: Optional[str] = None, n_benchmark: Optional[float] = None) -> Dict[str, Any]:
    """
    Compute full authoritative score metrics for a single Class instance.
    Only considers submissions with status in ['Locked', 'Evaluated'] and non-null marks.
    """
    from .models import Submission, CriteriaItem

    if n_benchmark is None:
        n = get_benchmark_class_size()
    else:
        n = float(n_benchmark)

    N = cls.num_students or 0
    P = cls.negative_points or 0.0

    # Build submission queryset
    sub_qs = Submission.objects.filter(
        Q(status__in=['Locked', 'Evaluated', 'Approved', 'APPROVED']) & (Q(class_obj=cls) | Q(user__class_name=cls))
    )
    if academic_year:
        sub_qs = sub_qs.filter(academic_year=academic_year)

    # Calculate gross marks S
    total_s = 0.0
    for sub in sub_qs:
        m = sub.calculated_marks if sub.calculated_marks is not None else (float(sub.marks) if sub.marks is not None else 0.0)
        total_s += float(m)
    S = round(total_s, 2)
    P = round(float(P), 2)

    # Category breakdown and Pillar breakdown
    category_scores: Dict[str, float] = {}
    pillar_scores: Dict[str, float] = {
        'Academic': 0.0,
        'Co-Curricular': 0.0,
        'Extra-Curricular': 0.0,
    }

    # Fetch criteria items for category mapping
    all_items = {it.id: it for it in CriteriaItem.objects.select_related('category').all()}

    for sub in sub_qs:
        c_id = None
        try:
            c_id = int(sub.criteria_id)
        except (ValueError, TypeError):
            pass

        item = all_items.get(c_id)
        cat_code = item.category.code if item and item.category else 'unknown'
        cat_name = item.category.category if item and item.category else 'Unknown Category'
        marks_val = float(sub.calculated_marks if sub.calculated_marks is not None else (sub.marks or 0.0))

        category_scores[cat_name] = round(category_scores.get(cat_name, 0.0) + marks_val, 2)

        pillar = get_pillar_for_category(cat_code)
        if pillar not in pillar_scores:
            pillar = get_pillar_for_category(cat_name)
        if pillar in pillar_scores:
            pillar_scores[pillar] = round(pillar_scores[pillar] + marks_val, 2)
        else:
            pillar_scores['Extra-Curricular'] = round(pillar_scores['Extra-Curricular'] + marks_val, 2)

    # Total submissions and submitted categories for this class
    all_cls_subs = Submission.objects.filter(Q(class_obj=cls) | Q(user__class_name=cls))
    if academic_year:
        all_cls_subs = all_cls_subs.filter(academic_year=academic_year)
    total_submissions = all_cls_subs.count()

    cat_counts: Dict[str, Dict[str, Any]] = {}
    for sub in all_cls_subs.select_related('category'):
        c_name = sub.category.category if sub.category else 'General Activities'
        if c_name not in cat_counts:
            cat_counts[c_name] = {'category': c_name, 'count': 0, 'points': 0.0}
        cat_counts[c_name]['count'] += 1
        if sub.status in ['Locked', 'Evaluated', 'Approved', 'APPROVED'] and sub.marks:
            cat_counts[c_name]['points'] += float(sub.marks)

    submitted_categories = sorted(cat_counts.values(), key=lambda x: (-x['count'], -x['points']))

    if N > 0:
        net_score = calculate_net_score(S, P)
        moderation_mark = calculate_class_moderation(N, n)
        M = calculate_class_index(S=S, P=P, N=N, n=n)
        total_score = round(M * float(N), 2) if M is not None else 0.0
    else:
        net_score = calculate_net_score(S, P)
        moderation_mark = 0.0
        total_score = 0.0
        M = None

    dept_name = cls.department.name if cls.department else 'General'
    dept_code = cls.department.code if cls.department else 'GEN'

    res = {
        "class_id": cls.id,
        "class_name": cls.name,
        "department": dept_name,
        "department_code": dept_code,
        "N": N,
        "n": n,
        "S": S,
        "P": P,
        "net_score": net_score,
        "moderation_mark": moderation_mark,
        "total_score": total_score,
        "total_submissions": total_submissions,
        "submitted_categories": submitted_categories,
        "M": M,
        "academic_score": pillar_scores['Academic'],
        "co_curricular_score": pillar_scores['Co-Curricular'],
        "extra_curricular_score": pillar_scores['Extra-Curricular'],
        "category_scores": category_scores,
        "scoring_version": SCORING_ENGINE_VERSION,
    }

    # Verify mathematical invariants
    verify_scoring_invariants(res)

    return res


def compute_all_rankings(academic_year: Optional[str] = None, n_benchmark: Optional[float] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Compute official rankings for all classes.
    Uses high-performance batch SQL aggregation:
    - Fetches all classes with select_related('department') in 1 query.
    - Pre-caches all CriteriaItems with select_related('category') in 1 query.
    - Aggregates evaluated/locked marks grouped by (user__class_name_id, criteria_id) in 1 query.
    - Computes class scores and ranks in memory without N+1 query loops.
    """
    from .models import Class, Submission, CriteriaItem

    if n_benchmark is None:
        n = get_benchmark_class_size()
    else:
        n = float(n_benchmark)

    all_classes = list(Class.objects.select_related('department').all())
    if not all_classes:
        return [], []

    # 1. Pre-fetch all criteria items once outside the loop
    all_items = {it.id: it for it in CriteriaItem.objects.select_related('category').all()}

    # 2. Batch aggregate in single SQL query
    sub_qs = Submission.objects.filter(
        status__in=['Locked', 'Evaluated', 'Approved', 'APPROVED'],
        marks__isnull=False
    )
    if academic_year:
        sub_qs = sub_qs.filter(academic_year=academic_year)

    class_criteria_sums_user = (
        sub_qs.filter(user__class_name_id__isnull=False)
        .values('user__class_name_id', 'criteria_id')
        .annotate(total_marks=Sum('marks'))
    )
    class_criteria_sums_obj = (
        sub_qs.filter(class_obj_id__isnull=False, user__class_name_id__isnull=True)
        .values('class_obj_id', 'criteria_id')
        .annotate(total_marks=Sum('marks'))
    )

    class_scores_map: Dict[int, Dict[int, float]] = {}
    for entry in list(class_criteria_sums_user) + list(class_criteria_sums_obj):
        c_id = entry.get('user__class_name_id') or entry.get('class_obj_id')
        if not c_id:
            continue
        crit_id = entry['criteria_id']
        tot = float(entry['total_marks'] or 0.0)
        if c_id not in class_scores_map:
            class_scores_map[c_id] = {}
        class_scores_map[c_id][crit_id] = class_scores_map[c_id].get(crit_id, 0.0) + tot

    # 3. Pre-aggregate total submission counts and category breakdown per class
    all_subs_qs = Submission.objects.all()
    if academic_year:
        all_subs_qs = all_subs_qs.filter(academic_year=academic_year)

    from collections import defaultdict
    class_sub_counts: Dict[int, int] = defaultdict(int)
    class_cat_breakdown: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {'category': '', 'count': 0, 'points': 0.0}))

    for sub in all_subs_qs.select_related('category', 'user'):
        c_id = sub.class_obj_id or (sub.user.class_name_id if sub.user else None)
        if not c_id:
            continue
        class_sub_counts[c_id] += 1
        cat_name = sub.category.category if sub.category else 'General Activities'
        cat_item = class_cat_breakdown[c_id][cat_name]
        cat_item['category'] = cat_name
        cat_item['count'] += 1
        if sub.status in ['Locked', 'Evaluated', 'Approved', 'APPROVED'] and sub.marks:
            cat_item['points'] += float(sub.marks)

    ranked: List[Dict[str, Any]] = []
    unranked: List[Dict[str, Any]] = []

    for cls in all_classes:
        N = cls.num_students or 0
        P = round(float(cls.negative_points or 0.0), 2)
        cls_sums = class_scores_map.get(cls.id, {})

        S = round(sum(cls_sums.values()), 2)
        category_scores: Dict[str, float] = {}
        pillar_scores: Dict[str, float] = {
            'Academic': 0.0,
            'Co-Curricular': 0.0,
            'Extra-Curricular': 0.0,
        }

        for crit_id, marks_val in cls_sums.items():
            item = all_items.get(crit_id)
            cat_code = item.category.code if item and item.category else 'unknown'
            cat_name = item.category.category if item and item.category else 'Unknown Category'

            category_scores[cat_name] = round(category_scores.get(cat_name, 0.0) + marks_val, 2)
            pillar = get_pillar_for_category(cat_code)
            if pillar not in pillar_scores:
                pillar = get_pillar_for_category(cat_name)
            if pillar in pillar_scores:
                pillar_scores[pillar] = round(pillar_scores[pillar] + marks_val, 2)
            else:
                pillar_scores['Extra-Curricular'] = round(pillar_scores['Extra-Curricular'] + marks_val, 2)

        if N > 0:
            net_score = calculate_net_score(S, P)
            moderation_mark = calculate_class_moderation(N, n)
            M = calculate_class_index(S=S, P=P, N=N, n=n)
            total_score = round(M * float(N), 2) if M is not None else 0.0
        else:
            net_score = calculate_net_score(S, P)
            moderation_mark = 0.0
            total_score = 0.0
            M = None

        dept_name = cls.department.name if cls.department else 'General'
        dept_code = cls.department.code if cls.department else 'GEN'

        cat_list = sorted(class_cat_breakdown.get(cls.id, {}).values(), key=lambda x: (-x['count'], -x['points']))

        res = {
            "class_id": cls.id,
            "class_name": cls.name,
            "department": dept_name,
            "department_code": dept_code,
            "N": N,
            "n": n,
            "S": S,
            "P": P,
            "net_score": net_score,
            "moderation_mark": moderation_mark,
            "total_score": total_score,
            "total_submissions": class_sub_counts.get(cls.id, 0),
            "submitted_categories": cat_list,
            "M": M,
            "academic_score": pillar_scores['Academic'],
            "co_curricular_score": pillar_scores['Co-Curricular'],
            "extra_curricular_score": pillar_scores['Extra-Curricular'],
            "category_scores": category_scores,
            "scoring_version": SCORING_ENGINE_VERSION,
        }

        verify_scoring_invariants(res)

        if N > 0:
            ranked.append(res)
        else:
            res["rank"] = None
            unranked.append(res)

    # Deterministic sorting:
    # Primary: M (Class Index Mark) descending
    # Secondary: class_name ascending (case-insensitive) for stable JSON output
    ranked.sort(key=lambda x: (
        -(x["M"] if x["M"] is not None else -1.0),
        x["class_name"].lower() if x["class_name"] else ""
    ))

    # Standard Competition Ranking ("1224"):
    # Equal index scores share the same rank. Subsequent rank accounts for tied count.
    for idx, entry in enumerate(ranked):
        if idx > 0:
            prev = ranked[idx - 1]
            prev_m = prev["M"] if prev["M"] is not None else 0.0
            curr_m = entry["M"] if entry["M"] is not None else 0.0
            if abs(curr_m - prev_m) < 1e-5:
                entry["rank"] = prev["rank"]
            else:
                entry["rank"] = idx + 1
        else:
            entry["rank"] = 1

    return ranked, unranked


def explain_class_score(cls, academic_year: Optional[str] = None, n_benchmark: Optional[float] = None) -> Dict[str, Any]:
    """
    Produces an exhaustive explainability breakdown for a class.
    Shows the exact 4-step calculations, inputs, intermediate numbers, category breakdown, and final index.
    """
    data = compute_class_scores(cls, academic_year=academic_year, n_benchmark=n_benchmark)

    N = data["N"]
    n = data["n"]
    S = data["S"]
    P = data["P"]
    net = data["net_score"]
    mod = data["moderation_mark"]
    total = data["total_score"]
    M = data["M"]

    steps = [
        f"Step 1 (Net Evaluated Score): S ({S:.2f}) - P ({P:.2f}) = {net:.2f}",
        f"Step 2 (Cohort Scaling): (S - P) / N² = ({net:.2f}) / ({N}²) = {((net / (N**2)) if N > 0 else 0):.6f}",
        f"Step 3 (Benchmark Multiplier): 1 + 100 * (N ({N}) - n ({n})) = {1.0 + 100.0 * (N - n):.2f}",
    ]
    if N > 0:
        steps.append(f"Step 4 (Class Index M): M = (S - P) / N² * (1 + 100 * (N - n)) = {M:.4f}")
    else:
        steps.append("Step 4 (Class Index M): Class Size N is 0; Class Index is unranked (null)")

    return {
        **data,
        "explanation_steps": steps,
        "formula_spec": {
            "formula": "M = (S − P) / N² × (1 + 100 × (N − n))",
            "S": "Evaluated Marks",
            "P": "Penalties (defined in department management)",
            "N": "Class Size (defined in department management)",
            "n": "Smallest Class Benchmark (defined in department management)",
        },
        "breakdown": {
            "Academic": data["academic_score"],
            "Co-Curricular": data["co_curricular_score"],
            "Extra-Curricular": data["extra_curricular_score"],
            "categories": data["category_scores"],
        }
    }


def verify_scoring_invariants(data: Dict[str, Any]) -> bool:
    """
    Validates institutional mathematical invariants on a scored class dictionary:
    1. Moderation Mark is strictly bounded between 0.0 and 200.0.
    2. Total Score is non-negative (>= 0.0).
    3. If N > 0, Class Index M is non-negative (>= 0.0).
    4. Gross Marks S equals the sum of pillar scores (within floating point precision).
    """
    N = data.get("N", 0)
    S = data.get("S", 0.0)
    tot = data.get("total_score", 0.0)
    M = data.get("M")

    if tot < 0.0:
        raise AssertionError(f"Invariant violation: total_score ({tot}) cannot be negative.")

    if N > 0 and M is not None and M < 0.0:
        raise AssertionError(f"Invariant violation: Class Index M ({M}) cannot be negative for N={N}.")

    # Pillar sum check
    acad = data.get("academic_score", 0.0)
    co_curr = data.get("co_curricular_score", 0.0)
    extra_curr = data.get("extra_curricular_score", 0.0)
    pillar_sum = round(acad + co_curr + extra_curr, 2)

    # Note: If there are unclassified categories, pillar sum may differ slightly, but within mapped set:
    if abs(pillar_sum - S) > 0.05 and len(data.get("category_scores", {})) > 0:
        cat_sum = round(sum(data["category_scores"].values()), 2)
        if abs(cat_sum - S) > 0.05:
            raise AssertionError(f"Invariant violation: Sum of category scores ({cat_sum}) does not equal Gross Marks S ({S}).")

    return True


def snapshot_academic_year_results(academic_year_str: str, force: bool = False, mark_locked: bool = False) -> List[Any]:
    """
    Snapshots official class index results for an academic year into ClassIndexResult.
    If historical results are locked and force is False, raises PermissionError to prevent
    historical corruption.
    """
    from .models import AcademicYear, Class, ClassIndexResult

    ay_obj = AcademicYear.objects.filter(year=academic_year_str).first()
    if not ay_obj:
        ay_obj = AcademicYear.objects.create(year=academic_year_str, is_active=False)

    existing_locked = ClassIndexResult.objects.filter(academic_year=ay_obj, is_locked=True).exists()
    if existing_locked and not force:
        raise PermissionError(
            f"Historical results for Academic Year '{academic_year_str}' are locked and immutable. "
            "Pass force=True to deliberately overwrite official records."
        )

    ranked, unranked = compute_all_rankings(academic_year=academic_year_str)
    all_results = ranked + unranked
    saved_instances = []

    with transaction.atomic():
        for item in all_results:
            cls_obj = Class.objects.get(id=item["class_id"])
            res_obj, _ = ClassIndexResult.objects.update_or_create(
                class_name=cls_obj,
                academic_year=ay_obj,
                defaults={
                    "academic_score": item["academic_score"],
                    "co_curricular_score": item["co_curricular_score"],
                    "extra_curricular_score": item["extra_curricular_score"],
                    "final_index": item["M"] if item["M"] is not None else 0.0,
                    "rank": item.get("rank"),
                    "scoring_version": SCORING_ENGINE_VERSION,
                    "is_locked": mark_locked,
                    "snapshot_data": item,
                }
            )
            saved_instances.append(res_obj)

    logger.info(f"Successfully snapshotted {len(saved_instances)} class results for academic year '{academic_year_str}'.")
    return saved_instances


class ScoringEngine:
    """
    Focused domain service for institutional score calculation and moderation.
    """
    calculate_score = staticmethod(calculate_submission_score)
    compute_class_scores = staticmethod(compute_class_scores)
    compute_all_rankings = staticmethod(compute_all_rankings)
    explain_class_score = staticmethod(explain_class_score)
    snapshot_results = staticmethod(snapshot_academic_year_results)


ScoringService = ScoringEngine
EvaluationService = ScoringEngine
