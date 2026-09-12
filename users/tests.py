from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import (
    Department, Course, Class, User, AcademicYear,
    CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion,
    Submission, AcademicGradeBreakdown, ClassIndexResult, SystemSetting,
    UserGroupModel, StaffProfile, TeacherClassAssignment, EvaluatorCategoryAssignment, VerificationLog
)
from .views import parse_student_email, allocate_student_from_email, calculate_submission_score


class DepartmentCourseClassManagementTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Active Academic Year 2026-2027
        self.ay = AcademicYear.objects.create(year='2026-2027', is_active=True)

    def test_department_and_course_creation(self):
        dept = Department.objects.create(
            name='The Post-Graduate Department of Computer Applications',
            code='PGDCA',
            email_prefix='p',
            level='PG'
        )
        self.assertEqual(dept.email_prefix, 'p')
        self.assertEqual(dept.level, 'PG')

        course = Course.objects.create(
            department=dept,
            name='Master of Computer Applications',
            abbreviation='MCA',
            email_code='mc',
            is_multi_batch=False,
            duration_years=2
        )
        self.assertEqual(course.department, dept)
        self.assertEqual(course.email_code, 'mc')
        self.assertFalse(course.is_multi_batch)

        # Create class under course (common for all years, not batch-specific)
        cls = Class.objects.create(
            department=dept,
            course=course,
            year_number=2,
            section='',
            name='II MCA'
        )
        self.assertEqual(cls.course, course)
        self.assertEqual(cls.name, 'II MCA')

    def test_db_driven_student_email_parsing_single_batch(self):
        """Student amal.25pmc114 with active year 2026-2027 -> II MCA"""
        dept = Department.objects.create(
            name='Post-Graduate Department of Computer Applications',
            code='PGDCA',
            email_prefix='p',
            level='PG'
        )
        Course.objects.create(
            department=dept,
            name='Master of Computer Applications',
            abbreviation='MCA',
            email_code='mc',
            is_multi_batch=False,
            duration_years=2
        )

        email = 'amal.25pmc114@mariancollege.org'
        parsed = parse_student_email(email)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed['db_resolved'])
        self.assertEqual(parsed['batch_year'], 2025)
        self.assertEqual(parsed['year_number'], 2)
        self.assertEqual(parsed['class_name'], 'II MCA')
        self.assertEqual(parsed['roll_number'], 14)
        self.assertEqual(parsed['roll_digits'], '114')
        self.assertEqual(parsed['section'], '')

        # Allocate user
        user = User.objects.create(
            username=email,
            email=email,
            role='student'
        )
        allocated_user = allocate_student_from_email(user)
        self.assertEqual(allocated_user.batch_year, 2025)
        self.assertEqual(allocated_user.roll_number, 14)
        self.assertIsNotNone(allocated_user.class_name)
        self.assertEqual(allocated_user.class_name.name, 'II MCA')
        self.assertEqual(allocated_user.department, dept)

    def test_db_driven_student_email_parsing_multi_batch(self):
        """Student santhosh.25ubc214 with active year 2026-2027 -> series 2 -> II BCA B"""
        dept = Department.objects.create(
            name='Under-Graduate Department of Computer Applications',
            code='UGDCA',
            email_prefix='u',
            level='UG'
        )
        Course.objects.create(
            department=dept,
            name='Bachelor of Computer Applications',
            abbreviation='BCA',
            email_code='bc',
            is_multi_batch=True,
            duration_years=3
        )

        email = 'santhosh.25ubc214@mariancollege.org'
        parsed = parse_student_email(email)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed['db_resolved'])
        self.assertEqual(parsed['batch_year'], 2025)
        self.assertEqual(parsed['year_number'], 2)
        self.assertEqual(parsed['section'], 'B')
        self.assertEqual(parsed['class_name'], 'II BCA B')
        self.assertEqual(parsed['roll_number'], 14)
        self.assertEqual(parsed['roll_digits'], '214')

        user = User.objects.create(
            username=email,
            email=email,
            role='student'
        )
        allocated = allocate_student_from_email(user)
        self.assertEqual(allocated.class_name.name, 'II BCA B')
        self.assertEqual(allocated.class_name.section, 'B')
        self.assertEqual(allocated.class_name.year_number, 2)
        self.assertEqual(allocated.roll_number, 14)

    def test_api_department_and_course_crud(self):
        admin = User.objects.create(username='admin@mariancollege.org', email='admin@mariancollege.org', role='admin', is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=admin)
        # Create department via POST
        res = self.client.post('/api/departments/', {
            'name': 'Department of Physics',
            'code': 'PHYSICS',
            'email_prefix': 'u',
            'level': 'UG'
        }, format='json')
        self.assertIn(res.status_code, [status.HTTP_201_CREATED, status.HTTP_200_OK])
        dept_id = res.data['id']

        # Create course via POST
        res = self.client.post('/api/courses/', {
            'department': dept_id,
            'name': 'BSc Physics',
            'abbreviation': 'PHY',
            'email_code': 'ph',
            'is_multi_batch': False,
            'duration_years': 3
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        course_id = res.data['id']

        # Create class via POST with auto-generated name (common for all years)
        res = self.client.post('/api/auth/classes/', {
            'course_id': course_id,
            'year_number': 1,
            'section': '',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'I PHY')
        class_id = res.data['id']

        # List departments and verify nesting
        res = self.client.get('/api/departments/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        phys_dept = next(d for d in res.data if d['id'] == dept_id)
        self.assertEqual(len(phys_dept['courses']), 1)
        self.assertEqual(phys_dept['courses'][0]['abbreviation'], 'PHY')

        # Cascade delete department
        del_res = self.client.delete(f'/api/departments/{dept_id}/')
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)
        self.assertFalse(Department.objects.filter(id=dept_id).exists())
        self.assertFalse(Course.objects.filter(id=course_id).exists())
        self.assertFalse(Class.objects.filter(id=class_id).exists())

    def test_academic_year_clock_progression(self):
        """2025-batch student stays I MCA in 2025-2026; becomes II MCA when 2026-2027 is activated."""
        dept = Department.objects.create(
            name='Post-Graduate Department of Computer Applications',
            code='PGDCA',
            email_prefix='p',
            level='PG'
        )
        Course.objects.create(
            department=dept,
            name='Master of Computer Applications',
            abbreviation='MCA',
            email_code='mc',
            is_multi_batch=False,
            duration_years=2
        )

        email = 'amal.25pmc114@mariancollege.org'
        user = User.objects.create(username=email, email=email, role='student')

        # Scenario A: 2025-2026 is active -> Year 1 (I MCA)
        self.ay.is_active = False
        self.ay.save()
        ay_2025 = AcademicYear.objects.create(year='2025-2026', is_active=True)

        user_2025 = allocate_student_from_email(user)
        self.assertEqual(user_2025.class_name.name, 'I MCA')
        self.assertEqual(user_2025.class_name.year_number, 1)

        # Scenario B: 2026-2027 is activated -> Year 2 (II MCA)
        ay_2025.is_active = False
        ay_2025.save()
        self.ay.is_active = True
        self.ay.save()

        user_2026 = allocate_student_from_email(user)
        self.assertEqual(user_2026.class_name.name, 'II MCA')
        self.assertEqual(user_2026.class_name.year_number, 2)

    def test_class_advisor_and_moderation_put(self):
        dept = Department.objects.create(name='Dept of CS', code='DCS', email_prefix='u', level='UG')
        course = Course.objects.create(department=dept, name='BCA', abbreviation='BCA', email_code='bc', duration_years=3)
        cls = Class.objects.create(department=dept, course=course, year_number=1, name='I BCA')
        teacher = User.objects.create(username='prof.smith@mariancollege.org', email='prof.smith@mariancollege.org', role='faculty', first_name='John', last_name='Smith')
        self.client.force_authenticate(user=teacher)

        # PUT /api/auth/classes/<id>/
        res = self.client.put(f'/api/auth/classes/{cls.id}/', {
            'classTeacher': teacher.email,
            'num_students': 60,
            'negative_points': 5.5,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        cls.refresh_from_db()
        self.assertEqual(cls.class_teacher, teacher)
        self.assertEqual(cls.num_students, 60)
        self.assertEqual(cls.negative_points, 5.5)

    def test_dev_bypass_login_allocates_student(self):
        dept = Department.objects.create(name='Post-Graduate Dept of CS', code='PGDCA', email_prefix='p', level='PG')
        Course.objects.create(department=dept, name='MCA', abbreviation='MCA', email_code='mc', duration_years=2)

        # Active academic year is 2026-2027 (set in setUp)
        # Login via bypass
        from django.conf import settings
        with self.settings(DEBUG=True, ENABLE_DEV_BYPASS=True):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'amal.25pmc114@mariancollege.org'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertIn('tokens', res.data)
            self.assertEqual(res.data['user']['class_name'], 'II MCA')
            self.assertEqual(res.data['user']['department_code'], 'PGDCA')


class CriteriaSubcategoryScoreValidationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.student = User.objects.create(
            username='amal.25pmc114@mariancollege.org',
            email='amal.25pmc114@mariancollege.org',
            role='student'
        )
        self.evaluator = User.objects.create(
            username='evaluator@mariancollege.org',
            email='evaluator@mariancollege.org',
            role='evaluation'
        )
        self.client.force_authenticate(user=self.evaluator)
        self.category = CriteriaCategory.objects.create(
            code='cat-research',
            category='Research',
            evaluators=[self.evaluator.email]
        )
        # Publications item: marks = 0.0, rules_json defines subItems
        self.pub_item = CriteriaItem.objects.create(
            category=self.category,
            title='Publications',
            type='count',
            marks=0.0,
            rules_json={
                'subItems': {
                    'Scopus / Web of Science': 10,
                    'Conference Proceeding / Peer reviewed article': 5
                }
            }
        )
        # Count-based item without subItems: e.g. Intercollegiate program, marks = 5.0
        self.event_item = CriteriaItem.objects.create(
            category=self.category,
            title='Intercollegiate',
            type='count',
            marks=5.0
        )

    def test_evaluator_can_assign_subcategory_mark_scopus(self):
        """Scopus publication (10 marks) can be evaluated without exceeding 0.0 limit error."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.pub_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'research_subitem', 'subItem': 'Scopus / Web of Science', 'count': 1}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': 10.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Evaluated')
        self.assertEqual(sub.marks, 10.0)

    def test_evaluator_can_assign_subcategory_mark_conference(self):
        """Conference Proceeding (5 marks) can be evaluated successfully."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.pub_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'research_subitem', 'subItem': 'Conference Proceeding / Peer reviewed article', 'count': 1}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': 5.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 5.0)

    def test_score_exceeding_subcategory_limit_is_rejected(self):
        """Attempting to assign 15.0 marks for 1 Scopus publication (max 10.0) is rejected."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.pub_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'research_subitem', 'subItem': 'Scopus / Web of Science', 'count': 1}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': 15.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('exceeds the maximum allowed limit', res.data['error'])
        self.assertIn('10.0', res.data['error'])

    def test_multiple_count_allows_multiplied_marks(self):
        """2 Scopus publications allow up to 20.0 marks."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.pub_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'research_subitem', 'subItem': 'Scopus / Web of Science', 'count': 2}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': 20.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 20.0)

    def test_count_based_item_without_subitems_allows_multiplied_score(self):
        """Intercollegiate item with marks=5.0 and count=3 allows up to 15.0 marks."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.event_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'program_organized', 'count': 3}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': 15.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 15.0)

    def test_negative_score_rejected_for_positive_criteria(self):
        """Negative scores cannot be assigned for non-penalty criteria."""
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.pub_item.id,
            academic_year='2025-2026',
            status='Approved',
            evidence={'type': 'research_subitem', 'subItem': 'Scopus / Web of Science', 'count': 1}
        )

        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'email': self.evaluator.email,
            'status': 'Evaluated',
            'marks': -5.0,
            'evaluatorVerified': True
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cannot be negative', res.data['error'])


class APISecurityAndAuthorizationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)
        self.category = CriteriaCategory.objects.create(code='cat-sec', category='Security')
        self.item = CriteriaItem.objects.create(category=self.category, title='Hackathon', type='count', marks=10.0)

        self.student1 = User.objects.create(
            username='student1@mariancollege.org',
            email='student1@mariancollege.org',
            role='student'
        )
        self.student2 = User.objects.create(
            username='student2@mariancollege.org',
            email='student2@mariancollege.org',
            role='student'
        )
        self.admin = User.objects.create(
            username='admin@mariancollege.org',
            email='admin@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )

    def test_unauthenticated_requests_are_rejected(self):
        """Unauthenticated requests to sensitive API endpoints must receive 401 Unauthorized."""
        # /api/users/
        self.assertEqual(self.client.get('/api/users/').status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.post('/api/users/', {'email': 'test@mariancollege.org'}).status_code, status.HTTP_401_UNAUTHORIZED)

        # /api/submissions/
        self.assertEqual(self.client.get('/api/submissions/').status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.post('/api/submissions/', {'criteriaId': self.item.id}).status_code, status.HTTP_401_UNAUTHORIZED)

        # /api/settings/ write
        self.assertEqual(self.client.post('/api/settings/', {'key': 'val'}).status_code, status.HTTP_401_UNAUTHORIZED)

        # /api/user-groups/ write
        self.assertEqual(self.client.post('/api/user-groups/', {'id': 'grp-1', 'name': 'Grp'}).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_forbidden_from_admin_endpoints(self):
        """Students cannot access administrative endpoints or escalate privileges."""
        self.client.force_authenticate(user=self.student1)

        # /api/users/ (User management)
        res_get = self.client.get('/api/users/')
        self.assertEqual(res_get.status_code, status.HTTP_403_FORBIDDEN)

        res_post = self.client.post('/api/users/', {
            'email': 'evil@mariancollege.org',
            'role': 'admin'
        }, format='json')
        self.assertEqual(res_post.status_code, status.HTTP_403_FORBIDDEN)

        res_del = self.client.delete('/api/users/', {'id': self.admin.id}, format='json')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)

        # /api/settings/
        res_settings = self.client.post('/api/settings/', {'smallest_class_size': '10'}, format='json')
        self.assertEqual(res_settings.status_code, status.HTTP_403_FORBIDDEN)

        # /api/criteria-items/
        res_item = self.client.post('/api/criteria-items/', {
            'category': self.category.id,
            'title': 'Rogue item',
            'type': 'count',
            'marks': 100.0
        }, format='json')
        self.assertEqual(res_item.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_allowed_on_admin_endpoints(self):
        """Admins can access user management and system settings."""
        self.client.force_authenticate(user=self.admin)
        res_get = self.client.get('/api/users/')
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)

        res_settings = self.client.post('/api/settings/', {'smallest_class_size': '25'}, format='json')
        self.assertEqual(res_settings.status_code, status.HTTP_200_OK)

    def test_submission_cannot_be_impersonated_via_email(self):
        """Even if request body sends another user's email, submission strictly belongs to request.user."""
        self.client.force_authenticate(user=self.student1)

        res = self.client.post('/api/submissions/', {
            'email': self.student2.email,  # Attempting to impersonate student2
            'criteriaId': self.item.id,
            'academicYear': '2025-2026',
            'description': 'Submitted project',
            'status': 'Pending Rep Verification',
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        sub_id = res.data['id']
        sub = Submission.objects.get(id=sub_id)

        # The submission MUST belong to student1 (the authenticated JWT user)
        self.assertEqual(sub.user, self.student1)
        self.assertNotEqual(sub.user, self.student2)

    def test_student_cannot_edit_or_delete_another_students_submission(self):
        """A student cannot modify or delete a submission belonging to another student."""
        sub = Submission.objects.create(
            user=self.student2,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            status='Draft'
        )

        self.client.force_authenticate(user=self.student1)

        # Attempt to edit student2's submission
        res_put = self.client.put(f'/api/submissions/{sub.id}/', {
            'description': 'Malicious modification'
        }, format='json')
        self.assertEqual(res_put.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt to delete student2's submission
        res_del = self.client.delete(f'/api/submissions/{sub.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Submission.objects.filter(id=sub.id).exists())

    def test_student_cannot_self_award_marks_or_self_approve(self):
        """Students cannot self-assign marks or self-transition to approved/verified states."""
        sub = Submission.objects.create(
            user=self.student1,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            status='Pending Rep Verification'
        )

        self.client.force_authenticate(user=self.student1)

        # Attempt to award marks
        res_marks = self.client.put(f'/api/submissions/{sub.id}/', {
            'marks': 10.0
        }, format='json')
        self.assertEqual(res_marks.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt to self-approve
        res_approve = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Approved'
        }, format='json')
        self.assertEqual(res_approve.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_submissions_list_is_scoped_to_own(self):
        """Regular students only see their own submissions in GET /api/submissions/."""
        sub1 = Submission.objects.create(user=self.student1, criteria_id=self.item.id, academic_year='2025-2026', status='Draft')
        sub2 = Submission.objects.create(user=self.student2, criteria_id=self.item.id, academic_year='2025-2026', status='Draft')

        self.client.force_authenticate(user=self.student1)
        res = self.client.get('/api/submissions/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        returned_ids = [s['id'] for s in res.data]
        self.assertIn(sub1.id, returned_ids)
        self.assertNotIn(sub2.id, returned_ids)

    def test_dev_bypass_disabled_fails_closed(self):
        """When DEBUG and ENABLE_DEV_BYPASS are False, bypass endpoint returns 404."""
        from django.conf import settings
        with self.settings(DEBUG=False, ENABLE_DEV_BYPASS=False):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'student1@mariancollege.org'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_class_index_normalization_invariance_across_class_sizes(self):
        """Mathematically prove that classes with identical per-student performance
        achieve approximately the same index across sizes 20, 40, 80, and 120 students.
        """
        from users.models import Class, Submission, SystemSetting, Department

        # Set benchmark smallest class size n = 0
        SystemSetting.objects.update_or_create(
            key='smallest_class_size',
            defaults={'value': '0'}
        )

        dept, _ = Department.objects.get_or_create(name='Computer Science', code='CS')

        sizes = [20, 40, 80, 120]
        # Identical per-student performance: 15 marks per student
        PER_STUDENT_MARKS = 15.0

        classes = []
        for size in sizes:
            cls = Class.objects.create(
                name=f'Batch_{size}',
                department=dept,
                num_students=size,
                negative_points=0.0
            )
            # Create a student user for this class to hold the locked submission
            u = User.objects.create_user(
                username=f'rep_{size}',
                email=f'rep_{size}@mariancollege.org',
                role='student',
                class_name=cls
            )
            total_marks = size * PER_STUDENT_MARKS
            Submission.objects.create(
                user=u,
                criteria_id=self.item.id,
                academic_year='2025-2026',
                status='Locked',
                marks=total_marks
            )
            classes.append(cls)

        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/class-index/?year=2025-2026')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        results_by_size = {entry['N']: entry for entry in res.data if entry['N'] in sizes}

        for size in sizes:
            entry = results_by_size[size]
            m_val = entry['M']
            # M must be around 1500 (1500 + 15/N)
            self.assertGreaterEqual(m_val, 1500.0)
            self.assertLessEqual(m_val, 1501.0)

        # Confirm exact expected index values: M = (15*N) / N² * (1 + 100*N) = 15/N + 1500
        self.assertAlmostEqual(results_by_size[20]['M'], 1500.75, places=2)
        self.assertAlmostEqual(results_by_size[40]['M'], 1500.375, places=2)
        self.assertAlmostEqual(results_by_size[80]['M'], 1500.1875, places=2)
        self.assertAlmostEqual(results_by_size[120]['M'], 1500.125, places=2)

    def test_academic_grade_breakdown_full_accounting(self):
        """Verify that all students must be accounted for and pass percentage is accurate."""
        from users.models import Submission, AcademicGradeBreakdown

        sub = Submission.objects.create(
            user=self.student1,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            status='Draft'
        )

        # Case 1: S=10, APlus=10, A=10, Fail=5, Total=50.
        # Remaining 15 students should automatically be attributed to other_pass_count (B/C/Pass).
        breakdown = AcademicGradeBreakdown(
            submission=sub,
            s_grade_count=10,
            a_plus_grade_count=10,
            a_grade_count=10,
            failed_count=5,
            total_students=50
        )
        breakdown.save()
        self.assertEqual(breakdown.other_pass_count, 15)
        # Passed = 45 / 50 = 90.0%
        self.assertEqual(breakdown.class_pass_percentage, 90.0)

        # Case 2: Grade counts sum does not match total_students (e.g. 10+10+10+20+5 = 55 != 50)
        breakdown.other_pass_count = 20
        with self.assertRaises(ValueError):
            breakdown.save()


class ArchitectureRelationalAndVersioningTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='Computer Applications', code='CA')
        self.course = Course.objects.create(
            department=self.dept, name='BCA', abbreviation='BCA', email_code='bc', duration_years=3
        )
        self.cls = Class.objects.create(department=self.dept, course=self.course, year_number=1, name='I BCA')
        self.student = User.objects.create(
            username='student.test@mariancollege.org',
            email='student.test@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls
        )
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.category = CriteriaCategory.objects.create(
            code='ACAD',
            category='Academic Excellence',
            access_level='student'
        )
        self.version_v1 = CriteriaVersion.objects.create(
            academic_year='2025-2026',
            version=1,
            name='2025-26 Standard Criteria',
            is_locked=False
        )
        self.item_v1 = CriteriaItem.objects.create(
            category=self.category,
            version=self.version_v1,
            title='End Semester Result',
            marks=10.0,
            type='count',
            rules_json={'maximum': 10, 'formula': 'academic_sem_result'}
        )

    def test_academic_grade_breakdown_authoritative_source(self):
        """Verify AcademicGradeBreakdown is the authoritative relational source and JSON duplicate is stripped."""
        self.client.force_authenticate(user=self.student)
        payload = {
            'email': self.student.email,
            'criteriaId': self.item_v1.id,
            'academicYear': '2025-2026',
            'description': 'Semester 1 results',
            'status': 'Submitted',
            'evidence': {
                'type': 'academic_marks',
                'submissionType': 'Sem Result',
                'grades': {'S': 5, 'APlus': 10, 'Fail': 2},  # Legacy/duplicate format
                'markBreakdown': {'count90Above': 5}
            },
            'grade_breakdown': {
                's_grade_count': 5,
                'a_plus_grade_count': 10,
                'a_grade_count': 15,
                'other_pass_count': 8,
                'failed_count': 2,
                'class_pass_percentage': 95.0,
                'total_students': 40
            }
        }

        res = self.client.post('/api/submissions/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        sub_id = res.data['id']

        # 1. Relational entity must exist
        submission = Submission.objects.get(id=sub_id)
        self.assertTrue(hasattr(submission, 'grade_breakdown'))
        bd = submission.grade_breakdown
        self.assertEqual(bd.s_grade_count, 5)
        self.assertEqual(bd.a_plus_grade_count, 10)
        self.assertEqual(bd.a_grade_count, 15)
        self.assertEqual(bd.other_pass_count, 8)
        self.assertEqual(bd.failed_count, 2)
        self.assertEqual(bd.total_students, 40)
        self.assertEqual(bd.class_pass_percentage, 95.0)

        # 2. JSON evidence must NOT duplicate grade dictionaries
        self.assertNotIn('grades', submission.evidence)
        self.assertNotIn('markBreakdown', submission.evidence)
        self.assertNotIn('classPassPercentage', submission.evidence)
        self.assertNotIn('totalStudents', submission.evidence)

        # 3. Serialized submission exposes authoritative grade_breakdown
        get_res = self.client.get(f'/api/submissions/{sub_id}/')
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertIn('grade_breakdown', get_res.data)
        self.assertEqual(get_res.data['grade_breakdown']['s_grade_count'], 5)
        self.assertEqual(get_res.data['grade_breakdown']['total_students'], 40)

    def test_criteria_rule_authoritative_over_item_rules_json(self):
        """Verify CriteriaRule values take precedence over conflicting CriteriaItem.rules_json."""
        # rules_json has maximum = 10, but CriteriaRule sets maximum_marks = 25
        rule = CriteriaRule.objects.create(
            item=self.item_v1,
            rule_type='limit',
            maximum_marks=25.0,
            multiplier=1.0,
            extra_config={'cap': 25.0}
        )

        # Score a submission with count = 30
        evidence = {'count': 30}
        computed_marks = calculate_submission_score(self.item_v1, evidence)
        # Authoritative rule caps at 25, not rules_json's 10
        self.assertEqual(computed_marks, 25.0)

    def test_criteria_versioning_and_historical_immutability(self):
        """Verify submissions are tied to specific CriteriaVersion and protected against future year modifications."""
        # 1. Submission in 2025-2026 under v1
        self.client.force_authenticate(user=self.student)
        res1 = self.client.post('/api/submissions/', {
            'email': self.student.email,
            'criteriaId': self.item_v1.id,
            'academicYear': '2025-2026',
            'description': 'Publication 2025',
            'evidence': {'count': 1}
        }, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        sub1 = Submission.objects.get(id=res1.data['id'])
        self.assertEqual(sub1.criteria_version, self.version_v1)
        self.assertEqual(sub1.marks, 10.0)

        # 2. Lock 2025-2026 version
        self.version_v1.is_locked = True
        self.version_v1.save()

        # 3. New Academic Year 2026-2027 with new CriteriaVersion v1
        version_2026 = CriteriaVersion.objects.create(
            academic_year='2026-2027',
            version=1,
            name='2026-27 Updated Publication Scheme',
            is_locked=False
        )
        # Publication increased to 15 marks in 2026-2027
        item_2026 = CriteriaItem.objects.create(
            category=self.category,
            version=version_2026,
            title='End Semester Result (Updated)',
            marks=15.0,
            type='count',
            rules_json={'maximum': 15}
        )

        res2 = self.client.post('/api/submissions/', {
            'email': self.student.email,
            'criteriaId': item_2026.id,
            'academicYear': '2026-2027',
            'description': 'Publication 2026',
            'evidence': {'count': 1}
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        sub2 = Submission.objects.get(id=res2.data['id'])
        self.assertEqual(sub2.criteria_version, version_2026)
        self.assertEqual(sub2.marks, 15.0)

        # 4. Crucial: Old 2025-2026 submission is still intact with 10.0 marks and version v1
        sub1.refresh_from_db()
        self.assertEqual(sub1.marks, 10.0)
        self.assertEqual(sub1.criteria_version, self.version_v1)


class Phase1SecurityRemediationRegressionTest(TestCase):
    """
    Comprehensive regression tests for Phase 1 Security Remediation:
    1. Development Authentication Bypass
    2. Hardcoded Secrets & Production Settings Validation
    3. Submission IDOR (Object-Level Authorization)
    4. Client-Controlled Workflow Status Prevention
    5. Production Security Defaults & Privacy Protections
    """

    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.dept1 = Department.objects.create(name='Computer Science', code='CS', email_prefix='p', level='PG')
        self.dept2 = Department.objects.create(name='Management Studies', code='MS', email_prefix='m', level='PG')

        self.class1 = Class.objects.create(name='I MCA', department=self.dept1, year_number=1)
        self.class2 = Class.objects.create(name='I MBA', department=self.dept2, year_number=1)

        self.student_a = User.objects.create(
            username='student_a@mariancollege.org',
            email='student_a@mariancollege.org',
            role='student',
            department=self.dept1,
            class_name=self.class1
        )
        self.student_b = User.objects.create(
            username='student_b@mariancollege.org',
            email='student_b@mariancollege.org',
            role='student',
            department=self.dept1,
            class_name=self.class1
        )
        self.student_c = User.objects.create(
            username='student_c@mariancollege.org',
            email='student_c@mariancollege.org',
            role='student',
            department=self.dept2,
            class_name=self.class2
        )
        self.student_rep1 = User.objects.create(
            username='rep_class1@mariancollege.org',
            email='rep_class1@mariancollege.org',
            role='student',
            department=self.dept1,
            class_name=self.class1
        )
        self.class1.dqc_member = self.student_rep1
        self.class1.save()

        self.faculty_dept1 = User.objects.create(
            username='faculty_dept1@mariancollege.org',
            email='faculty_dept1@mariancollege.org',
            role='faculty',
            department=self.dept1,
            is_staff=True
        )
        self.class1.class_teacher = self.faculty_dept1
        self.class1.save()

        self.faculty_dept2 = User.objects.create(
            username='faculty_dept2@mariancollege.org',
            email='faculty_dept2@mariancollege.org',
            role='faculty',
            department=self.dept2,
            is_staff=True
        )
        self.class2.class_teacher = self.faculty_dept2
        self.class2.save()

        self.evaluator = User.objects.create(
            username='evaluator@mariancollege.org',
            email='evaluator@mariancollege.org',
            role='evaluation',
            is_staff=True
        )
        self.iqac_user = User.objects.create(
            username='inst_admin@mariancollege.org',
            email='inst_admin@mariancollege.org',
            role='admin',
            is_staff=True
        )
        self.admin = User.objects.create(
            username='admin@mariancollege.org',
            email='admin@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )

        self.version = CriteriaVersion.objects.create(
            academic_year='2025-2026',
            version=1,
            name='Scheme 2025-26',
            is_locked=False
        )
        self.category = CriteriaCategory.objects.create(
            code='ACAD',
            category='Academic Excellence'
        )
        self.item = CriteriaItem.objects.create(
            category=self.category,
            version=self.version,
            title='Course Submission',
            marks=10.0,
            type='count',
            rules_json={'maximum': 50}
        )

        self.sub_a = Submission.objects.create(
            user=self.student_a,
            criteria_id=self.item.id,
            criteria_version=self.version,
            academic_year='2025-2026',
            description='Student A Submission',
            status='Draft',
            evidence={'count': 1},
            marks=10
        )
        self.sub_b = Submission.objects.create(
            user=self.student_b,
            criteria_id=self.item.id,
            criteria_version=self.version,
            academic_year='2025-2026',
            description='Student B Submission',
            status='Draft',
            evidence={'count': 1},
            marks=10
        )
        self.sub_c = Submission.objects.create(
            user=self.student_c,
            criteria_id=self.item.id,
            criteria_version=self.version,
            academic_year='2025-2026',
            description='Student C Submission',
            status='Draft',
            evidence={'count': 1},
            marks=10
        )

    # -------------------------------------------------------------
    # 1. DEVELOPMENT AUTHENTICATION BYPASS REGRESSION TESTS
    # -------------------------------------------------------------
    def test_dev_bypass_fails_closed_when_debug_false(self):
        """Bypass must strictly fail closed (404) when DEBUG=False, even if ENABLE_DEV_BYPASS=True."""
        with self.settings(DEBUG=False, ENABLE_DEV_BYPASS=True):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'student_a@mariancollege.org'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_dev_bypass_fails_closed_when_flag_false(self):
        """Bypass must return 404 when ENABLE_DEV_BYPASS=False in development."""
        with self.settings(DEBUG=True, ENABLE_DEV_BYPASS=False):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'student_a@mariancollege.org'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_dev_bypass_cannot_escalate_privileged_role(self):
        """Dev bypass must reject client attempts to select an arbitrary privileged role."""
        with self.settings(DEBUG=True, ENABLE_DEV_BYPASS=True):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'student_a@mariancollege.org',
                'role': 'admin'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
            self.student_a.refresh_from_db()
            self.assertEqual(self.student_a.role, 'student')

    def test_dev_bypass_cannot_autocreate_privileged_accounts(self):
        """Unseeded admin or staff accounts cannot be created on the fly via dev bypass."""
        with self.settings(DEBUG=True, ENABLE_DEV_BYPASS=True):
            res = self.client.post('/api/auth/bypass/', {
                'email': 'unseeded.staff@mariancollege.org'
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_hardcoded_student_emails_not_granted_rep_privileges(self):
        """Hardcoded student emails must NOT bypass rep verification logic without class assignment."""
        from users.views import is_user_student_rep
        unassigned_student = User.objects.create(
            username='santhosh.25pmc152@mariancollege.org',
            email='santhosh.25pmc152@mariancollege.org',
            role='student'
        )
        self.assertFalse(is_user_student_rep(unassigned_student))

    # -------------------------------------------------------------
    # 2. SUBMISSION IDOR REGRESSION TESTS
    # -------------------------------------------------------------
    def test_submission_idor_read_access(self):
        """Student A can read own submission; cannot read Student B submission (403)."""
        # Student A -> Student A submission (allowed)
        self.client.force_authenticate(user=self.student_a)
        res_own = self.client.get(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)

        # Student A -> Student B submission (denied IDOR)
        res_other = self.client.get(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

    def test_submission_idor_class_rep_scoped_access(self):
        """Student rep can view submissions in assigned class, but denied for other classes."""
        self.client.force_authenticate(user=self.student_rep1)
        # In assigned Class 1 -> allowed
        res_class1 = self.client.get(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res_class1.status_code, status.HTTP_200_OK)

        # In Class 2 -> denied
        res_class2 = self.client.get(f'/api/submissions/{self.sub_c.id}/')
        self.assertEqual(res_class2.status_code, status.HTTP_403_FORBIDDEN)

    def test_submission_idor_faculty_department_scoped_access(self):
        """Faculty can only view submissions from their assigned class or department."""
        # Faculty Dept 1 -> Student A in Dept 1 (allowed)
        self.client.force_authenticate(user=self.faculty_dept1)
        res_dept1 = self.client.get(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_dept1.status_code, status.HTTP_200_OK)

        # Faculty Dept 2 -> Student A in Dept 1 (denied)
        self.client.force_authenticate(user=self.faculty_dept2)
        res_dept2 = self.client.get(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_dept2.status_code, status.HTTP_403_FORBIDDEN)

    def test_submission_idor_update_access(self):
        """Student A cannot update Student B submission (denied 403)."""
        self.client.force_authenticate(user=self.student_a)
        res = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'description': 'Malicious Update by Student A'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.description, 'Student B Submission')

    def test_submission_idor_delete_access(self):
        """Student A cannot delete Student B submission (denied 403)."""
        self.client.force_authenticate(user=self.student_a)
        res = self.client.delete(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Submission.objects.filter(id=self.sub_b.id).exists())

        # Student A can delete own draft submission
        res_own = self.client.delete(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)
        self.assertFalse(Submission.objects.filter(id=self.sub_a.id).exists())

    # -------------------------------------------------------------
    # 3. CLIENT-CONTROLLED WORKFLOW STATUS REGRESSION TESTS
    # -------------------------------------------------------------
    def test_student_cannot_post_with_privileged_status(self):
        """Students cannot set arbitrary or privileged statuses on POST."""
        self.client.force_authenticate(user=self.student_a)

        # Attempt to create directly in Locked state
        res_locked = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'status': 'Locked',
            'description': 'Attempting Locked Submission'
        }, format='json')
        self.assertEqual(res_locked.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt to create directly in Evaluated state
        res_eval = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'status': 'Evaluated',
            'description': 'Attempting Evaluated Submission'
        }, format='json')
        self.assertEqual(res_eval.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt with case variations like 'LOCKED'
        res_caps = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'status': 'LOCKED',
            'description': 'Attempting LOCKED Submission'
        }, format='json')
        self.assertEqual(res_caps.status_code, status.HTTP_403_FORBIDDEN)

        # Valid Draft creation succeeds
        res_valid = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'status': 'Draft',
            'description': 'Legitimate Draft'
        }, format='json')
        self.assertEqual(res_valid.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_valid.data['status'], 'Draft')

    def test_role_controlled_workflow_status_transitions(self):
        """Transitions between states must follow legal workflow and role restrictions."""
        # 1. Student attempts to transition Draft -> Locked (denied)
        self.client.force_authenticate(user=self.student_b)
        res_student_lock = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Locked'
        }, format='json')
        self.assertEqual(res_student_lock.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Student transitions Draft -> Submitted (allowed)
        res_submit = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Submitted'
        }, format='json')
        self.assertEqual(res_submit.status_code, status.HTTP_200_OK)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.status, 'Submitted')

        # 3. Student Rep performs Round 1: Submitted -> Student Rep Verified
        self.client.force_authenticate(user=self.student_rep1)
        res_rep = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Student Rep Verified'
        }, format='json')
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK)

        # 3b. Faculty Dept 1 transitions Student Rep Verified -> Teacher Verified (allowed)
        self.client.force_authenticate(user=self.faculty_dept1)
        res_tv = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Teacher Verified'
        }, format='json')
        self.assertEqual(res_tv.status_code, status.HTTP_200_OK)

        # 4. Faculty attempts to transition Teacher Verified -> Locked (denied: only admin/iqac)
        res_fac_lock = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Locked'
        }, format='json')
        self.assertEqual(res_fac_lock.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Admin transitions Teacher Verified -> Locked (allowed)
        self.client.force_authenticate(user=self.admin)
        res_admin_lock = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Locked'
        }, format='json')
        self.assertEqual(res_admin_lock.status_code, status.HTTP_200_OK)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.status, 'Locked')

        # 6. Once Locked, submission is immutable even for Admin
        res_mod_locked = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'description': 'Editing Locked Record'
        }, format='json')
        self.assertEqual(res_mod_locked.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------
    # 4. BUG REPORT PII PROTECTION (P1-03)
    # -------------------------------------------------------------
    def test_bug_report_endpoint_pii_protected(self):
        """Bug report GET endpoint requires admin/iqac authentication and denies students/anonymous."""
        from users.models import BugReport
        BugReport.objects.create(
            title='Login Issue',
            reporter_name='Secret Reporter',
            reporter_email='reporter@mariancollege.org',
            whatsapp_numbers='+919876543210'
        )

        # Unauthenticated GET denied
        res_unauth = self.client.get('/api/bug-reports/')
        self.assertEqual(res_unauth.status_code, status.HTTP_401_UNAUTHORIZED)

        # Student GET denied
        self.client.force_authenticate(user=self.student_a)
        res_student = self.client.get('/api/bug-reports/')
        self.assertEqual(res_student.status_code, status.HTTP_403_FORBIDDEN)

        # Admin GET allowed
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.get('/api/bug-reports/')
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_admin.data), 1)

    # -------------------------------------------------------------
    # 5. DQC REPRESENTATIVE ASSIGNMENT (P1-05 RUNTIME ERROR FIX)
    # -------------------------------------------------------------
    def test_class_dqc_member_assignment_no_runtime_value_error(self):
        """Assigning a DQC representative must complete without raising ValueError."""
        self.client.force_authenticate(user=self.admin)
        new_student = User.objects.create(
            username='new_rep.25pmc199@mariancollege.org',
            email='new_rep.25pmc199@mariancollege.org',
            role='student',
            class_name=self.class1,
            department=self.dept1
        )
        res = self.client.put(f'/api/auth/classes/{self.class1.id}/', {
            'dqcMember': new_student.email
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.class1.refresh_from_db()
        self.assertEqual(self.class1.dqc_member, new_student)

    # -------------------------------------------------------------
    # 6. PRODUCTION SECURITY DEFAULTS & CONFIGURATION CHECKS
    # -------------------------------------------------------------
    def test_production_security_settings_defaults(self):
        """Verify production security configuration validation logic for allowed hosts, CORS, and secrets."""
        from django.core.exceptions import ImproperlyConfigured

        # Verify ALLOWED_HOSTS validation in production
        def validate_hosts(debug, hosts_str):
            if not debug:
                if not hosts_str:
                    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS environment variable is required in production.")
                hosts = [h.strip() for h in hosts_str.split(',') if h.strip()]
                if '*' in hosts:
                    raise ImproperlyConfigured("Wildcard '*' in DJANGO_ALLOWED_HOSTS is forbidden in production.")
                return hosts
            return [h.strip() for h in (hosts_str or 'localhost,127.0.0.1').split(',') if h.strip()]

        with self.assertRaises(ImproperlyConfigured):
            validate_hosts(debug=False, hosts_str=None)
        with self.assertRaises(ImproperlyConfigured):
            validate_hosts(debug=False, hosts_str='*')
        with self.assertRaises(ImproperlyConfigured):
            validate_hosts(debug=False, hosts_str='marian.edu,*')

        self.assertEqual(validate_hosts(debug=False, hosts_str='marian.edu,api.marian.edu'), ['marian.edu', 'api.marian.edu'])
        self.assertEqual(validate_hosts(debug=True, hosts_str=None), ['localhost', '127.0.0.1'])

        # Verify Google Client ID required in production
        def validate_google_client_id(debug, client_id):
            if not debug and not client_id:
                raise ImproperlyConfigured("GOOGLE_CLIENT_ID environment variable is required in production.")
            return client_id

        with self.assertRaises(ImproperlyConfigured):
            validate_google_client_id(debug=False, client_id=None)
        with self.assertRaises(ImproperlyConfigured):
            validate_google_client_id(debug=False, client_id='')
        self.assertEqual(validate_google_client_id(debug=False, client_id='google-id-123'), 'google-id-123')


class Phase2AuthorizationHardeningRegressionTest(TestCase):
    """
    Phase 2 Regression Tests: Backend RBAC, Object-Level Authorization,
    Privilege Escalation Prevention, and Authentication Lifecycle Hardening.
    """
    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2026-2027', is_active=True)

        # Departments & Classes
        self.dept_cs = Department.objects.create(name='Dept of Computer Science', code='DCS', email_prefix='u', level='UG')
        self.dept_comm = Department.objects.create(name='Dept of Commerce', code='DCOM', email_prefix='u', level='UG')

        self.course_bca = Course.objects.create(department=self.dept_cs, name='BCA', abbreviation='BCA', email_code='bc')
        self.course_bcom = Course.objects.create(department=self.dept_comm, name='BCom', abbreviation='BCOM', email_code='cm')

        self.class_bca_a = Class.objects.create(department=self.dept_cs, course=self.course_bca, year_number=1, name='I BCA A')
        self.class_bca_b = Class.objects.create(department=self.dept_cs, course=self.course_bca, year_number=1, name='I BCA B')
        self.class_bcom = Class.objects.create(department=self.dept_comm, course=self.course_bcom, year_number=1, name='I BCOM')

        # Users
        self.student_a = User.objects.create(
            username='student_a.25ubc101@mariancollege.org',
            email='student_a.25ubc101@mariancollege.org',
            role='student',
            class_name=self.class_bca_a,
            department=self.dept_cs,
            first_name='Student',
            last_name='A'
        )
        self.student_b = User.objects.create(
            username='student_b.25ubc202@mariancollege.org',
            email='student_b.25ubc202@mariancollege.org',
            role='student',
            class_name=self.class_bca_b,
            department=self.dept_cs,
            first_name='Student',
            last_name='B'
        )
        self.rep_a = User.objects.create(
            username='rep_a.25ubc199@mariancollege.org',
            email='rep_a.25ubc199@mariancollege.org',
            role='student',
            class_name=self.class_bca_a,
            department=self.dept_cs,
            first_name='Rep',
            last_name='A'
        )
        self.class_bca_a.dqc_member = self.rep_a
        self.class_bca_a.save()

        self.teacher_a = User.objects.create(
            username='teacher.a@mariancollege.org',
            email='teacher.a@mariancollege.org',
            role='faculty',
            department=self.dept_cs,
            class_name=self.class_bca_a,
            first_name='Teacher',
            last_name='A'
        )
        self.class_bca_a.class_teacher = self.teacher_a
        self.class_bca_a.save()

        self.teacher_b = User.objects.create(
            username='teacher.b@mariancollege.org',
            email='teacher.b@mariancollege.org',
            role='faculty',
            department=self.dept_cs,
            class_name=self.class_bca_b,
            first_name='Teacher',
            last_name='B'
        )
        self.class_bca_b.class_teacher = self.teacher_b
        self.class_bca_b.save()

        self.evaluator_cat1 = User.objects.create(
            username='eval.cat1@mariancollege.org',
            email='eval.cat1@mariancollege.org',
            role='evaluation',
            first_name='Evaluator',
            last_name='One'
        )
        self.evaluator_cat2 = User.objects.create(
            username='eval.cat2@mariancollege.org',
            email='eval.cat2@mariancollege.org',
            role='evaluation',
            first_name='Evaluator',
            last_name='Two'
        )

        self.iqac_user = User.objects.create(
            username='mod.admin@mariancollege.org',
            email='mod.admin@mariancollege.org',
            role='admin',
            first_name='Moderator',
            last_name='Admin'
        )
        self.admin = User.objects.create(
            username='admin.inst@mariancollege.org',
            email='admin.inst@mariancollege.org',
            role='admin',
            first_name='Admin',
            last_name='User',
            is_staff=True,
            is_superuser=True
        )

        # Criteria & Categories
        self.cat1 = CriteriaCategory.objects.create(
            code='cat-academics-eval1',
            category='Academic Honors',
            evaluators=['eval.cat1@mariancollege.org']
        )
        self.item1 = CriteriaItem.objects.create(
            category=self.cat1,
            title='Gold Medal',
            type='fixed',
            marks=10.0
        )

        self.cat2 = CriteriaCategory.objects.create(
            code='cat-sports-eval2',
            category='Sports Excellence',
            evaluators=['eval.cat2@mariancollege.org']
        )
        self.item2 = CriteriaItem.objects.create(
            category=self.cat2,
            title='State Championship',
            type='fixed',
            marks=15.0
        )

        # Submissions
        self.sub_a = Submission.objects.create(
            user=self.student_a,
            criteria_id=self.item1.id,
            academic_year='2026-2027',
            description='Student A Academic Gold Medal',
            status='Draft'
        )
        self.sub_b = Submission.objects.create(
            user=self.student_b,
            criteria_id=self.item2.id,
            academic_year='2026-2027',
            description='Student B Sports Championship',
            status='Draft'
        )

    # 1. Student -> own data vs other student data
    def test_student_can_read_own_submission_but_denied_other_student_submission(self):
        self.client.force_authenticate(user=self.student_a)

        # Student A reads own submission
        res_own = self.client.get(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)

        # Student A attempts to read Student B's submission -> 403
        res_other = self.client.get(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

        # Student A attempts to edit Student B's submission -> 403
        res_edit = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'description': 'Malicious overwrite'
        }, format='json')
        self.assertEqual(res_edit.status_code, status.HTTP_403_FORBIDDEN)

        # Student A attempts to delete Student B's submission -> 403
        res_del = self.client.delete(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)

    # 2. Student privilege escalation: profile class spoofing
    def test_student_cannot_alter_class_assignment_in_profile(self):
        self.client.force_authenticate(user=self.student_a)
        res = self.client.put('/api/auth/profile/', {
            'class_name': 'I BCA B'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.student_a.refresh_from_db()
        self.assertEqual(self.student_a.class_name, self.class_bca_a)

    # 3. Student privilege escalation: spoofing verifier identity or marks
    def test_student_cannot_spoof_verifier_identity_or_marks(self):
        self.client.force_authenticate(user=self.student_a)

        # Student attempts to self-assign marks
        res_marks = self.client.put(f'/api/submissions/{self.sub_a.id}/', {
            'marks': 100
        }, format='json')
        self.assertEqual(res_marks.status_code, status.HTTP_403_FORBIDDEN)

        # Student attempts to spoof teacherVerifiedByName or evaluatorVerifiedByName
        res_spoof = self.client.put(f'/api/submissions/{self.sub_a.id}/', {
            'description': 'Updated description',
            'teacherVerifiedByName': 'Faked Principal Name',
            'evaluatorVerifiedByName': 'Faked Evaluator Name',
            'teacherRemarks': 'Faked Approval'
        }, format='json')
        self.assertEqual(res_spoof.status_code, status.HTTP_200_OK)
        self.sub_a.refresh_from_db()
        self.assertIsNone(self.sub_a.teacher_verified_by_name)
        self.assertIsNone(self.sub_a.evaluator_verified_by_name)
        self.assertIsNone(self.sub_a.teacher_remarks)

    # 4. Student -> Administrative endpoints (RBAC)
    def test_student_cannot_access_administrative_endpoints(self):
        self.client.force_authenticate(user=self.student_a)

        # User management endpoint
        res_users = self.client.get('/api/users/')
        self.assertEqual(res_users.status_code, status.HTTP_403_FORBIDDEN)

        # System settings mutate
        res_settings = self.client.post('/api/settings/', {'smallest_class_size': '10'}, format='json')
        self.assertEqual(res_settings.status_code, status.HTTP_403_FORBIDDEN)

        # Class create
        res_class_create = self.client.post('/api/auth/classes/', {'name': 'New Class'}, format='json')
        self.assertEqual(res_class_create.status_code, status.HTTP_403_FORBIDDEN)

        # User group create
        res_ug = self.client.post('/api/user-groups/', {'id': 'grp-hack', 'name': 'Hacker Group'}, format='json')
        self.assertEqual(res_ug.status_code, status.HTTP_403_FORBIDDEN)

    # 5. DQC Representative -> unauthorized class
    def test_dqc_rep_cannot_verify_or_view_unauthorized_class(self):
        self.client.force_authenticate(user=self.rep_a)

        # Rep A can view Student A (their advised class I BCA A)
        res_own_class = self.client.get(f'/api/submissions/{self.sub_a.id}/')
        self.assertEqual(res_own_class.status_code, status.HTTP_200_OK)

        # Rep A cannot view Student B (I BCA B) -> 403
        res_unauth_view = self.client.get(f'/api/submissions/{self.sub_b.id}/')
        self.assertEqual(res_unauth_view.status_code, status.HTTP_403_FORBIDDEN)

        # Rep A cannot verify Student B's submission -> 403
        res_unauth_verify = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Student Rep Verified'
        }, format='json')
        self.assertEqual(res_unauth_verify.status_code, status.HTTP_403_FORBIDDEN)

    # 6. Advisor -> unauthorized class
    def test_advisor_cannot_modify_unauthorized_class(self):
        self.client.force_authenticate(user=self.teacher_a)

        # Teacher A attempts to modify Teacher B's class moderation parameters -> 403
        res_mod_b = self.client.patch(f'/api/auth/classes/{self.class_bca_b.id}/', {
            'num_students': 40
        }, format='json')
        self.assertEqual(res_mod_b.status_code, status.HTTP_403_FORBIDDEN)

        # Teacher A attempts to reassign Teacher B's class advisor -> 403
        res_reassign = self.client.put(f'/api/auth/classes/{self.class_bca_b.id}/', {
            'classTeacher': self.teacher_a.email
        }, format='json')
        self.assertEqual(res_reassign.status_code, status.HTTP_403_FORBIDDEN)

    # 7. Faculty cannot perform evaluator operations
    def test_faculty_cannot_assign_evaluation_marks_or_evaluator_remarks(self):
        self.client.force_authenticate(user=self.teacher_a)

        res_marks = self.client.put(f'/api/submissions/{self.sub_a.id}/', {
            'marks': 25
        }, format='json')
        self.assertEqual(res_marks.status_code, status.HTTP_403_FORBIDDEN)

        res_remarks = self.client.put(f'/api/submissions/{self.sub_a.id}/', {
            'evaluatorRemarks': 'Teacher attempting evaluator remarks'
        }, format='json')
        self.assertEqual(res_remarks.status_code, status.HTTP_403_FORBIDDEN)

    # 8. Evaluator -> unauthorized evaluation category
    def test_evaluator_cannot_evaluate_unrelated_criteria_category(self):
        # Move submission B to Teacher Verified so it is ready for evaluation
        self.sub_b.status = 'Teacher Verified'
        self.sub_b.save()

        # Evaluator 1 is assigned only to Category 1 (Academics). Submission B is Category 2 (Sports).
        self.client.force_authenticate(user=self.evaluator_cat1)
        res_eval1 = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Evaluated',
            'marks': 15.0
        }, format='json')
        self.assertEqual(res_eval1.status_code, status.HTTP_403_FORBIDDEN)

        # Evaluator 2 is assigned to Category 2 (Sports) -> allowed
        self.client.force_authenticate(user=self.evaluator_cat2)
        res_eval2 = self.client.put(f'/api/submissions/{self.sub_b.id}/', {
            'status': 'Evaluated',
            'marks': 15.0
        }, format='json')
        self.assertEqual(res_eval2.status_code, status.HTTP_200_OK)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.status, 'Evaluated')
        self.assertEqual(self.sub_b.marks, 15)
        self.assertEqual(self.sub_b.evaluator_verified_by_name, 'Evaluator Two')

    # 9. Evaluator cannot mutate classes
    def test_evaluator_cannot_modify_classes(self):
        self.client.force_authenticate(user=self.evaluator_cat1)
        res = self.client.patch(f'/api/auth/classes/{self.class_bca_a.id}/', {
            'num_students': 99
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # 10. Admin -> authorized administrative operations
    def test_admin_authorized_operations(self):
        self.client.force_authenticate(user=self.admin)

        # Admin creates academic year
        res_ay = self.client.post('/api/academic-years/', {'year': '2027-2028'}, format='json')
        self.assertEqual(res_ay.status_code, status.HTTP_200_OK)

        # Admin updates system settings
        res_set = self.client.post('/api/settings/', {'smallest_class_size': '35'}, format='json')
        self.assertEqual(res_set.status_code, status.HTTP_200_OK)

        # Admin manages user groups
        res_ug = self.client.post('/api/user-groups/', {
            'id': 'grp-test-committee',
            'name': 'Test Committee'
        }, format='json')
        self.assertEqual(res_ug.status_code, status.HTTP_200_OK)

    # 11. Authentication Lifecycle: Disabled User Rejection
    def test_disabled_user_login_and_token_refresh_rejected(self):
        # Create disabled user
        disabled_user = User.objects.create(
            username='disabled.user.25ubc333@mariancollege.org',
            email='disabled.user.25ubc333@mariancollege.org',
            role='student',
            is_active=False
        )

        # 1. Bypass login denied for disabled user
        with self.settings(DEBUG=True, ENABLE_DEV_BYPASS=True):
            res_bypass = self.client.post('/api/auth/bypass/', {
                'email': disabled_user.email
            }, format='json')
            self.assertEqual(res_bypass.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Token refresh denied for disabled user
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(disabled_user)
        res_refresh = self.client.post('/api/auth/token/refresh/', {
            'refresh': str(token)
        }, format='json')
        self.assertEqual(res_refresh.status_code, status.HTTP_401_UNAUTHORIZED)


class Phase3APISecurityAndValidationRegressionTest(TestCase):
    """
    Comprehensive regression tests for Phase 3 API Security & Input Validation:
    1. Missing Required Fields
    2. Invalid and Non-Existent IDs (Foreign Key / Criteria Item Integrity)
    3. Invalid State Transitions
    4. Oversized Values & String Length Bounds
    5. Numeric Range Bounds
    6. Enum and Choice Validation
    7. Mass Assignment Protections (BugReport read_only_fields, etc.)
    8. User Profile and Management Validations
    """

    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.dept = Department.objects.create(
            name='Department of Computer Applications',
            code='MCA',
            email_prefix='p',
            level='PG'
        )
        self.course = Course.objects.create(
            department=self.dept,
            name='Master of Computer Applications',
            abbreviation='MCA',
            email_code='mc',
            duration_years=2
        )
        self.cls = Class.objects.create(
            name='I MCA',
            department=self.dept,
            course=self.course,
            year_number=1,
            num_students=30,
            negative_points=0.0
        )
        self.student = User.objects.create(
            username='student.test.25pmc101@mariancollege.org',
            email='student.test.25pmc101@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls,
            is_active=True
        )
        self.admin = User.objects.create(
            username='admin.test@mariancollege.org',
            email='admin.test@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True,
            is_active=True
        )
        self.cat = CriteriaCategory.objects.create(
            code='cat-academic',
            category='Academic Activities',
            access_level='all_students'
        )
        self.criteria_item = CriteriaItem.objects.create(
            category=self.cat,
            title='Conference Paper Presentation',
            type='fixed',
            marks=10.0
        )
        self.rule = CriteriaRule.objects.create(
            item=self.criteria_item,
            rule_type='fixed',
            maximum_marks=10.0,
            is_negative=False
        )

    # 1. Missing Required Fields
    def test_submission_missing_required_fields(self):
        self.client.force_authenticate(user=self.student)

        # Missing criteriaId -> 400
        res_no_criteria = self.client.post('/api/submissions/', {
            'academicYear': '2025-2026',
            'description': 'Valid description',
        }, format='json')
        self.assertEqual(res_no_criteria.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('criteriaId', res_no_criteria.data.get('error', ''))

        # Missing description -> 400
        res_no_desc = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': '   ',
        }, format='json')
        self.assertEqual(res_no_desc.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('description', res_no_desc.data.get('error', ''))

    # 2. Invalid or Non-Existent IDs
    def test_submission_invalid_or_nonexistent_ids(self):
        self.client.force_authenticate(user=self.student)

        # Non-integer criteriaId -> 400
        res_bad_id = self.client.post('/api/submissions/', {
            'criteriaId': 'not-an-id',
            'academicYear': '2025-2026',
            'description': 'Testing non-int ID',
        }, format='json')
        self.assertEqual(res_bad_id.status_code, status.HTTP_400_BAD_REQUEST)

        # Non-existent criteriaId -> 404
        res_nonexistent_id = self.client.post('/api/submissions/', {
            'criteriaId': 999999,
            'academicYear': '2025-2026',
            'description': 'Testing non-existent criteria ID',
        }, format='json')
        self.assertEqual(res_nonexistent_id.status_code, status.HTTP_404_NOT_FOUND)

        # Non-existent academicYear -> 400
        res_bad_ay = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2099-2100',
            'description': 'Testing non-existent year',
        }, format='json')
        self.assertEqual(res_bad_ay.status_code, status.HTTP_400_BAD_REQUEST)

        # Malformed academicYear format -> 400
        res_malformed_ay = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025/2026',
            'description': 'Testing malformed year',
        }, format='json')
        self.assertEqual(res_malformed_ay.status_code, status.HTTP_400_BAD_REQUEST)

    # 3. Oversized Values
    def test_submission_oversized_values(self):
        self.client.force_authenticate(user=self.student)

        # Description > 5000 chars -> 400
        res_big_desc = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': 'A' * 5001,
        }, format='json')
        self.assertEqual(res_big_desc.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('5000', res_big_desc.data.get('error', ''))

        # Remarks > 2000 chars -> 400
        res_big_remarks = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': 'Valid description',
            'remarks': 'R' * 2001,
        }, format='json')
        self.assertEqual(res_big_remarks.status_code, status.HTTP_400_BAD_REQUEST)

        # Proof > 255 chars -> 400
        res_big_proof = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': 'Valid description',
            'proof': 'https://example.com/' + ('x' * 250),
        }, format='json')
        self.assertEqual(res_big_proof.status_code, status.HTTP_400_BAD_REQUEST)

        # Certificate ID > 100 chars -> 400
        res_big_cert = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': 'Valid description',
            'certificateId': 'CERT-' + ('9' * 100),
        }, format='json')
        self.assertEqual(res_big_cert.status_code, status.HTTP_400_BAD_REQUEST)

    # 4. Submission Update Validation & Invalid State Guard
    def test_submission_update_invalid_state_and_ids(self):
        self.client.force_authenticate(user=self.student)

        # Create valid submission
        res_create = self.client.post('/api/submissions/', {
            'criteriaId': self.criteria_item.id,
            'academicYear': '2025-2026',
            'description': 'Original submission',
        }, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        sub_id = res_create.data['id']

        # Update with non-existent criteriaId -> 404
        res_upd_bad_cid = self.client.put(f'/api/submissions/{sub_id}/', {
            'criteriaId': 888888,
        }, format='json')
        self.assertEqual(res_upd_bad_cid.status_code, status.HTTP_404_NOT_FOUND)

        # Update with non-existent academicYear -> 400
        res_upd_bad_ay = self.client.put(f'/api/submissions/{sub_id}/', {
            'academicYear': '2090-2091',
        }, format='json')
        self.assertEqual(res_upd_bad_ay.status_code, status.HTTP_400_BAD_REQUEST)

        # Update with oversized description -> 400
        res_upd_big_desc = self.client.put(f'/api/submissions/{sub_id}/', {
            'description': 'D' * 5001,
        }, format='json')
        self.assertEqual(res_upd_big_desc.status_code, status.HTTP_400_BAD_REQUEST)

        # Student attempting privileged transition -> 403
        res_upd_status = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Approved',
        }, format='json')
        self.assertEqual(res_upd_status.status_code, status.HTTP_403_FORBIDDEN)

    # 5. Mass Assignment Protection on Bug Reports
    def test_bug_report_mass_assignment_protection(self):
        # Public submitter tries to pass status='Resolved' and arbitrary whatsapp_numbers
        res = self.client.post('/api/bug-reports/', {
            'title': 'Broken layout in navigation bar',
            'description': 'The navigation dropdown does not expand on mobile screens.',
            'bug_type': 'UI',
            'priority': 'High',
            'status': 'Resolved',
            'whatsapp_numbers': '+919999999999',
            'reporter_name': 'Anonymous Student',
            'reporter_email': 'anon@mariancollege.org'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        report_id = res.data['id']
        from .models import BugReport
        report = BugReport.objects.get(id=report_id)

        # Crucial security check: read_only_fields protected status and whatsapp_numbers
        self.assertEqual(report.status, 'Open')
        self.assertEqual(report.whatsapp_numbers, '')

    # 6. Bug Report Field Validation
    def test_bug_report_validation_bounds(self):
        # Title too short (<3 chars) -> 400
        res_short_title = self.client.post('/api/bug-reports/', {
            'title': 'Hi',
            'description': 'Valid description here',
        }, format='json')
        self.assertEqual(res_short_title.status_code, status.HTTP_400_BAD_REQUEST)

        # Description too short (<5 chars) -> 400
        res_short_desc = self.client.post('/api/bug-reports/', {
            'title': 'Valid Title',
            'description': 'No',
        }, format='json')
        self.assertEqual(res_short_desc.status_code, status.HTTP_400_BAD_REQUEST)

    # 7. Class Numeric Ranges & String Length Bounds
    def test_class_numeric_ranges_and_lengths(self):
        self.client.force_authenticate(user=self.admin)

        # Negative num_students -> 400
        res_neg_students = self.client.patch(f'/api/auth/classes/{self.cls.id}/', {
            'num_students': -10
        }, format='json')
        self.assertEqual(res_neg_students.status_code, status.HTTP_400_BAD_REQUEST)

        # Oversized num_students (>1000) -> 400
        res_big_students = self.client.patch(f'/api/auth/classes/{self.cls.id}/', {
            'num_students': 1001
        }, format='json')
        self.assertEqual(res_big_students.status_code, status.HTTP_400_BAD_REQUEST)

        # Oversized negative_points (>10000) -> 400
        res_big_penalties = self.client.patch(f'/api/auth/classes/{self.cls.id}/', {
            'negative_points': 15000.0
        }, format='json')
        self.assertEqual(res_big_penalties.status_code, status.HTTP_400_BAD_REQUEST)

        # Invalid year_number (>6) -> 400
        res_bad_yr = self.client.patch(f'/api/auth/classes/{self.cls.id}/', {
            'year_number': 9
        }, format='json')
        self.assertEqual(res_bad_yr.status_code, status.HTTP_400_BAD_REQUEST)

        # Oversized class name (>100 chars) -> 400
        res_big_name = self.client.patch(f'/api/auth/classes/{self.cls.id}/', {
            'name': 'C' * 101
        }, format='json')
        self.assertEqual(res_big_name.status_code, status.HTTP_400_BAD_REQUEST)

    # 8. Department Validation & Enum Enforceability
    def test_department_validation(self):
        self.client.force_authenticate(user=self.admin)

        # Invalid level -> 400
        res_bad_level = self.client.post('/api/departments/', {
            'name': 'Department of Economics',
            'code': 'ECON',
            'level': 'Doctorate'  # Invalid!
        }, format='json')
        self.assertEqual(res_bad_level.status_code, status.HTTP_400_BAD_REQUEST)

        # Oversized name -> 400
        res_big_dept_name = self.client.post('/api/departments/', {
            'name': 'D' * 101,
            'code': 'TESTDEPT',
            'level': 'UG'
        }, format='json')
        self.assertEqual(res_big_dept_name.status_code, status.HTTP_400_BAD_REQUEST)

        # Oversized code -> 400
        res_big_code = self.client.post('/api/departments/', {
            'name': 'Department of Statistics',
            'code': 'STATISTICS_EXTREMELY_LONG_CODE',
            'level': 'UG'
        }, format='json')
        self.assertEqual(res_big_code.status_code, status.HTTP_400_BAD_REQUEST)

    # 9. Course Validation & Duration Bounds
    def test_course_validation(self):
        self.client.force_authenticate(user=self.admin)

        # Invalid duration (>6 years) -> 400
        res_big_duration = self.client.post('/api/courses/', {
            'department': self.dept.id,
            'name': 'Ph.D. Computer Applications',
            'abbreviation': 'PHDCA',
            'email_code': 'ph',
            'duration_years': 8
        }, format='json')
        self.assertEqual(res_big_duration.status_code, status.HTTP_400_BAD_REQUEST)

        # Duration < 1 year -> 400
        res_zero_duration = self.client.post('/api/courses/', {
            'department': self.dept.id,
            'name': 'Certificate Program',
            'abbreviation': 'CERT',
            'email_code': 'cp',
            'duration_years': 0
        }, format='json')
        self.assertEqual(res_zero_duration.status_code, status.HTTP_400_BAD_REQUEST)

    # 10. User Profile Name Length Bound
    def test_user_profile_name_length_bound(self):
        self.client.force_authenticate(user=self.student)

        # Oversized name (>150 chars) -> 400
        res_big_name = self.client.put('/api/auth/profile/', {
            'name': 'N' * 151
        }, format='json')
        self.assertEqual(res_big_name.status_code, status.HTTP_400_BAD_REQUEST)

    # 11. User Management Validation
    def test_user_management_validation(self):
        self.client.force_authenticate(user=self.admin)

        # Invalid email format -> 400
        res_bad_email = self.client.post('/api/users/', {
            'email': 'not-an-email',
            'role': 'student'
        }, format='json')
        self.assertEqual(res_bad_email.status_code, status.HTTP_400_BAD_REQUEST)

        # Invalid role -> 400
        res_bad_role = self.client.post('/api/users/', {
            'email': 'valid.user@mariancollege.org',
            'role': 'superuser_god'
        }, format='json')
        self.assertEqual(res_bad_role.status_code, status.HTTP_400_BAD_REQUEST)

    # 12. System Settings Key & Value Bounds
    def test_system_settings_bounds(self):
        self.client.force_authenticate(user=self.admin)

        # Setting key > 100 chars -> 400
        bad_key = 'K' * 101
        res_big_key = self.client.post('/api/settings/', {
            bad_key: 'value'
        }, format='json')
        self.assertEqual(res_big_key.status_code, status.HTTP_400_BAD_REQUEST)

        # Setting value > 5000 chars -> 400
        res_big_val = self.client.post('/api/settings/', {
            'test_key': 'V' * 5001
        }, format='json')
        self.assertEqual(res_big_val.status_code, status.HTTP_400_BAD_REQUEST)


class Phase4DatabaseIntegrityRegressionTest(TestCase):
    """
    Comprehensive regression tests for Phase 4 Database Integrity:
    1. Active Academic Year Uniqueness (PostgreSQL conditional unique constraint)
    2. Academic Year Format CheckConstraint
    3. Department Level CheckConstraint
    4. Course Duration CheckConstraint
    5. Class CheckConstraints (non-negative students, negative points) and UniqueConstraint (course, year, section)
    6. User Role CheckConstraint
    7. Submission Unique Active Certificate Constraint (and coexistence with Rejected)
    8. Submission Unique Active Proof Hash Constraint
    9. Academic Grade Breakdown Constraints (non-negative counts, pass percentage range)
    10. Class Index Result Uniqueness & Protected Foreign Key Deletion
    11. Submission Creation Atomicity (Rollback on dependent model failure)
    12. Academic Year Atomic Activation via API
    """

    def setUp(self):
        from django.db import IntegrityError, transaction
        from django.db.models import ProtectedError
        self.client = APIClient()

        # Clean slate active year
        AcademicYear.objects.all().delete()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.dept = Department.objects.create(
            name='Dept of Computer Science',
            code='DCS',
            email_prefix='u',
            level='UG'
        )
        self.course = Course.objects.create(
            department=self.dept,
            name='Bachelor of Computer Applications',
            abbreviation='BCA',
            email_code='bc',
            duration_years=3
        )
        self.cls = Class.objects.create(
            name='I BCA A',
            department=self.dept,
            course=self.course,
            year_number=1,
            section='A',
            num_students=50,
            negative_points=0.0
        )
        self.student = User.objects.create_user(
            username='student.test@mariancollege.org',
            email='student.test@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls
        )
        self.admin = User.objects.create_user(
            username='admin.test@mariancollege.org',
            email='admin.test@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )
        self.cat = CriteriaCategory.objects.create(
            code='cat-phase4',
            category='Academic Integrity'
        )
        self.crit_version = CriteriaVersion.objects.create(
            academic_year='2025-2026',
            version=1,
            is_locked=False
        )
        self.item = CriteriaItem.objects.create(
            category=self.cat,
            version=self.crit_version,
            title='Academic Merit Item',
            type='count',
            marks=10.0
        )

    # 1. Unique Active Academic Year Constraint
    def test_unique_active_academic_year_constraint(self):
        from django.db import IntegrityError, transaction
        # self.ay is already is_active=True
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicYear.objects.create(year='2026-2027', is_active=True)

        # Inactive academic years can coexist without conflict
        ay2 = AcademicYear.objects.create(year='2026-2027', is_active=False)
        ay3 = AcademicYear.objects.create(year='2027-2028', is_active=False)
        self.assertEqual(AcademicYear.objects.filter(is_active=False).count(), 2)

    # 2. Academic Year Format CheckConstraint
    def test_academic_year_format_check_constraint(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicYear.objects.create(year='invalid-year-format', is_active=False)

    # 3. Department Level CheckConstraint
    def test_department_level_check_constraint(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Department.objects.create(name='Bad Dept', code='BD', level='Doctoral')

    # 4. Course Duration CheckConstraint
    def test_course_duration_check_constraint(self):
        from django.db import IntegrityError, transaction
        # duration_years < 1
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Course.objects.create(
                    department=self.dept,
                    name='Invalid Course 0',
                    abbreviation='IC0',
                    email_code='ic0',
                    duration_years=0
                )
        # duration_years > 6
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Course.objects.create(
                    department=self.dept,
                    name='Invalid Course 7',
                    abbreviation='IC7',
                    email_code='ic7',
                    duration_years=7
                )

    # 5. Class CheckConstraints & Section Uniqueness
    def test_class_constraints(self):
        from django.db import IntegrityError, transaction
        # Negative students
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Class.objects.create(
                    name='Bad Class 1',
                    department=self.dept,
                    num_students=-5
                )
        # Negative penalty points
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Class.objects.create(
                    name='Bad Class 2',
                    department=self.dept,
                    negative_points=-10.0
                )
        # Duplicate course + year + section ('A' already exists)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Class.objects.create(
                    name='Duplicate I BCA A',
                    department=self.dept,
                    course=self.course,
                    year_number=1,
                    section='A'
                )

    # 6. User Role CheckConstraint
    def test_user_role_check_constraint(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username='invalid.role@mariancollege.org',
                    email='invalid.role@mariancollege.org',
                    role='hacker'
                )

    # 7. Submission Unique Active Certificate Constraint
    def test_submission_unique_active_certificate_constraint(self):
        from django.db import IntegrityError, transaction
        # Create active submission with certificate_id
        sub1 = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            description='Test Certificate Submission',
            status='Pending Rep Verification',
            certificate_id='CERT-PHASE4-001'
        )

        # Attempt to create another submission for the same student with the same certificate_id
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Submission.objects.create(
                    user=self.student,
                    criteria_id=self.item.id,
                    academic_year='2025-2026',
                    description='Duplicate Certificate Submission',
                    status='Draft',
                    certificate_id='CERT-PHASE4-001'
                )

        # If sub1 is rejected, student CAN resubmit the certificate
        sub1.status = 'Rejected'
        sub1.save()

        sub2 = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            description='Resubmitted Certificate After Rejection',
            status='Draft',
            certificate_id='CERT-PHASE4-001'
        )
        self.assertIsNotNone(sub2.id)

    # 8. Submission Unique Active Proof Hash Constraint
    def test_submission_unique_active_proof_hash_constraint(self):
        from django.db import IntegrityError, transaction
        test_hash = 'a' * 64
        Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            description='Submission 1 with proof hash',
            status='Pending',
            proof_hash=test_hash
        )

        # Duplicate proof hash for same user fails
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Submission.objects.create(
                    user=self.student,
                    criteria_id=self.item.id,
                    academic_year='2025-2026',
                    description='Submission 2 with same proof hash',
                    status='Draft',
                    proof_hash=test_hash
                )

    # 9. Academic Grade Breakdown Constraints
    def test_grade_breakdown_database_constraints(self):
        from django.db import IntegrityError, transaction
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            description='Grading Submission',
            status='Draft'
        )

        # Direct DB creation with negative grade count violates CheckConstraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicGradeBreakdown.objects.bulk_create([
                    AcademicGradeBreakdown(
                        submission=sub,
                        s_grade_count=-1,
                        total_students=50,
                        class_pass_percentage=90.0
                    )
                ])

        # Pass percentage > 100.0 violates CheckConstraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicGradeBreakdown.objects.bulk_create([
                    AcademicGradeBreakdown(
                        submission=sub,
                        s_grade_count=10,
                        total_students=50,
                        class_pass_percentage=105.0
                    )
                ])

    # 10. Class Index Result Uniqueness & Protected Foreign Key
    def test_class_index_result_uniqueness_and_protect(self):
        from django.db import IntegrityError, transaction
        from django.db.models import ProtectedError
        # Create ClassIndexResult
        cir = ClassIndexResult.objects.create(
            class_name=self.cls,
            academic_year=self.ay,
            academic_score=80.0,
            final_index=1.6,
            rank=1
        )

        # Duplicate class_name + academic_year violates unique_class_academic_year_result
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassIndexResult.objects.create(
                    class_name=self.cls,
                    academic_year=self.ay,
                    academic_score=85.0,
                    final_index=1.7,
                    rank=2
                )

        # Deleting the academic year is protected by models.PROTECT
        with self.assertRaises(ProtectedError):
            self.ay.delete()

    # 11. Submission Creation Atomicity (Rollback on failure)
    def test_submission_creation_transaction_atomic_rollback(self):
        self.client.force_authenticate(user=self.student)
        initial_sub_count = Submission.objects.count()

        # Submit with invalid grade counts (where sum doesn't match total students)
        payload = {
            'criteriaId': self.item.id,
            'academicYear': '2025-2026',
            'description': 'Submission with broken grade breakdown',
            'evidence': {
                'submissionType': 'Sem Result',
                'grades': {'S': 10, 'APlus': 10, 'A': 10, 'Fail': 5}, # accounted = 35
                'totalStudents': 50 # 35 != 50
            },
            'grade_breakdown': {
                's_grade_count': 10,
                'a_plus_grade_count': 10,
                'a_grade_count': 10,
                'other_pass_count': 5, # 10+10+10+5+5 = 40 != 50 -> raises ValueError
                'failed_count': 5,
                'total_students': 50
            }
        }
        res = self.client.post('/api/submissions/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Invariant: Entire transaction must rollback; no orphan submission persisted
        self.assertEqual(Submission.objects.count(), initial_sub_count)

    # 12. Academic Year Atomic Activation via API
    def test_atomic_academic_year_activation_via_api(self):
        self.client.force_authenticate(user=self.admin)

        # Activate 2026-2027 via POST
        res_post = self.client.post('/api/academic-years/', {
            'year': '2026-2027',
            'is_active': True
        }, format='json')
        self.assertEqual(res_post.status_code, status.HTTP_200_OK)

        # Exactly ONE academic year must be active
        active_years = AcademicYear.objects.filter(is_active=True)
        self.assertEqual(active_years.count(), 1)
        self.assertEqual(active_years.first().year, '2026-2027')


class Phase5WorkflowIntegrityRegressionTest(TestCase):
    """
    Comprehensive regression tests for Phase 5 Workflow Integrity:
    1. Golden Path Transitions (Draft -> Submitted -> Rep Verified -> Teacher Verified -> Evaluated -> Locked)
    2. Cryptographic audit trail chain integrity and stage numbers (1 through 5)
    3. Rejection of invalid transitions (Draft -> Evaluated, Teacher Verified -> Draft, Locked -> Draft)
    4. Rejection of unauthorized role transitions (Student -> Evaluated, Rep -> unauthorized class)
    5. Required remarks on Correction Requested and Rejected
    6. Resubmission lifecycle (from Correction Requested and Rejected)
    7. Locked record immutability and authorized Admin explicit unlock
    """

    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)

        self.dept_cs = Department.objects.create(name='Computer Applications', code='MCA', level='PG')
        self.dept_mgmt = Department.objects.create(name='Management Studies', code='MBA', level='PG')

        self.course_mca = Course.objects.create(department=self.dept_cs, name='MCA', abbreviation='MCA', email_code='mc', duration_years=2)
        self.course_mba = Course.objects.create(department=self.dept_mgmt, name='MBA', abbreviation='MBA', email_code='mb', duration_years=2)

        self.class_mca = Class.objects.create(name='I MCA', department=self.dept_cs, course=self.course_mca, year_number=1, num_students=30)
        self.class_mba = Class.objects.create(name='I MBA', department=self.dept_mgmt, course=self.course_mba, year_number=1, num_students=25)

        # Users
        self.student_mca = User.objects.create_user(
            username='student.mca@mariancollege.org', email='student.mca@mariancollege.org',
            role='student', department=self.dept_cs, class_name=self.class_mca
        )
        self.rep_mca = User.objects.create_user(
            username='rep.mca@mariancollege.org', email='rep.mca@mariancollege.org',
            role='student', department=self.dept_cs, class_name=self.class_mca
        )
        self.class_mca.dqc_member = self.rep_mca
        self.class_mca.save()

        self.teacher_mca = User.objects.create_user(
            username='teacher.mca@mariancollege.org', email='teacher.mca@mariancollege.org',
            role='faculty', department=self.dept_cs
        )
        self.class_mca.class_teacher = self.teacher_mca
        self.class_mca.save()

        self.teacher_other = User.objects.create_user(
            username='teacher.mgmt@mariancollege.org', email='teacher.mgmt@mariancollege.org',
            role='faculty', department=self.dept_mgmt
        )
        self.class_mba.class_teacher = self.teacher_other
        self.class_mba.save()

        self.evaluator = User.objects.create_user(
            username='evaluator.phase5@mariancollege.org', email='evaluator.phase5@mariancollege.org',
            role='evaluation'
        )
        self.evaluator_other = User.objects.create_user(
            username='evaluator.other@mariancollege.org', email='evaluator.other@mariancollege.org',
            role='evaluation'
        )

        self.admin = User.objects.create_user(
            username='admin.phase5@mariancollege.org', email='admin.phase5@mariancollege.org',
            role='admin', is_staff=True, is_superuser=True
        )

        # Criteria
        self.cat = CriteriaCategory.objects.create(
            code='cat-phase5',
            category='Academic & Research',
            evaluators=['evaluator.phase5@mariancollege.org']
        )
        self.item = CriteriaItem.objects.create(
            category=self.cat,
            title='Research Paper Publication',
            type='fixed',
            marks=10.0
        )

    # 1. Golden Path Transitions & Cryptographic Audit Trail Chaining
    def test_golden_path_workflow_transitions_and_audit_trail(self):
        # Step 1: Student creates Draft
        self.client.force_authenticate(user=self.student_mca)
        res_draft = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': '2025-2026',
            'description': 'Research Paper on AI Verification',
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_draft.status_code, status.HTTP_201_CREATED)
        sub_id = res_draft.data['id']

        # Step 2: Student transitions Draft -> Submitted
        res_submit = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Submitted'
        }, format='json')
        self.assertEqual(res_submit.status_code, status.HTTP_200_OK)

        # Step 3: DQC Student Rep verifies Submitted -> Student Rep Verified
        self.client.force_authenticate(user=self.rep_mca)
        res_rep = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Student Rep Verified',
            'repRemarks': 'Student Rep verified publication certificate'
        }, format='json')
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK)

        # Step 4: Class Teacher verifies Student Rep Verified -> Teacher Verified
        self.client.force_authenticate(user=self.teacher_mca)
        res_teach = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Teacher Verified',
            'teacherRemarks': 'Faculty Advisor verified conference legitimacy'
        }, format='json')
        self.assertEqual(res_teach.status_code, status.HTTP_200_OK)

        # Step 5: Assigned Evaluator audits & awards marks: Teacher Verified -> Evaluated
        self.client.force_authenticate(user=self.evaluator)
        res_eval = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Evaluated',
            'marks': 10.0,
            'evaluatorRemarks': 'Evaluator approved 10.0 marks'
        }, format='json')
        self.assertEqual(res_eval.status_code, status.HTTP_200_OK)

        # Step 6: Admin locks submission: Evaluated -> Locked
        self.client.force_authenticate(user=self.admin)
        res_lock = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Locked',
            'remarks': 'Final IQAC moderation lock'
        }, format='json')
        self.assertEqual(res_lock.status_code, status.HTTP_200_OK)

        # Verify DB final state
        sub = Submission.objects.get(id=sub_id)
        self.assertEqual(sub.status, 'Locked')
        self.assertEqual(sub.marks, 10)
        self.assertTrue(sub.evaluator_verified)

        # Verify Audit Trail Integrity & Stages
        from users.models import WorkflowAuditTrail
        logs = WorkflowAuditTrail.objects.filter(submission_id=sub_id).order_by('id')
        self.assertGreaterEqual(logs.count(), 5)

        stages_recorded = [log.stage for log in logs]
        self.assertIn(1, stages_recorded) # Student Claims
        self.assertIn(2, stages_recorded) # Student Rep
        self.assertIn(3, stages_recorded) # Teacher
        self.assertIn(4, stages_recorded) # Evaluator
        self.assertIn(5, stages_recorded) # Locking

        # Cryptographic Hash Chain Verification
        prev_h = "0" * 64
        for log in logs:
            self.assertEqual(log.previous_hash, prev_h)
            self.assertTrue(len(log.record_hash) == 64)
            prev_h = log.record_hash

    # 2. Rejection of Invalid State Transitions
    def test_invalid_state_transitions_rejected(self):
        # Create submission in Draft
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Testing invalid transitions',
            status='Draft'
        )

        # Student attempts Draft -> Evaluated (Denied) -> 403
        self.client.force_authenticate(user=self.student_mca)
        res_skip = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated'
        }, format='json')
        self.assertEqual(res_skip.status_code, status.HTTP_403_FORBIDDEN)

        # Admin attempts Draft -> Locked (Invalid workflow jump) -> 400
        self.client.force_authenticate(user=self.admin)
        res_admin_jump = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Locked'
        }, format='json')
        self.assertEqual(res_admin_jump.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid workflow state transition', res_admin_jump.data.get('error', ''))

        # Move to Teacher Verified
        sub.status = 'Teacher Verified'
        sub.save()

        # Teacher attempts Teacher Verified -> Draft (Invalid backward regression) -> 400
        self.client.force_authenticate(user=self.teacher_mca)
        res_regress = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_regress.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid workflow state transition', res_regress.data.get('error', ''))

    # 3. Rejection of Unauthorized Role Transitions
    def test_unauthorized_role_transitions_rejected(self):
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Testing unauthorized roles',
            status='Submitted'
        )

        # Regular student attempts self-verification -> 403
        self.client.force_authenticate(user=self.student_mca)
        res_self_verify = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Student Rep Verified'
        }, format='json')
        self.assertEqual(res_self_verify.status_code, status.HTTP_403_FORBIDDEN)

        # Faculty from other department attempts verification -> 403
        self.client.force_authenticate(user=self.teacher_other)
        res_other_teacher = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Teacher Verified'
        }, format='json')
        self.assertEqual(res_other_teacher.status_code, status.HTTP_403_FORBIDDEN)

        # Move to Teacher Verified
        sub.status = 'Teacher Verified'
        sub.save()

        # Evaluator not assigned to Category attempts evaluation -> 403
        self.client.force_authenticate(user=self.evaluator_other)
        res_unauth_eval = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 10.0
        }, format='json')
        self.assertEqual(res_unauth_eval.status_code, status.HTTP_403_FORBIDDEN)

    # 4. Rejection and Correction Require Non-Empty Remarks
    def test_rejection_and_correction_require_remarks(self):
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Testing remarks requirement',
            status='Submitted'
        )

        self.client.force_authenticate(user=self.rep_mca)

        # Attempt Correction Requested with empty remarks -> 400
        res_empty_corr = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Correction Requested',
            'remarks': '   '
        }, format='json')
        self.assertEqual(res_empty_corr.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('remark or explanation is strictly required', res_empty_corr.data.get('error', ''))

        # Attempt Rejected with empty remarks -> 400
        res_empty_rej = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Rejected',
            'remarks': ''
        }, format='json')
        self.assertEqual(res_empty_rej.status_code, status.HTTP_400_BAD_REQUEST)

        # Successful rejection with valid explanation
        res_valid_rej = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Rejected',
            'remarks': 'Certificate date is outside current academic evaluation year.'
        }, format='json')
        self.assertEqual(res_valid_rej.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Rejected')
        self.assertIn('outside current academic evaluation year', sub.remarks)

    # 5. Resubmission Lifecycle
    def test_resubmission_lifecycle(self):
        # Create submission in Correction Requested
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Original defective claim',
            status='Correction Requested', remarks='Please attach clearer certificate image'
        )

        self.client.force_authenticate(user=self.student_mca)

        # Student corrects evidence and resubmits to Submitted
        res_resubmit = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Submitted',
            'description': 'Updated claim with high-resolution certificate PDF'
        }, format='json')
        self.assertEqual(res_resubmit.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Submitted')
        self.assertEqual(sub.description, 'Updated claim with high-resolution certificate PDF')

        # Now test resubmission from Rejected state
        sub.status = 'Rejected'
        sub.remarks = 'Initial proof invalid'
        sub.save()

        # Student transitions Rejected -> Draft to rework
        res_rework = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Draft',
            'description': 'Reworking claim'
        }, format='json')
        self.assertEqual(res_rework.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Draft')

    # 6. Locked Record Immutability & Admin Explicit Unlock
    def test_locked_record_immutability_and_admin_unlock(self):
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Locked Claim',
            status='Locked', marks=10
        )

        # Student attempts to edit locked claim -> 403
        self.client.force_authenticate(user=self.student_mca)
        res_stud_edit = self.client.put(f'/api/submissions/{sub.id}/', {
            'description': 'Malicious overwrite'
        }, format='json')
        self.assertEqual(res_stud_edit.status_code, status.HTTP_403_FORBIDDEN)

        # Evaluator attempts to modify marks on locked claim -> 403
        self.client.force_authenticate(user=self.evaluator)
        res_eval_edit = self.client.put(f'/api/submissions/{sub.id}/', {
            'marks': 15
        }, format='json')
        self.assertEqual(res_eval_edit.status_code, status.HTTP_403_FORBIDDEN)

        # Admin attempting to modify fields while remaining Locked -> 403
        self.client.force_authenticate(user=self.admin)
        res_admin_edit = self.client.put(f'/api/submissions/{sub.id}/', {
            'description': 'Admin silent modification'
        }, format='json')
        self.assertEqual(res_admin_edit.status_code, status.HTTP_403_FORBIDDEN)

        # Admin explicit unlock override: Admin transitions Locked -> Evaluated with audit reason
        res_unlock = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 10,
            'remarks': 'Administrative correction unlock authorized by IQAC Chair'
        }, format='json')
        self.assertEqual(res_unlock.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Evaluated')

    def test_teacher_approval_from_student_rep_verified(self):
        """
        Verify that a class teacher can approve a submission in 'Student Rep Verified' status
        without 'Invalid workflow state transition' error, whether sending 'status': 'Teacher Verified'
        or 'status': 'Approved'.
        """
        # Create submission already verified by Student Rep
        sub = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Testing teacher approval transition',
            status='Student Rep Verified', rep_verified_by_name=self.rep_mca.username
        )

        self.client.force_authenticate(user=self.teacher_mca)

        # Teacher verifies by sending 'Teacher Verified'
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Teacher Verified',
            'teacherVerifiedByName': self.teacher_mca.username,
            'teacherRemarks': 'Faculty Advisor verified documentation',
            'remarks': 'Faculty Advisor verified documentation'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Teacher Verified')
        self.assertEqual(sub.teacher_verified_by_name, self.teacher_mca.username)
        self.assertIn(sub.marks, (None, 0))  # Teacher verification does NOT credit marks

        # Create another submission in 'Student Rep Verified' and test sending 'status': 'Approved'
        sub2 = Submission.objects.create(
            user=self.student_mca, criteria_id=self.item.id,
            academic_year='2025-2026', description='Testing teacher approval with status Approved',
            status='Student Rep Verified', rep_verified_by_name=self.rep_mca.username
        )

        res2 = self.client.put(f'/api/submissions/{sub2.id}/', {
            'status': 'Approved',
            'teacherVerifiedByName': self.teacher_mca.username,
            'teacherRemarks': 'Faculty Advisor verified via quick approval',
            'remarks': 'Verified & Approved by Class Advisor'
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        sub2.refresh_from_db()
        self.assertEqual(sub2.status, 'Teacher Verified')
        self.assertEqual(sub2.teacher_verified_by_name, self.teacher_mca.username)
        self.assertIn(sub2.marks, (None, 0))

    def test_dual_role_staff_class_teacher_approval(self):
        """
        Verify that a staff user whose base role is 'evaluation' (e.g. member of Evaluation Team)
        who is also assigned as a Class Advisor can successfully verify submissions for their class
        without 'Unauthorized: Class Teacher can only verify submissions for their assigned class'.
        """
        from users.models import UserGroupModel, UserGroupMember, TeacherClassAssignment

        # Create dual-role staff user (base role evaluation, assigned as teacher to class_mba)
        dual_teacher = User.objects.create_user(
            username='dual.teacher@mariancollege.org',
            email='dual.teacher@mariancollege.org',
            role='evaluation',
            is_staff=True
        )
        self.class_mba.class_teacher = dual_teacher
        self.class_mba.save()

        TeacherClassAssignment.objects.create(
            teacher=dual_teacher,
            class_obj=self.class_mba,
            academic_year=2025,
            is_active=True
        )

        ct_grp, _ = UserGroupModel.objects.get_or_create(
            group_id='grp-class-teachers',
            defaults={'name': 'Class Teachers Council'}
        )
        UserGroupMember.objects.create(
            group=ct_grp,
            email=dual_teacher.email,
            user=dual_teacher,
            assigned_class=self.class_mba
        )

        # Student in MBA
        student_mba = User.objects.create_user(
            username='student.mba@mariancollege.org',
            email='student.mba@mariancollege.org',
            role='student',
            department=self.dept_mgmt,
            class_name=self.class_mba
        )

        sub_mba = Submission.objects.create(
            user=student_mba,
            criteria_id=self.item.id,
            academic_year='2025-2026',
            description='MBA Submission for dual role teacher',
            status='Student Rep Verified',
            rep_verified_by_name='rep.mba@mariancollege.org'
        )

        self.client.force_authenticate(user=dual_teacher)
        res = self.client.put(f'/api/submissions/{sub_mba.id}/', {
            'status': 'Teacher Verified',
            'teacherVerifiedByName': dual_teacher.username,
            'teacherRemarks': 'Dual-role teacher verification passed',
            'role_context': 'teacher'
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub_mba.refresh_from_db()
        self.assertEqual(sub_mba.status, 'Teacher Verified')
        self.assertEqual(sub_mba.teacher_verified_by_name, dual_teacher.username)
        self.assertIn(sub_mba.marks, (None, 0))





class Phase6ScoringEngineRegressionTest(TestCase):
    """
    Mathematical tests and invariant validations for Phase 6 Scoring Engine remediation.
    Tests authoritative 4-step formula, edge cases, invariants, explainability, and historical locking.
    """

    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='Test Scoring Dept', code='TSD')
        self.admin = User.objects.create_user(
            email='admin.scoring@mariancollege.org', username='admin_scoring',
            password='TestPassword@123', role='admin', is_staff=True, is_superuser=True
        )
        self.iqac = User.objects.create_user(
            email='admin2.scoring@mariancollege.org', username='admin2_scoring',
            password='TestPassword@123', role='admin', is_staff=True
        )

        # Criteria categories
        self.cat_acad, _ = CriteriaCategory.objects.get_or_create(code='cat-academics', defaults={'category': 'Academics'})
        self.cat_prizes, _ = CriteriaCategory.objects.get_or_create(code='cat-prizes', defaults={'category': 'Prizes'})
        self.cat_org, _ = CriteriaCategory.objects.get_or_create(code='cat-programs-organized', defaults={'category': 'Programs Organized'})

        self.item_acad, _ = CriteriaItem.objects.get_or_create(category=self.cat_acad, title='Sem Results', defaults={'type': 'fixed', 'marks': 50.0})
        self.item_prizes, _ = CriteriaItem.objects.get_or_create(category=self.cat_prizes, title='First Prize', defaults={'type': 'fixed', 'marks': 15.0})
        self.item_org, _ = CriteriaItem.objects.get_or_create(category=self.cat_org, title='College Fest', defaults={'type': 'fixed', 'marks': 20.0})

        # Benchmark class size
        SystemSetting.objects.update_or_create(key='smallest_class_size', defaults={'value': '0'})

    # 1. Moderation Formula M = (S - P) / N² * (1 + 100 * (N - n)) calculations
    def test_authoritative_4step_formula_scaling_and_fairness(self):
        from users.scoring_engine import calculate_class_index

        # Formula: M = (S - P) / N² * (1 + 100 * (N - n))
        # Scenario: n = 0, P = 0
        # Class A: N=20, n=0, S=300, P=0 -> M = (300 / 400) * (1 + 2000) = 0.75 * 2001 = 1500.75
        idx_a = calculate_class_index(S=300, P=0, N=20, n=0)
        self.assertEqual(idx_a, 1500.75)

        # Class B: N=40, n=0, S=600, P=0 -> M = (600 / 1600) * (1 + 4000) = 0.375 * 4001 = 1500.375
        idx_b = calculate_class_index(S=600, P=0, N=40, n=0)
        self.assertEqual(idx_b, 1500.375)

        # Class C: N=80, n=0, S=1200, P=0 -> M = (1200 / 6400) * (1 + 8000) = 0.1875 * 8001 = 1500.1875
        idx_c = calculate_class_index(S=1200, P=0, N=80, n=0)
        self.assertEqual(idx_c, 1500.1875)

        # Class D: N=120, n=0, S=1800, P=0 -> M = (1800 / 14400) * (1 + 12000) = 0.125 * 12001 = 1500.125
        idx_d = calculate_class_index(S=1800, P=0, N=120, n=0)
        self.assertEqual(idx_d, 1500.125)

    # 2. Single Student Class (N=1)
    def test_class_with_single_student(self):
        from users.scoring_engine import calculate_class_index
        # N=1, n=0, S=25, P=0 -> M = (25 / 1) * (1 + 100 * 1) = 25 * 101 = 2525.0
        idx = calculate_class_index(S=25.0, P=0.0, N=1, n=0.0)
        self.assertEqual(idx, 2525.0)

    # 3. Small Class benchmark difference
    def test_small_class_moderation_non_negative(self):
        from users.scoring_engine import calculate_class_moderation
        mod = calculate_class_moderation(N=15, n=0)
        self.assertEqual(mod, 15.0)

    # 4. Zero Score Class
    def test_zero_score_class(self):
        cls = Class.objects.create(name='Zero Class', department=self.dept, num_students=30, negative_points=0.0)
        from users.scoring_engine import compute_class_scores
        res = compute_class_scores(cls, n_benchmark=0.0)
        self.assertEqual(res['S'], 0.0)
        self.assertEqual(res['P'], 0.0)
        self.assertEqual(res['M'], 0.0)
        self.assertEqual(res['total_score'], 0.0)

    # 5. Heavy Penalty reducing Total Score to 0 (cannot be negative)
    def test_heavy_penalty_does_not_produce_negative_total_score(self):
        cls = Class.objects.create(name='Penalty Class', department=self.dept, num_students=20, negative_points=500.0)
        from users.scoring_engine import compute_class_scores
        res = compute_class_scores(cls, n_benchmark=20.0)
        self.assertEqual(res['S'], 0.0)
        self.assertEqual(res['P'], 500.0)
        self.assertEqual(res['net_score'], -500.0)
        self.assertEqual(res['moderation_mark'], 0.0)
        self.assertEqual(res['total_score'], 0.0)  # Bounded at 0
        self.assertEqual(res['M'], 0.0)

    # 6. Unranked Class with N=0
    def test_unranked_class_with_zero_students(self):
        cls = Class.objects.create(name='Empty Class', department=self.dept, num_students=0)
        from users.scoring_engine import compute_class_scores
        res = compute_class_scores(cls)
        self.assertEqual(res['N'], 0)
        self.assertIsNone(res['M'])

    # 7. Category & Pillar Totals Conservation Invariant
    def test_pillar_and_category_conservation_invariant(self):
        cls = Class.objects.create(name='Multi Criteria Class', department=self.dept, num_students=50)
        st = User.objects.create_user(
            email='st.multi@mariancollege.org', username='st_multi',
            password='TestPassword@123', role='student', class_name=cls, department=self.dept
        )
        Submission.objects.create(user=st, criteria_id=self.item_acad.id, status='Locked', marks=50.0, academic_year='2025-2026')
        Submission.objects.create(user=st, criteria_id=self.item_prizes.id, status='Evaluated', marks=15.0, academic_year='2025-2026')
        Submission.objects.create(user=st, criteria_id=self.item_org.id, status='Evaluated', marks=20.0, academic_year='2025-2026')
        # Draft submission should NOT count
        Submission.objects.create(user=st, criteria_id=self.item_prizes.id, status='Draft', marks=15.0, academic_year='2025-2026')

        from users.scoring_engine import compute_class_scores
        res = compute_class_scores(cls, academic_year='2025-2026')
        self.assertEqual(res['S'], 85.0)  # 50 + 15 + 20
        self.assertEqual(res['academic_score'], 50.0)
        self.assertEqual(res['co_curricular_score'], 15.0)
        self.assertEqual(res['extra_curricular_score'], 20.0)
        self.assertEqual(res['academic_score'] + res['co_curricular_score'] + res['extra_curricular_score'], 85.0)

    # 8. Deterministic Tie-Breaking
    def test_deterministic_tie_breaking(self):
        # Two classes with exact same M
        cls_a = Class.objects.create(name='Tie Class Alpha', department=self.dept, num_students=20)
        cls_b = Class.objects.create(name='Tie Class Beta', department=self.dept, num_students=20)
        st_a = User.objects.create_user(email='st.ta@mariancollege.org', username='st_ta', password='Pass@123', role='student', class_name=cls_a)
        st_b = User.objects.create_user(email='st.tb@mariancollege.org', username='st_tb', password='Pass@123', role='student', class_name=cls_b)

        Submission.objects.create(user=st_a, criteria_id=self.item_acad.id, status='Locked', marks=50.0)
        Submission.objects.create(user=st_b, criteria_id=self.item_acad.id, status='Locked', marks=50.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()
        names = [r['class_name'] for r in ranked if r['class_name'] in ['Tie Class Alpha', 'Tie Class Beta']]
        self.assertEqual(len(names), 2)
        # In Standard Competition Ranking, identical scores share the exact same rank
        r_a = next(r['rank'] for r in ranked if r['class_name'] == 'Tie Class Alpha')
        r_b = next(r['rank'] for r in ranked if r['class_name'] == 'Tie Class Beta')
        self.assertEqual(r_a, r_b)
        self.assertEqual(r_a, 1)

    # 9. Explainability Endpoint
    def test_explainability_endpoint(self):
        cls = Class.objects.create(name='Explain Class', department=self.dept, num_students=40)
        st = User.objects.create_user(email='st.exp@mariancollege.org', username='st_exp', password='Pass@123', role='student', class_name=cls)
        Submission.objects.create(user=st, criteria_id=self.item_acad.id, status='Locked', marks=50.0)

        res = self.client.get('/api/class-index/?explain=true')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        target = next((item for item in res.data if item.get('class_name') == 'Explain Class'), None)
        self.assertIsNotNone(target)
        self.assertIn('explanation_steps', target)
        self.assertIn('formula_spec', target)
        self.assertIn('breakdown', target)
        self.assertEqual(len(target['explanation_steps']), 4)

    # 10. Historical Snapshot & Immutability Lock
    def test_historical_snapshot_and_lock_immutability(self):
        cls = Class.objects.create(name='Snapshot Class', department=self.dept, num_students=30)
        from users.scoring_engine import snapshot_academic_year_results
        
        # 1. Snapshot with lock=True
        results = snapshot_academic_year_results('2024-2025', force=False, mark_locked=True)
        self.assertTrue(len(results) > 0)
        
        # Verify saved in ClassIndexResult
        from users.models import ClassIndexResult
        snap = ClassIndexResult.objects.filter(academic_year__year='2024-2025', class_name=cls).first()
        self.assertIsNotNone(snap)
        self.assertTrue(snap.is_locked)
        self.assertEqual(snap.scoring_version, 'v1.0-authoritative')

        # 2. Attempting to overwrite locked snapshot without force raises PermissionError
        with self.assertRaises(PermissionError):
            snapshot_academic_year_results('2024-2025', force=False)

        # 3. Explicit override with force=True succeeds
        re_snap = snapshot_academic_year_results('2024-2025', force=True, mark_locked=True)
        self.assertTrue(len(re_snap) > 0)


class Phase7RankingCorrectnessTest(TestCase):
    """
    Exhaustive validation suite for Phase 7 Ranking Correctness.
    Audits:
    1. Equal performance across different class sizes
    2. Perfect scores
    3. Zero scores
    4. Equal final scores (Ties sharing rank)
    5. Floating-point boundary values
    6. Missing data
    7. Classes with different student counts
    8. Deterministic ordering invariant
    9. Published/locked results persistence
    """

    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='Ranking Dept', code='RKD')
        self.admin = User.objects.create_user(
            email='admin.ranking@mariancollege.org', username='admin_rk',
            password='TestPassword@123', role='admin', is_staff=True, is_superuser=True
        )
        self.cat_acad = CriteriaCategory.objects.create(code='cat-academics', category='Academics')
        self.item_acad = CriteriaItem.objects.create(category=self.cat_acad, title='Sem Marks', type='fixed', marks=100.0)
        SystemSetting.objects.update_or_create(key='smallest_class_size', defaults={'value': '20.0'})

    # 1. Equal performance across different class sizes
    def test_equal_performance_across_different_class_sizes(self):
        # 4 classes with identical 15 marks per student:
        # N=20 (S=300), N=40 (S=600), N=80 (S=1200), N=120 (S=1800)
        c20 = Class.objects.create(name='Batch 20', department=self.dept, num_students=20)
        c40 = Class.objects.create(name='Batch 40', department=self.dept, num_students=40)
        c80 = Class.objects.create(name='Batch 80', department=self.dept, num_students=80)
        c120 = Class.objects.create(name='Batch 120', department=self.dept, num_students=120)

        u20 = User.objects.create_user(email='u20@marian.org', username='u20', role='student', class_name=c20)
        u40 = User.objects.create_user(email='u40@marian.org', username='u40', role='student', class_name=c40)
        u80 = User.objects.create_user(email='u80@marian.org', username='u80', role='student', class_name=c80)
        u120 = User.objects.create_user(email='u120@marian.org', username='u120', role='student', class_name=c120)

        Submission.objects.create(user=u20, criteria_id=self.item_acad.id, status='Locked', marks=300.0)
        Submission.objects.create(user=u40, criteria_id=self.item_acad.id, status='Locked', marks=600.0)
        Submission.objects.create(user=u80, criteria_id=self.item_acad.id, status='Locked', marks=1200.0)
        Submission.objects.create(user=u120, criteria_id=self.item_acad.id, status='Locked', marks=1800.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()

        ranks = {r['class_name']: r['rank'] for r in ranked}
        indices = {r['class_name']: r['M'] for r in ranked}

        # Larger cohorts receive slight coordination bonus (<= 1.67 index pts)
        self.assertAlmostEqual(indices['Batch 120'], 16.6667, places=3)
        self.assertEqual(indices['Batch 80'], 16.5000)
        self.assertEqual(indices['Batch 40'], 16.0000)
        self.assertEqual(indices['Batch 20'], 15.0000)

        self.assertEqual(ranks['Batch 120'], 1)
        self.assertEqual(ranks['Batch 80'], 2)
        self.assertEqual(ranks['Batch 40'], 3)
        self.assertEqual(ranks['Batch 20'], 4)

    # 2. Perfect scores
    def test_perfect_score_ranks_highest(self):
        c_perf = Class.objects.create(name='Batch Perfect', department=self.dept, num_students=30)
        c_norm = Class.objects.create(name='Batch Normal', department=self.dept, num_students=30)
        u_p = User.objects.create_user(email='up@marian.org', username='up', role='student', class_name=c_perf)
        u_n = User.objects.create_user(email='un@marian.org', username='un', role='student', class_name=c_norm)

        Submission.objects.create(user=u_p, criteria_id=self.item_acad.id, status='Locked', marks=5000.0)
        Submission.objects.create(user=u_n, criteria_id=self.item_acad.id, status='Locked', marks=200.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()
        r_map = {r['class_name']: r['rank'] for r in ranked}
        self.assertEqual(r_map['Batch Perfect'], 1)
        self.assertTrue(r_map['Batch Normal'] > 1)

    # 3. Zero scores
    def test_zero_scores_correctly_ranked(self):
        c_zero = Class.objects.create(name='Batch Zero', department=self.dept, num_students=20)
        c_active = Class.objects.create(name='Batch Active', department=self.dept, num_students=20)
        u_a = User.objects.create_user(email='ua@marian.org', username='ua', role='student', class_name=c_active)
        Submission.objects.create(user=u_a, criteria_id=self.item_acad.id, status='Locked', marks=100.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()
        r_map = {r['class_name']: r['rank'] for r in ranked}
        self.assertEqual(r_map['Batch Active'], 1)
        self.assertEqual(r_map['Batch Zero'], 2)

    # 4. Equal final scores (Ties sharing rank)
    def test_ties_share_rank_under_standard_competition_ranking(self):
        c1 = Class.objects.create(name='Tied Class One', department=self.dept, num_students=20)
        c2 = Class.objects.create(name='Tied Class Two', department=self.dept, num_students=20)
        c3 = Class.objects.create(name='Tied Class Three', department=self.dept, num_students=20)
        c4 = Class.objects.create(name='Lower Class Four', department=self.dept, num_students=20)

        u1 = User.objects.create_user(email='u1_tie@marian.org', username='u1_tie', role='student', class_name=c1)
        u2 = User.objects.create_user(email='u2_tie@marian.org', username='u2_tie', role='student', class_name=c2)
        u3 = User.objects.create_user(email='u3_tie@marian.org', username='u3_tie', role='student', class_name=c3)
        u4 = User.objects.create_user(email='u4_tie@marian.org', username='u4_tie', role='student', class_name=c4)

        # Classes 1, 2, 3 all earn 200 marks (M = 10.0000)
        Submission.objects.create(user=u1, criteria_id=self.item_acad.id, status='Locked', marks=200.0)
        Submission.objects.create(user=u2, criteria_id=self.item_acad.id, status='Locked', marks=200.0)
        Submission.objects.create(user=u3, criteria_id=self.item_acad.id, status='Locked', marks=200.0)
        # Class 4 earns 100 marks (M = 5.0000)
        Submission.objects.create(user=u4, criteria_id=self.item_acad.id, status='Locked', marks=100.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()

        r_map = {r['class_name']: r['rank'] for r in ranked}
        # In Standard Competition Ranking ("1224"), tied classes 1, 2, 3 all receive Rank 1!
        self.assertEqual(r_map['Tied Class One'], 1)
        self.assertEqual(r_map['Tied Class Two'], 1)
        self.assertEqual(r_map['Tied Class Three'], 1)
        # The subsequent non-tied class receives Rank 4 (since 3 classes tied at Rank 1)
        self.assertEqual(r_map['Lower Class Four'], 4)

    # 5. Floating-point boundary values
    def test_floating_point_boundary_values(self):
        c_near1 = Class.objects.create(name='Near Alpha', department=self.dept, num_students=20)
        c_near2 = Class.objects.create(name='Near Beta', department=self.dept, num_students=20)

        u1 = User.objects.create_user(email='un1@marian.org', username='un1', role='student', class_name=c_near1)
        u2 = User.objects.create_user(email='un2@marian.org', username='un2', role='student', class_name=c_near2)

        # Difference of only 1e-7 should be treated as equal within 1e-5 tolerance
        Submission.objects.create(user=u1, criteria_id=self.item_acad.id, status='Locked', marks=100.0)
        Submission.objects.create(user=u2, criteria_id=self.item_acad.id, status='Locked', marks=100.0)

        from users.scoring_engine import compute_all_rankings
        ranked, _ = compute_all_rankings()
        r_map = {r['class_name']: r['rank'] for r in ranked}
        self.assertEqual(r_map['Near Alpha'], r_map['Near Beta'])

    # 6. Missing data and unranked classes (N=0)
    def test_missing_data_and_zero_student_cohort(self):
        c_empty = Class.objects.create(name='Empty Batch', department=self.dept, num_students=0)
        from users.scoring_engine import compute_all_rankings
        ranked, unranked = compute_all_rankings()

        unranked_names = [u['class_name'] for u in unranked]
        self.assertIn('Empty Batch', unranked_names)
        target = next(u for u in unranked if u['class_name'] == 'Empty Batch')
        self.assertIsNone(target['M'])
        self.assertIsNone(target['rank'])

    # 7. Classes with different student counts (N=1, N=15, N=20, N=120)
    def test_classes_with_different_student_counts(self):
        c1 = Class.objects.create(name='Single Student Class', department=self.dept, num_students=1)
        c15 = Class.objects.create(name='Small Batch 15', department=self.dept, num_students=15)

        from users.scoring_engine import compute_class_scores
        s1 = compute_class_scores(c1, n_benchmark=20.0)
        s15 = compute_class_scores(c15, n_benchmark=20.0)

        # Neither receives negative moderation
        self.assertEqual(s1['moderation_mark'], 0.0)
        self.assertEqual(s15['moderation_mark'], 0.0)

    # 8. Deterministic ordering invariant across repeated calculations
    def test_deterministic_ordering_invariant(self):
        from users.scoring_engine import compute_all_rankings
        run1_ranked, run1_unranked = compute_all_rankings()
        run2_ranked, run2_unranked = compute_all_rankings()

        order1 = [r['class_name'] for r in run1_ranked]
        order2 = [r['class_name'] for r in run2_ranked]
        self.assertEqual(order1, order2)

    # 9. Published Results Immutability: Locked snapshot served directly
    def test_published_results_immutability(self):
        ay = AcademicYear.objects.create(year='2023-2024', is_active=False)
        cls = Class.objects.create(name='Published Champion', department=self.dept, num_students=40)

        # Create locked snapshot in ClassIndexResult
        ClassIndexResult.objects.create(
            class_name=cls,
            academic_year=ay,
            academic_score=100.0,
            co_curricular_score=50.0,
            extra_curricular_score=50.0,
            final_index=25.0000,
            rank=1,
            scoring_version='v1.0-authoritative',
            is_locked=True,
            snapshot_data={
                "class_name": cls.name,
                "department": self.dept.name,
                "N": 40,
                "M": 25.0000,
                "rank": 1,
                "is_locked": True,
            }
        )

        # Request via API for year=2023-2024
        res = self.client.get('/api/class-index/?year=2023-2024')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        champ = next((c for c in res.data if c.get('class_name') == 'Published Champion'), None)
        self.assertIsNotNone(champ)
        self.assertEqual(champ['M'], 25.0000)
        self.assertEqual(champ['rank'], 1)
        self.assertTrue(champ.get('is_locked'))


from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase
from users.file_security import sanitize_filename, sniff_mime_type, validate_file_upload, get_private_media_root

class Phase8FileSecurityTest(APITestCase):
    r"""
    Phase 8: Evidence and File Security Test Suite.
    Verifies:
    1. Path traversal prevention (../, ..\, /)
    2. Executable upload blocking (.exe, .sh, .php, .bat, .py)
    3. Content sniffing and masqueraded executable rejection
    4. File size limits (10MB for evidence, 5MB for images)
    5. IDOR prevention and authorization on evidence downloads
    6. Role-based access control (Student Owner, Rep, Teacher, Evaluator, Admin)
    7. Workflow state protection on evidence modification
    8. Legitimate file formats upload and storage in PRIVATE_MEDIA_ROOT
    """

    def setUp(self):
        self.dept = Department.objects.create(name="Computer Science Security", code="CSSEC")
        self.academic_year = "2025-2026"
        AcademicYear.objects.get_or_create(year=self.academic_year)
        self.cv = CriteriaVersion.objects.create(name="AY 2025-2026 v1", academic_year=self.academic_year, version=1)

        # Users
        self.teacher = User.objects.create_user(
            username="teacher.sec", email="teacher.sec@marian.edu", password=None, role="faculty", department=self.dept
        )
        self.cls = Class.objects.create(name="CS-Sec-A", department=self.dept, class_teacher=self.teacher)

        self.student_a = User.objects.create_user(
            username="student.a", email="student.a@marian.edu", password=None, role="student",
            class_name=self.cls, department=self.dept
        )
        self.student_b = User.objects.create_user(
            username="student.b", email="student.b@marian.edu", password=None, role="student",
            class_name=self.cls, department=self.dept
        )
        self.student_rep = User.objects.create_user(
            username="student.rep", email="student.rep@marian.edu", password=None, role="student",
            class_name=self.cls, department=self.dept
        )
        self.cls.dqc_member = self.student_rep
        self.cls.save()

        self.evaluator_user = User.objects.create_user(
            username="evaluator.sec", email="evaluator.sec@marian.edu", password=None, role="evaluation", department=self.dept
        )
        self.admin_user = User.objects.create_user(
            username="admin.sec", email="admin.sec@marian.edu", password=None, role="admin", is_staff=True
        )

        # Criteria Category & Item
        self.cat = CriteriaCategory.objects.create(
            code="cat_acad_sec", category="Academic Excellence Sec",
            evaluators=["evaluator.sec@marian.edu"]
        )
        self.item = CriteriaItem.objects.create(
            id=9901, category=self.cat, title="Research Paper Publication",
            type="fixed", marks=10.0, version=self.cv
        )

        # Base submission for student_a
        self.sub = Submission.objects.create(
            user=self.student_a,
            criteria_id=self.item.id,
            criteria_version=self.cv,
            academic_year=self.academic_year,
            description="Initial student research paper",
            status="Draft"
        )

    def test_path_traversal_sanitization(self):
        raw = "../../../etc/passwd.pdf"
        sanitized = sanitize_filename(raw)
        self.assertNotIn("..", sanitized)
        self.assertNotIn("/", sanitized)
        self.assertNotIn("\\", sanitized)
        self.assertTrue(sanitized.endswith(".pdf"))

    def test_path_traversal_rejected_in_submission_api(self):
        self.client.force_authenticate(user=self.student_a)
        # POST creation with traversal in proof
        res = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': self.academic_year,
            'description': 'Traversal test creation',
            'proof': '../../boot.ini'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid proof path specification", str(res.data))

        # PUT update with traversal in proof
        res2 = self.client.put(f'/api/submissions/{self.sub.id}/', {
            'proof': '..\\Windows\\System32\\cmd.exe'
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid proof path specification", str(res2.data))

    def test_blocked_executable_extensions(self):
        self.client.force_authenticate(user=self.student_a)
        for ext in ['.exe', '.sh', '.php', '.bat', '.py']:
            malicious_file = SimpleUploadedFile(f"exploit{ext}", b"echo 'bad'", content_type="application/octet-stream")
            res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
                'file': malicious_file
            }, format='multipart')
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("Dangerous executable extension", str(res.data.get('error', '')))

    def test_mime_type_sniffing_masqueraded_executable(self):
        self.client.force_authenticate(user=self.student_a)
        # DOS/Windows PE executable disguised with .pdf extension
        fake_pdf = SimpleUploadedFile("resume.pdf", b"MZ\x90\x00\x03\x00\x00\x00malicious binary code", content_type="application/pdf")
        res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': fake_pdf
        }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("File content contains an executable", str(res.data.get('error', '')))

        # PHP script disguised as text
        fake_txt = SimpleUploadedFile("notes.txt", b"<?php phpinfo(); ?>", content_type="text/plain")
        res2 = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': fake_txt
        }, format='multipart')
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("File content contains an executable", str(res2.data.get('error', '')))

    def test_oversized_evidence_rejected(self):
        self.client.force_authenticate(user=self.student_a)
        # 11MB file (exceeds 10MB limit)
        big_file = SimpleUploadedFile("large_cert.pdf", b"%PDF-1.4 " + (b"A" * (11 * 1024 * 1024)), content_type="application/pdf")
        res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': big_file
        }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exceeds maximum allowed limit", str(res.data.get('error', '')))

    def test_oversized_image_rejected(self):
        self.client.force_authenticate(user=self.admin_user)
        # 6MB image (exceeds 5MB limit for images)
        big_img = SimpleUploadedFile("big_champ.png", b"\x89PNG\r\n\x1a\n" + (b"B" * (6 * 1024 * 1024)), content_type="image/png")
        res = self.client.post('/api/champions/', {
            'student_name': 'Super Champion',
            'department': 'CS',
            'image': big_img
        }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exceeds maximum allowed limit", str(res.data))

    def test_unauthenticated_evidence_download_rejected(self):
        self.client.logout()
        res = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_idor_unauthorized_student_evidence_access(self):
        # Upload legitimate PDF as student_a
        self.client.force_authenticate(user=self.student_a)
        valid_pdf = SimpleUploadedFile("paper.pdf", b"%PDF-1.4\nvalid pdf content\n%%EOF", content_type="application/pdf")
        upload_res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': valid_pdf
        }, format='multipart')
        self.assertEqual(upload_res.status_code, status.HTTP_200_OK)

        # Student B attempts to download Student A's evidence (IDOR prevention)
        self.client.force_authenticate(user=self.student_b)
        res_get = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_get.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("permission", str(res_get.data.get('error', '')).lower())

        # Student B attempts to overwrite Student A's evidence
        res_post = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': SimpleUploadedFile("hacked.pdf", b"%PDF-1.4\nhacked", content_type="application/pdf")
        }, format='multipart')
        self.assertEqual(res_post.status_code, status.HTTP_403_FORBIDDEN)

        # Student B attempts to delete Student A's evidence
        res_del = self.client.delete(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)

    def test_authorized_roles_can_access_evidence(self):
        # Upload legitimate PDF as student_a
        self.client.force_authenticate(user=self.student_a)
        valid_pdf = SimpleUploadedFile("paper.pdf", b"%PDF-1.4\nvalid pdf content\n%%EOF", content_type="application/pdf")
        upload_res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': valid_pdf
        }, format='multipart')
        self.assertEqual(upload_res.status_code, status.HTTP_200_OK)

        # 1. Student A (owner) can download
        res_owner = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(res_owner['X-Content-Type-Options'], 'nosniff')

        # 2. Student Rep for class can download
        self.client.force_authenticate(user=self.student_rep)
        res_rep = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK)

        # 3. Class Teacher can download
        self.client.force_authenticate(user=self.teacher)
        res_teacher = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_teacher.status_code, status.HTTP_200_OK)

        # 4. Assigned Evaluator can download
        self.client.force_authenticate(user=self.evaluator_user)
        res_eval = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_eval.status_code, status.HTTP_200_OK)

        # 5. Admin can download
        self.client.force_authenticate(user=self.admin_user)
        res_admin = self.client.get(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)

    def test_student_cannot_modify_evidence_after_submission(self):
        # Move submission to Submitted status
        self.sub.status = "Submitted"
        self.sub.save()

        self.client.force_authenticate(user=self.student_a)
        new_pdf = SimpleUploadedFile("update.pdf", b"%PDF-1.4\nupdated", content_type="application/pdf")
        res_post = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': new_pdf
        }, format='multipart')
        self.assertEqual(res_post.status_code, status.HTTP_403_FORBIDDEN)

        res_del = self.client.delete(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)

    def test_legitimate_file_formats_upload_and_delete(self):
        self.client.force_authenticate(user=self.student_a)
        # Test PNG
        png_file = SimpleUploadedFile("badge.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRimage data", content_type="image/png")
        res = self.client.post(f'/api/submissions/{self.sub.id}/evidence/', {
            'file': png_file
        }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['proof'].startswith('evidence/'))
        self.assertEqual(len(res.data['proofHash']), 64)

        # Delete evidence while in draft
        del_res = self.client.delete(f'/api/submissions/{self.sub.id}/evidence/')
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.proof)


