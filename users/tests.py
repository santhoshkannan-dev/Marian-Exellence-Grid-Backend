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
            role='evaluator'
        )
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

