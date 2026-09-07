from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import Department, Course, Class, User, AcademicYear, CriteriaCategory, CriteriaItem, CriteriaRule, Submission
from .views import parse_student_email, allocate_student_from_email


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