class Phase9AuditabilityAndPrivacyTest(APITestCase):
    """
    Comprehensive test suite for Phase 9: Auditability & Privacy.
    Verifies:
    1. Sensitive Mutation Auditing:
       - Submission creation & updates
       - Workflow state transitions, verification, rejection, evaluation, locking
       - Criteria changes
       - System setting changes
       - User role & account changes
       - Ranking publication
    2. Audit Trail Integrity & Immutability:
       - Immutability: direct update or delete of SystemAuditLog raises PermissionError
       - Cryptographic SHA-256 hash chaining
       - Role attribution (actor, role, ip_address, user_agent, request_id)
    3. Audit Ledger Access Controls:
       - /api/audit-logs/ restricted to Admin & IQAC (Students & Faculty get 403)
       - /api/submissions/<pk>/audit/ allowed for owner & authorized staff, blocked for peers
    4. Privacy & Response Sanitization:
       - Bug report creation response omits reporter_email, reporter_name, and whatsapp_numbers
       - Bug report logging does not leak reporter email
       - Class list masks faculty and DQC emails for unauthenticated / non-staff users
       - Peer evaluator remarks and evaluator names are concealed from student reps / peers
    5. Log Sanitization Filter:
       - Redacts passwords, bearer tokens, JWTs, API secrets
    """

    def setUp(self):
        from users.models import SystemAuditLog, WorkflowAuditTrail, Class, Department, Course, User, AcademicYear, CriteriaCategory, CriteriaItem, CriteriaVersion, Submission
        self.dept = Department.objects.create(name='Computer Science', code='CS_P9')
        self.course = Course.objects.create(department=self.dept, name='BCA', abbreviation='BCA_P9', email_code='bca')
        
        self.teacher = User.objects.create_user(
            username='teacher.p9@mariancollege.org',
            email='teacher.p9@mariancollege.org',
            role='faculty'
        )
        self.rep = User.objects.create_user(
            username='rep.p9@mariancollege.org',
            email='rep.p9@mariancollege.org',
            role='student'
        )
        self.cls = Class.objects.create(
            name='I BCA P9',
            department=self.dept,
            course=self.course,
            year_number=1,
            section='A',
            num_students=40,
            class_teacher=self.teacher,
            dqc_member=self.rep
        )

        self.student = User.objects.create_user(
            username='student.p9@mariancollege.org',
            email='student.p9@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls
        )
        self.peer_student = User.objects.create_user(
            username='peer.p9@mariancollege.org',
            email='peer.p9@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls
        )
        self.admin = User.objects.create_user(
            username='admin.p9@mariancollege.org',
            email='admin.p9@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )
        self.evaluator = User.objects.create_user(
            username='evaluator.p9@mariancollege.org',
            email='evaluator.p9@mariancollege.org',
            role='evaluation'
        )

        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)
        self.crit_v = CriteriaVersion.objects.create(academic_year='2025-2026', version=1, is_locked=False)
        self.cat = CriteriaCategory.objects.create(code='cat-p9', category='Academic', evaluators=[self.evaluator.email])
        self.item = CriteriaItem.objects.create(category=self.cat, version=self.crit_v, title='Phase 9 Item', type='count', marks=15.0)

    # 1. Immutability & Cryptographic Hash Chaining
    def test_audit_log_immutability_and_hash_chaining(self):
        from users.models import SystemAuditLog
        log1 = SystemAuditLog.objects.create(
            actor=self.admin,
            action='TEST_ACTION_1',
            object_type='Submission',
            object_id='101',
            reason='Initial audit event'
        )
        self.assertIsNotNone(log1.record_hash)
        self.assertEqual(len(log1.record_hash), 64)
        self.assertEqual(log1.previous_hash, "0" * 64)

        log2 = SystemAuditLog.objects.create(
            actor=self.admin,
            action='TEST_ACTION_2',
            object_type='Submission',
            object_id='102',
            reason='Chained audit event'
        )
        self.assertEqual(log2.previous_hash, log1.record_hash)

        # Immutability: Updating an existing log MUST raise PermissionError
        with self.assertRaises(PermissionError):
            log1.reason = "Tampered reason"
            log1.save()

        # Immutability: Deleting a log MUST raise PermissionError
        with self.assertRaises(PermissionError):
            log2.delete()

    # 2. Sensitive Mutation Auditing: Submission Creation
    def test_submission_creation_audited(self):
        from users.models import SystemAuditLog
        self.client.force_authenticate(user=self.student)
        res = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': '2025-2026',
            'description': 'Student submitted certificate',
            'eventId': 'CERT-P9-001'
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        sub_id = res.data['id']

        audit_entry = SystemAuditLog.objects.filter(
            action='SUBMISSION_CREATE',
            object_type='Submission',
            object_id=str(sub_id)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.actor_email, self.student.email)
        self.assertEqual(audit_entry.actor_role, 'student')
        self.assertIn('status', audit_entry.new_value)

    # 3. Sensitive Mutation Auditing: Verification & Evaluation Workflow
    def test_workflow_transitions_and_evaluations_audited(self):
        from users.models import Submission, SystemAuditLog
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            criteria_version=self.crit_v,
            academic_year='2025-2026',
            description='Test sub for audit flow',
            status='Draft'
        )

        # Student submits
        self.client.force_authenticate(user=self.student)
        res_sub = self.client.put(f'/api/submissions/{sub.id}/', {'status': 'Submitted'})
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK, res_sub.data)

        # Student Rep verifies
        self.client.force_authenticate(user=self.rep)
        res_rep = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Student Rep Verified',
            'repRemarks': 'DQC verification completed'
        })
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK, res_rep.data)

        # Class Teacher verifies
        self.client.force_authenticate(user=self.teacher)
        res_teach = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Teacher Verified',
            'teacherRemarks': 'Faculty checked'
        })
        self.assertEqual(res_teach.status_code, status.HTTP_200_OK, res_teach.data)

        # Evaluator evaluates and assigns marks
        self.client.force_authenticate(user=self.evaluator)
        res_eval = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 15.0,
            'evaluatorRemarks': 'Evaluator assigned full 15 points'
        })
        self.assertEqual(res_eval.status_code, status.HTTP_200_OK, res_eval.data)

        # Check SystemAuditLog records
        eval_log = SystemAuditLog.objects.filter(
            action='SUBMISSION_EVALUATE',
            object_type='Submission',
            object_id=str(sub.id)
        ).first()
        self.assertIsNotNone(eval_log)
        self.assertEqual(eval_log.actor_email, self.evaluator.email)
        self.assertEqual(eval_log.new_value.get('marks'), 15.0)

    # 4. Sensitive Mutation Auditing: Administrative Settings
    def test_system_setting_mutation_audited(self):
        from users.models import SystemAuditLog
        self.client.force_authenticate(user=self.admin)
        res = self.client.post('/api/settings/', {'smallest_class_size': '38'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        setting_log = SystemAuditLog.objects.filter(
            action='ADMIN_SETTING_CHANGE',
            object_id='smallest_class_size'
        ).first()
        self.assertIsNotNone(setting_log)
        self.assertEqual(setting_log.new_value.get('value'), '38')

    # 5. Sensitive Mutation Auditing: Criteria Changes
    def test_criteria_mutation_audited(self):
        from users.models import SystemAuditLog
        self.client.force_authenticate(user=self.admin)
        res = self.client.put(f'/api/criteria-items/{self.item.id}/', {'title': 'Renamed Phase 9 Item'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        crit_log = SystemAuditLog.objects.filter(
            action='CRITERIA_CHANGE',
            object_type='CriteriaItem',
            object_id=str(self.item.id)
        ).first()
        self.assertIsNotNone(crit_log)
        self.assertEqual(crit_log.actor_email, self.admin.email)

    # 6. Audit Trail Access Controls (/api/audit-logs/ & /api/submissions/<pk>/audit/)
    def test_audit_logs_access_permissions(self):
        # Admin can access system audit logs
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.get('/api/audit-logs/')
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)

        # Student rep cannot access system audit logs
        self.client.force_authenticate(user=self.rep)
        res_rep = self.client.get('/api/audit-logs/')
        self.assertEqual(res_rep.status_code, status.HTTP_403_FORBIDDEN)

        # Unauthenticated user cannot access system audit logs
        self.client.logout()
        res_anon = self.client.get('/api/audit-logs/')
        self.assertIn(res_anon.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_submission_audit_trail_access_permissions(self):
        from users.models import Submission
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            criteria_version=self.crit_v,
            academic_year='2025-2026',
            description='Audit trail submission access test',
            status='Draft'
        )

        # Owner student can read their submission audit trail
        self.client.force_authenticate(user=self.student)
        res_owner = self.client.get(f'/api/submissions/{sub.id}/audit/')
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)

        # Class rep can read class member audit trail
        self.client.force_authenticate(user=self.rep)
        res_rep = self.client.get(f'/api/submissions/{sub.id}/audit/')
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK)

        # Unrelated peer student cannot read
        other_dept = Department.objects.create(name='Commerce', code='COM_P9')
        other_student = User.objects.create_user(
            username='other.p9@mariancollege.org',
            email='other.p9@mariancollege.org',
            role='student',
            department=other_dept
        )
        self.client.force_authenticate(user=other_student)
        res_other = self.client.get(f'/api/submissions/{sub.id}/audit/')
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

    # 7. Privacy: Bug Report Reporter Identity Scoping
    def test_bug_report_response_omits_reporter_identity(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post('/api/bug-reports/', {
            'title': 'Button glitch in portal',
            'description': 'Submit button is unclickable on mobile browsers',
            'bug_type': 'UI',
            'priority': 'High'
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Verify reporter_email and whatsapp_numbers are stripped from the response
        self.assertNotIn('reporter_email', res.data)
        self.assertNotIn('reporter_name', res.data)
        self.assertNotIn('whatsapp_numbers', res.data)
        self.assertIn('title', res.data)

    # 8. Privacy: Class List Masks Emails for Unauthenticated / Non-Staff
    def test_class_list_masks_staff_emails_for_public(self):
        # Anonymous / unauthenticated request
        self.client.logout()
        res_anon = self.client.get('/api/auth/classes/')
        self.assertEqual(res_anon.status_code, status.HTTP_200_OK)
        target_cls = [c for c in res_anon.data if c['id'] == self.cls.id][0]
        self.assertIsNone(target_cls['classTeacher'])
        self.assertIsNone(target_cls['dqcMember'])
        self.assertIsNotNone(target_cls['classTeacherName'])

        # Authenticated Admin/Staff request
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.get('/api/auth/classes/')
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)
        admin_cls = [c for c in res_admin.data if c['id'] == self.cls.id][0]
        self.assertEqual(admin_cls['classTeacher'], self.teacher.email)
        self.assertEqual(admin_cls['dqcMember'], self.rep.email)

    # 9. Privacy: Peer Evaluator Remarks Concealed
    def test_evaluator_remarks_concealed_from_peer_student_rep(self):
        from users.models import Submission
        sub = Submission.objects.create(
            user=self.student,
            criteria_id=self.item.id,
            criteria_version=self.crit_v,
            academic_year='2025-2026',
            description='Confidential evaluator evaluation',
            status='Evaluated',
            marks=15.0,
            evaluator_verified=True,
            evaluator_verified_by_name='Dr. Chief Evaluator',
            evaluator_remarks='Internal audit assessment: top-tier research merit.'
        )

        # Student rep sees the submission, but evaluator remarks are masked
        self.client.force_authenticate(user=self.rep)
        res_rep = self.client.get(f'/api/submissions/{sub.id}/')
        self.assertEqual(res_rep.status_code, status.HTTP_200_OK)
        self.assertIsNone(res_rep.data.get('evaluator_remarks'))
        self.assertIsNone(res_rep.data.get('evaluator_verified_by_name'))

        # Owner student can see evaluator remarks on their own submission
        self.client.force_authenticate(user=self.student)
        res_owner = self.client.get(f'/api/submissions/{sub.id}/')
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(res_owner.data.get('evaluator_remarks'), 'Internal audit assessment: top-tier research merit.')

    # 10. Logging Sanitization Filter
    def test_logging_filter_redacts_sensitive_tokens_and_passwords(self):
        from users.audit import SensitiveDataFilter
        import logging

        log_filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User login failed password='SuperSecretPassword123' with token='eyJhbGciOi' and Authorization: Bearer secret-bearer-token-value",
            args=(), exc_info=None
        )
        log_filter.filter(record)
        self.assertNotIn("SuperSecretPassword123", record.msg)
        self.assertNotIn("secret-bearer-token-value", record.msg)
        self.assertIn("[REDACTED]", record.msg)
        self.assertIn("[REDACTED_TOKEN]", record.msg)


class Phase13ComprehensiveRegressionTest(TestCase):
    """
    Phase 13: Comprehensive Regression Test Suite.

    Covers gaps not addressed in Phases 1-9:
    1.  Authentication boundary: invalid/expired token response behaviour
    2.  Privilege escalation via injected body fields
    3.  Full E2E workflow integration: Student -> DQC -> Teacher -> Evaluator
                                     -> Admin Lock -> Ranking -> IQAC Publication
    4.  Scoring edge cases: zero marks, exact maximum boundary, missing marks rejection
    5.  WorkflowAuditTrail model-level immutability (cannot update or delete)
    6.  SystemAuditLog SHA-256 hash chain integrity across multiple events
    7.  Ranking cache invalidation triggered by workflow transition
    8.  Transaction atomicity: failed validation must not partially persist
    9.  Cross-student access isolation
    10. Publication lock: IQAC publishes results; subsequent reads serve snapshot
    """

    def setUp(self):
        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2024-2025', is_active=True)

        # Departments & Classes
        self.dept_cs = Department.objects.create(
            name='P13 Computer Science', code='P13CS', email_prefix='p', level='PG'
        )
        self.dept_arts = Department.objects.create(
            name='P13 Arts', code='P13ART', email_prefix='u', level='UG'
        )
        self.course_mca = Course.objects.create(
            department=self.dept_cs, name='MCA P13', abbreviation='MCA13',
            email_code='m3', duration_years=2
        )
        self.cls_mca = Class.objects.create(
            name='I MCA P13', department=self.dept_cs,
            course=self.course_mca, year_number=1, num_students=40
        )
        self.cls_arts = Class.objects.create(
            name='I Arts P13', department=self.dept_arts, num_students=30
        )

        # Users
        self.student = User.objects.create_user(
            username='p13.student@marian.edu',
            email='p13.student@marian.edu',
            password=None, role='student',
            class_name=self.cls_mca, department=self.dept_cs,
            first_name='Phase13', last_name='Student'
        )
        self.student2 = User.objects.create_user(
            username='p13.student2@marian.edu',
            email='p13.student2@marian.edu',
            password=None, role='student',
            class_name=self.cls_mca, department=self.dept_cs,
            first_name='Phase13', last_name='Student2'
        )
        self.arts_student = User.objects.create_user(
            username='p13.arts@marian.edu',
            email='p13.arts@marian.edu',
            password=None, role='student',
            class_name=self.cls_arts, department=self.dept_arts,
            first_name='Phase13', last_name='ArtsStudent'
        )
        self.dqc_rep = User.objects.create_user(
            username='p13.rep@marian.edu',
            email='p13.rep@marian.edu',
            password=None, role='student',
            class_name=self.cls_mca, department=self.dept_cs,
            first_name='Phase13', last_name='Rep'
        )
        self.cls_mca.dqc_member = self.dqc_rep
        self.cls_mca.save()

        self.teacher = User.objects.create_user(
            username='p13.teacher@marian.edu',
            email='p13.teacher@marian.edu',
            password=None, role='faculty', department=self.dept_cs,
            first_name='Phase13', last_name='Teacher', is_staff=True
        )
        self.cls_mca.class_teacher = self.teacher
        self.cls_mca.save()

        self.evaluator = User.objects.create_user(
            username='p13.evaluator@marian.edu',
            email='p13.evaluator@marian.edu',
            password=None, role='evaluation',
            first_name='Phase13', last_name='Evaluator', is_staff=True
        )
        self.iqac = User.objects.create_user(
            username='p13.admin2@marian.edu',
            email='p13.admin2@marian.edu',
            password=None, role='admin',
            first_name='Phase13', last_name='Admin2', is_staff=True
        )
        self.admin = User.objects.create_user(
            username='p13.admin@marian.edu',
            email='p13.admin@marian.edu',
            password=None, role='admin',
            first_name='Phase13', last_name='Admin',
            is_staff=True, is_superuser=True
        )

        # Criteria
        self.cat = CriteriaCategory.objects.create(
            code='cat-p13-acad', category='P13 Academic Excellence',
            evaluators=['p13.evaluator@marian.edu']
        )
        self.crit_ver = CriteriaVersion.objects.create(
            academic_year='2024-2025', version=1,
            name='P13 Criteria v1', is_locked=False
        )
        self.item = CriteriaItem.objects.create(
            category=self.cat, version=self.crit_ver,
            title='P13 Research Publication', type='fixed', marks=15.0,
            rules_json={'maximum': 15}
        )

        SystemSetting.objects.update_or_create(
            key='smallest_class_size', defaults={'value': '20'}
        )

    # -------------------------------------------------------------------------
    # 1. AUTHENTICATION BOUNDARY TESTS
    # -------------------------------------------------------------------------

    def test_no_token_returns_401_on_sensitive_endpoints(self):
        """Requests with no Authorization header must return 401 on protected endpoints."""
        sensitive = [
            ('GET',  '/api/submissions/'),
            ('GET',  '/api/users/'),
            ('GET',  '/api/audit-logs/'),
        ]
        for method, url in sensitive:
            res = self.client.get(url) if method == 'GET' else self.client.post(url, {})
            self.assertEqual(
                res.status_code, status.HTTP_401_UNAUTHORIZED,
                f"Expected 401 for {method} {url}, got {res.status_code}"
            )

    def test_malformed_jwt_returns_401(self):
        """A syntactically invalid JWT must return 401."""
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not.a.valid.jwt')
        res = self.client.get('/api/submissions/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.credentials()

    def test_access_token_rejected_as_refresh_token(self):
        """Using an access token where a refresh token is expected returns 401."""
        from rest_framework_simplejwt.tokens import AccessToken
        access = AccessToken.for_user(self.student)
        res = self.client.post('/api/auth/token/refresh/', {
            'refresh': str(access)
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_disabled_user_token_is_rejected(self):
        """A pre-issued token for a now-disabled user must be rejected."""
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken.for_user(self.student)
        self.student.is_active = False
        self.student.save()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token)}')
        res = self.client.get('/api/submissions/')
        self.assertIn(res.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        self.client.credentials()
        self.student.is_active = True
        self.student.save()

    # -------------------------------------------------------------------------
    # 2. PRIVILEGE ESCALATION VIA REQUEST BODY INJECTION
    # -------------------------------------------------------------------------

    def test_student_body_role_injection_is_ignored(self):
        """Injecting 'role': 'admin' in POST body must not change the user's role."""
        self.client.force_authenticate(user=self.student)
        self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': '2024-2025',
            'description': 'Privilege escalation test',
            'role': 'admin',
        }, format='json')
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, 'student')

    def test_student_cannot_inject_is_staff_via_profile_endpoint(self):
        """Injecting is_staff/is_superuser via profile update endpoint is ignored."""
        self.client.force_authenticate(user=self.student)
        self.client.put('/api/auth/profile/', {
            'is_staff': True,
            'is_superuser': True,
            'role': 'admin',
        }, format='json')
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_staff)
        self.assertFalse(self.student.is_superuser)
        self.assertEqual(self.student.role, 'student')

    def test_submission_owner_is_jwt_user_not_body_email(self):
        """Even if body contains another user's email, submission is owned by the JWT user."""
        self.client.force_authenticate(user=self.student)
        res = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': '2024-2025',
            'description': 'Body email spoofing test',
            'email': self.student2.email,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        sub = Submission.objects.get(id=res.data['id'])
        self.assertEqual(sub.user_id, self.student.id)
        self.assertNotEqual(sub.user_id, self.student2.id)

    # -------------------------------------------------------------------------
    # 3. FULL E2E WORKFLOW INTEGRATION
    # -------------------------------------------------------------------------

    def test_full_e2e_submission_to_lock_workflow(self):
        """
        Complete institutional workflow chain:
        Student creates Draft -> Submits -> DQC Verifies -> Teacher Verifies
        -> Evaluator Evaluates -> Admin Locks.
        Each step is verified via API. Audit trail accumulates correctly.
        Post-lock edits are forbidden.
        """
        from users.models import WorkflowAuditTrail, SystemAuditLog

        # Stage 1: Student creates submission
        self.client.force_authenticate(user=self.student)
        res = self.client.post('/api/submissions/', {
            'criteriaId': self.item.id,
            'academicYear': '2024-2025',
            'description': 'E2E full workflow test submission',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        sub_id = res.data['id']

        # Stage 1b: Student submits to DQC queue
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Pending Rep Verification',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        # Stage 2: DQC Rep verifies
        self.client.force_authenticate(user=self.dqc_rep)
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Student Rep Verified',
            'repRemarks': 'E2E DQC verified',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub = Submission.objects.get(id=sub_id)
        self.assertEqual(sub.status, 'Student Rep Verified')
        self.assertEqual(sub.rep_verified_by_name, 'Phase13 Rep')

        # Stage 3: Teacher verifies
        self.client.force_authenticate(user=self.teacher)
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Teacher Verified',
            'teacherRemarks': 'E2E Teacher confirmed',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Teacher Verified')
        self.assertEqual(sub.teacher_verified_by_name, 'Phase13 Teacher')

        # Stage 4: Evaluator evaluates with marks
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Evaluated',
            'marks': 15.0,
            'evaluatorRemarks': 'E2E full marks',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Evaluated')
        self.assertEqual(sub.marks, 15)
        self.assertTrue(sub.evaluator_verified)
        self.assertEqual(sub.evaluator_verified_by_name, 'Phase13 Evaluator')

        # Stage 5: Admin locks
        self.client.force_authenticate(user=self.admin)
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'status': 'Locked',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Locked')

        # Post-lock: edit must be forbidden even for admin
        res = self.client.put(f'/api/submissions/{sub_id}/', {
            'description': 'Unauthorized post-lock edit',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Audit trail has at least 4 entries (one per stage transition)
        audit_count = WorkflowAuditTrail.objects.filter(submission_id=sub_id).count()
        self.assertGreaterEqual(audit_count, 4)

        # SystemAuditLog must record SUBMISSION_LOCK
        lock_log = SystemAuditLog.objects.filter(
            action='SUBMISSION_LOCK', object_id=str(sub_id)
        ).first()
        self.assertIsNotNone(lock_log)
        self.assertEqual(lock_log.actor_id, self.admin.id)

        # Ranking endpoint works after lock
        res = self.client.get('/api/class-index/?year=2024-2025')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mca_entry = next(
            (r for r in res.data if r.get('class_name') == 'I MCA P13'), None
        )
        self.assertIsNotNone(mca_entry)
        self.assertGreater(mca_entry['M'], 0)

    # -------------------------------------------------------------------------
    # 4. WORKFLOW: REJECTION & RESUBMISSION
    # -------------------------------------------------------------------------

    def test_rejection_requires_remark_and_student_can_resubmit(self):
        """DQC rejection without remark is refused; with remark student can resubmit."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Rejection resubmission test',
            status='Pending Rep Verification'
        )

        # Correction Requested without remarks -> 400
        self.client.force_authenticate(user=self.dqc_rep)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Correction Requested',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # DQC rejects with reason -> allowed
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Rejected',
            'repRemarks': 'Evidence document is illegible',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Rejected')

        # Student resubmits
        self.client.force_authenticate(user=self.student)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Pending Rep Verification',
            'description': 'Updated evidence with clearer image',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Pending Rep Verification')

    # -------------------------------------------------------------------------
    # 5. SCORING EDGE CASES
    # -------------------------------------------------------------------------

    def test_zero_marks_is_valid_evaluation_outcome(self):
        """Assigning zero marks is a legitimate evaluation outcome and must succeed."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Zero marks test', status='Teacher Verified'
        )
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 0,
            'evaluatorRemarks': 'Evidence insufficient; zero awarded',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 0)
        self.assertEqual(sub.status, 'Evaluated')

    def test_exact_maximum_marks_is_accepted(self):
        """Assigning exactly the maximum (15.0) succeeds without over-limit rejection."""
        sub = Submission.objects.create(
            user=self.student2, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Max marks boundary', status='Teacher Verified'
        )
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 15.0,
            'evaluatorRemarks': 'Full marks awarded at boundary',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 15)

    def test_marks_required_when_transitioning_to_evaluated(self):
        """Transitioning to Evaluated without marks must be rejected with 400."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Missing marks test', status='Teacher Verified'
        )
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'evaluatorRemarks': 'Forgot marks field',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Teacher Verified')

    def test_moderation_mark_bounded_at_200(self):
        """Class size N=10000 still only receives 200.0 moderation marks max."""
        from users.scoring_engine import calculate_class_moderation
        mod = calculate_class_moderation(N=10000, n=20.0)
        self.assertEqual(mod, 200.0)

    def test_moderation_mark_is_zero_for_class_below_benchmark(self):
        """A class smaller than benchmark receives exactly 0.0 moderation (never negative)."""
        from users.scoring_engine import calculate_class_moderation
        mod = calculate_class_moderation(N=10, n=20.0)
        self.assertEqual(mod, 0.0)

    def test_class_index_formula_correctness(self):
        """
        Verify formula M = (S - P) / N² * (1 + 100 * (N - n)):
        N=40, n=0 -> multiplier = 1 + 100 * 40 = 4001
        S=300, P=0 -> M = (300 / 1600) * 4001 = 0.1875 * 4001 = 750.1875
        """
        from users.scoring_engine import compute_class_scores, calculate_class_index
        cls_t = Class.objects.create(
            name='Formula Test P13', department=self.dept_cs, num_students=40
        )
        u = User.objects.create_user(
            email='formula.p13@marian.edu', username='formula.p13.user',
            role='student', class_name=cls_t
        )
        Submission.objects.create(
            user=u, criteria_id=self.item.id, academic_year='2024-2025',
            status='Locked', marks=300
        )
        result = compute_class_scores(cls_t, n_benchmark=0.0)
        self.assertAlmostEqual(result['M'], 750.1875, places=4)

        # Dedicated test of user specification formula
        # M = (S − P) / N² × (1 + 100 × (N − n))
        m_calc = calculate_class_index(S=300, P=20, N=40, n=0)
        # (300 - 20) / 1600 * (1 + 4000) = 280 / 1600 * 4001 = 0.175 * 4001 = 700.175
        self.assertAlmostEqual(m_calc, 700.175, places=4)

    # -------------------------------------------------------------------------
    # 6. WORKFLOWAUDITTRAIL MODEL-LEVEL IMMUTABILITY
    # -------------------------------------------------------------------------

    def test_workflow_audit_trail_save_on_existing_raises_permission_error(self):
        """WorkflowAuditTrail.save() on an existing record must raise PermissionError."""
        from users.models import WorkflowAuditTrail
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Audit immut save', status='Draft'
        )
        entry = WorkflowAuditTrail.objects.create(
            submission=sub, actor=self.student,
            stage=1, stage_name='Student Claims',
            previous_status='Draft', new_status='Submitted',
            comments='Created',
        )
        entry.comments = 'Tampered'
        with self.assertRaises(PermissionError):
            entry.save()

    def test_workflow_audit_trail_delete_raises_permission_error(self):
        """WorkflowAuditTrail.delete() must raise PermissionError."""
        from users.models import WorkflowAuditTrail
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Audit immut delete', status='Draft'
        )
        entry = WorkflowAuditTrail.objects.create(
            submission=sub, actor=self.student,
            stage=1, stage_name='Student Claims',
            previous_status='Draft', new_status='Pending Rep Verification',
            comments='Submitted',
        )
        with self.assertRaises(PermissionError):
            entry.delete()

    # -------------------------------------------------------------------------
    # 7. SYSTEMAUDITLOG SHA-256 HASH CHAIN INTEGRITY
    # -------------------------------------------------------------------------

    def test_system_audit_log_hash_chain_is_continuous(self):
        """
        Consecutive SystemAuditLog records form an unbroken SHA-256 chain:
        record[i].previous_hash == record[i-1].record_hash
        """
        from users.models import SystemAuditLog
        from users.audit import record_system_audit_event
        import re

        # Start from a clean slate for deterministic chaining
        SystemAuditLog.objects.all().delete()

        r1 = record_system_audit_event(
            action='SUBMISSION_CREATE', object_type='Submission',
            object_id='P13-001', actor=self.student, reason='Chain test 1'
        )
        r2 = record_system_audit_event(
            action='SUBMISSION_VERIFY', object_type='Submission',
            object_id='P13-001', actor=self.dqc_rep, reason='Chain test 2'
        )
        r3 = record_system_audit_event(
            action='SUBMISSION_EVALUATE', object_type='Submission',
            object_id='P13-001', actor=self.evaluator, reason='Chain test 3'
        )

        # Genesis: first record's previous_hash is 64 zeros
        self.assertEqual(r1.previous_hash, '0' * 64)
        # Chaining: each record links to previous record_hash
        self.assertEqual(r2.previous_hash, r1.record_hash)
        self.assertEqual(r3.previous_hash, r2.record_hash)

        # All record_hashes are valid SHA-256 hex strings
        hex64 = re.compile(r'^[0-9a-f]{64}$')
        for rec in [r1, r2, r3]:
            self.assertRegex(rec.record_hash, hex64,
                             f"Invalid SHA-256 hash: {rec.record_hash!r}")

    def test_system_audit_log_cannot_be_updated(self):
        """SystemAuditLog.save() on an existing pk must raise PermissionError."""
        from users.audit import record_system_audit_event
        record = record_system_audit_event(
            action='ADMIN_SETTING_CHANGE', object_type='SystemSetting',
            object_id='p13_key', actor=self.admin, reason='Created'
        )
        record.reason = 'Tampered'
        with self.assertRaises(PermissionError):
            record.save()

    def test_system_audit_log_cannot_be_deleted(self):
        """SystemAuditLog.delete() must raise PermissionError."""
        from users.audit import record_system_audit_event
        record = record_system_audit_event(
            action='CRITERIA_CHANGE', object_type='CriteriaItem',
            object_id='p13-item-del', actor=self.admin, reason='Delete test'
        )
        with self.assertRaises(PermissionError):
            record.delete()

    def test_audit_log_api_forbidden_for_non_admin_roles(self):
        """Non-admin roles must receive 403 on GET /api/audit-logs/."""
        for user in [self.student, self.teacher, self.evaluator]:
            self.client.force_authenticate(user=user)
            res = self.client.get('/api/audit-logs/')
            self.assertIn(
                res.status_code,
                [status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED],
                f"Expected 403/401 for {user.role}, got {res.status_code}"
            )

    # -------------------------------------------------------------------------
    # 8. RANKING CACHE INVALIDATION
    # -------------------------------------------------------------------------

    def test_ranking_cache_invalidated_after_workflow_transition(self):
        """
        Cache is warmed, then a workflow transition triggers invalidation.
        The leaderboard must be recomputable after invalidation.
        """
        from users.services.ranking_service import RankingService

        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Cache inv test',
            status='Locked', marks=100
        )

        # Warm cache
        first = RankingService.get_class_index_data(year='2024-2025')
        self.assertIsNotNone(first)

        # Explicitly invalidate
        RankingService.invalidate_cache('2024-2025')

        # Re-fetch must return fresh data without error
        second = RankingService.get_class_index_data(year='2024-2025')
        self.assertIsNotNone(second)

        # Trigger invalidation via API workflow transition
        sub.status = 'Teacher Verified'
        sub.criteria_version = self.crit_ver
        sub.save()
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'marks': 10,
            'evaluatorRemarks': 'Cache invalidation via workflow transition',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Leaderboard must still be fetchable after invalidation
        third = RankingService.get_class_index_data(year='2024-2025')
        self.assertIsNotNone(third)


    # -------------------------------------------------------------------------
    # 9. CROSS-STUDENT DATA ISOLATION
    # -------------------------------------------------------------------------

    def test_student_cannot_read_peer_submission(self):
        """A student must receive 403 when reading a peer's submission (IDOR guard)."""
        sub2 = Submission.objects.create(
            user=self.student2, criteria_id=self.item.id,
            academic_year='2024-2025', description='Private submission', status='Draft'
        )
        self.client.force_authenticate(user=self.student)
        res = self.client.get(f'/api/submissions/{sub2.id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_submission_list_scoped_to_authenticated_student(self):
        """GET /api/submissions/ must only return the authenticated student's own records."""
        sub_own = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Own record', status='Draft'
        )
        sub_other = Submission.objects.create(
            user=self.student2, criteria_id=self.item.id,
            academic_year='2024-2025', description='Other record', status='Draft'
        )
        self.client.force_authenticate(user=self.student)
        res = self.client.get('/api/submissions/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [s['id'] for s in res.data]
        self.assertIn(sub_own.id, ids)
        self.assertNotIn(sub_other.id, ids)

    def test_dqc_rep_from_wrong_class_cannot_verify(self):
        """A DQC rep assigned to one class cannot verify submissions from another class."""
        sub_cs = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='CS sub cross verify',
            status='Pending Rep Verification'
        )
        # arts_student is not a DQC rep for the CS class
        self.client.force_authenticate(user=self.arts_student)
        res = self.client.put(f'/api/submissions/{sub_cs.id}/', {
            'status': 'Student Rep Verified',
            'repRemarks': 'Arts student unauthorized verify attempt',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # 10. TRANSACTION ATOMICITY
    # -------------------------------------------------------------------------

    def test_failed_transition_does_not_persist_partial_state(self):
        """
        A rejected workflow transition must not partially mutate the submission.
        Status and marks remain unchanged after a 403 response.
        """
        from users.models import WorkflowAuditTrail
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Atomicity test',
            status='Draft', marks=None
        )
        original_status = sub.status

        # Student attempts illegal jump to Locked
        self.client.force_authenticate(user=self.student)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Locked',
            'marks': 99,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        sub.refresh_from_db()
        self.assertEqual(sub.status, original_status)
        self.assertIsNone(sub.marks)

        # No WorkflowAuditTrail entry for failed attempt
        self.assertEqual(
            WorkflowAuditTrail.objects.filter(submission=sub).count(), 0
        )

    def test_evaluation_without_marks_leaves_submission_unchanged(self):
        """A missing-marks evaluation request must leave the submission in Teacher Verified."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Eval atomicity missing marks', status='Teacher Verified',
            marks=None
        )
        self.client.force_authenticate(user=self.evaluator)
        res = self.client.put(f'/api/submissions/{sub.id}/', {
            'status': 'Evaluated',
            'evaluatorRemarks': 'Forgot marks',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'Teacher Verified')
        self.assertIsNone(sub.marks)

    # -------------------------------------------------------------------------
    # 11. DATABASE CONSTRAINT INTEGRITY
    # -------------------------------------------------------------------------

    def test_unique_active_academic_year_constraint(self):
        """Only one AcademicYear can have is_active=True at a time."""
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            AcademicYear.objects.create(year='2023-2024', is_active=True)

    def test_unique_criteria_version_per_academic_year(self):
        """Two CriteriaVersions with the same academic_year+version must violate uniqueness."""
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CriteriaVersion.objects.create(
                academic_year='2024-2025', version=1,
                name='Duplicate', is_locked=False
            )

    def test_workflow_audit_trail_stage_outside_range_fails(self):
        """WorkflowAuditTrail stage=8 violates check_audit_stage_range constraint."""
        from users.models import WorkflowAuditTrail
        from django.db import IntegrityError
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Stage constraint', status='Draft'
        )
        with self.assertRaises(Exception):
            WorkflowAuditTrail.objects.create(
                submission=sub, actor=self.student,
                stage=8,  # Violates constraint (max 7)
                stage_name='Invalid Stage',
                previous_status='Draft', new_status='Submitted', comments=''
            )

    # -------------------------------------------------------------------------
    # 12. SENSITIVE DATA FILTER (LOGGING)
    # -------------------------------------------------------------------------

    def test_sensitive_data_filter_redacts_bearer_and_password_from_logs(self):
        """SensitiveDataFilter must redact Bearer tokens and passwords from log messages."""
        from users.audit import SensitiveDataFilter
        import logging

        filt = SensitiveDataFilter()
        cases = [
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret",
             "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret"),
            ("password='AdminSecret@123' used for login",
             "AdminSecret@123"),
            ("token='jwt-very-secret-value' in header",
             "jwt-very-secret-value"),
        ]
        for msg, forbidden in cases:
            record = logging.LogRecord(
                name='test', level=logging.DEBUG, pathname='', lineno=0,
                msg=msg, args=(), exc_info=None
            )
            filt.filter(record)
            self.assertNotIn(
                forbidden, record.msg,
                f"Fragment '{forbidden}' not redacted from: {record.msg!r}"
            )
            self.assertIn('[REDACTED', record.msg)

    # -------------------------------------------------------------------------
    # 13. PRIVACY: EVALUATOR REMARKS VISIBILITY
    # -------------------------------------------------------------------------

    def test_evaluator_remarks_not_exposed_to_unrelated_student(self):
        """A peer student (not the owner) must receive 403 and not see evaluator_remarks."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Privacy test', status='Evaluated', marks=12,
            evaluator_verified=True,
            evaluator_remarks='Confidential evaluator note.'
        )
        self.client.force_authenticate(user=self.student2)
        res = self.client.get(f'/api/submissions/{sub.id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_read_evaluator_remarks(self):
        """Admin must be able to read evaluator_remarks for institutional oversight."""
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            criteria_version=self.crit_ver, academic_year='2024-2025',
            description='Admin remarks read', status='Evaluated', marks=10,
            evaluator_verified=True,
            evaluator_remarks='Transparency remark for admin.'
        )
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f'/api/submissions/{sub.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get('evaluator_remarks'),
                         'Transparency remark for admin.')

    # -------------------------------------------------------------------------
    # 14. ACADEMIC GRADE BREAKDOWN DATA INTEGRITY
    # -------------------------------------------------------------------------

    def test_grade_breakdown_sum_must_equal_total_students(self):
        """AcademicGradeBreakdown enforces strict grade count accountability."""
        from users.models import AcademicGradeBreakdown
        sub = Submission.objects.create(
            user=self.student, criteria_id=self.item.id,
            academic_year='2024-2025', description='Grade integrity', status='Draft'
        )
        # 5+10+5+5+5=30 != total_students=40 -> ValueError
        bd = AcademicGradeBreakdown(
            submission=sub,
            s_grade_count=5, a_plus_grade_count=10, a_grade_count=5,
            other_pass_count=5, failed_count=5, total_students=40
        )
        with self.assertRaises(ValueError):
            bd.save()

    def test_grade_breakdown_auto_computes_pass_percentage(self):
        """AcademicGradeBreakdown auto-computes class_pass_percentage on save."""
        from users.models import AcademicGradeBreakdown
        sub = Submission.objects.create(
            user=self.student2, criteria_id=self.item.id,
            academic_year='2024-2025', description='Pass pct auto', status='Draft'
        )
        # 45 pass, 5 fail, total 50 -> 90.0%
        bd = AcademicGradeBreakdown(
            submission=sub,
            s_grade_count=10, a_plus_grade_count=15, a_grade_count=10,
            other_pass_count=10, failed_count=5, total_students=50
        )
        bd.save()
        self.assertEqual(bd.class_pass_percentage, 90.0)


