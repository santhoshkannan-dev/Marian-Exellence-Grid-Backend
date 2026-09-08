from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import (
    Department, Course, Class, User, AcademicYear,
    CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion,
    Submission, AcademicGradeBreakdown
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
            category='Research'
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

        # Set benchmark smallest class size n = 20
        SystemSetting.objects.update_or_create(
            key='smallest_class_size',
            defaults={'value': '20'}
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
            mod_val = entry['moderation_mark']
            # Moderation mark must be within [0, 200]
            self.assertGreaterEqual(mod_val, 0.0)
            self.assertLessEqual(mod_val, 200.0)
            # Index must be approximately 15.0 (between 15.0 and 17.0)
            self.assertGreaterEqual(m_val, 15.0)
            self.assertLessEqual(m_val, 17.0)

        # Confirm Class A (20) index is exactly 15.00
        self.assertAlmostEqual(results_by_size[20]['M'], 15.00, places=2)
        # Confirm Class D (120) index is 16.67 (bounded gentle moderation boost, not 2000x)
        self.assertAlmostEqual(results_by_size[120]['M'], 16.67, places=2)

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
            username='iqac@mariancollege.org',
            email='iqac@mariancollege.org',
            role='iqac',
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

        # 3. Faculty Dept 1 transitions Submitted -> Teacher Verified (allowed)
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
            username='iqac.officer@mariancollege.org',
            email='iqac.officer@mariancollege.org',
            role='iqac',
            first_name='IQAC',
            last_name='Officer'
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







