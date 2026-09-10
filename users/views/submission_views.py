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

from users.models import Submission, Class, AcademicYear, CriteriaItem, CriteriaVersion, AcademicGradeBreakdown
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


class SubmissionListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        user = request.user
        academic_year = request.query_params.get('academicYear')
        limit_param = request.query_params.get('limit')
        offset_param = request.query_params.get('offset', 0)

        queryset = SubmissionService.filter_submissions_for_user(
            user=user,
            academic_year=academic_year
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

        criteria_id = request.data.get('criteriaId')
        if not criteria_id:
            return Response({"error": "criteriaId is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            criteria_id_int = int(criteria_id)
        except (ValueError, TypeError):
            return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)

        criteria_item = CriteriaItem.objects.filter(pk=criteria_id_int).first()
        if not criteria_item:
            return Response({"error": f"Criteria item with id '{criteria_id_int}' does not exist."}, status=status.HTTP_404_NOT_FOUND)

        academic_year = request.data.get('academicYear', '2025-2026')
        clean_ay = str(academic_year).strip()
        if not re.match(r'^\d{4}-\d{4}$', clean_ay) or len(clean_ay) > 20:
            return Response({"error": "academicYear must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not (AcademicYear.objects.filter(year=clean_ay).exists() or CriteriaVersion.objects.filter(academic_year=clean_ay).exists()):
            return Response({"error": f"Academic year '{clean_ay}' does not exist in the system."}, status=status.HTTP_400_BAD_REQUEST)
        academic_year = clean_ay

        description = request.data.get('description', '')
        clean_desc = str(description).strip()
        if not clean_desc:
            return Response({"error": "description is required and cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
        if len(clean_desc) > 5000:
            return Response({"error": "description cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        description = clean_desc

        raw_status = request.data.get('status')
        user_role = getattr(user, 'role', None)

        # Validate workflow status on creation: client cannot set privileged or terminal states
        if user_role == 'student':
            if raw_status is not None and raw_status not in ('Draft', 'Submitted', 'Pending Verification', 'Pending Rep Verification'):
                return Response(
                    {"error": "Unauthorized: Students cannot create submissions in verified, evaluated, or locked status."},
                    status=status.HTTP_403_FORBIDDEN
                )
            status_val = raw_status if raw_status else 'Draft'
        else:
            status_val = raw_status if raw_status else 'Pending Verification'
            if status_val not in dict(Submission.STATUS_CHOICES):
                return Response({"error": f"Invalid status: '{status_val}'."}, status=status.HTTP_400_BAD_REQUEST)

        remarks = request.data.get('remarks', '')
        if remarks and len(str(remarks)) > 2000:
            return Response({"error": "remarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)

        proof = request.data.get('proof', '')
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

        # Check submission limits for Online Courses and UPSC/PSC Exams
        try:
            criteria_id_int = int(criteria_id)
            online_item_ids = SubmissionService.get_online_courses_item_ids()
            if criteria_id_int in online_item_ids:
                existing_count = Submission.objects.filter(
                    user=user,
                    criteria_id__in=online_item_ids
                ).exclude(status='Rejected').count()
                if existing_count >= 3:
                    return Response(
                        {"error": "Maximum 3 online courses can be submitted per student. Limit of 3 reached."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            upsc_item_ids = SubmissionService.get_upsc_psc_item_ids()
            if criteria_id_int in upsc_item_ids:
                existing_count = Submission.objects.filter(
                    user=user,
                    criteria_id__in=upsc_item_ids
                ).exclude(status='Rejected').count()
                if existing_count >= 3:
                    return Response(
                        {"error": "Maximum 3 submissions allowed for UPSC/PSC Exam Participation. Limit of 3 reached."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
        except (ValueError, TypeError):
            pass

        # Academic Grade Breakdown Validation & Auto-Calculation
        if isinstance(evidence, dict) and "grades" in evidence:
            grades_data = evidence.get("grades") or {}
            s_cnt = int(grades_data.get("S", 0))
            ap_cnt = int(grades_data.get("APlus", 0))
            a_cnt = int(grades_data.get("A", 0))
            fail_cnt = int(grades_data.get("Fail", 0))
            t_students = int(evidence.get("totalStudents", 0))

            if s_cnt < 0 or ap_cnt < 0 or a_cnt < 0 or fail_cnt < 0 or t_students < 0:
                return Response({"error": "Grade counts and total students cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

            g_sum = s_cnt + ap_cnt + a_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)
                evidence["totalStudents"] = t_students

            if g_sum > t_students:
                return Response({"error": f"Sum of student grades ({g_sum}) exceeds total class students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            pass_pct = round((passed / float(t_students)) * 100.0, 2)
            evidence["classPassPercentage"] = pass_pct
            evidence["passCount"] = passed

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
        if not gb_data and isinstance(evidence, dict) and "grades" in evidence:
            ev_g = evidence.get('grades') or {}
            gb_data = {
                "s_grade_count": ev_g.get("S", 0),
                "a_plus_grade_count": ev_g.get("APlus", 0),
                "a_grade_count": ev_g.get("A", 0),
                "other_pass_count": ev_g.get("OtherPass", 0) or ev_g.get("B", 0),
                "failed_count": ev_g.get("Fail", 0),
                "total_students": evidence.get("totalStudents", 0)
            }

        # Clean evidence to avoid duplicating grade data in JSON
        clean_evidence = dict(evidence or {}) if isinstance(evidence, dict) else {}
        clean_evidence.pop("grades", None)
        clean_evidence.pop("markBreakdown", None)
        clean_evidence.pop("classPassPercentage", None)
        clean_evidence.pop("totalStudents", None)
        clean_evidence.pop("passCount", None)

        if marks is None:
            c_item = CriteriaItem.objects.filter(pk=criteria_id_int).first()
            if c_item:
                marks = calculate_submission_score(c_item, evidence)

        try:
            with transaction.atomic():
                submission = Submission.objects.create(
                    user=user,
                    criteria_id=criteria_id_int,
                    criteria_version=active_cv,
                    academic_year=academic_year,
                    description=description,
                    status=status_val,
                    remarks=remarks,
                    marks=marks,
                    proof=proof,
                    proof_hash=proof_h,
                    certificate_id=cert_id,
                    event_id=event_id,
                    evidence=clean_evidence,
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
        if user.is_superuser or user_role in ('admin', 'iqac', 'evaluation'):
            pass
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if not (is_class_teacher or is_same_dept):
                return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        elif user_role == 'student':
            if submission.user_id != user.id:
                if not is_user_student_rep(user):
                    return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
                rep_classes = list(Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                ))
                if user.class_name and user.class_name not in rep_classes:
                    rep_classes.append(user.class_name)
                if not (submission.user and submission.user.class_name in rep_classes):
                    return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)

        serializer = SubmissionSerializer(submission)
        resp_data = dict(serializer.data)
        is_owner = bool(user and getattr(user, 'is_authenticated', False) and submission.user_id == user.id)
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'iqac', 'faculty', 'evaluation') or
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
        is_rep = is_user_student_rep(user)

        if user_role == 'student':
            if is_owner:
                if submission.status in UNEDITABLE_BY_STUDENT_STATES:
                    return Response(
                        {"error": f"Submission cannot be edited in '{submission.status}' status."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                req_status = request.data.get('status')
                if req_status and req_status != submission.status:
                    if req_status in ('Approved', 'Verified', 'Teacher Verified', 'Student Rep Verified', 'Evaluated', 'Locked'):
                        return Response(
                            {"error": "Unauthorized: Students cannot alter verification or evaluation status."},
                            status=status.HTTP_403_FORBIDDEN
                        )
                if request.data.get('marks') is not None and request.data.get('marks') != submission.marks:
                    return Response(
                        {"error": "Unauthorized: Students cannot assign evaluation marks."},
                        status=status.HTTP_403_FORBIDDEN
                    )
            elif is_rep:
                rep_classes = list(Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                ))
                if user.class_name and user.class_name not in rep_classes:
                    rep_classes.append(user.class_name)
                if not (submission.user and submission.user.class_name in rep_classes):
                    return Response(
                        {"error": "Student representative is not assigned to this student's class."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                allowed_rep_statuses = {'Student Rep Verified', 'Correction Requested', 'Rejected', 'Pending Rep Verification', 'Pending', 'Submitted'}
                req_status = request.data.get('status')
                if req_status and req_status not in allowed_rep_statuses:
                    return Response(
                        {"error": f"Student representatives cannot transition submission to '{req_status}'."},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                return Response(
                    {"error": "You do not have permission to modify this submission."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
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
        elif user_role == 'evaluation':
            req_c_val = request.data.get('criteriaId', submission.criteria_id)
            try:
                req_criteria_id = int(req_c_val)
            except (ValueError, TypeError):
                return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)
            criteria_item = CriteriaItem.objects.filter(pk=req_criteria_id).select_related('category').first()
            if not criteria_item:
                return Response({"error": f"Criteria item with id '{req_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
            if criteria_item and criteria_item.category and criteria_item.category.evaluators:
                cat_evaluators = [str(e).strip().lower() for e in criteria_item.category.evaluators if e]
                user_email = (user.email or '').strip().lower()
                if cat_evaluators and user_email not in cat_evaluators:
                    return Response(
                        {"error": "Unauthorized: Evaluator is not assigned to evaluate this criteria category."},
                        status=status.HTTP_403_FORBIDDEN
                    )
        elif user.is_superuser or user_role in ('admin', 'iqac'):
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
            c_check = CriteriaItem.objects.filter(pk=target_criteria_id).first()
            if not c_check:
                return Response({"error": f"Criteria item with id '{target_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
        else:
            target_criteria_id = int(submission.criteria_id)

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
        if isinstance(gb_data, dict):
            s_cnt = int(gb_data.get('s_grade_count', 0) or 0)
            ap_cnt = int(gb_data.get('a_plus_grade_count', 0) or 0)
            a_cnt = int(gb_data.get('a_grade_count', 0) or 0)
            oth_cnt = int(gb_data.get('other_pass_count', 0) or 0)
            fail_cnt = int(gb_data.get('failed_count', 0) or 0)
            t_students = int(gb_data.get('total_students', 0) or 0)

            if s_cnt < 0 or ap_cnt < 0 or a_cnt < 0 or oth_cnt < 0 or fail_cnt < 0 or t_students < 0:
                return Response({"error": "Grade counts and total students cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            if oth_cnt <= 0 and passed > (s_cnt + ap_cnt + a_cnt):
                oth_cnt = passed - (s_cnt + ap_cnt + a_cnt)

            g_sum = s_cnt + ap_cnt + a_cnt + oth_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)

            if g_sum != t_students:
                return Response({"error": f"Sum of grade counts ({g_sum}) must strictly equal total students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            pass_pct = round((passed / float(t_students)) * 100.0, 2)
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
            data=request.data
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

            criteria_item = CriteriaItem.objects.filter(pk=target_criteria_id).first()
            if criteria_item:
                target_ev = request.data.get('evidence', submission.evidence)
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
                if submission.status == 'Locked' and not (user_role in ('admin', 'iqac') or getattr(user, 'is_superuser', False)):
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
                if 'criteriaId' in request.data:
                    extra_updates['criteria_id'] = int(request.data.get('criteriaId'))
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

                submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks_val,
                    marks=marks_val,
                    request=request,
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
