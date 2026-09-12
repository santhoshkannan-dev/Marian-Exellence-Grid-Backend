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
        # Only fire when the description matches AND no new/distinct proof is provided.
        # If the student supplies a different proof URL/hash, it is a distinct submission.
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
                # If a new proof_hash is provided and does NOT match any existing row,
                # the student has added different evidence — do NOT block as duplicate.
                if proof_hash and str(proof_hash).strip():
                    new_hash = str(proof_hash).strip()
                    # Check whether any of the matching rows share the same proof hash
                    same_proof_exists = qs.filter(proof_hash=new_hash).exists()
                    if not same_proof_exists:
                        # Different proof on same description — allow through
                        return None

                return (
                    f"\u26a0\ufe0f Duplicate detected: A submission with identical activity description "
                    f"has already been submitted for this criteria in {academic_year}. "
                    f"If this is a different activity, please use a distinct description."
                )

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
    def filter_submissions_for_user(user, academic_year=None, queue=None, status_filter=None, role_context=None):
        from users.models import Submission, Class, TeacherClassAssignment, EvaluatorCategoryAssignment, CriteriaCategory, CriteriaItem
        from users.services.user_service import UserService

        queryset = Submission.objects.select_related('user', 'user__class_name', 'user__department', 'class_obj', 'category').prefetch_related('verification_logs').all()
        user_role = getattr(user, 'role', None)
        norm_context = (role_context or '').strip().lower() if role_context else None

        # Check dual-role status
        auth_info = UserService.get_user_roles_and_dual_status(user) if user and getattr(user, 'is_authenticated', False) else {}
        has_dual = auth_info.get('has_dual_role', False)

        # 1. Explicit Role Context (or queue-derived context)
        is_admin_user = (user_role == 'admin' or getattr(user, 'is_superuser', False))
        active_ctx = norm_context
        if not active_ctx:
            if queue in ('teacher', 'round2'):
                active_ctx = 'teacher'
            elif queue in ('evaluator', 'round3'):
                active_ctx = 'evaluator'
            elif queue in ('dqc', 'round1'):
                active_ctx = 'dqc'
            elif is_admin_user:
                active_ctx = 'admin'
            elif user_role in ('evaluation', 'evaluator'):
                active_ctx = 'evaluator'
            elif has_dual:
                active_ctx = 'teacher'  # Default dual-role landing context
            elif user_role in ('faculty', 'staff') or getattr(user, 'is_staff', False):
                active_ctx = 'teacher'

        # 2. Verification Queue Filtering
        if active_ctx == 'admin' or (is_admin_user and not active_ctx and not queue):
            # Admin has oversight across all submissions
            pass
        elif queue in ('dqc', 'round1', 'student_rep', 'rep') or active_ctx in ('dqc', 'round1', 'student_rep', 'rep'):
            is_round1 = (
                user_role == 'student' and (
                    UserService.is_user_round1_verifier(user) or
                    UserService.is_user_student_rep(user) or
                    UserService.is_user_dqc_rep(user)
                )
            ) or user_role == 'admin' or getattr(user, 'is_superuser', False)

            if is_round1:
                from users.models import UserGroupMember
                user_email = (getattr(user, 'email', '') or '').strip().lower()

                # Collect all assigned classes for this Student Rep / DQC member
                assigned_class_ids = set()
                if getattr(user, 'class_name_id', None):
                    assigned_class_ids.add(user.class_name_id)

                if user_email:
                    rep_memberships = UserGroupMember.objects.filter(
                        Q(group__group_id__in=['grp-student-reps', 'grp-dqc-student-rep']) |
                        Q(group__name__in=['Student Representatives', 'DQC Student Rep Group']),
                        email__iexact=user_email,
                        assigned_class__isnull=False
                    ).values_list('assigned_class_id', flat=True)
                    assigned_class_ids.update(rep_memberships)

                dqc_class_ids = Class.objects.filter(
                    Q(dqc_member=user) | (Q(dqc_member__email__iexact=user_email) if user_email else Q(pk__in=[]))
                ).values_list('id', flat=True)
                assigned_class_ids.update(dqc_class_ids)

                if assigned_class_ids or user_role == 'admin' or getattr(user, 'is_superuser', False):
                    class_filter = (
                        (Q(class_obj_id__in=assigned_class_ids) | Q(user__class_name_id__in=assigned_class_ids))
                        if not (user_role == 'admin' or getattr(user, 'is_superuser', False))
                        else Q()
                    )
                    queryset = queryset.filter(
                        class_filter,
                        # Canonical DQC intake states only — 'Submitted' is the student self-submission
                        # state, not the DQC queue. Legacy 'Submitted' records are migrated to DQC_PENDING.
                        status__in=['DQC_PENDING', 'Pending Rep Verification']
                    )
                else:
                    queryset = queryset.none()
            else:
                queryset = queryset.none()
        elif active_ctx == 'teacher':
            # Class Teacher View: Strictly restricted to assigned class
            assigned_classes = Class.objects.filter(
                Q(class_teacher=user) |
                Q(teacher_assignments__teacher=user, teacher_assignments__is_active=True) |
                (Q(id=user.class_name_id) if getattr(user, 'class_name_id', None) else Q(pk__in=[]))
            ).distinct()
            if assigned_classes.exists():
                queryset = queryset.filter(
                    Q(class_obj__in=assigned_classes) | Q(user__class_name__in=assigned_classes)
                )
                if queue in ('teacher', 'round2'):
                    queryset = queryset.filter(status__in=['TEACHER_PENDING', 'Student Rep Verified'])
            else:
                queryset = queryset.none()
        elif active_ctx == 'evaluator':
            # Evaluator View: Strictly restricted to assigned categories across all classes
            if is_admin_user:
                if queue in ('evaluator', 'round3'):
                    queryset = queryset.filter(status__in=['EVALUATOR_PENDING', 'Teacher Verified'])
            else:
                eval_cats = CriteriaCategory.objects.filter(
                    Q(evaluator_assignments__evaluator=user) |
                    Q(evaluator_assignments__member__email__iexact=user.email) |
                    Q(evaluators__contains=user.email)
                ).distinct()
                if eval_cats.exists():
                    eval_items = CriteriaItem.objects.filter(category__in=eval_cats).values_list('id', flat=True)
                    queryset = queryset.filter(
                        Q(category__in=eval_cats) | Q(criteria_id__in=eval_items)
                    )
                    if queue in ('evaluator', 'round3'):
                        queryset = queryset.filter(status__in=['EVALUATOR_PENDING', 'Teacher Verified'])
                else:
                    queryset = queryset.none()
        else:
            # Standard Role Scoping (non-staff / single-role fallback)
            if is_admin_user:
                pass
            elif user_role == 'student':
                if UserService.is_user_student_rep(user) or UserService.is_user_dqc_rep(user) or UserService.is_user_round1_verifier(user):
                    user_email = (getattr(user, 'email', '') or '').strip().lower()
                    from users.models import UserGroupMember
                    rep_classes = Class.objects.filter(
                        Q(dqc_member=user) |
                        (Q(dqc_member__email__iexact=user_email) if user_email else Q(pk__in=[])) |
                        (Q(assigned_group_members__email__iexact=user_email) if user_email else Q(pk__in=[])) |
                        (Q(id=user.class_name_id) if getattr(user, 'class_name_id', None) else Q(pk__in=[]))
                    ).distinct()
                    queryset = queryset.filter(Q(user=user) | Q(user__class_name__in=rep_classes) | Q(class_obj__in=rep_classes))
                else:
                    queryset = queryset.filter(user=user)
            elif user_role in ('evaluation', 'evaluator'):
                eval_cats = CriteriaCategory.objects.filter(
                    Q(evaluator_assignments__evaluator=user) |
                    Q(evaluator_assignments__member__email__iexact=user.email) |
                    Q(evaluators__contains=user.email)
                ).distinct()
                if eval_cats.exists():
                    eval_items = CriteriaItem.objects.filter(category__in=eval_cats).values_list('id', flat=True)
                    queryset = queryset.filter(Q(category__in=eval_cats) | Q(criteria_id__in=eval_items))
                else:
                    queryset = queryset.none()
            elif user_role in ('faculty', 'staff') or getattr(user, 'is_staff', False):
                assigned_classes = Class.objects.filter(
                    Q(class_teacher=user) | Q(teacher_assignments__teacher=user, teacher_assignments__is_active=True)
                )
                dept_q = Q(user__department=user.department) if user.department else Q(pk__in=[])
                if assigned_classes.exists() or user.department:
                    queryset = queryset.filter(Q(class_obj__in=assigned_classes) | Q(user__class_name__in=assigned_classes) | dept_q)
                else:
                    queryset = queryset.none()

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if academic_year:
            queryset = queryset.filter(academic_year=academic_year)

        return queryset

    @staticmethod
    def serialize_submission_for_user(s, user):
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'faculty', 'evaluation', 'staff') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        is_owner = bool(user and getattr(user, 'is_authenticated', False) and s.user_id == user.id)
        can_see_eval_remarks = is_owner or is_staff_or_eval

        # Construct verification_logs audit sequence
        v_logs = []
        for log in s.verification_logs.all().order_by('timestamp'):
            v_logs.append({
                "id": log.id,
                "verification_level": log.verification_level,
                "verificationLevel": log.verification_level,
                "verifier_id": log.verifier_id,
                "verifier_name": log.verifier_name,
                "verifierName": log.verifier_name,
                "action": log.action,
                "remarks": log.remarks,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "date": log.timestamp.strftime('%Y-%m-%d %H:%M') if log.timestamp else "",
            })

        # Backfill verifier names and remarks from audit logs if empty
        rep_name = s.rep_verified_by_name
        rep_rem = s.rep_remarks
        teacher_name = s.teacher_verified_by_name
        teacher_rem = s.teacher_remarks
        eval_name = s.evaluator_verified_by_name
        eval_rem = s.evaluator_remarks

        for log in v_logs:
            if log["verification_level"] == "DQC":
                rep_name = rep_name or log["verifier_name"]
                rep_rem = rep_rem or log["remarks"]
            elif log["verification_level"] == "CLASS_TEACHER":
                teacher_name = teacher_name or log["verifier_name"]
                teacher_rem = teacher_rem or log["remarks"]
            elif log["verification_level"] == "EVALUATOR":
                eval_name = eval_name or log["verifier_name"]
                eval_rem = eval_rem or log["remarks"]

        class_name = (s.class_obj.name if s.class_obj else (s.user.class_name.name if s.user and s.user.class_name else None))
        class_id = s.class_obj_id or (s.user.class_name_id if s.user else None)
        category_id = s.category_id or (s.criteria_item.category_id if getattr(s, 'criteria_item', None) else None)
        sub_date = s.submission_date.isoformat() if s.submission_date else (s.created_at.isoformat() if s.created_at else None)

        sub_obj = getattr(s, 'subcategory', None)
        sub_name = sub_obj.subcategory_name if sub_obj else None
        sub_default = float(sub_obj.default_marks) if sub_obj else None

        return {
            "id": s.id,
            "studentId": s.user.id if s.user else 1,
            "student_id": s.user.id if s.user else 1,
            "classId": class_id,
            "class_id": class_id,
            "categoryId": category_id,
            "category_id": category_id,
            "subcategoryId": s.subcategory_id or s.criteria_id,
            "subcategory_id": s.subcategory_id or s.criteria_id,
            "subcategory_name": sub_name,
            "subcategoryName": sub_name,
            "subcategory_default_marks": sub_default,
            "subcategoryDefaultMarks": sub_default,
            "submissionDate": sub_date,
            "submission_date": sub_date,
            "user_email": s.user.email if s.user else None,
            "userEmail": s.user.email if s.user else None,
            "user_name": s.user.get_full_name() if s.user else None,
            "className": class_name,
            "class_name": class_name,
            "criteriaId": s.criteria_id,
            "academicYear": s.academic_year,
            "description": s.description,
            "status": s.status,
            "remarks": s.remarks,
            "marks": s.marks,
            "calculatedMarks": s.calculated_marks if s.calculated_marks is not None else s.marks,
            "calculated_marks": s.calculated_marks if s.calculated_marks is not None else s.marks,
            "isManualEval": s.is_manual_eval,
            "is_manual_eval": s.is_manual_eval,
            "proof": s.proof or s.proof_url,
            "proofUrl": s.proof_url or s.proof,
            "proof_url": s.proof_url or s.proof,
            "eventId": s.event_id,
            "startDate": s.start_date,
            "start_date": s.start_date,
            "endDate": s.end_date,
            "end_date": s.end_date,
            "evaluatorVerified": s.evaluator_verified,
            "evidence": s.evidence or s.submission_metadata,
            "submissionMetadata": s.submission_metadata or s.evidence,
            "submission_metadata": s.submission_metadata or s.evidence,
            "verifiedByName": s.verified_by_name,
            "repVerifiedByName": rep_name,
            "repRemarks": rep_rem,
            "teacherVerifiedByName": teacher_name,
            "teacherRemarks": teacher_rem,
            "evaluatorVerifiedByName": eval_name if can_see_eval_remarks else None,
            "evaluatorRemarks": eval_rem if can_see_eval_remarks else None,
            "verification_logs": v_logs,
            "verificationLogs": v_logs,
        }


# Standalone alias exports
check_duplicate_submission = SubmissionService.check_duplicate_submission
get_online_courses_item_ids = SubmissionService.get_online_courses_item_ids
get_upsc_psc_item_ids = SubmissionService.get_upsc_psc_item_ids
get_criteria_allowed_bounds = SubmissionService.get_criteria_allowed_bounds
