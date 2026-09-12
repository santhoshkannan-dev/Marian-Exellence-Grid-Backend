import os
import hashlib
import logging
import re
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from django.utils import timezone
from users.models import Submission, Class, AcademicYear, CriteriaItem, CriteriaVersion, AcademicGradeBreakdown, CriteriaCategory
from users.serializers import SubmissionSerializer
from users.file_security import save_private_evidence_file, resolve_safe_private_path
from users.scoring_engine import calculate_submission_score
from users.services.user_service import UserService
from users.services.submission_service import SubmissionService
from users.workflow import validate_workflow_transition, execute_workflow_transition
from .base import (
    create_audit_entry,
    record_system_audit_event,
    UNEDITABLE_BY_STUDENT_STATES,
    is_user_student_rep,
    get_online_courses_item_ids,
    get_upsc_psc_item_ids,
    check_duplicate_submission,
    get_criteria_allowed_bounds,
)

logger = logging.getLogger(__name__)


HISTORICAL_CRITERIA_TITLES = {
    # Prizes
    336: "From Marian College", 258: "From Marian College", 21: "From Marian College",
    337: "Outside Marian College", 259: "Outside Marian College", 22: "Outside Marian College",
    # Online Courses
    318: "Swayam / NPTEL Course", 199: "Swayam / NPTEL Course", 3: "Swayam / NPTEL Course",
    319: "MOOC Course", 200: "MOOC Course", 4: "MOOC Course",
    # Academics
    316: "Class Pass Percentage %", 236: "Class Pass Percentage %", 1: "Class Pass Percentage %",
    317: "SAVE Sem Result", 237: "SAVE Sem Result", 2: "SAVE Sem Result",
    # Competitive Exams
    320: "JRF Passed", 5: "JRF Passed",
    321: "NET Passed", 6: "NET Passed",
    322: "Any Other Relevant Exam (IELTS, PET, Language Specific, etc.)", 7: "Any Other Relevant Exam (IELTS, PET, Language Specific, etc.)",
    323: "Participation in Relevant Exam (UPSC / PSC Exams)", 8: "Participation in Relevant Exam (UPSC / PSC Exams)",
    # Internships
    324: "Offline Internship (Min. 1 month)", 9: "Offline Internship (Min. 1 month)",
    325: "Online Internship (Min. 1 month)", 10: "Online Internship (Min. 1 month)",
    # Scholarships
    326: "International Level Scholarship", 11: "International Level Scholarship",
    327: "National Level Scholarship", 12: "National Level Scholarship",
    328: "State Level Scholarship", 13: "State Level Scholarship",
    329: "District Level Scholarship", 14: "District Level Scholarship",
    # Research
    330: "Publications", 252: "Publications", 15: "Publications",
    331: "Paper Presentation", 16: "Paper Presentation",
    332: "Patents", 17: "Patents",
    333: "Book Publications", 18: "Book Publications",
    334: "Funded Projects", 19: "Funded Projects",
    # Startups
    335: "Government-Registered Start-up", 20: "Government-Registered Start-up",
    # Programs Organized
    338: "Intercollegiate", 23: "Intercollegiate",
    339: "Intra - Collegiate", 24: "Intra - Collegiate",
    340: "Class Magazine", 25: "Class Magazine",
    # Leaderships
    341: "MCSC Executive Body Position", 26: "MCSC Executive Body Position",
    342: "SAHYA Executive Body Position", 27: "SAHYA Executive Body Position",
    343: "Clubs & Associations Leadership Position", 28: "Clubs & Associations Leadership Position",
    344: "Innovative / Sustainable Suggestion", 29: "Innovative / Sustainable Suggestion",
    # Social Responsibilities
    345: "Coordination of Event (Community Action / Outreach)", 30: "Coordination of Event (Community Action / Outreach)",
    346: "Participation in Event", 31: "Participation in Event",
    347: "News Media Coverage (Excluding Social Media)", 32: "News Media Coverage (Excluding Social Media)",
    # Career Advancement
    348: "Library - Regular Footfall (Biometric / Entry)", 270: "Library - Regular Footfall (Biometric / Entry)", 33: "Library - Regular Footfall (Biometric / Entry)",
    349: "Library - Academic & Career Books Issued/Read", 34: "Library - Academic & Career Books Issued/Read",
    350: "Repository Creation (Drive / GitHub / LMS / Website)", 35: "Repository Creation (Drive / GitHub / LMS / Website)",
    351: "LinkedIn - Profile Completion (Active Profile)", 36: "LinkedIn - Profile Completion (Active Profile)",
    352: "LinkedIn - Skill Badges Earned", 37: "LinkedIn - Skill Badges Earned",
    353: "LinkedIn - Micro-credentials / Learning Certifications", 38: "LinkedIn - Micro-credentials / Learning Certifications",
    # Documentation
    354: "Class Activity Report & Documents", 39: "Class Activity Report & Documents",
}


def validate_academic_field_positive_integer(field_name, val):
    """
    Strictly validates that field_name only permits positive whole numbers (>= 0).
    Prohibits characters like '-', '+', '*', '/', 'e', 'E', '.', or non-digit entries.
    Returns (is_valid, cleaned_int_val, error_message).
    """
    if val is None or val == '':
        return True, None, None
    if isinstance(val, bool):
        return False, None, f"Field '{field_name}' must be a numeric integer, not boolean."
    if isinstance(val, str):
        s_val = val.strip()
        if not s_val:
            return True, None, None
        if not re.match(r'^\d+$', s_val):
            return False, None, f"Field '{field_name}' must only contain positive whole numbers (no -, +, *, e, etc.)."
        try:
            num = int(s_val)
        except (ValueError, TypeError):
            return False, None, f"Field '{field_name}' must be a valid positive whole number."
    elif isinstance(val, (int, float)):
        if isinstance(val, float) and not val.is_integer():
            return False, None, f"Field '{field_name}' must be a whole number, not a decimal."
        num = int(val)
    else:
        return False, None, f"Field '{field_name}' must be a positive whole number."

    if num < 0:
        return False, None, f"Field '{field_name}' cannot be negative. Only positive numbers are permitted."
    return True, num, None


def validate_academic_pass_percentage(val):
    """
    Strictly validates that pass_percentage only permits positive numbers between 0 and 100.
    Prohibits characters like '-', '+', '*', '/', 'e', 'E', or non-numeric entries.
    Returns (is_valid, cleaned_float_val, error_message).
    """
    if val is None or val == '':
        return True, None, None
    if isinstance(val, bool):
        return False, None, "Field 'pass_percentage' must be a number, not boolean."
    if isinstance(val, str):
        s_val = val.strip()
        if not s_val:
            return True, None, None
        if not re.match(r'^\d+(\.\d+)?$', s_val):
            return False, None, "Field 'pass_percentage' must only contain positive numbers between 0 and 100 (no -, +, *, e, etc.)."
        try:
            num = float(s_val)
        except (ValueError, TypeError):
            return False, None, "Field 'pass_percentage' must be a valid number."
    elif isinstance(val, (int, float)):
        num = float(val)
    else:
        return False, None, "Field 'pass_percentage' must be a positive number between 0 and 100."

    if num < 0.0 or num > 100.0:
        return False, None, "Field 'pass_percentage' must be a positive number between 0.0 and 100.0."
    return True, num, None


