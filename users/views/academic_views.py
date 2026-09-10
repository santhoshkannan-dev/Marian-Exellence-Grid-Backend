import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from users.models import AcademicYear, Department, Course, Class, User
from users.serializers import DepartmentSerializer, CourseSerializer
from users.permissions import (
    IsAdminOrPublicReadOnly,
    IsAdminOrStaffOrReadOnly,
)

logger = logging.getLogger(__name__)

OFFICIAL_DEPT_ORDER = [
    'ENG',
    'SCPS',
    'UGBBA',
    'UGDCA',
    'SSW',
    'MATHS',
    'MCMS',
    'MHTM',
    'PHYSICS',
    'ECONOMICS',
    'PSYCHOLOGY',
    'MBA',
    'PGDCA',
]

OFFICIAL_CLASS_ORDER = [
    # 1. Department of English / Languages
    "I BACE", "II BACE", "III BACE",
    # 2. School of Commerce and Professional Studies
    "I BCOM A", "I BCOM B", "I BCOM C", "I BCOM (FINTECH)",
    "II BCOM A", "II BCOM B", "II BCOM C",
    "III BCOM A", "III BCOM B", "III BCOM C",
    "I MCOM A", "I MCOM B", "II MCOM A", "II MCOM B",
    # 3. UG Department of Business Administration
    "I BBA A", "I BBA B", "II BBA A", "II BBA B", "III BBA A", "III BBA B",
    # 4. UG Department of Computer Applications
    "I BCA A", "I BCA B", "II BCA A", "II BCA B", "III BCA A", "III BCA B",
    # 5. School of Social Work
    "I BSW A", "I BSW B", "II BSW A", "II BSW B", "III BSW A", "III BSW B",
    "I MSW", "II MSW",
    # 6. Department of Mathematics
    "I MATHS", "II MATHS", "III MATHS",
    # 7. Department of Communication and Media Studies
    "I MCMS", "II MCMS",
    # 8. Department of Hospitality and Tourism Management
    "I MHTM", "II MHTM",
    # 9. Department of Physics
    "I MSC PHYSICS", "II MSC PHYSICS", "III MSC PHYSICS", "IV MSC PHYSICS", "V MSC PHYSICS",
    # 10. Department of Economics
    "I ECONOMICS", "II ECONOMICS", "III ECONOMICS",
    # 11. Department of Psychology
    "I PSYCHOLOGY", "II PSYCHOLOGY", "III PSYCHOLOGY",
    # 12. Masters of Business Administration
    "I MBA A", "I MBA B", "I MBA C", "II MBA A", "II MBA B", "II MBA C",
    # 13. PG Department of Computer Applications
    "I MCA", "II MCA",
]


class AcademicYearListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        years = AcademicYear.objects.all().order_by('-year')
        return Response([
            {"year": y.year, "status": "Active" if y.is_active else "Inactive"}
            for y in years
        ])

    def post(self, request):
        year_str = request.data.get('year')
        is_active = request.data.get('status') == 'Active' or request.data.get('is_active') == True

        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        year_str = str(year_str).strip()
        import re
        if not re.match(r'^\d{4}-\d{4}$', year_str) or len(year_str) > 20:
            return Response(
                {"error": "Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            ay, created = AcademicYear.objects.get_or_create(year=year_str)
            if is_active:
                AcademicYear.objects.exclude(pk=ay.pk).update(is_active=False)
                ay.is_active = True
                ay.save()

        return Response({"year": ay.year, "status": "Active" if ay.is_active else "Inactive"})

    def put(self, request):
        year_str = request.data.get('year')
        is_active = request.data.get('is_active', True)
        if isinstance(is_active, str):
            is_active = is_active.lower() == 'true' or is_active.lower() == 'active'

        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        year_str = str(year_str).strip()
        import re
        if not re.match(r'^\d{4}-\d{4}$', year_str) or len(year_str) > 20:
            return Response(
                {"error": "Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            try:
                ay = AcademicYear.objects.select_for_update().get(year=year_str)
            except AcademicYear.DoesNotExist:
                ay = AcademicYear.objects.create(year=year_str, is_active=is_active)

            if is_active:
                AcademicYear.objects.exclude(pk=ay.pk).update(is_active=False)
                ay.is_active = True
                ay.save()
            else:
                ay.is_active = False
                ay.save()

        return Response({"year": ay.year, "status": "Active" if ay.is_active else "Inactive"})

    def delete(self, request):
        year_str = request.data.get('year') or request.query_params.get('year')
        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        AcademicYear.objects.filter(year=year_str).delete()
        return Response({"success": True, "deleted_year": year_str}, status=status.HTTP_200_OK)


class DepartmentListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        depts = Department.objects.prefetch_related('courses', 'classes').all()
        return Response(DepartmentSerializer(depts, many=True).data)

    def post(self, request):
        name = request.data.get('name')
        code = request.data.get('code')
        email_prefix = request.data.get('email_prefix', '').strip().lower()
        level = request.data.get('level', 'UG')
        if not name:
            return Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
        clean_name = str(name).strip()
        if len(clean_name) > 100:
            return Response({"error": "name cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not code:
            code = ''.join([w[0] for w in clean_name.split()]).upper()[:5] or "DEPT"
        clean_code = str(code).strip().upper()
        if len(clean_code) > 20:
            return Response({"error": "code cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_prefix) > 5:
            return Response({"error": "email_prefix cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
        valid_levels = [c[0] for c in Department.LEVEL_CHOICES]
        if level not in valid_levels:
            return Response({"error": f"Invalid level '{level}'. Allowed choices: {', '.join(valid_levels)}."}, status=status.HTTP_400_BAD_REQUEST)

        dept, created = Department.objects.get_or_create(
            code=clean_code,
            defaults={"name": clean_name, "email_prefix": email_prefix, "level": level}
        )
        if not created:
            dept.name = clean_name
            dept.email_prefix = email_prefix
            dept.level = level
            dept.save(update_fields=['name', 'email_prefix', 'level'])
        return Response(DepartmentSerializer(dept).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DepartmentDetailView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def _get_dept(self, pk):
        try:
            return Department.objects.prefetch_related('courses', 'classes').get(pk=pk)
        except Department.DoesNotExist:
            return None

    def get(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(DepartmentSerializer(dept).data)

    def put(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        name = request.data.get('name', dept.name)
        code = request.data.get('code', dept.code)
        email_prefix = request.data.get('email_prefix', dept.email_prefix).strip().lower()
        level = request.data.get('level', dept.level)

        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 100:
            return Response({"error": "name is required and cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        clean_code = str(code).strip().upper()
        if not clean_code or len(clean_code) > 20:
            return Response({"error": "code is required and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_prefix) > 5:
            return Response({"error": "email_prefix cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
        valid_levels = [c[0] for c in Department.LEVEL_CHOICES]
        if level not in valid_levels:
            return Response({"error": f"Invalid level '{level}'. Allowed choices: {', '.join(valid_levels)}."}, status=status.HTTP_400_BAD_REQUEST)

        dept.name = clean_name
        dept.code = clean_code
        dept.email_prefix = email_prefix
        dept.level = level
        dept.save()
        return Response(DepartmentSerializer(dept).data)

    def delete(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        course_count = dept.courses.count()
        class_count = dept.classes.count()
        dept.delete()
        return Response({"success": True, "deleted_courses": course_count, "deleted_classes": class_count})


class CourseListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        dept_id = request.query_params.get('department')
        qs = Course.objects.select_related('department').all()
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        return Response(CourseSerializer(qs, many=True).data)

    def post(self, request):
        dept_id = request.data.get('department')
        name = request.data.get('name', '').strip()
        abbreviation = request.data.get('abbreviation', '').strip().upper()
        email_code = request.data.get('email_code', '').strip().lower()
        is_multi_batch = request.data.get('is_multi_batch', False)
        duration_years = request.data.get('duration_years', 2)

        if not dept_id or not name or not abbreviation or not email_code:
            return Response(
                {"error": "department, name, abbreviation, and email_code are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(name) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(abbreviation) > 20:
            return Response({"error": "abbreviation cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_code) > 10:
            return Response({"error": "email_code cannot exceed 10 characters."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            dur_years = int(duration_years)
            if dur_years < 1 or dur_years > 6:
                return Response({"error": "duration_years must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError):
            return Response({"error": "duration_years must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            dept = Department.objects.get(pk=dept_id)
        except Department.DoesNotExist:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)

        if Course.objects.filter(department=dept, email_code=email_code).exists():
            return Response(
                {"error": f"A course with email_code '{email_code}' already exists in this department."},
                status=status.HTTP_400_BAD_REQUEST
            )

        course = Course.objects.create(
            department=dept,
            name=name,
            abbreviation=abbreviation,
            email_code=email_code,
            is_multi_batch=bool(is_multi_batch),
            duration_years=dur_years,
        )
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)


class CourseDetailView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def _get_course(self, pk):
        try:
            return Course.objects.select_related('department').get(pk=pk)
        except Course.DoesNotExist:
            return None

    def get(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CourseSerializer(course).data)

    def put(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        name = request.data.get('name', course.name)
        abbreviation = request.data.get('abbreviation', course.abbreviation)
        email_code = request.data.get('email_code', course.email_code)
        if name and len(str(name).strip()) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if abbreviation and len(str(abbreviation).strip()) > 20:
            return Response({"error": "abbreviation cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if email_code and len(str(email_code).strip()) > 10:
            return Response({"error": "email_code cannot exceed 10 characters."}, status=status.HTTP_400_BAD_REQUEST)

        if 'duration_years' in request.data:
            try:
                dur_years = int(request.data['duration_years'])
                if dur_years < 1 or dur_years > 6:
                    return Response({"error": "duration_years must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                course.duration_years = dur_years
            except (ValueError, TypeError):
                return Response({"error": "duration_years must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)

        course.name = str(name).strip()
        course.abbreviation = str(abbreviation).strip().upper()
        course.email_code = str(email_code).strip().lower()
        course.is_multi_batch = request.data.get('is_multi_batch', course.is_multi_batch)
        if 'department' in request.data:
            try:
                course.department = Department.objects.get(pk=request.data['department'])
            except Department.DoesNotExist:
                return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        course.save()
        return Response(CourseSerializer(course).data)

    def delete(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
        class_count = course.classes.count()
        course.delete()
        return Response({"success": True, "deleted_classes": class_count})


class ClassListView(APIView):
    permission_classes = [IsAdminOrStaffOrReadOnly]

    def get(self, request):
        dept_id = request.query_params.get('department')
        course_id = request.query_params.get('course')
        qs = Class.objects.select_related('department', 'course', 'class_teacher', 'dqc_member').all()
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        if course_id:
            qs = qs.filter(course_id=course_id)
        classes = list(qs)

        def class_sort_key(c):
            dept_idx = 999
            if c.department and c.department.code in OFFICIAL_DEPT_ORDER:
                dept_idx = OFFICIAL_DEPT_ORDER.index(c.department.code)
            class_idx = 999
            if c.name in OFFICIAL_CLASS_ORDER:
                class_idx = OFFICIAL_CLASS_ORDER.index(c.name)
            return (dept_idx, class_idx)

        classes.sort(key=class_sort_key)
        user = request.user
        is_staff_or_admin = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'faculty') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        return Response([
            {
                "id": c.id,
                "name": c.name,
                "department": c.department.name,
                "department_code": c.department.code,
                "course": c.course.id if c.course else None,
                "course_name": c.course.name if c.course else None,
                "course_abbreviation": c.course.abbreviation if c.course else None,
                "year_number": c.year_number,
                "section": c.section,
                "batch_start_year": c.batch_start_year,
                "classTeacher": (c.class_teacher.email if is_staff_or_admin else None) if c.class_teacher else None,
                "classTeacherName": c.class_teacher.get_full_name() or c.class_teacher.username if c.class_teacher else None,
                "dqcMember": (c.dqc_member.email if is_staff_or_admin else None) if c.dqc_member else None,
                "dqcMemberName": c.dqc_member.get_full_name() or c.dqc_member.username if c.dqc_member else None,
                "num_students": c.num_students,
                "negative_points": c.negative_points,
            }
            for c in classes
        ])

    def post(self, request):
        user = request.user
        if not (user and user.is_authenticated and (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)):
            return Response(
                {"error": "Unauthorized: Only administrators can create classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        course_id = request.data.get('course_id')
        dept_code = request.data.get('department_code')
        name = request.data.get('name', '').strip()
        year_number = request.data.get('year_number')
        section = request.data.get('section', '').strip().upper()
        batch_start_year = request.data.get('batch_start_year')

        if course_id:
            try:
                course = Course.objects.select_related('department').get(pk=course_id)
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
            dept = course.department
            if not year_number:
                return Response({"error": "year_number is required when course_id is provided"}, status=status.HTTP_400_BAD_REQUEST)
            year_number = int(year_number)
            roman_map = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI'}
            year_roman = roman_map.get(year_number, str(year_number))
            if section:
                generated_name = f"{year_roman} {course.abbreviation} {section}"
            else:
                generated_name = f"{year_roman} {course.abbreviation}"
            name = name or generated_name

            if Class.objects.filter(course=course, year_number=year_number, section=section).exists():
                return Response(
                    {"error": f"Class '{name}' already exists for this course."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            cls = Class.objects.create(
                name=name,
                department=dept,
                course=course,
                year_number=year_number,
                section=section,
                batch_start_year=int(batch_start_year) if batch_start_year else None,
            )
        else:
            if not name or not dept_code:
                return Response({"error": "name and department_code are required"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                dept = Department.objects.get(code=dept_code)
            except Department.DoesNotExist:
                return Response({"error": f"Department '{dept_code}' not found"}, status=status.HTTP_404_NOT_FOUND)
            cls, _ = Class.objects.get_or_create(name=name, defaults={"department": dept})

        return Response({
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name,
            "department_code": cls.department.code,
            "course": cls.course.id if cls.course else None,
            "year_number": cls.year_number,
            "section": cls.section,
            "batch_start_year": cls.batch_start_year,
        }, status=status.HTTP_201_CREATED)

    def put(self, request):
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        name = request.data.get('name')
        teacher_email = request.data.get('classTeacher')
        dqc_email = request.data.get('dqcMember')

        if not name:
            return Response({"error": "Class name is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cls = Class.objects.get(name=name)
        except Class.DoesNotExist:
            return Response({"error": f"Class '{name}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            is_own_class = (cls.class_teacher_id == user.id)
            is_claiming_unassigned = (cls.class_teacher is None and teacher_email == user.email)
            if not (is_own_class or is_claiming_unassigned):
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if teacher_email is not None and teacher_email != (cls.class_teacher.email if cls.class_teacher else user.email):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        if teacher_email is not None:
            if teacher_email == "":
                if cls.class_teacher:
                    old_teacher = cls.class_teacher
                    cls.class_teacher = None
                    if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                        old_teacher.class_name = None
                        old_teacher.save(update_fields=['class_name'])
            else:
                try:
                    teacher = User.objects.get(email=teacher_email)
                    other_class = Class.objects.filter(class_teacher=teacher).exclude(id=cls.id).first()
                    if other_class:
                        return Response({
                            "error": f"Faculty '{teacher.get_full_name() or teacher_email}' is already assigned as Class Advisor to '{other_class.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    if cls.class_teacher and cls.class_teacher != teacher:
                        old_teacher = cls.class_teacher
                        if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                            old_teacher.class_name = None
                            old_teacher.save(update_fields=['class_name'])

                    cls.class_teacher = teacher
                    teacher.class_name = cls
                    teacher.department = cls.department
                    teacher.save(update_fields=['class_name', 'department'])
                except User.DoesNotExist:
                    return Response({"error": f"Teacher with email '{teacher_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if dqc_email is not None:
            if dqc_email == "":
                cls.dqc_member = None
            else:
                try:
                    student = User.objects.get(email=dqc_email)
                    other_class = Class.objects.filter(dqc_member=student).exclude(id=cls.id).first()
                    if other_class:
                        return Response({
                            "error": f"Student '{student.get_full_name() or dqc_email}' is already assigned as DQC Representative to '{other_class.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    if student.class_name and student.class_name.name != cls.name:
                        return Response({
                            "error": f"Student '{student.get_full_name() or dqc_email}' belongs to '{student.class_name.name}' and cannot be assigned to '{cls.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    cls.dqc_member = student
                except User.DoesNotExist:
                    return Response({"error": f"Student with email '{dqc_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        cls.save()

        return Response({
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name,
            "department_code": cls.department.code,
            "classTeacher": cls.class_teacher.email if cls.class_teacher else None,
            "classTeacherName": cls.class_teacher.get_full_name() or cls.class_teacher.username if cls.class_teacher else None,
            "dqcMember": cls.dqc_member.email if cls.dqc_member else None,
            "dqcMemberName": cls.dqc_member.get_full_name() or cls.dqc_member.username if cls.dqc_member else None,
        })


class ClassDetailView(APIView):
    permission_classes = [IsAdminOrStaffOrReadOnly]

    def _get_cls(self, pk):
        try:
            return Class.objects.select_related('department', 'course', 'class_teacher', 'dqc_member').get(pk=pk)
        except Class.DoesNotExist:
            return None

    def _serialize_class(self, cls):
        return {
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name if cls.department else None,
            "department_code": cls.department.code if cls.department else None,
            "course": cls.course.id if cls.course else None,
            "course_name": cls.course.name if cls.course else None,
            "course_abbreviation": cls.course.abbreviation if cls.course else None,
            "year_number": cls.year_number,
            "section": cls.section,
            "batch_start_year": cls.batch_start_year,
            "classTeacher": cls.class_teacher.email if cls.class_teacher else None,
            "classTeacherName": cls.class_teacher.get_full_name() or cls.class_teacher.username if cls.class_teacher else None,
            "dqcMember": cls.dqc_member.email if cls.dqc_member else None,
            "dqcMemberName": cls.dqc_member.get_full_name() or cls.dqc_member.username if cls.dqc_member else None,
            "num_students": cls.num_students,
            "negative_points": cls.negative_points,
        }

    def get(self, request, pk):
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._serialize_class(cls))

    def delete(self, request, pk):
        if not (request.user and request.user.is_authenticated and (getattr(request.user, 'role', None) == 'admin' or request.user.is_staff or request.user.is_superuser)):
            return Response({"error": "Admin permission required to delete classes."}, status=status.HTTP_403_FORBIDDEN)
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)
        class_name = cls.name
        cls.delete()
        return Response({"success": True, "deleted": class_name})

    def patch(self, request, pk):
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            if cls.class_teacher_id != user.id:
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'classTeacher' in request.data and request.data.get('classTeacher') != (cls.class_teacher.email if cls.class_teacher else ''):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        update_fields = []
        if 'num_students' in request.data:
            try:
                num_val = int(request.data['num_students'])
                if num_val < 0 or num_val > 1000:
                    return Response({"error": "num_students must be between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.num_students = num_val
                update_fields.append('num_students')
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                neg_val = float(request.data['negative_points'])
                if neg_val < 0 or neg_val > 10000:
                    return Response({"error": "negative_points must be between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.negative_points = neg_val
                update_fields.append('negative_points')
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a valid number between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            clean_name = str(request.data['name']).strip()
            if not clean_name or len(clean_name) > 100:
                return Response({"error": "Class name cannot be empty or exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.name = clean_name
            update_fields.append('name')
        if 'year_number' in request.data:
            try:
                yn = int(request.data['year_number'])
                if yn < 1 or yn > 6:
                    return Response({"error": "year_number must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                cls.year_number = yn
                update_fields.append('year_number')
            except (ValueError, TypeError):
                return Response({"error": "year_number must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        if 'section' in request.data:
            sec = str(request.data['section']).strip().upper()
            if len(sec) > 5:
                return Response({"error": "section cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.section = sec
            update_fields.append('section')
        if 'batch_start_year' in request.data:
            bsy = request.data['batch_start_year']
            if bsy:
                try:
                    bsy_int = int(bsy)
                    if bsy_int < 2000 or bsy_int > 2100:
                        return Response({"error": "batch_start_year must be between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
                    cls.batch_start_year = bsy_int
                except (ValueError, TypeError):
                    return Response({"error": "batch_start_year must be an integer between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                cls.batch_start_year = None
            update_fields.append('batch_start_year')

        if update_fields:
            cls.save(update_fields=update_fields)
        return Response(self._serialize_class(cls))

    def put(self, request, pk):
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            teacher_email_check = request.data.get('classTeacher')
            is_own_class = (cls.class_teacher_id == user.id)
            is_claiming_unassigned = (cls.class_teacher is None and teacher_email_check == user.email)
            if not (is_own_class or is_claiming_unassigned):
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if teacher_email_check is not None and teacher_email_check != (cls.class_teacher.email if cls.class_teacher else user.email):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        teacher_email = request.data.get('classTeacher')
        dqc_email = request.data.get('dqcMember')

        if teacher_email is not None:
            if teacher_email == "":
                if cls.class_teacher:
                    old_teacher = cls.class_teacher
                    cls.class_teacher = None
                    if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                        old_teacher.class_name = None
                        old_teacher.save(update_fields=['class_name'])
            else:
                try:
                    teacher = User.objects.get(email=teacher_email)
                    other_class = Class.objects.filter(class_teacher=teacher).exclude(id=cls.id).first()
                    if other_class:
                        return Response(
                            {"error": f"Faculty '{teacher.get_full_name() or teacher_email}' is already assigned as Class Advisor to '{other_class.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if cls.class_teacher and cls.class_teacher != teacher:
                        old_teacher = cls.class_teacher
                        if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                            old_teacher.class_name = None
                            old_teacher.save(update_fields=['class_name'])
                    cls.class_teacher = teacher
                    teacher.class_name = cls
                    teacher.department = cls.department
                    teacher.save(update_fields=['class_name', 'department'])
                except User.DoesNotExist:
                    return Response({"error": f"Teacher with email '{teacher_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if dqc_email is not None:
            if dqc_email == "":
                cls.dqc_member = None
            else:
                try:
                    student = User.objects.get(email=dqc_email)
                    other_class = Class.objects.filter(dqc_member=student).exclude(id=cls.id).first()
                    if other_class:
                        return Response(
                            {"error": f"Student '{student.get_full_name() or dqc_email}' is already assigned as DQC Representative to '{other_class.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if student.class_name and student.class_name.name != cls.name:
                        return Response(
                            {"error": f"Student '{student.get_full_name() or dqc_email}' belongs to '{student.class_name.name}' and cannot be assigned to '{cls.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    cls.dqc_member = student
                except User.DoesNotExist:
                    return Response({"error": f"Student with email '{dqc_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if 'num_students' in request.data:
            try:
                num_val = int(request.data['num_students'])
                if num_val < 0 or num_val > 1000:
                    return Response({"error": "num_students must be between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.num_students = num_val
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                neg_val = float(request.data['negative_points'])
                if neg_val < 0 or neg_val > 10000:
                    return Response({"error": "negative_points must be between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.negative_points = neg_val
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a valid number between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            clean_name = str(request.data['name']).strip()
            if not clean_name or len(clean_name) > 100:
                return Response({"error": "Class name cannot be empty or exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.name = clean_name
        if 'year_number' in request.data:
            try:
                yn = int(request.data['year_number'])
                if yn < 1 or yn > 6:
                    return Response({"error": "year_number must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                cls.year_number = yn
            except (ValueError, TypeError):
                return Response({"error": "year_number must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        if 'section' in request.data:
            sec = str(request.data['section']).strip().upper()
            if len(sec) > 5:
                return Response({"error": "section cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.section = sec
        if 'batch_start_year' in request.data:
            bsy = request.data['batch_start_year']
            if bsy:
                try:
                    bsy_int = int(bsy)
                    if bsy_int < 2000 or bsy_int > 2100:
                        return Response({"error": "batch_start_year must be between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
                    cls.batch_start_year = bsy_int
                except (ValueError, TypeError):
                    return Response({"error": "batch_start_year must be an integer between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                cls.batch_start_year = None
        if 'course' in request.data:
            try:
                cls.course = Course.objects.get(pk=request.data['course'])
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        cls.save()
        return Response(self._serialize_class(cls))