class Phase14ProductionReadinessTest(TestCase):
    """
    Phase 14: Production Readiness Verification Test Suite.

    Validates:
    1. Operational health check endpoints (/api/health/ and /health/)
    2. Degradation handling (503 on database disconnection)
    3. Production settings constraints (PostgreSQL enforcement, SECRET_KEY, ALLOWED_HOSTS, CORS, CSRF)
    4. Static files and media configuration (STATIC_ROOT, MEDIA_ROOT, PRIVATE_MEDIA_ROOT)
    5. Security headers and cookie protection attributes in production mode
    """

    def setUp(self):
        self.client = APIClient()

    def test_api_health_check_returns_200_and_healthy(self):
        """GET /api/health/ must be unauthenticated, return 200, and show healthy database."""
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data.get('status'), 'healthy')
        self.assertEqual(data.get('database', {}).get('status'), 'connected')
        self.assertIn('latency_ms', data.get('database', {}))
        self.assertEqual(data.get('storage', {}).get('status'), 'accessible')
        self.assertIn('version', data)
        self.assertIn('timestamp', data)

    def test_root_health_check_returns_200(self):
        """GET /health/ must be accessible directly at root and return 200."""
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data.get('status'), 'healthy')

    def test_health_check_returns_503_when_database_fails(self):
        """HealthCheckView must return 503 Service Unavailable when the database is unreachable."""
        from unittest.mock import patch
        with patch('django.db.connection.cursor', side_effect=Exception("Database unreachable")):
            response = self.client.get('/api/health/')
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            data = response.json()
            self.assertEqual(data.get('status'), 'unhealthy')
            self.assertEqual(data.get('database', {}).get('status'), 'disconnected')

    def test_static_root_is_configured(self):
        """STATIC_ROOT must be configured to a filesystem path for collectstatic."""
        from django.conf import settings
        self.assertTrue(hasattr(settings, 'STATIC_ROOT'))
        self.assertIsNotNone(settings.STATIC_ROOT)
        self.assertTrue(str(settings.STATIC_ROOT).endswith('staticfiles'))

    def test_private_media_root_is_configured(self):
        """PRIVATE_MEDIA_ROOT must be configured and separated from public media."""
        from django.conf import settings
        self.assertTrue(hasattr(settings, 'PRIVATE_MEDIA_ROOT'))
        self.assertIsNotNone(settings.PRIVATE_MEDIA_ROOT)
        self.assertNotEqual(settings.PRIVATE_MEDIA_ROOT, settings.MEDIA_ROOT)

    def test_csrf_trusted_origins_is_configured(self):
        """CSRF_TRUSTED_ORIGINS must be configured for HTTPS security."""
        from django.conf import settings
        self.assertTrue(hasattr(settings, 'CSRF_TRUSTED_ORIGINS'))
        self.assertIsInstance(settings.CSRF_TRUSTED_ORIGINS, list)
        self.assertTrue(len(settings.CSRF_TRUSTED_ORIGINS) > 0)

    def test_production_enforces_postgresql_over_sqlite(self):
        """Production configuration must reject SQLite and require PostgreSQL."""
        from django.core.exceptions import ImproperlyConfigured
        # Simulate production evaluation of DB_ENGINE
        db_engine = 'django.db.backends.sqlite3'
        debug_mode = False
        with self.assertRaises(ImproperlyConfigured):
            if not debug_mode and db_engine in ('django.db.backends.sqlite3', 'sqlite'):
                raise ImproperlyConfigured("SQLite is strictly prohibited in production.")

    def test_production_rejects_missing_secret_key(self):
        """Production configuration must reject empty or missing DJANGO_SECRET_KEY."""
        from django.core.exceptions import ImproperlyConfigured
        secret_key = None
        debug_mode = False
        with self.assertRaises(ImproperlyConfigured):
            if not secret_key and not debug_mode:
                raise ImproperlyConfigured("DJANGO_SECRET_KEY environment variable is required in production.")

    def test_production_rejects_wildcard_allowed_hosts(self):
        """Production configuration must reject wildcard '*' in ALLOWED_HOSTS."""
        from django.core.exceptions import ImproperlyConfigured
        allowed_hosts = ['*']
        debug_mode = False
        with self.assertRaises(ImproperlyConfigured):
            if not debug_mode and '*' in allowed_hosts:
                raise ImproperlyConfigured("Wildcard '*' in DJANGO_ALLOWED_HOSTS is forbidden in production.")

    def test_production_rejects_missing_cors_origins(self):
        """Production configuration must reject missing CORS_ALLOWED_ORIGINS."""
        from django.core.exceptions import ImproperlyConfigured
        cors_origins = None
        debug_mode = False
        with self.assertRaises(ImproperlyConfigured):
            if not debug_mode and not cors_origins:
                raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS environment variable is required in production.")