def validate_academic_submission_payload(request_data, evidence, gb_data, proof, description, is_submit=False):
    """
    Validates all academic fields across request_data, evidence, and grade_breakdown:
    - count_90_above, count_80_90, count_70_80, count_fail: must be positive integers (no -, +, *, e, etc.)
    - pass_percentage: must be positive float/int between 0 and 100 (no -, +, *, e, etc.)
    - proof_url: must be provided if is_submit is True
    - description: must be a valid text string if provided
    Returns (is_valid, parsed_data_dict, error_message).
    """
    if is_submit and not proof:
        return False, {}, "proof_url is required for academic submissions."

    if description is not None and not isinstance(description, str):
        return False, {}, "description must be a valid text string."

    req_d = request_data if isinstance(request_data, dict) else {}
    ev_d = evidence if isinstance(evidence, dict) else {}
    gb_d = gb_data if isinstance(gb_data, dict) else {}
    grades_d = ev_d.get('grades') if isinstance(ev_d.get('grades'), dict) else {}

    field_sources = [req_d, gb_d, ev_d]

    count_field_aliases = {
        'count_90_above': ['count_90_above', 'count90Above', 's_grade_count', 'S'],
        'count_80_90': ['count_80_90', 'count80to90', 'a_plus_grade_count', 'APlus'],
        'count_70_80': ['count_70_80', 'count70to80', 'a_grade_count', 'A'],
        'count_fail': ['count_fail', 'failCount', 'failed_count', 'Fail'],
    }

    parsed = {}
    for canonical_field, aliases in count_field_aliases.items():
        found_val = None
        for alias in aliases:
            for src in field_sources:
                if alias in src and src[alias] is not None and src[alias] != '':
                    found_val = src[alias]
                    break
            if found_val is not None:
                break
            if alias in grades_d and grades_d[alias] is not None and grades_d[alias] != '':
                found_val = grades_d[alias]
                break

        is_valid, clean_num, err_msg = validate_academic_field_positive_integer(canonical_field, found_val)
        if not is_valid:
            return False, {}, err_msg
        parsed[canonical_field] = clean_num

    # pass_percentage
    found_pct = None
    for alias in ['pass_percentage', 'passPercentage', 'class_pass_percentage', 'classPassPercentage']:
        for src in field_sources:
            if alias in src and src[alias] is not None and src[alias] != '':
                found_pct = src[alias]
                break
        if found_pct is not None:
            break

    is_valid_pct, clean_pct, err_msg_pct = validate_academic_pass_percentage(found_pct)
    if not is_valid_pct:
        return False, {}, err_msg_pct
    parsed['pass_percentage'] = clean_pct

    # total_students if provided
    total_stu_val = None
    for alias in ['total_students', 'totalStudents']:
        for src in field_sources:
            if alias in src and src[alias] is not None and src[alias] != '':
                total_stu_val = src[alias]
                break
        if total_stu_val is not None:
            break

    if total_stu_val is not None and total_stu_val != '':
        is_valid_t, clean_t, err_msg_t = validate_academic_field_positive_integer('total_students', total_stu_val)
        if not is_valid_t:
            return False, {}, err_msg_t
        parsed['total_students'] = clean_t
    else:
        parsed['total_students'] = None

    return True, parsed, None


def resolve_criteria_item(criteria_id, data=None, submission=None):
    if not criteria_id:
        return None
    try:
        c_id = int(criteria_id)
    except (ValueError, TypeError):
        return None

    # 1. Direct PK lookup
    item = CriteriaItem.objects.filter(pk=c_id).select_related('category').first()
    if item:
        return item

    # 2. Historical ID mapping
    if c_id in HISTORICAL_CRITERIA_TITLES:
        title = HISTORICAL_CRITERIA_TITLES[c_id]
        item = CriteriaItem.objects.filter(title__iexact=title).select_related('category').first()
        if item:
            return item

    # 3. Match from description or payload
    desc = ''
    if data and isinstance(data, dict):
        desc = str(data.get('description') or '')
    if not desc and submission:
        desc = str(submission.description or '')

    if desc:
        desc_lower = desc.lower()
        for cand in CriteriaItem.objects.select_related('category').all():
            if cand.title.lower() in desc_lower or desc_lower.startswith(cand.title.lower()[:15]):
                return cand

    # 4. Fallback: match via SubCategory if evidence contains subItem or subcategoryId
    from users.models import SubCategory
    subcat = None
    subcat_id = (data.get('subcategoryId') or data.get('subcategory_id')) if isinstance(data, dict) else None
    if not subcat_id and submission:
        subcat_id = submission.subcategory_id
    if subcat_id:
        try:
            subcat = SubCategory.objects.filter(pk=int(subcat_id)).select_related('category').first()
        except (ValueError, TypeError):
            pass

    if subcat and subcat.category:
        crit_cat = CriteriaCategory.objects.filter(code=subcat.category.code).first()
        if crit_cat:
            first_item = crit_cat.items.first()
            if first_item:
                return first_item

    return None


class SubmissionListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        user = request.user
        academic_year = request.query_params.get('academicYear')
        limit_param = request.query_params.get('limit')
        offset_param = request.query_params.get('offset', 0)

        queue_param = request.query_params.get('queue')
        status_param = request.query_params.get('status')
        role_context = (
            request.headers.get('X-Role-Context') or
            request.query_params.get('role_context') or
            request.query_params.get('role') or
            (request.session.get('active_role') if hasattr(request, 'session') and request.session else None)
        )
        if not role_context and user and getattr(user, 'id', None):
            from django.core.cache import cache
            role_context = cache.get(f"user_active_role_{user.id}")

        queryset = SubmissionService.filter_submissions_for_user(
            user=user,
            academic_year=academic_year,
            queue=queue_param,
            status_filter=status_param,
            role_context=role_context
        )

        total_count = None
        if limit_param is not None:
            try:
                limit = max(1, min(int(limit_param), 500))
                offset = max(0, int(offset_param))
                total_count = queryset.count()
                queryset = queryset[offset:offset + limit]
            except (ValueError, TypeError):
                pass

        data = [
            SubmissionService.serialize_submission_for_user(s, user)
            for s in queryset
        ]
        response = Response(data)
        if total_count is not None:
            response['X-Total-Count'] = str(total_count)
            response['X-Limit'] = str(limit_param)
            response['X-Offset'] = str(offset_param)
        return response

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        criteria_id = request.data.get('criteriaId') or request.data.get('criteria_id')
        if not criteria_id:
            cat_id = request.data.get('category_id') or request.data.get('categoryId')
            subcat_id = request.data.get('subcategory_id') or request.data.get('subcategoryId')
            if subcat_id:
                from users.models import SubCategory, CriteriaCategory
                sub_obj = SubCategory.objects.filter(pk=subcat_id).first()
                if sub_obj:
                    crit_item_cand = CriteriaItem.objects.filter(title__icontains=sub_obj.subcategory_name).first()
                    if not crit_item_cand and sub_obj.category:
                        crit_item_cand = CriteriaItem.objects.filter(category__code__icontains=sub_obj.category.code).first()
                    if not crit_item_cand:
                        cc = CriteriaCategory.objects.filter(code=sub_obj.category.code).first() if sub_obj.category else None
                        if not cc and sub_obj.category:
                            cc = CriteriaCategory.objects.create(category=sub_obj.category.name, code=sub_obj.category.code)
                        crit_item_cand = CriteriaItem.objects.create(
                            category=cc,
                            title=sub_obj.subcategory_name,
                            type='fixed',
                            marks=float(sub_obj.default_marks)
                        )
                    if crit_item_cand:
                        criteria_id = crit_item_cand.id
            elif cat_id:
                crit_item_cand = CriteriaItem.objects.filter(category__id=cat_id).first()
                if not crit_item_cand:
                    from users.models import Category, CriteriaCategory
                    cat_obj = Category.objects.filter(id=cat_id).first()
                    if cat_obj:
                        cc = CriteriaCategory.objects.filter(code=cat_obj.code).first()
                        if not cc:
                            cc = CriteriaCategory.objects.create(category=cat_obj.name, code=cat_obj.code)
                        crit_item_cand = CriteriaItem.objects.create(
                            category=cc,
                            title=cat_obj.name,
                            type='fixed',
                            marks=10.0
                        )
                        criteria_id = crit_item_cand.id

        if not criteria_id:
            return Response({"error": "criteriaId is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            criteria_id_int = int(criteria_id)
        except (ValueError, TypeError):
            return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)

        criteria_item = CriteriaItem.objects.filter(pk=criteria_id_int).select_related('category').first()
        if not criteria_item:
            criteria_item = resolve_criteria_item(criteria_id_int, request.data)
        if not criteria_item:
            return Response({"error": f"Criteria item with id '{criteria_id_int}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
        criteria_id_int = criteria_item.id

        academic_year = request.data.get('academicYear', '2025-2026')
        clean_ay = str(academic_year).strip()
        if not re.match(r'^\d{4}-\d{4}$', clean_ay) or len(clean_ay) > 20:
            return Response({"error": "academicYear must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not (AcademicYear.objects.filter(year=clean_ay).exists() or CriteriaVersion.objects.filter(academic_year=clean_ay).exists()):
            return Response({"error": f"Academic year '{clean_ay}' does not exist in the system."}, status=status.HTTP_400_BAD_REQUEST)
        academic_year = clean_ay

        description = request.data.get('description', '')
        clean_desc = str(description).strip()
        cat_code_chk = (criteria_item.category.code or '').lower() if (criteria_item and criteria_item.category) else ''
        if not clean_desc and ('academic' in cat_code_chk or (criteria_item and criteria_item.type == 'academic_grades')):
            clean_desc = f"Sem Result (End Semester Examination) — {criteria_item.title}"
        if not clean_desc:
            return Response({"error": "description is required and cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
        if len(clean_desc) > 5000:
            return Response({"error": "description cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        description = clean_desc

        raw_status = request.data.get('status')
        user_role = getattr(user, 'role', None)

        # Validate workflow status on creation: client cannot set privileged or terminal states
        if user_role == 'student':
            if raw_status is not None and raw_status not in ('Draft', 'Submitted', 'Pending Verification', 'Pending Rep Verification', 'DQC_PENDING'):
                return Response(
                    {"error": "Unauthorized: Students cannot create submissions in verified, evaluated, or locked status."},
                    status=status.HTTP_403_FORBIDDEN
                )
            status_val = raw_status if raw_status else 'DQC_PENDING'
        else:
            status_val = raw_status if raw_status else 'DQC_PENDING'
            if status_val not in dict(Submission.STATUS_CHOICES):
                return Response({"error": f"Invalid status: '{status_val}'."}, status=status.HTTP_400_BAD_REQUEST)

        remarks = request.data.get('remarks', '')
        if remarks and len(str(remarks)) > 2000:
            return Response({"error": "remarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)

        proof = request.data.get('proof', '') or request.data.get('proof_url', '')
        if proof:
            raw_proof = str(proof).strip()
            if '..' in raw_proof or raw_proof.startswith('/') or raw_proof.startswith('\\'):
                return Response({"error": "Invalid proof path specification."}, status=status.HTTP_400_BAD_REQUEST)
            if len(raw_proof) > 255:
                return Response({"error": "proof reference cannot exceed 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
            proof = raw_proof

        proof_file = request.FILES.get('proof_file') or request.FILES.get('file') or request.FILES.get('evidence_file')
        uploaded_proof_hash = None
        if proof_file:
            try:
                rel_p, f_h, _, _ = save_private_evidence_file(proof_file, academic_year=academic_year)
                proof = rel_p
                uploaded_proof_hash = f_h
            except ValueError as ve:
                return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.exception("Failed to save uploaded evidence file")
                return Response({"error": f"Failed to save evidence file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        event_id = request.data.get('eventId', '')
        if event_id and len(str(event_id)) > 100:
            return Response({"error": "eventId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)

        evidence = request.data.get('evidence')
        start_date = request.data.get('start_date') or request.data.get('startDate')
        if not start_date and isinstance(evidence, dict):
            start_date = evidence.get('startDate') or evidence.get('examDate')
        if start_date and len(str(start_date)) > 50:
            return Response({"error": "start_date cannot exceed 50 characters."}, status=status.HTTP_400_BAD_REQUEST)

        end_date = request.data.get('end_date') or request.data.get('endDate')
        if not end_date and isinstance(evidence, dict):
            end_date = evidence.get('endDate')
        if end_date and len(str(end_date)) > 50:
            return Response({"error": "end_date cannot exceed 50 characters."}, status=status.HTTP_400_BAD_REQUEST)

        marks = request.data.get('marks')
        if marks is not None and user and user_role == 'student':
            marks = None
        elif marks is not None:
            try:
                req_marks = float(marks)
                allowed_min, allowed_max, details = SubmissionService.get_criteria_allowed_bounds(criteria_item, evidence)
                is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (allowed_min < 0)
                if req_marks < 0 and not is_negative:
                    return Response(
                        {"error": f"Score ({req_marks}) cannot be negative for non-penalty criteria."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if req_marks > (allowed_max + 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) exceeds the maximum allowed limit ({allowed_max}) for criteria '{criteria_item.title}'{details}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if allowed_min is not None and req_marks < (allowed_min - 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) is below the minimum allowed limit ({allowed_min}) for criteria '{criteria_item.title}'."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response({"error": "Invalid marks value provided."}, status=status.HTTP_400_BAD_REQUEST)

        # --- 12-Category Subcategory-Level Access Control & Submit Limit Enforcement ---
        # Delegate to access_rules — the single source of truth for all 12 categories.
        from users.access_rules import validate_subcategory_access, get_submission_cycle_limit

        evidence_dict = evidence if isinstance(evidence, dict) else {}

        # 1. Access Control (DQC-only categories + hybrid subcategory rules)
        access_allowed, access_error = validate_subcategory_access(user, criteria_item, evidence_dict)
        if not access_allowed:
            return Response({"error": access_error}, status=status.HTTP_403_FORBIDDEN)

        # 2. Cycle Submission Limit Enforcement
        cycle_max, cycle_label = get_submission_cycle_limit(criteria_item, evidence_dict)
        if cycle_max is not None:
            cat_code_raw = (criteria_item.category.code or '') if (criteria_item and criteria_item.category) else ''
            cat_code_norm = cat_code_raw.lower()

            if 'academics' in cat_code_norm or cat_code_norm == 'cat-academics':
                # Academics: limit is per class (DQC submits on behalf of class)
                user_cls = user.class_name
                cycle_count = Submission.objects.filter(
                    Q(user=user) | Q(user__class_name=user_cls, class_obj=user_cls),
                    academic_year=academic_year,
                    criteria_id=criteria_id_int
                ).exclude(status__in=['Rejected', 'REJECTED']).count()
            else:
                # All other categories: limit is per student, per criteria_id
                cycle_count = Submission.objects.filter(
                    user=user,
                    criteria_id=criteria_id_int,
                    academic_year=academic_year
                ).exclude(status__in=['Rejected', 'REJECTED']).count()

            if cycle_count >= cycle_max:
                return Response(
                    {"error": f"Maximum {cycle_max} '{cycle_label}' submission(s) allowed per evaluation cycle. Limit reached."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # 3. Category Specific Field Validations
        # Resolve category identifiers needed by the field validation checks below.
        cat_code = (criteria_item.category.code or '').lower() if (criteria_item and criteria_item.category) else ''
        cat_name = (criteria_item.category.category or '').lower() if (criteria_item and criteria_item.category) else ''

        # A. Startups: Requires Startup Name, Registration Date, Govt ID
        if 'startup' in cat_code or 'startup' in cat_name:
            ev_dict = evidence if isinstance(evidence, dict) else {}
            startup_name = ev_dict.get('startupName') or ev_dict.get('companyName') or request.data.get('startupName') or clean_desc
            startup_reg_date = ev_dict.get('regDate') or ev_dict.get('registrationDate') or start_date
            startup_govt_id = ev_dict.get('govtId') or ev_dict.get('startupGovtId') or request.data.get('govtId') or request.data.get('certificateId') or request.data.get('certificate_id')

            if not startup_name:
                return Response({"error": "Startup Name is required for Startup submissions."}, status=status.HTTP_400_BAD_REQUEST)
            if not startup_reg_date:
                return Response({"error": "Registration Date is required for Startup submissions."}, status=status.HTTP_400_BAD_REQUEST)
            if not startup_govt_id:
                return Response({"error": "Govt Registration ID is required for Startup submissions."}, status=status.HTTP_400_BAD_REQUEST)

        # B. Programs Organized: Requires Event Name & Official Event ID
        if 'program' in cat_code or 'program' in cat_name:
            ev_dict = evidence if isinstance(evidence, dict) else {}
            prog_name = ev_dict.get('eventName') or request.data.get('eventName') or clean_desc
            prog_event_id = event_id or ev_dict.get('eventId') or ev_dict.get('officialEventId') or request.data.get('eventId')

            if not prog_name:
                return Response({"error": "Event Name is required for Programs Organized submissions."}, status=status.HTTP_400_BAD_REQUEST)
            if not prog_event_id:
                return Response({"error": "Official Event ID is required for Programs Organized submissions."}, status=status.HTTP_400_BAD_REQUEST)

        # C. Internships: Date window constraint (June 01 - March 30)
        if 'internship' in cat_code or 'internship' in cat_name:
            check_date_str = start_date or request.data.get('startDate') or ''
            if check_date_str:
                import datetime as dt_mod
                try:
                    # Support YYYY-MM-DD or DD/MM/YYYY
                    if '-' in check_date_str:
                        parts = check_date_str.split('-')
                        check_month = int(parts[1])
                    elif '/' in check_date_str:
                        parts = check_date_str.split('/')
                        check_month = int(parts[1])
                    else:
                        check_month = None

                    if check_month in (4, 5):
                        return Response(
                            {"error": "Internships must fall within the institutional window (June 01 - March 30). April and May dates are not eligible."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                except (ValueError, IndexError):
                    pass

        # Academic Grade Breakdown Validation & Auto-Calculation
        is_academic_cat = (
            (criteria_item.type == 'academic_grades') or
            ('academics' in cat_code) or
            ('academics' in cat_name) or
            (isinstance(evidence, dict) and evidence.get('type') == 'academic_marks') or
            (isinstance(request.data.get('grade_breakdown'), dict))
        )

        s_cnt, ap_cnt, a_cnt, fail_cnt, t_students, pass_pct = 0, 0, 0, 0, 0, 0.0
        if is_academic_cat:
            is_submitting = status_val in ('Submitted', 'SUBMITTED', 'Pending Rep Verification', 'Pending', 'Student Rep Verified')
            is_valid_acad, parsed_acad, acad_err = validate_academic_submission_payload(
                request_data=request.data,
                evidence=evidence,
                gb_data=request.data.get('grade_breakdown'),
                proof=proof,
                description=description,
                is_submit=is_submitting
            )
            if not is_valid_acad:
                return Response({"error": acad_err}, status=status.HTTP_400_BAD_REQUEST)

            s_cnt = parsed_acad.get('count_90_above') or 0
            ap_cnt = parsed_acad.get('count_80_90') or 0
            a_cnt = parsed_acad.get('count_70_80') or 0
            fail_cnt = parsed_acad.get('count_fail') or 0
            g_sum = s_cnt + ap_cnt + a_cnt + fail_cnt

            t_students = parsed_acad.get('total_students') or 0
            if t_students <= 0:
                t_students = max(1, g_sum)

            if g_sum > t_students:
                return Response({"error": f"Sum of student grades ({g_sum}) exceeds total class students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            if parsed_acad.get('pass_percentage') is not None:
                pass_pct = parsed_acad['pass_percentage']
            elif g_sum > 0 and t_students > 0:
                pass_pct = round((passed / float(t_students)) * 100.0, 2)
            else:
                pass_pct = 0.0

            if not isinstance(evidence, dict):
                evidence = {}

            evidence["classPassPercentage"] = pass_pct
            evidence["passCount"] = passed
            evidence["count_90_above"] = s_cnt
            evidence["count_80_90"] = ap_cnt
            evidence["count_70_80"] = a_cnt
            evidence["count_fail"] = fail_cnt
            evidence["pass_percentage"] = pass_pct
            evidence["totalStudents"] = t_students

        # Extract certificate ID and proof hash for duplicate detection
        cert_id = request.data.get('certificateId') or request.data.get('eventId')
        if not cert_id and isinstance(evidence, dict):
            cert_id = evidence.get('certificateId') or evidence.get('certId') or evidence.get('startupGovtId') or evidence.get('eventId')
        if cert_id and len(str(cert_id)) > 100:
            return Response({"error": "certificateId/identifier cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)

        proof_h = uploaded_proof_hash or request.data.get('proofHash')
        if not proof_h and isinstance(evidence, dict):
            proof_h = evidence.get('proofHash')
        if not proof_h and proof:
            proof_h = hashlib.sha256(str(proof).encode('utf-8')).hexdigest()

        # Check duplicate submission
        dup_err = SubmissionService.check_duplicate_submission(
            user=user,
            criteria_id=criteria_id,
            academic_year=academic_year,
            certificate_id=cert_id,
            proof_hash=proof_h,
            description=description
        )
        if dup_err:
            return Response({"error": dup_err}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve active CriteriaVersion for submission's academic_year
        active_cv = CriteriaVersion.objects.filter(academic_year=academic_year, is_locked=False).order_by('-version').first()
        if not active_cv:
            active_cv = CriteriaVersion.objects.filter(academic_year=academic_year).order_by('-version').first()
        if not active_cv:
            active_cv, _ = CriteriaVersion.objects.get_or_create(
                academic_year=academic_year or '2025-2026',
                version=1,
                defaults={'name': f'{academic_year} v1', 'is_locked': False}
            )

        # Academic Grade Breakdown: AcademicGradeBreakdown is the authoritative relational source
        gb_data = request.data.get('grade_breakdown')
        if not gb_data and is_academic_cat:
            gb_data = {
                "s_grade_count": s_cnt,
                "a_plus_grade_count": ap_cnt,
                "a_grade_count": a_cnt,
                "other_pass_count": 0,
                "failed_count": fail_cnt,
                "total_students": t_students,
                "class_pass_percentage": pass_pct
            }
        elif not gb_data and isinstance(evidence, dict) and "grades" in evidence:
            ev_g = evidence.get('grades') or {}
            gb_data = {
                "s_grade_count": ev_g.get("S", 0),
                "a_plus_grade_count": ev_g.get("APlus", 0),
                "a_grade_count": ev_g.get("A", 0),
                "other_pass_count": ev_g.get("OtherPass", 0) or ev_g.get("B", 0),
                "failed_count": ev_g.get("Fail", 0),
                "total_students": evidence.get("totalStudents", 0)
            }

        clean_evidence = dict(evidence or {}) if isinstance(evidence, dict) else {}

        # Resolve SubCategory for categories containing subcategories
        c_item = CriteriaItem.objects.filter(pk=criteria_id_int).select_related('category').first()
        c_category = c_item.category if c_item else None
        is_man_eval = getattr(c_item, 'is_manual_eval', False) or (c_category and getattr(c_category, 'is_manual_eval', False))

        raw_sub_id = request.data.get('subcategoryId') or request.data.get('subcategory_id')
        from users.models import SubCategory
        subcat = None
        if raw_sub_id:
            try:
                subcat = SubCategory.objects.filter(pk=int(raw_sub_id)).first()
            except (ValueError, TypeError):
                pass

        if not subcat:
            subcat = SubCategory.find_subcategory(
                subcategory_id=raw_sub_id,
                category_id=getattr(c_category, 'id', None),
                category_code=getattr(c_category, 'code', None),
                criteria_item=c_item,
                evidence=evidence
            )

        is_academic_item = (c_item and c_item.type == 'academic_grades') or (c_category and c_category.code == 'cat-academics')
        if subcat and not is_academic_item:
            # Strictly driven by subcategory.default_marks
            count_val = 1
            if (c_item and c_item.type == 'count') or (isinstance(evidence, dict) and 'count' in evidence):
                try:
                    count_val = max(1, int(evidence.get('count', 1)))
                except (ValueError, TypeError):
                    count_val = 1
            expected_marks = float(subcat.default_marks) * count_val
            if status_val in ('Approved', 'APPROVED', 'Evaluated', 'Locked', 'Submitted'):
                calculated_marks = expected_marks
                marks = int(round(calculated_marks))
            else:
                calculated_marks = 0.0
                marks = None
            subcat_id_to_store = subcat.id
        else:
            subcat_id_to_store = subcat.id if subcat else None
            if c_item:
                if is_academic_item or is_academic_cat:
                    gb = request.data.get('grade_breakdown') or (gb_data if isinstance(gb_data, dict) else {})
                    for k in ('count_90_above', 'count_80_90', 'count_70_80', 'count_fail', 'pass_percentage'):
                        if isinstance(evidence, dict) and k in evidence:
                            clean_evidence[k] = evidence[k]
                        elif k in gb:
                            clean_evidence[k] = gb[k]
                        elif k in request.data:
                            clean_evidence[k] = request.data[k]
                computed = calculate_submission_score(c_item, clean_evidence)
                if status_val in ('Approved', 'APPROVED', 'Evaluated', 'Locked', 'Submitted'):
                    calculated_marks = float(computed) if computed is not None else 0.0
                    marks = int(round(calculated_marks))
                else:
                    calculated_marks = 0.0
                    marks = None
            else:
                calculated_marks = 0.0
                marks = None

        try:
            with transaction.atomic():
                submission = Submission.objects.create(
                    user=user,
                    class_obj=user.class_name,
                    category=c_category,
                    criteria_id=criteria_id_int,
                    subcategory_id=subcat_id_to_store,
                    criteria_version=active_cv,
                    academic_year=academic_year,
                    submission_date=timezone.now(),
                    description=description,
                    status=status_val,
                    remarks=remarks,
                    marks=marks,
                    calculated_marks=calculated_marks,
                    is_manual_eval=is_man_eval,
                    proof=proof,
                    proof_url=proof,
                    proof_hash=proof_h,
                    certificate_id=cert_id,
                    event_id=event_id,
                    evidence=clean_evidence,
                    submission_metadata=clean_evidence,
                    start_date=start_date,
                    end_date=end_date
                )

                sub_type = clean_evidence.get("submissionType")
                if sub_type:
                    submission.submission_type = sub_type
                    submission.save(update_fields=["submission_type"])

                if isinstance(gb_data, dict):
                    s_c = int(gb_data.get("s_grade_count", 0) or 0)
                    ap_c = int(gb_data.get("a_plus_grade_count", 0) or 0)
                    a_c = int(gb_data.get("a_grade_count", 0) or 0)
                    other_c = int(gb_data.get("other_pass_count", 0) or 0)
                    fail_c = int(gb_data.get("failed_count", 0) or 0)
                    total_c = int(gb_data.get("total_students", 0) or 0)
                    AcademicGradeBreakdown.objects.update_or_create(
                        submission=submission,
                        defaults={
                            "s_grade_count": s_c,
                            "a_plus_grade_count": ap_c,
                            "a_grade_count": a_c,
                            "other_pass_count": other_c,
                            "failed_count": fail_c,
                            "total_students": total_c
                        }
                    )

                create_audit_entry(
                    submission=submission,
                    actor=user,
                    stage=1,
                    stage_name="Student Claims",
                    prev_status="Initial",
                    new_status=submission.status,
                    comments=remarks or "",
                    request=request
                )

                record_system_audit_event(
                    action='SUBMISSION_CREATE',
                    object_type='Submission',
                    object_id=submission.id,
                    actor=user,
                    object_repr=f"Submission #{submission.id} (Criteria: {submission.criteria_id}) -> {submission.status}",
                    old_value=None,
                    new_value={'status': submission.status, 'marks': submission.marks, 'criteria_id': submission.criteria_id},
                    reason=remarks or "New submission created",
                    request=request
                )
        except IntegrityError as e:
            logger.warning(f"IntegrityError creating submission: {e}")
            return Response(
                {"error": "A duplicate submission with this certificate or proof document was already recorded in the system."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to create submission")
            return Response({"error": "Failed to create submission. Please verify your input data."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SubmissionService.serialize_submission_for_user(submission, user), status=status.HTTP_201_CREATED)


class SubmissionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            submission = Submission.objects.select_related('user', 'user__class_name', 'user__department').get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        user_role = getattr(user, 'role', None)

        # Object-level authorization for reading submissions
        if user.is_superuser or user_role in ('admin', 'evaluation'):
            pass
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if not (is_class_teacher or is_same_dept):
                return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        elif user_role == 'student':
            if submission.user_id != user.id:
                sub_class = submission.user.class_name if submission.user else None
                from users.workflow import is_user_student_rep_for_class
                if not is_user_student_rep_for_class(user, sub_class):
                    return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)

        serializer = SubmissionSerializer(submission)
        resp_data = dict(serializer.data)
        is_owner = bool(user and getattr(user, 'is_authenticated', False) and submission.user_id == user.id)
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'faculty', 'evaluation') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        if not (is_owner or is_staff_or_eval):
            resp_data['evaluator_remarks'] = None
            resp_data['evaluator_verified_by_name'] = None
        return Response(resp_data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        is_owner = (submission.user_id == user.id)
        user_role = getattr(user, 'role', None)
        sub_class = getattr(submission, 'class_obj', None) or (submission.user.class_name if submission.user else None)
        from users.workflow import is_user_student_rep_for_class, is_user_class_advisor, is_evaluator_assigned_to_item
        is_rep_for_class = is_user_student_rep_for_class(user, sub_class)
        is_class_advisor = is_user_class_advisor(user, sub_class)

        req_role_context = (
            request.data.get('role_context') or
            request.data.get('role') or
            request.headers.get('X-Role-Context') or
            request.query_params.get('role_context') or
            (request.session.get('active_role') if hasattr(request, 'session') and request.session else None)
        )
        if not req_role_context and user and getattr(user, 'id', None):
            from django.core.cache import cache
            req_role_context = cache.get(f"user_active_role_{user.id}")
        if req_role_context:
            req_role_context = str(req_role_context).strip().lower()

        # Determine effective role for workflow execution
        effective_role = user_role
        is_eval_assigned = is_evaluator_assigned_to_item(user, submission.criteria_id)
        if req_role_context in ('evaluator', 'evaluation'):
            effective_role = 'evaluation'
        elif req_role_context in ('teacher', 'faculty'):
            effective_role = 'faculty'
        elif is_class_advisor and (
            request.data.get('teacherVerifiedByName') or
            request.data.get('teacherRemarks') or
            request.data.get('status') in ('Teacher Verified', 'Approved') or
            submission.status in ('Submitted', 'Pending Rep Verification', 'Student Rep Verified')
        ):
            effective_role = 'faculty'
        elif user_role in ('faculty', 'staff') and is_eval_assigned:
            is_eval_action = (
                request.data.get('status') in ('Evaluated', 'Approved') or
                request.data.get('evaluatorVerified') is True or
                request.data.get('actionType') == 'APPROVE_AND_CREDIT' or
                'evaluatorRemarks' in request.data or
                'evaluatorVerifiedByName' in request.data or
                req_role_context in ('evaluator', 'evaluation') or
                submission.status in ('Teacher Verified', 'EVALUATOR_PENDING')
            )
            if is_eval_action or not is_class_advisor:
                effective_role = 'evaluation'

        if user_role == 'student':
            req_status = request.data.get('status')

            # Students can never assign evaluation marks
            if request.data.get('marks') is not None and request.data.get('marks') != submission.marks:
                return Response(
                    {"error": "Unauthorized: Students cannot assign evaluation marks."},
                    status=status.HTTP_403_FORBIDDEN
                )

            # 1. Check if the user is an authorized Student Representative verifying/reviewing for this class
            if is_rep_for_class and req_status and req_status != submission.status:
                allowed_rep_statuses = {
                    'Student Rep Verified', 'TEACHER_PENDING',
                    'Correction Requested', 'SENT_BACK',
                    'Rejected', 'REJECTED',
                    'Pending Rep Verification', 'DQC_PENDING',
                    'Pending', 'Submitted'
                }
                if req_status not in allowed_rep_statuses:
                    return Response(
                        {"error": f"Student representatives cannot transition submission to '{req_status}'."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                # Authorized student representative verification permitted (even if self-submitted)
            # 2. Check regular student owner edit/submission rules
            elif is_owner:
                if submission.status in UNEDITABLE_BY_STUDENT_STATES:
                    return Response(
                        {"error": f"Submission cannot be edited in '{submission.status}' status."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                if req_status and req_status != submission.status:
                    if req_status in ('Approved', 'Verified', 'Teacher Verified', 'Student Rep Verified', 'Evaluated', 'Locked'):
                        return Response(
                            {"error": "Unauthorized: Students cannot alter verification or evaluation status."},
                            status=status.HTTP_403_FORBIDDEN
                        )
            # 3. Student rep updating remarks or metadata without status change
            elif is_rep_for_class:
                pass
            else:
                return Response(
                    {"error": "You do not have permission to modify this submission."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif effective_role in ('faculty', 'staff', 'teacher'):
            is_class_teacher = is_user_class_advisor(user, sub_class)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if not (is_class_teacher or is_same_dept):
                return Response(
                    {"error": "Faculty cannot modify submissions outside their advised class or department."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'marks' in request.data and request.data.get('marks') is not None and request.data.get('marks') != submission.marks:
                return Response(
                    {"error": "Unauthorized: Faculty cannot assign evaluation marks."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'evaluatorRemarks' in request.data and request.data.get('evaluatorRemarks') != submission.evaluator_remarks:
                return Response(
                    {"error": "Unauthorized: Faculty cannot assign evaluator remarks."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'evaluatorVerified' in request.data and request.data.get('evaluatorVerified') != submission.evaluator_verified:
                return Response(
                    {"error": "Unauthorized: Faculty cannot alter evaluator verification status."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif effective_role == 'evaluation':
            req_c_val = request.data.get('criteriaId', submission.criteria_id)
            try:
                req_criteria_id = int(req_c_val)
            except (ValueError, TypeError):
                return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)
            criteria_item = CriteriaItem.objects.filter(pk=req_criteria_id).select_related('category').first()
            if not criteria_item:
                criteria_item = resolve_criteria_item(req_criteria_id, request.data, submission=submission)
            if not criteria_item:
                return Response({"error": f"Criteria item with id '{req_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
            req_criteria_id = criteria_item.id
            if not is_evaluator_assigned_to_item(user, req_criteria_id):
                return Response(
                    {"error": "Unauthorized: Evaluator is not assigned to evaluate this criteria category."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif user.is_superuser or user_role == 'admin':
            pass
        else:
            return Response(
                {"error": "You do not have permission to modify this submission."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Validate criteriaId existence on update
        if 'criteriaId' in request.data and request.data.get('criteriaId') is not None:
            try:
                target_criteria_id = int(request.data['criteriaId'])
            except (ValueError, TypeError):
                return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)
            c_check = CriteriaItem.objects.filter(pk=target_criteria_id).select_related('category').first()
            if not c_check:
                c_check = resolve_criteria_item(target_criteria_id, request.data, submission=submission)
            if not c_check:
                return Response({"error": f"Criteria item with id '{target_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
            target_criteria_id = c_check.id
        else:
            target_criteria_id = int(submission.criteria_id)
            c_check = CriteriaItem.objects.filter(pk=target_criteria_id).select_related('category').first()
            if not c_check:
                c_check = resolve_criteria_item(target_criteria_id, request.data, submission=submission)
            if c_check:
                target_criteria_id = c_check.id

        # Subcategory-Level Access Control on update — delegate to access_rules
        # Class representatives have full verification authority over all 12 categories for their class.
        # Subcategory submission constraints only apply to student authors modifying evidence.
        if user_role == 'student' and c_check and not is_rep_for_class:
            from users.access_rules import validate_subcategory_access
            ev_to_check = request.data.get('evidence', submission.evidence)
            if isinstance(ev_to_check, str):
                import json as _json
                try:
                    ev_to_check = _json.loads(ev_to_check)
                except Exception:
                    ev_to_check = {}
            ev_to_check = ev_to_check if isinstance(ev_to_check, dict) else {}
            upd_access_allowed, upd_access_error = validate_subcategory_access(user, c_check, ev_to_check)
            if not upd_access_allowed:
                return Response({"error": upd_access_error}, status=status.HTTP_403_FORBIDDEN)

        # Validate academicYear on update
        if 'academicYear' in request.data and request.data.get('academicYear') is not None:
            upd_ay = str(request.data['academicYear']).strip()
            if not re.match(r'^\d{4}-\d{4}$', upd_ay) or len(upd_ay) > 20:
                return Response({"error": "academicYear must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
            if not (AcademicYear.objects.filter(year=upd_ay).exists() or CriteriaVersion.objects.filter(academic_year=upd_ay).exists()):
                return Response({"error": f"Academic year '{upd_ay}' does not exist in the system."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate string lengths on update
        if 'description' in request.data:
            clean_d = str(request.data['description']).strip()
            if not clean_d:
                return Response({"error": "description cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
            if len(clean_d) > 5000:
                return Response({"error": "description cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'remarks' in request.data and request.data.get('remarks') and len(str(request.data['remarks'])) > 2000:
            return Response({"error": "remarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'proof' in request.data and request.data.get('proof'):
            raw_proof = str(request.data.get('proof')).strip()
            if '..' in raw_proof or raw_proof.startswith('/') or raw_proof.startswith('\\'):
                return Response({"error": "Invalid proof path specification."}, status=status.HTTP_400_BAD_REQUEST)
            if len(raw_proof) > 255:
                return Response({"error": "proof cannot exceed 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'eventId' in request.data and request.data.get('eventId') and len(str(request.data['eventId'])) > 100:
            return Response({"error": "eventId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'certificateId' in request.data and request.data.get('certificateId') and len(str(request.data['certificateId'])) > 100:
            return Response({"error": "certificateId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'repRemarks' in request.data and request.data.get('repRemarks') and len(str(request.data['repRemarks'])) > 2000:
            return Response({"error": "repRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'teacherRemarks' in request.data and request.data.get('teacherRemarks') and len(str(request.data['teacherRemarks'])) > 2000:
            return Response({"error": "teacherRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'evaluatorRemarks' in request.data and request.data.get('evaluatorRemarks') and len(str(request.data['evaluatorRemarks'])) > 2000:
            return Response({"error": "evaluatorRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)

        # Check online courses & UPSC/PSC limits on update if changing criteriaId or status
        target_status = request.data.get('status', submission.status)
        online_item_ids = SubmissionService.get_online_courses_item_ids()
        if target_criteria_id in online_item_ids and target_status != 'Rejected':
            existing_count = Submission.objects.filter(
                user=submission.user,
                criteria_id__in=online_item_ids
            ).exclude(id=submission.id).exclude(status='Rejected').count()
            if existing_count >= 3:
                return Response(
                    {"error": "Maximum 3 online courses can be submitted per student. Limit of 3 reached."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        upsc_item_ids = SubmissionService.get_upsc_psc_item_ids()
        if target_criteria_id in upsc_item_ids and target_status != 'Rejected':
            existing_count = Submission.objects.filter(
                user=submission.user,
                criteria_id__in=upsc_item_ids
            ).exclude(id=submission.id).exclude(status='Rejected').count()
            if existing_count >= 3:
                return Response(
                    {"error": "Maximum 3 submissions allowed for UPSC/PSC Exam Participation. Limit of 3 reached."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Duplicate detection check on update
        target_cert_id = request.data.get('certificateId') or request.data.get('eventId', submission.certificate_id or submission.event_id)
        if not target_cert_id and isinstance(request.data.get('evidence'), dict):
            ev = request.data.get('evidence')
            target_cert_id = ev.get('certificateId') or ev.get('certId') or ev.get('startupGovtId') or ev.get('eventId')

        target_proof_h = request.data.get('proofHash')
        if not target_proof_h and isinstance(request.data.get('evidence'), dict):
            target_proof_h = request.data.get('evidence').get('proofHash')
        if not target_proof_h and 'proof' in request.data and request.data.get('proof'):
            target_proof_h = hashlib.sha256(str(request.data.get('proof')).encode('utf-8')).hexdigest()
        if not target_proof_h:
            target_proof_h = submission.proof_hash

        dup_err = SubmissionService.check_duplicate_submission(
            user=submission.user,
            criteria_id=target_criteria_id,
            academic_year=request.data.get('academicYear', submission.academic_year),
            certificate_id=target_cert_id,
            proof_hash=target_proof_h,
            description=request.data.get('description', submission.description),
            submission_id=submission.id
        )
        if dup_err:
            return Response({"error": dup_err}, status=status.HTTP_400_BAD_REQUEST)

        # Academic Grade Breakdown: AcademicGradeBreakdown is the authoritative relational source
        gb_data = request.data.get('grade_breakdown')
        if not gb_data and isinstance(request.data.get('evidence'), dict) and "grades" in request.data.get('evidence'):
            ev_g = request.data.get('evidence').get('grades') or {}
            gb_data = {
                "s_grade_count": ev_g.get("S", 0),
                "a_plus_grade_count": ev_g.get("APlus", 0),
                "a_grade_count": ev_g.get("A", 0),
                "other_pass_count": ev_g.get("OtherPass", 0) or ev_g.get("B", 0),
                "failed_count": ev_g.get("Fail", 0),
                "total_students": request.data.get('evidence').get("totalStudents", 0)
            }

        upd_ev = request.data.get('evidence', submission.evidence)
        gb_sync_defaults = None

        crit_for_acad = CriteriaItem.objects.filter(pk=target_criteria_id).select_related('category').first()
        acad_cat_code = ((crit_for_acad.category.code or '') if (crit_for_acad and crit_for_acad.category) else '').strip().lower()
        acad_cat_name = ((crit_for_acad.category.category or '') if (crit_for_acad and crit_for_acad.category) else '').strip().lower()
        is_acad_update = (acad_cat_code == 'cat-academics' or 'academic' in acad_cat_name or (crit_for_acad and crit_for_acad.type == 'academic_grades') or isinstance(gb_data, dict))

        if is_acad_update:
            is_submitting = target_status in ('Submitted', 'SUBMITTED', 'Pending Rep Verification', 'Pending', 'Student Rep Verified')
            check_proof = request.data.get('proof') or request.data.get('proof_url') or submission.proof
            check_desc = request.data.get('description', submission.description)
            is_valid_acad, parsed_acad, acad_err = validate_academic_submission_payload(
                request_data=request.data,
                evidence=upd_ev,
                gb_data=gb_data,
                proof=check_proof,
                description=check_desc,
                is_submit=is_submitting
            )
            if not is_valid_acad:
                return Response({"error": acad_err}, status=status.HTTP_400_BAD_REQUEST)

            s_cnt = parsed_acad.get('count_90_above') or 0
            ap_cnt = parsed_acad.get('count_80_90') or 0
            a_cnt = parsed_acad.get('count_70_80') or 0
            oth_cnt = 0
            if isinstance(gb_data, dict):
                oth_cnt = int(gb_data.get('other_pass_count', 0) or 0)
            fail_cnt = parsed_acad.get('count_fail') or 0
            t_students = parsed_acad.get('total_students') or 0
            if t_students <= 0 and (s_cnt or ap_cnt or a_cnt or oth_cnt or fail_cnt):
                t_students = s_cnt + ap_cnt + a_cnt + oth_cnt + fail_cnt

            passed = max(0, t_students - fail_cnt)
            if oth_cnt <= 0 and passed > (s_cnt + ap_cnt + a_cnt):
                oth_cnt = passed - (s_cnt + ap_cnt + a_cnt)

            g_sum = s_cnt + ap_cnt + a_cnt + oth_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)

            if g_sum != t_students:
                return Response({"error": f"Sum of grade counts ({g_sum}) must strictly equal total students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            if parsed_acad.get('pass_percentage') is not None:
                pass_pct = parsed_acad['pass_percentage']
            elif g_sum > 0 and t_students > 0:
                pass_pct = round((passed / float(t_students)) * 100.0, 2)
            else:
                pass_pct = 0.0

            gb_sync_defaults = {
                "s_grade_count": s_cnt,
                "a_plus_grade_count": ap_cnt,
                "a_grade_count": a_cnt,
                "other_pass_count": oth_cnt,
                "failed_count": fail_cnt,
                "class_pass_percentage": pass_pct,
                "total_students": t_students
            }
            # Ensure evidence does not duplicate grade data in JSON
            if isinstance(upd_ev, dict):
                upd_ev = dict(upd_ev)
                upd_ev.pop("grades", None)
                upd_ev.pop("markBreakdown", None)
                upd_ev.pop("classPassPercentage", None)
                upd_ev.pop("totalStudents", None)
                upd_ev.pop("passCount", None)

        # 1. Validate Workflow State Machine Transition & Role Scope
        req_status = request.data.get('status', submission.status)
        is_valid, err_msg, stage_num, stage_name = validate_workflow_transition(
            submission=submission,
            target_status_input=req_status,
            user=user,
            data=request.data,
            role_context=req_role_context
        )
        if not is_valid:
            if err_msg.startswith("Invalid workflow state transition"):
                status_code = status.HTTP_400_BAD_REQUEST
            elif "Unauthorized" in err_msg or "record has been locked" in err_msg.lower():
                status_code = status.HTTP_403_FORBIDDEN
            else:
                status_code = status.HTTP_400_BAD_REQUEST
            return Response({"error": err_msg}, status=status_code)

        # 2. Authorization Check: Students cannot assign marks.
        if user and user_role == 'student' and 'marks' in request.data and request.data.get('marks') is not None:
            return Response(
                {"error": "Unauthorized: Students cannot assign evaluation marks."},
                status=status.HTTP_403_FORBIDDEN
            )

        # 3. Score Range & Type Bounds Verification
        if 'marks' in request.data and request.data.get('marks') is not None:
            try:
                req_marks = float(request.data.get('marks'))
            except (ValueError, TypeError):
                return Response(
                    {"error": "Invalid marks value provided."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            criteria_item = CriteriaItem.objects.filter(pk=target_criteria_id).select_related('category').first()
            if criteria_item:
                target_ev = request.data.get('evidence', submission.evidence)
                is_manual = getattr(criteria_item, 'is_manual_eval', False) or (criteria_item.category and getattr(criteria_item.category, 'is_manual_eval', False))
                if is_manual:
                    if req_marks < 0 or req_marks > 500:
                        return Response(
                            {"error": "Manual evaluation score must be between 0 and 500."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                else:
                    allowed_min, allowed_max, details = SubmissionService.get_criteria_allowed_bounds(criteria_item, target_ev)

                    is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (allowed_min < 0)
                    if req_marks < 0 and not is_negative:
                        return Response(
                            {"error": f"Score ({req_marks}) cannot be negative for non-penalty criteria."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if req_marks > (allowed_max + 1e-5):
                        return Response(
                            {"error": f"Requested score ({req_marks}) exceeds the maximum allowed limit ({allowed_max}) for criteria '{criteria_item.title}'{details}."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if allowed_min is not None and req_marks < (allowed_min - 1e-5):
                        return Response(
                            {"error": f"Requested score ({req_marks}) is below the minimum allowed limit ({allowed_min}) for criteria '{criteria_item.title}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

        # 4. Save updates and record audit log inside atomic transaction
        try:
            with transaction.atomic():
                submission = Submission.objects.select_for_update().get(pk=pk)
                if submission.status == 'Locked' and not (user_role == 'admin' or getattr(user, 'is_superuser', False)):
                    return Response(
                        {"error": "This submission record has been locked and cannot be modified."},
                        status=status.HTTP_403_FORBIDDEN
                    )

                if gb_sync_defaults:
                    AcademicGradeBreakdown.objects.update_or_create(
                        submission=submission,
                        defaults=gb_sync_defaults
                    )

                target_status = request.data.get('status', submission.status)

                extra_updates = {}
                if 'criteriaId' in request.data or submission.criteria_id != target_criteria_id:
                    extra_updates['criteria_id'] = target_criteria_id
                if c_check and (not submission.category_id or submission.category_id != c_check.category_id):
                    extra_updates['category'] = c_check.category
                if 'academicYear' in request.data:
                    extra_updates['academic_year'] = request.data.get('academicYear')
                if 'description' in request.data:
                    extra_updates['description'] = request.data.get('description')
                if 'proof' in request.data:
                    extra_updates['proof'] = request.data.get('proof')
                if 'eventId' in request.data:
                    extra_updates['event_id'] = request.data.get('eventId')
                if 'evidence' in request.data:
                    extra_updates['evidence'] = upd_ev
                if 'start_date' in request.data or 'startDate' in request.data:
                    extra_updates['start_date'] = request.data.get('start_date') or request.data.get('startDate')
                elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                    ev = request.data.get('evidence')
                    if ev.get('startDate') or ev.get('examDate'):
                        extra_updates['start_date'] = ev.get('startDate') or ev.get('examDate')

                if 'end_date' in request.data or 'endDate' in request.data:
                    extra_updates['end_date'] = request.data.get('end_date') or request.data.get('endDate')
                elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                    ev = request.data.get('evidence')
                    if ev.get('endDate'):
                        extra_updates['end_date'] = ev.get('endDate')

                remarks_val = (
                    request.data.get('remarks') or
                    request.data.get('teacherRemarks') or
                    request.data.get('repRemarks') or
                    request.data.get('evaluatorRemarks') or
                    ""
                )
                marks_val = request.data.get('marks')

                if 'subcategoryId' in request.data or 'subcategory_id' in request.data:
                    raw_sub = request.data.get('subcategoryId') or request.data.get('subcategory_id')
                    try:
                        extra_updates['subcategory_id'] = int(raw_sub)
                    except (ValueError, TypeError):
                        pass

                crit_to_check = CriteriaItem.objects.filter(pk=extra_updates.get('criteria_id', submission.criteria_id)).select_related('category').first()
                if crit_to_check and crit_to_check.category:
                    cat_code_check = (crit_to_check.category.code or '').strip().lower()
                    if cat_code_check not in ('cat-academics', 'cat-career-advancement'):
                        from users.models import SubCategory
                        sub_to_find = extra_updates.get('subcategory_id', submission.subcategory_id)
                        matched_sub = SubCategory.find_subcategory(
                            subcategory_id=sub_to_find,
                            category_id=getattr(crit_to_check.category, 'id', None),
                            category_code=cat_code_check,
                            criteria_item=crit_to_check,
                            evidence=upd_ev
                        )
                        if matched_sub:
                            extra_updates['subcategory_id'] = matched_sub.id
                            count_val = 1
                            if crit_to_check.type == 'count' or 'count' in upd_ev:
                                try:
                                    count_val = max(1, int(upd_ev.get('count', 1)))
                                except (ValueError, TypeError):
                                    count_val = 1
                            calculated_val = float(matched_sub.default_marks) * count_val
                            if target_status in ('Approved', 'APPROVED', 'Evaluated', 'Locked'):
                                extra_updates['calculated_marks'] = calculated_val
                                if marks_val is None or user_role == 'student':
                                    marks_val = calculated_val
                            else:
                                extra_updates['calculated_marks'] = 0.0
                                marks_val = None
                    elif cat_code_check == 'cat-academics' or crit_to_check.type == 'academic_grades':
                        ev_for_score = dict(upd_ev or {})
                        if isinstance(gb_data, dict):
                            for k in ('count_90_above', 'count_80_90', 'count_70_80', 'count_fail', 'pass_percentage'):
                                if k in gb_data:
                                    ev_for_score[k] = gb_data[k]
                                elif k in request.data:
                                    ev_for_score[k] = request.data[k]
                        calculated_val = float(calculate_submission_score(crit_to_check, ev_for_score))
                        if target_status in ('Approved', 'APPROVED', 'Evaluated', 'Locked'):
                            extra_updates['calculated_marks'] = calculated_val
                            if marks_val is None or user_role == 'student':
                                marks_val = calculated_val
                        else:
                            extra_updates['calculated_marks'] = 0.0
                            marks_val = None

                action_type = request.data.get('actionType') or request.data.get('action')

                submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks_val,
                    marks=marks_val,
                    request=request,
                    role_context=req_role_context,
                    actionType=action_type,
                    **extra_updates
                )
        except IntegrityError as e:
            logger.warning(f"IntegrityError updating submission #{pk}: {e}")
            return Response(
                {"error": "A duplicate submission with this certificate or proof document was already recorded in the system."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SubmissionService.serialize_submission_for_user(submission, user))

    def delete(self, request, pk):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        is_owner = (submission.user_id == user.id)
        is_admin = bool(getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)

        if is_owner:
            if submission.status in UNEDITABLE_BY_STUDENT_STATES:
                return Response(
                    {"error": f"Submissions in '{submission.status}' status cannot be deleted by students."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if submission.proof:
                safe_p = resolve_safe_private_path(submission.proof)
                if safe_p and os.path.isfile(safe_p):
                    try:
                        os.remove(safe_p)
                    except Exception:
                        pass
            submission.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        elif is_admin:
            if submission.proof:
                safe_p = resolve_safe_private_path(submission.proof)
                if safe_p and os.path.isfile(safe_p):
                    try:
                        os.remove(safe_p)
                    except Exception:
                        pass
            submission.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        else:
            return Response(
                {"error": "You do not have permission to delete this submission."},
                status=status.HTTP_403_FORBIDDEN
            )
