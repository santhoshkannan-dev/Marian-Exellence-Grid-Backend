"""
Unit and Integration Tests for Forward-Only Multi-Tier Verification Pipeline
=============================================================================
Tests:
1. Round 1: DQC / Student Rep Group (POST /api/verification/dqc/)
   - Both Student Reps and DQC Members can verify & forward
   - Forwarding transitions state to TEACHER_PENDING (no marks)
   - Passing marks payload is stripped and never awarded
   - Foreign class reps denied 403
   - Ineligible statuses denied 400
   - SEND_BACK and REJECT require remarks
   - APPROVE_AND_CREDIT is rejected

2. Round 2: Class Teacher Verification (POST /api/verification/teacher/)
   - Class teacher can verify & forward to EVALUATOR_PENDING
   - APPROVE_AND_CREDIT is strictly forbidden (403)
   - Passing marks is blocked / ignored
   - Non-class teacher denied 403
   - SEND_BACK and REJECT require remarks

3. Round 3: Evaluator Verification (POST /api/verification/evaluator/)
   - Category-assigned evaluator can APPROVE_AND_CREDIT with marks
   - Marks are credited to submission and class index ledger
   - Unassigned evaluator denied 403
   - Missing marks for APPROVE_AND_CREDIT denied 400

4. Dual-Role Isolation & End-to-End Pipeline
   - Submission flows DQC_PENDING -> TEACHER_PENDING -> EVALUATOR_PENDING -> APPROVED
   - VerificationLog records every step with appropriate verification_level
"""

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from users.models import (
    Department, Course, Class, User, AcademicYear,
    CriteriaCategory, CriteriaItem, Submission, ClassIndexResult,
    UserGroupModel, UserGroupMember, VerificationLog,
    EvaluatorCategoryAssignment, TeacherClassAssignment
)
from users.workflow import WorkflowState


class VerificationPipelineTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # 1. Academic Year
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        # 2. Department & Course
        self.dept = Department.objects.create(
            name='Computer Science',
            code='CS',
            email_prefix='c',
            level='UG'
        )
        self.course = Course.objects.create(
            department=self.dept,
            name='Bachelor of Computer Applications',
            abbreviation='BCA',
            email_code='bc',
            is_multi_batch=True,
            duration_years=3
        )

        # 3. Classes
        self.class_a = Class.objects.create(
            department=self.dept,
            course=self.course,
            name='III BCA A',
            section='A',
            year_number=3,
            academic_year='2025-2026'
        )
        self.class_b = Class.objects.create(
            department=self.dept,
            course=self.course,
            name='III BCA B',
            section='B',
            year_number=3,
            academic_year='2025-2026'
        )

        # 4. User Groups
        self.grp_dqc = UserGroupModel.objects.create(
            group_id='grp-dqc-student-rep',
            name='DQC Student Rep Group'
        )
        self.grp_reps = UserGroupModel.objects.create(
            group_id='grp-student-reps',
            name='Student Representatives'
        )

        # 5. Users
        # Student author
        self.student = User.objects.create(
            username='student.23ubc101@mariancollege.org',
            email='student.23ubc101@mariancollege.org',
            role='student',
            class_name=self.class_a,
            department=self.dept
        )

        # DQC Member for Class A
        self.dqc_member = User.objects.create(
            username='dqc.23ubc102@mariancollege.org',
            email='dqc.23ubc102@mariancollege.org',
            role='student',
            class_name=self.class_a,
            department=self.dept
        )
        self.class_a.dqc_member = self.dqc_member
        self.class_a.save()
        UserGroupMember.objects.create(
            group=self.grp_dqc,
            email=self.dqc_member.email,
            assigned_class=self.class_a
        )

        # Student Rep for Class A
        self.student_rep = User.objects.create(
            username='rep.23ubc103@mariancollege.org',
            email='rep.23ubc103@mariancollege.org',
            role='student',
            class_name=self.class_a,
            department=self.dept
        )
        UserGroupMember.objects.create(
            group=self.grp_reps,
            email=self.student_rep.email,
            assigned_class=self.class_a
        )

        # Foreign Rep (Class B)
        self.foreign_rep = User.objects.create(
            username='foreign.23ubc201@mariancollege.org',
            email='foreign.23ubc201@mariancollege.org',
            role='student',
            class_name=self.class_b,
            department=self.dept
        )
        UserGroupMember.objects.create(
            group=self.grp_reps,
            email=self.foreign_rep.email,
            assigned_class=self.class_b
        )

        # Class Teacher for Class A
        self.teacher = User.objects.create(
            username='teacher.cs@mariancollege.org',
            email='teacher.cs@mariancollege.org',
            role='faculty',
            department=self.dept
        )
        self.class_a.class_teacher = self.teacher
        self.class_a.save()
        TeacherClassAssignment.objects.create(
            teacher=self.teacher,
            class_obj=self.class_a,
            academic_year=2025,
            is_active=True
        )

        # Other Teacher (not assigned to Class A)
        self.other_teacher = User.objects.create(
            username='other.teacher@mariancollege.org',
            email='other.teacher@mariancollege.org',
            role='faculty',
            department=self.dept
        )

        # Evaluator
        self.evaluator = User.objects.create(
            username='evaluator.cs@mariancollege.org',
            email='evaluator.cs@mariancollege.org',
            role='evaluation',
            department=self.dept
        )

        # Unassigned Evaluator
        self.unassigned_evaluator = User.objects.create(
            username='unassigned.eval@mariancollege.org',
            email='unassigned.eval@mariancollege.org',
            role='evaluation',
            department=self.dept
        )

        # 5. Criteria Category & Item
        self.category = CriteriaCategory.objects.create(
            code='cat-certifications',
            category='Certifications',
            evaluators=[self.evaluator.email]
        )
        EvaluatorCategoryAssignment.objects.create(
            category=self.category,
            evaluator=self.evaluator
        )

        self.item = CriteriaItem.objects.create(
            category=self.category,
            title='Academic Certification',
            type='count',
            marks=10.0
        )

    def _create_submission(self, status_val='DQC_PENDING'):
        return Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            status=status_val,
            description='Test Submission',
            marks=None
        )

    # -----------------------------------------------------------------------
    # Round 1: DQC / Student Rep Group Tests
    # -----------------------------------------------------------------------

    def test_dqc_member_can_verify_and_forward_without_marks(self):
        """DQC Member can verify & forward; marks are never awarded at Round 1."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.dqc_member)

        payload = {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'DQC verification complete.',
            'marks': 10.0  # Should be completely ignored / stripped
        }
        res = self.client.post('/api/verification/dqc/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sub.refresh_from_db()
        self.assertIn(sub.status, ('TEACHER_PENDING', 'Student Rep Verified'))
        self.assertIsNone(sub.marks)

        # Check VerificationLog
        vlog = VerificationLog.objects.filter(submission_id=sub.id, verification_level='DQC').first()
        self.assertIsNotNone(vlog)
        self.assertEqual(vlog.action, 'VERIFY_AND_FORWARD')

    def test_student_rep_can_also_verify_and_forward(self):
        """Student Rep has identical Round 1 verification powers as DQC Member."""
        sub = self._create_submission('Pending Rep Verification')
        self.client.force_authenticate(user=self.student_rep)

        payload = {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Rep verification OK.'
        }
        res = self.client.post('/api/verification/dqc/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sub.refresh_from_db()
        self.assertIn(sub.status, ('TEACHER_PENDING', 'Student Rep Verified'))

    def test_dqc_foreign_class_rep_forbidden(self):
        """Student rep from Class B cannot verify Class A submission (403)."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.foreign_rep)

        payload = {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD'
        }
        res = self.client.post('/api/verification/dqc/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_dqc_send_back_requires_remarks(self):
        """SEND_BACK requires remarks."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.dqc_member)

        # Without remarks -> 400
        res = self.client.post('/api/verification/dqc/', {
            'submissionId': sub.id,
            'action': 'SEND_BACK'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # With remarks -> 200
        res2 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub.id,
            'action': 'SEND_BACK',
            'remarks': 'Evidence document is blurry.'
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIn(sub.status, ('SENT_BACK', 'Correction Requested'))

    def test_dqc_cannot_execute_approve_and_credit(self):
        """DQC is strictly forbidden from executing APPROVE_AND_CREDIT."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.dqc_member)

        res = self.client.post('/api/verification/dqc/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # -----------------------------------------------------------------------
    # Round 2: Class Teacher Verification Tests
    # -----------------------------------------------------------------------

    def test_teacher_can_verify_and_forward_to_evaluator(self):
        """Class teacher can verify & forward TEACHER_PENDING to EVALUATOR_PENDING."""
        sub = self._create_submission('TEACHER_PENDING')
        self.client.force_authenticate(user=self.teacher)

        payload = {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Teacher verified and forwarded.'
        }
        res = self.client.post('/api/verification/teacher/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sub.refresh_from_db()
        self.assertIn(sub.status, ('EVALUATOR_PENDING', 'Teacher Verified'))
        self.assertIsNone(sub.marks)

        vlog = VerificationLog.objects.filter(submission_id=sub.id, verification_level='CLASS_TEACHER').first()
        self.assertIsNotNone(vlog)
        self.assertEqual(vlog.action, 'VERIFY_AND_FORWARD')

    def test_teacher_cannot_award_marks_approve_and_credit(self):
        """APPROVE_AND_CREDIT is strictly blocked for Class Teachers (403)."""
        sub = self._create_submission('TEACHER_PENDING')
        self.client.force_authenticate(user=self.teacher)

        res = self.client.post('/api/verification/teacher/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'marks': 10.0
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_unassigned_teacher_forbidden(self):
        """Teacher not assigned to this class cannot verify (403)."""
        sub = self._create_submission('TEACHER_PENDING')
        self.client.force_authenticate(user=self.other_teacher)

        res = self.client.post('/api/verification/teacher/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_on_wrong_status_rejected(self):
        """Teacher cannot verify submission that hasn't passed Round 1 (400)."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.teacher)

        res = self.client.post('/api/verification/teacher/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # -----------------------------------------------------------------------
    # Round 3: Evaluator Verification & Marks Crediting Tests
    # -----------------------------------------------------------------------

    def test_evaluator_can_approve_and_credit_marks(self):
        """Evaluator approves submission, awards marks, and updates class ledger."""
        sub = self._create_submission('EVALUATOR_PENDING')
        self.client.force_authenticate(user=self.evaluator)

        payload = {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'marks': 10.0,
            'remarks': 'Approved full marks.'
        }
        res = self.client.post('/api/verification/evaluator/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sub.refresh_from_db()
        self.assertIn(sub.status, ('Approved', 'APPROVED', 'Evaluated'))
        self.assertEqual(sub.marks, 10)
        self.assertEqual(sub.calculated_marks, 10.0)

        # Check VerificationLog
        vlog = VerificationLog.objects.filter(submission_id=sub.id, verification_level='EVALUATOR').first()
        self.assertIsNotNone(vlog)
        self.assertEqual(vlog.action, 'APPROVE_AND_CREDIT')

        # Check Class Leaderboard Ledger was credited
        ledger = ClassIndexResult.objects.filter(class_name=self.class_a, academic_year=self.ay).first()
        self.assertIsNotNone(ledger)

    def test_evaluator_missing_marks_rejected(self):
        """APPROVE_AND_CREDIT without marks is rejected (400)."""
        sub = self._create_submission('EVALUATOR_PENDING')
        self.client.force_authenticate(user=self.evaluator)

        res = self.client.post('/api/verification/evaluator/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unassigned_evaluator_forbidden(self):
        """Evaluator not assigned to this criteria category is rejected (403)."""
        sub = self._create_submission('EVALUATOR_PENDING')
        self.client.force_authenticate(user=self.unassigned_evaluator)

        res = self.client.post('/api/verification/evaluator/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'marks': 10.0
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # -----------------------------------------------------------------------
    # End-to-End Pipeline Test
    # -----------------------------------------------------------------------

    def test_full_three_round_pipeline(self):
        """End-to-End verification: DQC -> Teacher -> Evaluator -> Approved."""
        sub = self._create_submission('DQC_PENDING')

        # 1. Round 1: DQC
        self.client.force_authenticate(user=self.dqc_member)
        r1 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Round 1 passed.'
        }, format='json')
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIn(sub.status, ('TEACHER_PENDING', 'Student Rep Verified'))
        self.assertIsNone(sub.marks)

        # 2. Round 2: Teacher
        self.client.force_authenticate(user=self.teacher)
        r2 = self.client.post('/api/verification/teacher/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Round 2 passed.'
        }, format='json')
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIn(sub.status, ('EVALUATOR_PENDING', 'Teacher Verified'))
        self.assertIsNone(sub.marks)

        # 3. Round 3: Evaluator
        self.client.force_authenticate(user=self.evaluator)
        r3 = self.client.post('/api/verification/evaluator/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'marks': 10.0,
            'remarks': 'Round 3 evaluated and approved.'
        }, format='json')
        self.assertEqual(r3.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIn(sub.status, ('Approved', 'APPROVED', 'Evaluated'))
        self.assertEqual(sub.marks, 10)

        # 4. Verify full audit trail in VerificationLog
        logs = list(VerificationLog.objects.filter(submission_id=sub.id).order_by('id'))
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[0].verification_level, 'DQC')
        self.assertEqual(logs[1].verification_level, 'CLASS_TEACHER')
        self.assertEqual(logs[2].verification_level, 'EVALUATOR')

    # -----------------------------------------------------------------------
    # Extended Scope: Student Representatives Group Verification Authority
    # -----------------------------------------------------------------------

    def test_student_rep_full_authority_across_all_categories(self):
        """
        Student Representatives have full verification authority over ALL
        activity categories (1 through 12) for submissions of their assigned class.
        """
        # Category 1: Academics
        cat_acad = CriteriaCategory.objects.create(category='Academics', code='cat-academics')
        item_acad = CriteriaItem.objects.create(category=cat_acad, title='Semester Results', type='fixed', marks=15.0)

        # Category 9: Programs Organized
        cat_prog = CriteriaCategory.objects.create(category='Programs Organized', code='cat-programs-organized')
        item_prog = CriteriaItem.objects.create(category=cat_prog, title='Seminar Organizing', type='fixed', marks=10.0)

        # Category 12: Documentation
        cat_doc = CriteriaCategory.objects.create(category='Documentation', code='cat-documentation')
        item_doc = CriteriaItem.objects.create(category=cat_doc, title='Annual Documentation', type='fixed', marks=20.0)

        self.client.force_authenticate(user=self.student_rep)

        for item in (item_acad, item_prog, item_doc):
            sub = Submission.objects.create(
                user=self.student,
                criteria_id=item.id,
                category=item.category,
                class_obj=self.class_a,
                academic_year='2025-2026',
                status='DQC_PENDING',
                description=f'Class peer submission for {item.category.category}'
            )

            res = self.client.post('/api/verification/dqc/', {
                'submissionId': sub.id,
                'action': 'VERIFY_AND_FORWARD',
                'remarks': f'Verified category {item.category.category}'
            }, format='json')

            self.assertEqual(res.status_code, status.HTTP_200_OK, f"Failed verifying {item.category.category}")
            sub.refresh_from_db()
            self.assertEqual(sub.status, 'TEACHER_PENDING')
            self.assertIsNone(sub.marks)
            self.assertEqual(sub.rep_verified_by_name, self.student_rep.get_full_name() or self.student_rep.username)

    def test_student_rep_round1_action_effects(self):
        """
        Verify & Forward: DQC_PENDING -> TEACHER_PENDING (no marks)
        Send Back: DQC_PENDING -> SENT_BACK (remarks mandatory)
        Reject: DQC_PENDING -> REJECTED (remarks mandatory)
        """
        self.client.force_authenticate(user=self.student_rep)

        # 1. Verify & Forward
        sub1 = self._create_submission('DQC_PENDING')
        res1 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub1.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Approved by class rep.'
        }, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        sub1.refresh_from_db()
        self.assertEqual(sub1.status, 'TEACHER_PENDING')
        self.assertIsNone(sub1.marks)

        # 2. Send Back with remarks
        sub2 = self._create_submission('DQC_PENDING')
        res2 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub2.id,
            'action': 'SEND_BACK',
            'remarks': 'Please re-upload clearer certificate.'
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        sub2.refresh_from_db()
        self.assertEqual(sub2.status, 'SENT_BACK')

        # 3. Reject with remarks
        sub3 = self._create_submission('DQC_PENDING')
        res3 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub3.id,
            'action': 'REJECT',
            'remarks': 'Certificate not recognized.'
        }, format='json')
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        sub3.refresh_from_db()
        self.assertEqual(sub3.status, 'REJECTED')

        # 4. Remarks mandatory for REJECT
        sub4 = self._create_submission('DQC_PENDING')
        res4 = self.client.post('/api/verification/dqc/', {
            'submissionId': sub4.id,
            'action': 'REJECT'
        }, format='json')
        self.assertEqual(res4.status_code, status.HTTP_400_BAD_REQUEST)

    def test_round1_endpoint_alias(self):
        """POST /api/verification/round1/ works as an alias for Round 1 verification."""
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.student_rep)

        res = self.client.post('/api/verification/round1/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD',
            'remarks': 'Verified via /round1/ route.'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'TEACHER_PENDING')

    def test_unauthorized_user_denied_round1_with_exact_contract(self):
        """
        Users without 'Student Representatives' or 'DQC Student Rep Group' receive:
        403 {"error": "Access Denied", "message": "You do not have Round 1 verification authority."}
        """
        sub = self._create_submission('DQC_PENDING')
        self.client.force_authenticate(user=self.student)  # Regular student

        res = self.client.post('/api/verification/dqc/', {
            'submissionId': sub.id,
            'action': 'VERIFY_AND_FORWARD'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get('error'), 'Access Denied')
        self.assertEqual(res.data.get('message'), 'You do not have Round 1 verification authority.')

    def test_user_groups_property_resolution(self):
        """User.user_groups property dynamically resolves official group names."""
        self.assertIn('Student Representatives', self.student_rep.user_groups)
        self.assertIn('DQC Student Rep Group', self.dqc_member.user_groups)
        self.assertEqual(self.student.user_groups, [])

    def test_round1_queue_filtering_for_student_rep(self):
        """
        SubmissionService.filter_submissions_for_user returns DQC_PENDING submissions
        belonging to the student representative's assigned class across all categories.
        """
        from users.services.submission_service import SubmissionService

        # Submission in Class A, status DQC_PENDING -> should be included
        sub_a = self._create_submission('DQC_PENDING')

        # Submission in Class A, status TEACHER_PENDING -> should NOT be in Round 1 queue
        sub_a_teacher = self._create_submission('TEACHER_PENDING')

        # Submission in Class B, status DQC_PENDING -> should NOT be visible to Class A rep
        student_b = User.objects.create(
            username='student.b@mariancollege.org',
            email='student.b@mariancollege.org',
            role='student',
            class_name=self.class_b,
            department=self.dept
        )
        sub_b = Submission.objects.create(
            user=student_b,
            criteria_id=self.item.id,
            class_obj=self.class_b,
            academic_year='2025-2026',
            status='DQC_PENDING',
            description='Class B submission'
        )

        qs = SubmissionService.filter_submissions_for_user(self.student_rep, queue='round1')
        sub_ids = list(qs.values_list('id', flat=True))

        self.assertIn(sub_a.id, sub_ids)
        self.assertNotIn(sub_a_teacher.id, sub_ids)
        self.assertNotIn(sub_b.id, sub_ids)

    def test_profile_endpoint_returns_user_groups(self):
        """GET /api/auth/profile/ includes user_groups in payload."""
        self.client.force_authenticate(user=self.student_rep)
        res = self.client.get('/api/auth/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('user_groups', res.data)
        self.assertIn('Student Representatives', res.data['user_groups'])
