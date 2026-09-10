import hashlib
import logging
from django.db.models import Q

logger = logging.getLogger(__name__)


class SubmissionService:
    """
    Focused domain service for submission business logic, including deduplication,
    criteria version locking, category bounds computation, and visibility filtering.
    """

    @staticmethod
    def get_online_courses_item_ids():
        from users.models import CriteriaItem
        items = CriteriaItem.objects.filter(
            Q(category__code__iexact='cat-online-courses') |
            Q(category__category__icontains='online course')
        )
        ids = set(items.values_list('id', flat=True))
        ids.update([201, 202, 203])
        return ids

    @staticmethod
    def get_upsc_psc_item_ids():
        from users.models import CriteriaItem
        items = CriteriaItem.objects.filter(
            Q(title__icontains='UPSC') |
            Q(title__icontains='PSC') |
            Q(title__icontains='Participation in Relevant Exam')
        )
        ids = set(items.values_list('id', flat=True))
        ids.add(404)
        return ids

    @staticmethod
    def check_duplicate_submission(user, criteria_id, academic_year, certificate_id, proof_hash, description, submission_id=None):
        from users.models import Submission

        if not user:
            return None

        # 1. Certificate ID / Event ID Match
        if certificate_id and str(certificate_id).strip():
            cert_clean = str(certificate_id).strip()
            qs = Submission.objects.filter(user=user, certificate_id__iexact=cert_clean).exclude(status='Rejected')
            if submission_id:
                qs = qs.exclude(id=submission_id)
            if qs.exists():
                return f"⚠️ Duplicate detected: Certificate / Identifier '{cert_clean}' has already been submitted for evaluation."

        # 2. SHA-256 Proof File Hash Match
        if proof_hash and str(proof_hash).strip():
            p_clean = str(proof_hash).strip()
            qs = Submission.objects.filter(user=user, proof_hash=p_clean).exclude(status='Rejected')
            if submission_id:
                qs = qs.exclude(id=submission_id)
            if qs.exists():
                return "⚠️ Duplicate detected: An identical proof document has already been submitted for evaluation."

        # 3. Exact Criteria + Description Activity Fingerprint Match
        if description and str(description).strip() and criteria_id:
            desc_clean = str(description).strip()
            try:
                c_id = int(criteria_id)
            except (ValueError, TypeError):
                c_id = abs(int(hashlib.md5(str(criteria_id).encode()).hexdigest(), 16)) % 1000000

            qs = Submission.objects.filter(
                user=user,
                criteria_id=c_id,
                academic_year=academic_year,
                description__iexact=desc_clean
            ).exclude(status='Rejected')
            if submission_id:
                qs = qs.exclude(id=submission_id)
            if qs.exists():
                return f"⚠️ Duplicate detected: A submission with identical activity description has already been submitted for this criteria in {academic_year}."

        return None

    @staticmethod
    def get_criteria_allowed_bounds(criteria_item, evidence=None):
        """
        Determines the authoritative allowed score bounds [allowed_min, allowed_max, details] for a criteria item.
        Delegates directly to scoring_engine.
        """
        from users.scoring_engine import get_criteria_allowed_bounds as _get_bounds
        return _get_bounds(criteria_item, evidence)

    @staticmethod
    def filter_submissions_for_user(user, academic_year=None):
        from users.models import Submission, Class, UserGroupModel
        from users.services.user_service import UserService
        from django.db.models import Q

        queryset = Submission.objects.select_related('user', 'user__class_name', 'user__department').all()

        if getattr(user, 'role', None) == 'student':
            if UserService.is_user_student_rep(user):
                rep_classes = UserService.get_student_rep_classes(user)

                peer_q = Q(user__class_name__in=rep_classes)
                for rc in rep_classes:
                    if rc.name:
                        peer_q |= Q(user__class_name__name__iexact=rc.name)

                user_email = (user.email or '').strip().lower()
                rep_parsed = UserService.parse_email_code(user_email)
                if rep_parsed:
                    code_str = f"{str(rep_parsed['batch_year'])[-2:]}{rep_parsed['level_char']}{rep_parsed['email_code']}"
                    peer_q |= Q(user__email__icontains=f".{code_str}")

                queryset = queryset.filter(Q(user=user) | peer_q)
            else:
                queryset = queryset.filter(user=user)
        elif getattr(user, 'role', None) == 'faculty':
            # Faculty (class teacher) sees submissions from their assigned class
            # AND falls back to department if no class assigned
            advised_classes = Class.objects.filter(class_teacher=user)
            dept_q = Q(user__department=user.department) if user.department else Q(pk__in=[])
            if advised_classes.exists() or user.department:
                queryset = queryset.filter(Q(user__class_name__in=advised_classes) | dept_q)
            else:
                queryset = queryset.none()

        if academic_year:
            queryset = queryset.filter(academic_year=academic_year)

        return queryset

    @staticmethod
    def serialize_submission_for_user(s, user):
        from users.services.user_service import UserService
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'iqac', 'faculty', 'evaluation') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        is_owner = bool(user and getattr(user, 'is_authenticated', False) and s.user_id == user.id)
        can_see_eval_remarks = is_owner or is_staff_or_eval

        resolved_class_name = None
        if s.user:
            if s.user.class_name:
                resolved_class_name = s.user.class_name.name
            elif s.user.email:
                parsed = UserService.parse_student_email(s.user.email)
                if parsed and parsed.get('class_name'):
                    resolved_class_name = parsed.get('class_name')

        user_display_name = None
        if s.user:
            if hasattr(s.user, 'name') and s.user.name:
                user_display_name = s.user.name
            elif s.user.first_name or s.user.last_name:
                user_display_name = f"{s.user.first_name} {s.user.last_name}".strip()
            elif s.user.email:
                user_display_name = UserService.parse_name_from_email(s.user.email)

        return {
            "id": s.id,
            "studentId": s.user.id if s.user else 1,
            "user_email": s.user.email if s.user else None,
            "userEmail": s.user.email if s.user else None,
            "user_name": user_display_name,
            "className": resolved_class_name,
            "class_name": resolved_class_name,
            "criteriaId": s.criteria_id,
            "academicYear": s.academic_year,
            "description": s.description,
            "status": s.status,
            "remarks": s.remarks,
            "marks": s.marks,
            "proof": s.proof,
            "eventId": s.event_id,
            "startDate": s.start_date,
            "start_date": s.start_date,
            "endDate": s.end_date,
            "end_date": s.end_date,
            "evaluatorVerified": s.evaluator_verified,
            "evidence": s.evidence,
            "verifiedByName": s.verified_by_name,
            "repVerifiedByName": s.rep_verified_by_name,
            "repRemarks": s.rep_remarks,
            "teacherVerifiedByName": s.teacher_verified_by_name,
            "teacherRemarks": s.teacher_remarks,
            "evaluatorVerifiedByName": s.evaluator_verified_by_name if can_see_eval_remarks else None,
            "evaluatorRemarks": s.evaluator_remarks if can_see_eval_remarks else None
        }


# Standalone alias exports
check_duplicate_submission = SubmissionService.check_duplicate_submission
get_online_courses_item_ids = SubmissionService.get_online_courses_item_ids
get_upsc_psc_item_ids = SubmissionService.get_upsc_psc_item_ids
get_criteria_allowed_bounds = SubmissionService.get_criteria_allowed_bounds