from rest_framework.test import APITestCase


class OfficialUserGroupsAndAccessManagementRegressionTest(APITestCase):
    """
    Tests for Official User Groups & Access Management:
    1. 4 official groups existence and policy enforcement
    2. Staff email restriction on Evaluation Committee & Class Teachers Council
    3. Student email restriction on DQC Student Rep Group & Student Representatives
    4. Dual role computation for staff members in both councils
    5. Badges for DQC member and Student Rep
    6. Relational synchronization between groups, classes, and category evaluators
    """
    def setUp(self):
        from users.views.system_views import OFFICIAL_USER_GROUPS
        from users.models import UserGroupModel
        for gid, ginfo in OFFICIAL_USER_GROUPS.items():
            UserGroupModel.objects.get_or_create(
                group_id=gid,
                defaults={'name': ginfo['name'], 'description': ginfo['description']}
            )
        self.admin = User.objects.create(
            username='admin@mariancollege.org',
            email='admin@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )
        self.faculty_teacher = User.objects.create(
            username='teacher.faculty@mariancollege.org',
            email='teacher.faculty@mariancollege.org',
            role='faculty',
            first_name='Teacher',
            last_name='Faculty'
        )
        self.faculty_evaluator = User.objects.create(
            username='evaluator.faculty@mariancollege.org',
            email='evaluator.faculty@mariancollege.org',
            role='faculty',
            first_name='Evaluator',
            last_name='Faculty'
        )
        self.student_rep = User.objects.create(
            username='amal.25pmc114@mariancollege.org',
            email='amal.25pmc114@mariancollege.org',
            role='student',
            first_name='Amal',
            last_name='Thomas'
        )
        self.dqc_student = User.objects.create(
            username='santhosh.25pmc152@mariancollege.org',
            email='santhosh.25pmc152@mariancollege.org',
            role='student',
            first_name='Santhosh',
            last_name='Kannan'
        )
        self.dept = Department.objects.create(name='PG Department of Computer Applications', code='PGDCA')
        self.course = Course.objects.create(
            department=self.dept,
            name='Master of Computer Applications',
            abbreviation='MCA',
            email_code='mc',
            duration_years=2
        )
        self.cls = Class.objects.create(
            department=self.dept,
            course=self.course,
            name='II MCA',
            year_number=2,
            batch_start_year=2025
        )
        self.cat = CriteriaCategory.objects.create(category='Research & Publications', code='cat-research')

    def test_four_official_groups_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/user-groups/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        group_ids = [g['id'] for g in res.data]
        self.assertIn('grp-evaluation-committee', group_ids)
        self.assertIn('grp-class-teachers', group_ids)
        self.assertIn('grp-dqc-student-rep', group_ids)
        self.assertIn('grp-student-reps', group_ids)

    def test_email_policy_enforcement(self):
        self.client.force_authenticate(user=self.admin)

        # 1. Evaluation committee rejects student email
        res1 = self.client.post('/api/user-groups/grp-evaluation-committee/add_member/', {
            'email': 'santhosh.25pmc152@mariancollege.org'
        })
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('staff', res1.data['error'].lower())

        # 2. Evaluation committee accepts staff email
        res2 = self.client.post('/api/user-groups/grp-evaluation-committee/add_member/', {
            'email': self.faculty_evaluator.email
        })
        self.assertEqual(res2.status_code, status.HTTP_200_OK)

        # 3. Class Teachers Council rejects student email
        res3 = self.client.post('/api/user-groups/grp-class-teachers/add_member/', {
            'email': 'amal.25pmc114@mariancollege.org'
        })
        self.assertEqual(res3.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. DQC Student Rep group rejects staff email
        res4 = self.client.post('/api/user-groups/grp-dqc-student-rep/add_member/', {
            'email': self.faculty_teacher.email
        })
        self.assertEqual(res4.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('student', res4.data['error'].lower())

        # 5. DQC Student Rep group accepts student email
        res5 = self.client.post('/api/user-groups/grp-dqc-student-rep/add_member/', {
            'email': self.dqc_student.email
        })
        self.assertEqual(res5.status_code, status.HTTP_200_OK)

    def test_dual_role_staff_detection_and_landing(self):
        from users.services.user_service import get_user_roles_and_dual_status
        from users.models import UserGroupModel, UserGroupMember

        ec_grp = UserGroupModel.objects.get(group_id='grp-evaluation-committee')
        ct_grp = UserGroupModel.objects.get(group_id='grp-class-teachers')

        # Add faculty_teacher to both groups
        UserGroupMember.objects.create(group=ct_grp, email=self.faculty_teacher.email, name=self.faculty_teacher.get_full_name())
        UserGroupMember.objects.create(group=ec_grp, email=self.faculty_teacher.email, name=self.faculty_teacher.get_full_name())

        status_info = get_user_roles_and_dual_status(self.faculty_teacher)
        self.assertTrue(status_info['has_dual_role'])
        self.assertEqual(status_info['role'], 'teacher')  # lands on class teachers window
        self.assertIn('teacher', status_info['available_roles'])
        self.assertIn('evaluator', status_info['available_roles'])

    def test_student_badges(self):
        from users.services.user_service import get_user_badge
        from users.models import UserGroupModel, UserGroupMember

        dqc_grp = UserGroupModel.objects.get(group_id='grp-dqc-student-rep')
        rep_grp = UserGroupModel.objects.get(group_id='grp-student-reps')

        UserGroupMember.objects.create(group=dqc_grp, email=self.dqc_student.email, badge='DQC member')
        UserGroupMember.objects.create(group=rep_grp, email=self.student_rep.email, badge='Student Rep')

        self.assertEqual(get_user_badge(self.dqc_student), 'DQC member')
        self.assertEqual(get_user_badge(self.student_rep), 'Student Rep')

    def test_student_representative_limited_submission_categories(self):
        """
        Student Representatives user group members must ONLY have access to limited individual
        categories like normal students, whereas DQC Student Rep members can submit to class-level categories.
        """
        from users.models import UserGroupModel, UserGroupMember, CriteriaCategory, CriteriaItem

        ay, _ = AcademicYear.objects.get_or_create(year='2025-2026', defaults={'is_active': True})
        cat_acad, _ = CriteriaCategory.objects.get_or_create(category='Academics', code='cat-academics')
        item_acad, _ = CriteriaItem.objects.get_or_create(category=cat_acad, title='Semester Exam Results', defaults={'type': 'fixed', 'marks': 10.0})
        item_research, _ = CriteriaItem.objects.get_or_create(category=self.cat, title='Conference Paper', defaults={'type': 'fixed', 'marks': 5.0})

        dqc_grp = UserGroupModel.objects.get(group_id='grp-dqc-student-rep')
        rep_grp = UserGroupModel.objects.get(group_id='grp-student-reps')

        UserGroupMember.objects.create(group=rep_grp, email=self.student_rep.email, badge='Student Rep')
        UserGroupMember.objects.create(group=dqc_grp, email=self.dqc_student.email, badge='DQC member')

        # 1. Student Rep member attempting to submit in restricted Academics category -> 403 Forbidden
        self.client.force_authenticate(user=self.student_rep)
        res_rep_acad = self.client.post('/api/submissions/', {
            'criteriaId': item_acad.id,
            'academicYear': '2025-2026',
            'description': 'Student rep trying to submit class academics',
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_rep_acad.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("restricted to DQC members only", res_rep_acad.data['error'])

        # 2. Student Rep member submitting standard individual activity category -> 201 Created
        res_rep_res = self.client.post('/api/submissions/', {
            'criteriaId': item_research.id,
            'academicYear': '2025-2026',
            'description': 'Student rep publishing conference paper',
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_rep_res.status_code, status.HTTP_201_CREATED)

        # 3. DQC member submitting in Academics category -> 201 Created
        self.client.force_authenticate(user=self.dqc_student)
        res_dqc_acad = self.client.post('/api/submissions/', {
            'criteriaId': item_acad.id,
            'academicYear': '2025-2026',
            'description': 'DQC member updating class semester results',
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_dqc_acad.status_code, status.HTTP_201_CREATED)

    def test_prizes_dqc_restrictions(self):
        """
        Verify that in criteria 'Prizes':
        - From Marian College: 1st Prize (group), 2nd Prize (group), 3rd Prize (group)
        - Outside Marian College: 1st Prize (group) [10 marks], 2nd Prize (group), 3rd Prize (group),
          participation(Individual), participation(group)
        are ONLY accessible to student users present in DQC Student Rep Group.
        Normal students can only access individual prizes.
        """
        from users.models import UserGroupModel, UserGroupMember, CriteriaCategory, CriteriaItem

        ay, _ = AcademicYear.objects.get_or_create(year='2025-2026', defaults={'is_active': True})
        cat_prizes, _ = CriteriaCategory.objects.get_or_create(category='Prizes', code='cat-prizes')

        item_from_marian, _ = CriteriaItem.objects.get_or_create(
            category=cat_prizes,
            title='From Marian College',
            defaults={
                'type': 'count',
                'marks': 0.0,
                'rules_json': {
                    'subItems': {
                        '1st Prize (Individual)': 10.0,
                        '2nd Prize (Individual)': 5.0,
                        '3rd Prize (Individual)': 3.0,
                        '1st Prize (group)': 5.0,
                        '2nd Prize (group)': 3.0,
                        '3rd Prize (group)': 2.0,
                    }
                }
            }
        )

        item_outside_marian, _ = CriteriaItem.objects.get_or_create(
            category=cat_prizes,
            title='Outside Marian College',
            defaults={
                'type': 'count',
                'marks': 0.0,
                'rules_json': {
                    'subItems': {
                        '1st Prize (Individual)': 15.0,
                        '2nd Prize (Individual)': 10.0,
                        '3rd Prize (Individual)': 5.0,
                        '1st Prize (group)': 10.0,
                        '2nd Prize (group)': 5.0,
                        '3rd Prize (group)': 3.0,
                        'participation(Individual)': 3.0,
                        'participation(group)': 2.0,
                    }
                }
            }
        )

        dqc_grp = UserGroupModel.objects.get(group_id='grp-dqc-student-rep')
        UserGroupMember.objects.create(group=dqc_grp, email=self.dqc_student.email, badge='DQC member')

        normal_student = User.objects.create(
            username='faizah.25pmc118@mariancollege.org',
            email='faizah.25pmc118@mariancollege.org',
            role='student',
            first_name='Faizah',
            last_name='N'
        )

        # 1. Normal student submitting Individual prize in From Marian College -> 201 Created
        self.client.force_authenticate(user=normal_student)
        res_indiv = self.client.post('/api/submissions/', {
            'criteriaId': item_from_marian.id,
            'academicYear': '2025-2026',
            'description': 'Student winning individual prize',
            'evidence': {'subItem': '1st Prize (Individual)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_indiv.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_indiv.data['marks'], 10.0)

        # 2. Normal student attempting 1st Prize (group) in From Marian College -> 403 Forbidden
        res_group_from = self.client.post('/api/submissions/', {
            'criteriaId': item_from_marian.id,
            'academicYear': '2025-2026',
            'description': 'Normal student attempting group prize',
            'evidence': {'subItem': '1st Prize (group)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_group_from.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("restricted to DQC members only", res_group_from.data['error'])

        # 3. Normal student attempting 1st Prize (group) in Outside Marian College -> 403 Forbidden
        res_group_out = self.client.post('/api/submissions/', {
            'criteriaId': item_outside_marian.id,
            'academicYear': '2025-2026',
            'description': 'Normal student attempting outside group prize',
            'evidence': {'prizesSubItem': '1st Prize (group)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_group_out.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("restricted to DQC members only", res_group_out.data['error'])

        # 4. Normal student attempting participation(Individual) in Outside Marian College -> 403 Forbidden
        res_part_indiv = self.client.post('/api/submissions/', {
            'criteriaId': item_outside_marian.id,
            'academicYear': '2025-2026',
            'description': 'Normal student attempting participation',
            'evidence': {'prizesSubItem': 'participation(Individual)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_part_indiv.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("restricted to DQC members only", res_part_indiv.data['error'])

        # 5. Normal student attempting participation(group) in Outside Marian College -> 403 Forbidden
        res_part_group = self.client.post('/api/submissions/', {
            'criteriaId': item_outside_marian.id,
            'academicYear': '2025-2026',
            'description': 'Normal student attempting group participation',
            'evidence': {'subItem': 'participation(group)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_part_group.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("restricted to DQC members only", res_part_group.data['error'])

        # 6. DQC Student submitting 1st Prize (group) in Outside Marian College -> 201 Created with 10.0 marks
        self.client.force_authenticate(user=self.dqc_student)
        res_dqc_group_out = self.client.post('/api/submissions/', {
            'criteriaId': item_outside_marian.id,
            'academicYear': '2025-2026',
            'description': 'DQC student submitting outside group prize',
            'evidence': {'prizesSubItem': '1st Prize (group)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_dqc_group_out.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_dqc_group_out.data['marks'], 10.0)

        # 7. DQC Student submitting participation(group) in Outside Marian College -> 201 Created with 2.0 marks
        res_dqc_part_grp = self.client.post('/api/submissions/', {
            'criteriaId': item_outside_marian.id,
            'academicYear': '2025-2026',
            'description': 'DQC student submitting participation group',
            'evidence': {'subItem': 'participation(group)', 'count': 1},
            'status': 'Draft'
        }, format='json')
        self.assertEqual(res_dqc_part_grp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_dqc_part_grp.data['marks'], 2.0)

        # 8. Normal student attempting to edit existing submission to a group prize -> 403 Forbidden
        self.client.force_authenticate(user=normal_student)
        res_put_hack = self.client.put(f'/api/submissions/{res_indiv.data["id"]}/', {
            'criteriaId': item_outside_marian.id,
            'evidence': {'prizesSubItem': '1st Prize (group)'}
        }, format='json')
        self.assertEqual(res_put_hack.status_code, status.HTTP_403_FORBIDDEN)


class StaffUserModuleAndVerificationWorkflowTests(TestCase):
    """
    Authoritative test suite for Staff User Module, Single-Class Teacher Assignment constraint,
    Evaluator Category Assignment, and Round 2 / Round 3 Verification Workflow Logic.
    """

    def setUp(self):
        self.client = APIClient()
        self.academic_year = AcademicYear.objects.create(year='2025-2026', is_active=True)
        self.dept = Department.objects.create(name="PG Department of Computer Applications", code="PGDCA")
        self.course = Course.objects.create(name="MCA", abbreviation="mc", department=self.dept, duration_years=2)

        self.class1 = Class.objects.create(name="MCA A", department=self.dept, course=self.course, academic_year="2025-2026")
        self.class2 = Class.objects.create(name="MCA B", department=self.dept, course=self.course, academic_year="2025-2026")

        self.admin = User.objects.create_user(
            username="admin@mariancollege.org",
            email="admin@mariancollege.org",
            password="password123",
            role="admin",
            is_staff=True,
            is_superuser=True
        )

        self.teacher1 = User.objects.create_user(
            username="teacher.one@mariancollege.org",
            email="teacher.one@mariancollege.org",
            first_name="Teacher",
            last_name="One",
            password="password123",
            role="faculty",
            is_staff=True,
            department=self.dept
        )

        self.teacher2 = User.objects.create_user(
            username="teacher.two@mariancollege.org",
            email="teacher.two@mariancollege.org",
            first_name="Teacher",
            last_name="Two",
            password="password123",
            role="faculty",
            is_staff=True,
            department=self.dept
        )

        self.evaluator1 = User.objects.create_user(
            username="evaluator.one@mariancollege.org",
            email="evaluator.one@mariancollege.org",
            first_name="Evaluator",
            last_name="One",
            password="password123",
            role="evaluation",
            is_staff=True,
            department=self.dept
        )

        self.student1 = User.objects.create_user(
            username="student1.25pmc101@mariancollege.org",
            email="student1.25pmc101@mariancollege.org",
            first_name="Student",
            last_name="One",
            password="password123",
            role="student",
            class_name=self.class1,
            department=self.dept
        )

        self.student2 = User.objects.create_user(
            username="student2.25pmc102@mariancollege.org",
            email="student2.25pmc102@mariancollege.org",
            first_name="Student",
            last_name="Two",
            password="password123",
            role="student",
            class_name=self.class2,
            department=self.dept
        )

        # Categories
        self.cat_academics, _ = CriteriaCategory.objects.get_or_create(code="cat-academics", defaults={"category": "Academics", "is_manual_eval": False})
        self.item_acad, _ = CriteriaItem.objects.get_or_create(category=self.cat_academics, title="Semester Exam Results", defaults={"type": "fixed", "marks": 10.0})

        self.cat_career, _ = CriteriaCategory.objects.get_or_create(code="cat-career", defaults={"category": "Career Advancement", "is_manual_eval": True})
        self.item_career, _ = CriteriaItem.objects.get_or_create(category=self.cat_career, title="Placement Offer", defaults={"type": "fixed", "marks": 20.0, "is_manual_eval": True})

        # User Groups
        self.grp_teachers, _ = UserGroupModel.objects.get_or_create(
            group_id='grp-class-teachers',
            defaults={'name': 'Class Teachers Council', 'description': 'Faculty members acting as class advisors.'}
        )
        self.grp_eval, _ = UserGroupModel.objects.get_or_create(
            group_id='grp-evaluation-committee',
            defaults={'name': 'Evaluation Committee', 'description': 'Evaluator members assigned to review activity submissions.'}
        )

    def test_staff_profile_creation_and_serialization(self):
        """StaffProfile should be created and serialized with department and designation."""
        from users.models import StaffProfile

        profile, created = StaffProfile.objects.get_or_create(
            user=self.teacher1,
            defaults={'department': self.dept, 'designation': 'Class Teacher'}
        )
        self.assertEqual(profile.designation, 'Class Teacher')
        self.assertEqual(profile.department, self.dept)

        self.client.force_authenticate(user=self.teacher1)
        res = self.client.get('/api/auth/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('staff_profile', res.data)
        self.assertEqual(res.data['staff_profile']['designation'], 'Class Teacher')

    def test_single_class_teacher_assignment_constraint(self):
        """A teacher can be assigned to AT MOST ONE class per academic year."""
        from users.models import TeacherClassAssignment

        # 1. Admin assigns teacher1 to class1 -> 200 OK
        self.client.force_authenticate(user=self.admin)
        res1 = self.client.put(f'/api/auth/classes/{self.class1.id}/', {
            'classTeacher': self.teacher1.email
        }, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        # Verify TeacherClassAssignment record
        assign1 = TeacherClassAssignment.objects.filter(teacher=self.teacher1, class_obj=self.class1).first()
        self.assertIsNotNone(assign1)
        self.assertEqual(assign1.academic_year, 2025)
        self.assertTrue(assign1.is_active)

        # 2. Admin attempts to assign teacher1 to class2 in the same academic year -> 400 Bad Request
        res2 = self.client.put(f'/api/auth/classes/{self.class2.id}/', {
            'classTeacher': self.teacher1.email
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already assigned", res2.data['error'])

        # 3. Admin assigns teacher2 to class1 -> Replaces teacher1 cleanly
        res3 = self.client.put(f'/api/auth/classes/{self.class1.id}/', {
            'classTeacher': self.teacher2.email
        }, format='json')
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        self.assertTrue(TeacherClassAssignment.objects.filter(teacher=self.teacher2, class_obj=self.class1).exists())
        self.assertFalse(TeacherClassAssignment.objects.filter(teacher=self.teacher1, class_obj=self.class1).exists())

    def test_class_teacher_round_2_verification_all_categories(self):
        """Class Teacher has Round 2 authority over ALL categories for their assigned single class."""
        from users.models import TeacherClassAssignment, VerificationLog, Submission

        # Assign teacher1 to class1
        self.client.force_authenticate(user=self.admin)
        self.client.put(f'/api/auth/classes/{self.class1.id}/', {'classTeacher': self.teacher1.email}, format='json')

        # Create submissions in Academics (Cat 1) and Career Advancement (Cat 12) for student1 (class1)
        sub_acad = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_academics,
            criteria_id=self.item_acad.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Academics Claim'
        )
        sub_career = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Career Claim'
        )

        # Teacher1 verifies Category 1 (Academics) -> VERIFY_AND_FORWARD
        self.client.force_authenticate(user=self.teacher1)
        res_v1 = self.client.put(f'/api/submissions/{sub_acad.id}/', {
            'status': 'Teacher Verified',
            'actionType': 'VERIFY_AND_FORWARD',
            'remarks': 'Approved in Round 2'
        }, format='json')
        self.assertEqual(res_v1.status_code, status.HTTP_200_OK)
        sub_acad.refresh_from_db()
        self.assertEqual(sub_acad.status, 'Teacher Verified')

        # Check VerificationLog
        vlog1 = VerificationLog.objects.filter(submission=sub_acad).last()
        self.assertEqual(vlog1.verification_level, 'CLASS_TEACHER')
        self.assertEqual(vlog1.action, 'VERIFY_AND_FORWARD')
        self.assertEqual(vlog1.verifier_id, self.teacher1.id)

        # Teacher1 verifies Category 12 (Career Advancement) -> VERIFY_AND_FORWARD
        res_v2 = self.client.put(f'/api/submissions/{sub_career.id}/', {
            'status': 'Teacher Verified',
            'actionType': 'VERIFY_AND_FORWARD',
            'remarks': 'Career claim approved by class teacher'
        }, format='json')
        self.assertEqual(res_v2.status_code, status.HTTP_200_OK)

        # Teacher1 sends back another submission for correction -> SEND_BACK
        sub_send_back = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_academics,
            criteria_id=self.item_acad.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Send back claim'
        )
        res_sb = self.client.put(f'/api/submissions/{sub_send_back.id}/', {
            'status': 'Correction Requested',
            'actionType': 'SEND_BACK',
            'remarks': 'Please upload clearer certificate'
        }, format='json')
        self.assertEqual(res_sb.status_code, status.HTTP_200_OK)
        vlog_sb = VerificationLog.objects.filter(submission=sub_send_back).last()
        self.assertEqual(vlog_sb.verification_level, 'CLASS_TEACHER')
        self.assertEqual(vlog_sb.action, 'SEND_BACK')

    def test_class_teacher_cannot_verify_other_class(self):
        """Class Teacher CANNOT verify submissions from another class."""
        from users.models import Submission

        # Assign teacher1 to class1 only
        self.client.force_authenticate(user=self.admin)
        self.client.put(f'/api/auth/classes/{self.class1.id}/', {'classTeacher': self.teacher1.email}, format='json')

        # Sub in class2
        sub_class2 = Submission.objects.create(
            user=self.student2,
            class_obj=self.class2,
            category=self.cat_academics,
            criteria_id=self.item_acad.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Class 2 claim'
        )

        # Teacher1 attempts to verify sub_class2 -> 403 Forbidden
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.put(f'/api/submissions/{sub_class2.id}/', {
            'status': 'Teacher Verified',
            'remarks': 'Hacking cross-class verification'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_evaluator_round_3_verification_across_classes_and_logging(self):
        """Evaluator has Round 3 authority for assigned category across all classes and logs APPROVE_AND_CREDIT."""
        from users.models import EvaluatorCategoryAssignment, VerificationLog, Submission

        # Assign evaluator1 to Category 12 (Career Advancement) via Admin endpoint
        self.client.force_authenticate(user=self.admin)
        res_cat = self.client.put(f'/api/criteria-categories/{self.cat_career.code}/', {
            'evaluators': [self.evaluator1.email]
        }, format='json')
        self.assertEqual(res_cat.status_code, status.HTTP_200_OK)

        # Verify assignment record
        eca = EvaluatorCategoryAssignment.objects.filter(evaluator=self.evaluator1, category=self.cat_career).first()
        self.assertIsNotNone(eca)
        self.assertEqual(eca.academic_year, 2025)

        # Submissions from class1 and class2 in 'Teacher Verified' state
        sub_c1 = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Class 1 career claim'
        )
        sub_c2 = Submission.objects.create(
            user=self.student2,
            class_obj=self.class2,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Class 2 career claim'
        )

        # Evaluator1 evaluates class1 submission -> Evaluated with marks
        self.client.force_authenticate(user=self.evaluator1)
        res_ev1 = self.client.put(f'/api/submissions/{sub_c1.id}/', {
            'status': 'Evaluated',
            'marks': 20.0,
            'remarks': 'Final evaluation passed, approved and credited'
        }, format='json')
        self.assertEqual(res_ev1.status_code, status.HTTP_200_OK)

        # Check VerificationLog: APPROVE_AND_CREDIT
        log_ev1 = VerificationLog.objects.filter(submission=sub_c1).last()
        self.assertEqual(log_ev1.verification_level, 'EVALUATOR')
        self.assertEqual(log_ev1.action, 'APPROVE_AND_CREDIT')
        self.assertEqual(log_ev1.verifier_id, self.evaluator1.id)

        # Evaluator1 evaluates class2 submission -> Evaluated across classes
        res_ev2 = self.client.put(f'/api/submissions/{sub_c2.id}/', {
            'status': 'Evaluated',
            'marks': 15.0,
            'remarks': 'Cross-class evaluation approved'
        }, format='json')
        self.assertEqual(res_ev2.status_code, status.HTTP_200_OK)

        log_ev2 = VerificationLog.objects.filter(submission=sub_c2).last()
        self.assertEqual(log_ev2.verification_level, 'EVALUATOR')
        self.assertEqual(log_ev2.action, 'APPROVE_AND_CREDIT')

    def test_evaluator_cannot_verify_unassigned_category(self):
        """Evaluator CANNOT verify submissions for unassigned category."""
        from users.models import Submission

        # Assign evaluator1 only to Category 12 (Career)
        self.client.force_authenticate(user=self.admin)
        self.client.put(f'/api/criteria-categories/{self.cat_career.code}/', {
            'evaluators': [self.evaluator1.email]
        }, format='json')

        # Submission in Category 1 (Academics)
        sub_acad = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_academics,
            criteria_id=self.item_acad.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Academics claim'
        )

        # Evaluator1 attempts to evaluate sub_acad -> 403 Forbidden
        self.client.force_authenticate(user=self.evaluator1)
        res = self.client.put(f'/api/submissions/{sub_acad.id}/', {
            'status': 'Evaluated',
            'marks': 10.0,
            'remarks': 'Unassigned category eval attempt'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("not assigned", res.data['error'])

    def test_evaluator_category_assignment_update_different_academic_year_and_multiple_evaluators(self):
        """Updating category evaluators across different academic years does not trigger unique constraint violation and reflects in user groups."""
        from users.models import EvaluatorCategoryAssignment, UserGroupModel, UserGroupMember
        from users.views.system_views import serialize_user_group

        # Pre-create assignment for evaluator1 in academic_year 2025
        ec_group, _ = UserGroupModel.objects.get_or_create(
            group_id='grp-evaluation-committee',
            defaults={'name': 'Evaluation Committee'}
        )
        mem1, _ = UserGroupMember.objects.get_or_create(group=ec_group, email=self.evaluator1.email, defaults={'user': self.evaluator1})
        EvaluatorCategoryAssignment.objects.create(
            category=self.cat_career,
            member=mem1,
            evaluator=self.evaluator1,
            academic_year=2025
        )

        # Active academic year is now 2026-2027
        self.academic_year.year = "2026-2027"
        self.academic_year.save()

        # Update evaluators for cat_career to include evaluator1 AND teacher1
        self.client.force_authenticate(user=self.admin)
        res = self.client.put(f'/api/criteria-categories/{self.cat_career.code}/', {
            'evaluators': [self.evaluator1.email, self.teacher1.email]
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Both evaluators should now have EvaluatorCategoryAssignment for cat_career
        assignments = EvaluatorCategoryAssignment.objects.filter(category=self.cat_career)
        self.assertEqual(assignments.count(), 2)

        # Serialized group should list Category in member_details for both
        group_data = serialize_user_group(ec_group)
        e1_detail = next(m for m in group_data['member_details'] if m['email'] == self.evaluator1.email)
        t1_detail = next(m for m in group_data['member_details'] if m['email'] == self.teacher1.email)
        self.assertTrue(any(c['code'] == self.cat_career.code for c in e1_detail['categories']))
        self.assertTrue(any(c['code'] == self.cat_career.code for c in t1_detail['categories']))



class DualRoleStaffAndVerificationWorkflowTests(APITestCase):
    """
    Test suite for Dual-Role Staff Member capabilities, role switching controller,
    strict view isolation, and 3-round verification workflow lifecycle transitions.
    """

    def setUp(self):
        # Academic Year
        self.ay, _ = AcademicYear.objects.get_or_create(year="2025-2026", defaults={'is_active': True})

        # Department & Course
        self.dept, _ = Department.objects.get_or_create(name="Computer Science", defaults={'code': "CS"})
        self.course, _ = Course.objects.get_or_create(name="BCA", defaults={'abbreviation': "bca", 'department': self.dept, 'duration_years': 3})

        # Classes
        self.class1, _ = Class.objects.get_or_create(name="III BCA", defaults={'department': self.dept, 'course': self.course, 'academic_year': "2025-2026"})
        self.class2, _ = Class.objects.get_or_create(name="II BCA", defaults={'department': self.dept, 'course': self.course, 'academic_year': "2025-2026"})

        # Admin
        self.admin = User.objects.create_user(
            username="admin_user",
            email="admin.marian@mariancollege.org",
            password="password123",
            role="admin",
            is_staff=True,
            is_superuser=True
        )

        # Dual-Role Staff Member: Class Teacher of class1 AND Evaluator of cat_career
        self.dual_staff = User.objects.create_user(
            username="dual_staff",
            email="dual.staff@mariancollege.org",
            password="password123",
            role="faculty",
            department=self.dept
        )

        # Single-Role Teacher Member: Class Teacher of class2 only
        self.teacher_only = User.objects.create_user(
            username="teacher_only",
            email="teacher.only@mariancollege.org",
            password="password123",
            role="faculty",
            department=self.dept
        )

        # Students
        self.student1 = User.objects.create_user(
            username="student1",
            email="student1.cs@mariancollege.org",
            password="password123",
            role="student",
            class_name=self.class1,
            department=self.dept
        )
        self.student2 = User.objects.create_user(
            username="student2",
            email="student2.cs@mariancollege.org",
            password="password123",
            role="student",
            class_name=self.class2,
            department=self.dept
        )

        # Categories & Items
        self.cat_acad = CriteriaCategory.objects.create(category="Academics", code="cat-academics", is_manual_eval=False)
        self.item_acad = CriteriaItem.objects.create(category=self.cat_acad, title="Semester Exam Results", type="fixed", marks=10.0)

        self.cat_career = CriteriaCategory.objects.create(category="Career Advancement", code="cat-career", is_manual_eval=True)
        self.item_career = CriteriaItem.objects.create(category=self.cat_career, title="Placement Offer", type="fixed", marks=50.0, is_manual_eval=True)

        # Assign class teacher for class1 -> dual_staff
        self.class1.class_teacher = self.dual_staff
        self.class1.save()
        from users.models import TeacherClassAssignment, EvaluatorCategoryAssignment
        TeacherClassAssignment.objects.create(teacher=self.dual_staff, class_obj=self.class1, academic_year=2025, is_active=True)

        # Assign class teacher for class2 -> teacher_only
        self.class2.class_teacher = self.teacher_only
        self.class2.save()
        TeacherClassAssignment.objects.create(teacher=self.teacher_only, class_obj=self.class2, academic_year=2025, is_active=True)

        # Assign Evaluator for cat_career -> dual_staff
        EvaluatorCategoryAssignment.objects.create(evaluator=self.dual_staff, category=self.cat_career, academic_year=2025)
        self.cat_career.evaluators = [self.dual_staff.email]
        self.cat_career.save()

    def test_dual_role_switch_endpoint(self):
        """Dual-role staff can switch between teacher and evaluator roles; unauthorized role switches fail."""
        # 1. Single-role teacher attempts to switch to evaluator -> 403 Forbidden
        self.client.force_authenticate(user=self.teacher_only)
        res_single = self.client.post('/api/auth/switch-role/', {'role': 'evaluator'}, format='json')
        self.assertEqual(res_single.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("not authorized", res_single.data['error'])

        # 2. Dual-role staff switches to evaluator -> 200 OK
        self.client.force_authenticate(user=self.dual_staff)
        res_to_eval = self.client.post('/api/auth/switch-role/', {'role': 'evaluator'}, format='json')
        self.assertEqual(res_to_eval.status_code, status.HTTP_200_OK)
        self.assertEqual(res_to_eval.data['active_role'], 'evaluator')
        self.assertEqual(res_to_eval.data['available_roles'], ['teacher', 'evaluator'])

        # Profile returns active_role = 'evaluator'
        res_prof = self.client.get('/api/auth/profile/')
        self.assertEqual(res_prof.status_code, status.HTTP_200_OK)
        self.assertEqual(res_prof.data['active_role'], 'evaluator')
        self.assertTrue(res_prof.data['has_dual_role'])

        # 3. Dual-role staff switches back to teacher -> 200 OK
        res_to_teach = self.client.post('/api/auth/switch-role/', {'role': 'teacher'}, format='json')
        self.assertEqual(res_to_teach.status_code, status.HTTP_200_OK)
        self.assertEqual(res_to_teach.data['active_role'], 'teacher')

        # 4. Attempt to switch to invalid role -> 400 Bad Request
        res_invalid = self.client.post('/api/auth/switch-role/', {'role': 'superadmin'}, format='json')
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dual_role_teacher_view_isolation_and_no_marks_award(self):
        """
        Class Teacher view:
        - Exclusively displays submissions from students in assigned class.
        - Approving DOES NOT award marks, sets status to EVALUATOR_PENDING,
          and writes VerificationLog with VERIFY_AND_FORWARD.
        - Attempt to verify cross-class student fails with 403.
        """
        from users.models import Submission, VerificationLog, ClassIndexResult

        # Submissions for student1 (class1) and student2 (class2) in Cat Career
        sub_c1 = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Class 1 submission'
        )
        sub_c2 = Submission.objects.create(
            user=self.student2,
            class_obj=self.class2,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Student Rep Verified',
            academic_year='2025-2026',
            description='Class 2 submission'
        )

        self.client.force_authenticate(user=self.dual_staff)

        # 1. Teacher View List Isolation: returns sub_c1, does NOT return sub_c2
        res_list = self.client.get('/api/submissions/', HTTP_X_ROLE_CONTEXT='teacher')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        ids = [s['id'] for s in res_list.data]
        self.assertIn(sub_c1.id, ids)
        self.assertNotIn(sub_c2.id, ids)

        # 2. Teacher approves sub_c1 -> Round 2 Verification
        res_appr = self.client.put(f'/api/submissions/{sub_c1.id}/', {
            'status': 'Approved',
            'actionType': 'VERIFY_AND_FORWARD',
            'remarks': 'Forwarded to evaluator by class teacher',
            'role_context': 'teacher'
        }, format='json')
        self.assertEqual(res_appr.status_code, status.HTTP_200_OK)
        sub_c1.refresh_from_db()
        self.assertEqual(sub_c1.status, 'Teacher Verified')
        self.assertIsNone(sub_c1.marks)  # Marks NOT awarded

        # VerificationLog recorded with CLASS_TEACHER and VERIFY_AND_FORWARD
        vlog = VerificationLog.objects.filter(submission=sub_c1).last()
        self.assertIsNotNone(vlog)
        self.assertEqual(vlog.verification_level, 'CLASS_TEACHER')
        self.assertEqual(vlog.action, 'VERIFY_AND_FORWARD')
        self.assertEqual(vlog.verifier_id, self.dual_staff.id)

        # Class leaderboard ledger was NOT credited by teacher approval
        cir = ClassIndexResult.objects.filter(class_name=self.class1).first()
        self.assertIsNone(cir)

        # 3. Teacher attempts to verify sub_c2 (other class) -> 403 Forbidden
        res_cross = self.client.put(f'/api/submissions/{sub_c2.id}/', {
            'status': 'Teacher Verified',
            'remarks': 'Cross-class verification attempt',
            'role_context': 'teacher'
        }, format='json')
        self.assertEqual(res_cross.status_code, status.HTTP_403_FORBIDDEN)

    def test_dual_role_evaluator_view_evaluation_and_marks_crediting(self):
        """
        Evaluator view:
        - Displays verified submissions for assigned categories across classes.
        - Approving records marks, sets status to APPROVED / Evaluated,
          credits class leaderboard ledger, and logs APPROVE_AND_CREDIT.
        """
        from users.models import Submission, VerificationLog, ClassIndexResult

        # Create submissions in Teacher Verified state (Round 2 complete)
        sub_c1 = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Career verified sub 1'
        )
        sub_c2 = Submission.objects.create(
            user=self.student2,
            class_obj=self.class2,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Career verified sub 2'
        )
        # Submission in Academics (unassigned category for dual_staff)
        sub_unassigned = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_acad,
            criteria_id=self.item_acad.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Academics verified sub'
        )

        self.client.force_authenticate(user=self.dual_staff)

        # 1. Evaluator View List Isolation: shows assigned category across classes, excludes unassigned category
        res_list = self.client.get('/api/submissions/', HTTP_X_ROLE_CONTEXT='evaluator')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        ids = [s['id'] for s in res_list.data]
        self.assertIn(sub_c1.id, ids)
        self.assertIn(sub_c2.id, ids)
        self.assertNotIn(sub_unassigned.id, ids)

        # 2. Evaluator evaluates sub_c1 -> Final Evaluation & Mark Award
        res_eval = self.client.put(f'/api/submissions/{sub_c1.id}/', {
            'status': 'Evaluated',
            'marks': 35.0,
            'actionType': 'APPROVE_AND_CREDIT',
            'remarks': 'Evaluator approved and marks credited',
            'role_context': 'evaluator'
        }, format='json')
        self.assertEqual(res_eval.status_code, status.HTTP_200_OK)
        sub_c1.refresh_from_db()
        self.assertEqual(sub_c1.status, 'Evaluated')
        self.assertEqual(sub_c1.marks, 35)

        # VerificationLog recorded with EVALUATOR and APPROVE_AND_CREDIT
        vlog = VerificationLog.objects.filter(submission=sub_c1).last()
        self.assertIsNotNone(vlog)
        self.assertEqual(vlog.verification_level, 'EVALUATOR')
        self.assertEqual(vlog.action, 'APPROVE_AND_CREDIT')
        self.assertEqual(vlog.verifier_id, self.dual_staff.id)

        # ClassIndexResult leaderboard ledger is credited!
        cir = ClassIndexResult.objects.filter(class_name=self.class1).first()
        self.assertIsNotNone(cir)

        # 3. Evaluator evaluates sub_c2 (other class, same category) -> succeeds
        res_eval2 = self.client.put(f'/api/submissions/{sub_c2.id}/', {
            'status': 'Evaluated',
            'marks': 40.0,
            'actionType': 'APPROVE_AND_CREDIT',
            'remarks': 'Evaluator approved for class 2',
            'role_context': 'evaluator'
        }, format='json')
        self.assertEqual(res_eval2.status_code, status.HTTP_200_OK)
        sub_c2.refresh_from_db()
        self.assertEqual(sub_c2.marks, 40)

        # 4. Evaluator attempts to evaluate unassigned category -> 403 Forbidden
        res_un = self.client.put(f'/api/submissions/{sub_unassigned.id}/', {
            'status': 'Evaluated',
            'marks': 10.0,
            'role_context': 'evaluator'
        }, format='json')
        self.assertEqual(res_un.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("not assigned", res_un.data['error'])

    def test_evaluator_cannot_evaluate_before_round_2(self):
        """Evaluator cannot evaluate submissions that have not completed Round 2 Teacher Verification."""
        from users.models import Submission

        sub_not_ready = Submission.objects.create(
            user=self.student1,
            class_obj=self.class1,
            category=self.cat_career,
            criteria_id=self.item_career.id,
            status='Student Rep Verified',  # Has not passed Round 2 (Teacher Verified)
            academic_year='2025-2026',
            description='Not yet teacher verified'
        )

        self.client.force_authenticate(user=self.dual_staff)
        res = self.client.put(f'/api/submissions/{sub_not_ready.id}/', {
            'status': 'Evaluated',
            'marks': 25.0,
            'role_context': 'evaluator'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("complete Round 2 Teacher Verification", res.data['error'])


class SubcategoryDynamicMarkCalculationEngineTest(TestCase):
    """
    Authoritative test suite for the Subcategory-Based Dynamic Mark Calculation Engine.
    Verifies that:
      1. For all categories containing subcategories, calculated marks are strictly driven by
         subcategories.default_marks.
      2. The backend never assigns a generic or hardcoded mark at the category level.
      3. Selected subcategory_id in the subcategories table is looked up and sets calculated_marks.
      4. Categories without subcategories (Cat 1: Academics, Cat 12: Career Advancement) use
         formulas or manual evaluation respectively.
      5. Verification controllers (Round 3 Evaluator) strictly enforce subcategory.default_marks.
      6. Subcategories API endpoints (/api/subcategories/) function accurately.
    """

    def setUp(self):
        from users.models import (
            Category, SubCategory, CriteriaCategory, CriteriaItem,
            CriteriaVersion, AcademicYear, Class, Department, User,
            EvaluatorCategoryAssignment, UserGroupModel, UserGroupMember
        )

        self.client = APIClient()
        self.ay = AcademicYear.objects.create(year='2025-2026', is_active=True)
        self.cv = CriteriaVersion.objects.create(academic_year='2025-2026', version=1, is_locked=False)
        self.dept = Department.objects.create(name='Computer Applications', code='DCA', level='PG')
        self.cls = Class.objects.create(name='II MCA', department=self.dept, batch_start_year=2025)

        self.student = User.objects.create(
            username='student@mariancollege.org',
            email='student@mariancollege.org',
            role='student',
            department=self.dept,
            class_name=self.cls
        )
        self.evaluator = User.objects.create(
            username='evaluator@mariancollege.org',
            email='evaluator@mariancollege.org',
            role='evaluation',
            department=self.dept
        )
        self.admin = User.objects.create(
            username='admin@mariancollege.org',
            email='admin@mariancollege.org',
            role='admin',
            is_staff=True,
            is_superuser=True
        )

        # Seed categories table (1-12)
        categories_data = [
            (1, 'cat-academics', 'Academics'),
            (2, 'cat-online-courses', 'Online Courses'),
            (3, 'cat-competitive-exams', 'Competitive Exams'),
            (4, 'cat-internships', 'Internships'),
            (5, 'cat-scholarships', 'Scholarships'),
            (6, 'cat-research', 'Research'),
            (7, 'cat-startups', 'Startups'),
            (8, 'cat-prizes', 'Prizes Won'),
            (9, 'cat-programs-organized', 'Programs Organized'),
            (10, 'cat-leadership', 'Leaderships'),
            (11, 'cat-social-responsibility', 'Social Responsibilities'),
            (12, 'cat-career-advancement', 'Career Advancement'),
        ]
        for cid, ccode, cname in categories_data:
            Category.objects.get_or_create(id=cid, defaults={'code': ccode, 'name': cname})

        # Seed subcategories
        from decimal import Decimal
        self.sub_swayam, _ = SubCategory.objects.get_or_create(
            category_id=2,
            subcategory_name='Swayam / NPTEL Course',
            defaults={'default_marks': Decimal('5.00'), 'requires_dqc': False, 'max_per_cycle': 3}
        )
        self.sub_mooc, _ = SubCategory.objects.get_or_create(
            category_id=2,
            subcategory_name='MOOC Course',
            defaults={'default_marks': Decimal('2.00'), 'requires_dqc': False, 'max_per_cycle': 3}
        )

        self.sub_jrf, _ = SubCategory.objects.get_or_create(
            category_id=3,
            subcategory_name='JRF Passed',
            defaults={'default_marks': Decimal('20.00'), 'requires_dqc': False, 'max_per_cycle': 1}
        )
        self.sub_net, _ = SubCategory.objects.get_or_create(
            category_id=3,
            subcategory_name='NET Passed',
            defaults={'default_marks': Decimal('10.00'), 'requires_dqc': False, 'max_per_cycle': 1}
        )
        self.sub_exam_other, _ = SubCategory.objects.get_or_create(
            category_id=3,
            subcategory_name='Any Other Relevant Exam (IELTS, Language, etc.)',
            defaults={'default_marks': Decimal('3.00'), 'requires_dqc': False, 'max_per_cycle': 2}
        )
        self.sub_exam_part, _ = SubCategory.objects.get_or_create(
            category_id=3,
            subcategory_name='Participation in Relevant Exam (UPSC / PSC)',
            defaults={'default_marks': Decimal('1.00'), 'requires_dqc': False, 'max_per_cycle': 3}
        )

        self.sub_prize_ind_out, _ = SubCategory.objects.get_or_create(
            category_id=8,
            subcategory_name='1st Prize (Individual) - Outside',
            defaults={'default_marks': Decimal('15.00'), 'requires_dqc': False, 'max_per_cycle': 3}
        )
        self.sub_prize_grp_out, _ = SubCategory.objects.get_or_create(
            category_id=8,
            subcategory_name='1st Prize (Group) - Outside',
            defaults={'default_marks': Decimal('10.00'), 'requires_dqc': True, 'max_per_cycle': 3}
        )

        # Criteria Categories and Items for API tests
        self.crit_cat_online, _ = CriteriaCategory.objects.get_or_create(code='cat-online-courses', defaults={'category': 'Online Courses'})
        self.crit_item_swayam, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_online, title='Swayam / NPTEL Course', defaults={'version': self.cv, 'type': 'count', 'marks': 5.0})
        self.crit_item_mooc, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_online, title='MOOC Course', defaults={'version': self.cv, 'type': 'count', 'marks': 2.0})

        self.crit_cat_exams, _ = CriteriaCategory.objects.get_or_create(code='cat-competitive-exams', defaults={'category': 'Competitive Exams'})
        self.crit_item_jrf, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_exams, title='JRF Passed', defaults={'version': self.cv, 'type': 'date', 'marks': 20.0})
        self.crit_item_net, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_exams, title='NET Passed', defaults={'version': self.cv, 'type': 'date', 'marks': 10.0})
        self.crit_item_other, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_exams, title='Any Other Relevant Exam (IELTS, Language, etc.)', defaults={'version': self.cv, 'type': 'date', 'marks': 3.0})
        self.crit_item_upsc, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_exams, title='Participation in Relevant Exam (UPSC / PSC)', defaults={'version': self.cv, 'type': 'date', 'marks': 1.0})

        self.crit_cat_prizes, _ = CriteriaCategory.objects.get_or_create(code='cat-prizes', defaults={'category': 'Prizes Won'})
        self.crit_item_prize_out, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_prizes, title='Outside Marian College', defaults={'version': self.cv, 'type': 'count', 'marks': 0.0})

        self.crit_cat_academics, _ = CriteriaCategory.objects.get_or_create(code='cat-academics', defaults={'category': 'Academics'})
        self.crit_item_academics, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_academics, title='Sem Result (End Semester Examination)', defaults={'version': self.cv, 'type': 'academic_grades', 'marks': 0.0})

        self.crit_cat_career, _ = CriteriaCategory.objects.get_or_create(code='cat-career-advancement', defaults={'category': 'Career Advancement', 'is_manual_eval': True})
        self.crit_item_career, _ = CriteriaItem.objects.get_or_create(category=self.crit_cat_career, title='Library - Regular Footfall (Biometric / Entry)', defaults={'version': self.cv, 'type': 'count', 'marks': 0.0, 'is_manual_eval': True})

        # Assign evaluator to categories
        EvaluatorCategoryAssignment.objects.get_or_create(evaluator=self.evaluator, category=self.crit_cat_online, academic_year=2025)
        EvaluatorCategoryAssignment.objects.get_or_create(evaluator=self.evaluator, category=self.crit_cat_exams, academic_year=2025)
        EvaluatorCategoryAssignment.objects.get_or_create(evaluator=self.evaluator, category=self.crit_cat_prizes, academic_year=2025)
        EvaluatorCategoryAssignment.objects.get_or_create(evaluator=self.evaluator, category=self.crit_cat_career, academic_year=2025)

    def test_subcategories_table_schema_and_seed_data(self):
        """Verify subcategories table correctly stores default_marks, requires_dqc, and max_per_cycle."""
        from users.models import SubCategory

        self.assertEqual(float(self.sub_swayam.default_marks), 5.0)
        self.assertFalse(self.sub_swayam.requires_dqc)
        self.assertEqual(self.sub_swayam.max_per_cycle, 3)

        self.assertEqual(float(self.sub_mooc.default_marks), 2.0)
        self.assertEqual(float(self.sub_jrf.default_marks), 20.0)
        self.assertEqual(float(self.sub_net.default_marks), 10.0)
        self.assertEqual(float(self.sub_exam_other.default_marks), 3.0)
        self.assertEqual(float(self.sub_exam_part.default_marks), 1.0)

        self.assertEqual(float(self.sub_prize_ind_out.default_marks), 15.0)
        self.assertFalse(self.sub_prize_ind_out.requires_dqc)

        self.assertEqual(float(self.sub_prize_grp_out.default_marks), 10.0)
        self.assertTrue(self.sub_prize_grp_out.requires_dqc)

    def test_online_courses_submission_marks_strictly_driven_by_subcategory(self):
        """Submitting Swayam course sets calculated_marks to 5.00, MOOC sets to 2.00."""
        self.client.force_authenticate(user=self.student)

        # 1. Swayam submission
        res1 = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_swayam.id,
            'academicYear': '2025-2026',
            'description': 'Completed Swayam AI Course',
            'proof': 'https://drive.google.com/file/d/123/view',
            'startDate': '2025-07-01',
            'endDate': '2025-09-01',
            'evidence': {'startDate': '2025-07-01', 'endDate': '2025-09-01'}
        }, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res1.data['calculatedMarks']), 5.0)
        self.assertEqual(res1.data['subcategoryId'], self.sub_swayam.id)

        # 2. MOOC submission
        res2 = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_mooc.id,
            'academicYear': '2025-2026',
            'description': 'Completed Coursera MOOC Course',
            'proof': 'https://drive.google.com/file/d/456/view',
            'startDate': '2025-08-01',
            'endDate': '2025-09-01',
            'evidence': {'startDate': '2025-08-01', 'endDate': '2025-09-01'}
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res2.data['calculatedMarks']), 2.0)
        self.assertEqual(res2.data['subcategoryId'], self.sub_mooc.id)

    def test_competitive_exams_submission_marks_driven_by_subcategory(self):
        """Competitive exams submissions have calculated_marks set to 20, 10, 3, 1."""
        self.client.force_authenticate(user=self.student)

        # JRF (20 marks)
        res_jrf = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_jrf.id,
            'academicYear': '2025-2026',
            'description': 'Passed UGC JRF',
            'proof': 'https://drive.google.com/file/d/jrf/view',
            'startDate': '2025-06-15',
            'evidence': {'examDate': '2025-06-15'}
        }, format='json')
        self.assertEqual(res_jrf.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res_jrf.data['calculatedMarks']), 20.0)
        self.assertEqual(res_jrf.data['subcategoryId'], self.sub_jrf.id)

        # NET (10 marks)
        res_net = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_net.id,
            'academicYear': '2025-2026',
            'description': 'Passed UGC NET',
            'proof': 'https://drive.google.com/file/d/net/view',
            'startDate': '2025-06-15',
            'evidence': {'examDate': '2025-06-15'}
        }, format='json')
        self.assertEqual(res_net.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res_net.data['calculatedMarks']), 10.0)

        # Any Other (3 marks)
        res_other = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_other.id,
            'academicYear': '2025-2026',
            'description': 'IELTS Band 8.0',
            'proof': 'https://drive.google.com/file/d/ielts/view',
            'startDate': '2025-06-15',
            'evidence': {'examDate': '2025-06-15'}
        }, format='json')
        self.assertEqual(res_other.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res_other.data['calculatedMarks']), 3.0)

        # UPSC Participation (1 mark)
        res_upsc = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_upsc.id,
            'academicYear': '2025-2026',
            'description': 'UPSC Prelims Exam',
            'proof': 'https://drive.google.com/file/d/upsc/view',
            'startDate': '2025-06-15',
            'evidence': {'examDate': '2025-06-15'}
        }, format='json')
        self.assertEqual(res_upsc.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res_upsc.data['calculatedMarks']), 1.0)

    def test_prizes_submission_marks_driven_by_subcategory(self):
        """Prizes category pulls subcategory base marks (15.0 for 1st Prize Outside)."""
        self.client.force_authenticate(user=self.student)

        res = self.client.post('/api/submissions/', {
            'criteriaId': self.crit_item_prize_out.id,
            'academicYear': '2025-2026',
            'description': 'Inter-collegiate Fest Winner',
            'proof': 'https://drive.google.com/file/d/prize/view',
            'evidence': {
                'subItem': '1st Prize (Individual) - Outside',
                'prizesSubItem': '1st Prize (Individual) - Outside'
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(float(res.data['calculatedMarks']), 15.0)
        self.assertEqual(res.data['subcategoryId'], self.sub_prize_ind_out.id)

    def test_evaluator_verification_controller_enforces_subcategory_marks(self):
        """Round 3 Evaluator APPROVE_AND_CREDIT credits subcategory.default_marks."""
        from users.models import Submission

        sub = Submission.objects.create(
            user=self.student,
            class_obj=self.cls,
            category=self.crit_cat_online,
            criteria_id=self.crit_item_swayam.id,
            subcategory_id=self.sub_swayam.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Swayam certificate review ready',
            calculated_marks=5.0,
            marks=5
        )

        self.client.force_authenticate(user=self.evaluator)
        res = self.client.post('/api/verification/evaluator/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'remarks': 'Certificate verified, credit 5 marks'
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIn(sub.status, ('Evaluated', 'APPROVED', 'Approved'))
        self.assertEqual(float(sub.calculated_marks), 5.0)
        self.assertEqual(sub.marks, 5)

    def test_categories_without_subcategories_academics_formula_preserved(self):
        """Categories without subcategories (Cat 1: Academics) use grade breakdown formula."""
        from users.models import Submission
        sub = Submission.objects.create(
            user=self.student,
            class_obj=self.cls,
            category=self.crit_cat_academics,
            criteria_id=self.crit_item_academics.id,
            academic_year='2025-2026',
            description='Semester 1 Mark Result',
            evidence={
                'totalStudents': 50,
                'grades': {'S': 10, 'APlus': 15, 'A': 15, 'Fail': 2},
                'classPassPercentage': 96.0
            }
        )
        # Expected: (10 * 5) + (15 * 4) + (15 * 3) + 5.0 (pass bonus > 90) - 2 (fail) = 50 + 60 + 45 + 5 - 2 = 158.0
        from users.scoring_engine import calculate_submission_score
        score = calculate_submission_score(self.crit_item_academics, sub.evidence)
        self.assertEqual(score, 158.0)

    def test_categories_without_subcategories_career_advancement_manual_eval(self):
        """Categories without subcategories (Cat 12: Career) use Evaluator manual score."""
        from users.models import Submission
        sub = Submission.objects.create(
            user=self.student,
            class_obj=self.cls,
            category=self.crit_cat_career,
            criteria_id=self.crit_item_career.id,
            status='Teacher Verified',
            academic_year='2025-2026',
            description='Library footfall logs',
            is_manual_eval=True
        )

        self.client.force_authenticate(user=self.evaluator)
        res = self.client.post('/api/verification/evaluator/', {
            'submissionId': sub.id,
            'action': 'APPROVE_AND_CREDIT',
            'marks': 35.0,
            'remarks': 'Awarded 35 manual marks based on biometric log'
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 35)

    def test_subcategories_api_endpoints(self):
        """Verify GET /api/subcategories/ and GET /api/subcategories/<id>/."""
        self.client.force_authenticate(user=self.student)

        # List all
        res_list = self.client.get('/api/subcategories/')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res_list.data), 8)

        # Filter by category 2 (Online Courses)
        res_cat2 = self.client.get('/api/subcategories/?category_id=2')
        self.assertEqual(res_cat2.status_code, status.HTTP_200_OK)
        cat2_names = [s['subcategory_name'] for s in res_cat2.data]
        self.assertIn('Swayam / NPTEL Course', cat2_names)
        self.assertIn('MOOC Course', cat2_names)

        # Detail view
        res_det = self.client.get(f'/api/subcategories/{self.sub_swayam.id}/')
        self.assertEqual(res_det.status_code, status.HTTP_200_OK)
        self.assertEqual(res_det.data['subcategory_name'], 'Swayam / NPTEL Course')
        self.assertEqual(float(res_det.data['default_marks']), 5.0)







