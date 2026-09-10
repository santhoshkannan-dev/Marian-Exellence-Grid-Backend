import logging
from datetime import datetime
from django.db.models import Q
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)


class UserService:
    """
    Focused service for user identity, role determination, student academic allocation,
    and JWT token issuance.
    """

    @staticmethod
    def get_tokens_for_user(user):
        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    @staticmethod
    def parse_name_from_email(email):
        """
        Dynamically derives user name from email local part for any email.
        e.g. amal.thomas.25pmc114@mariancollege.org -> 'Amal Thomas'
        e.g. kochumol.abraham@mariancollege.org -> 'Kochumol Abraham'
        e.g. amal.25pmc114@mariancollege.org -> 'Amal'
        """
        if not email or '@' not in email:
            return "User"
        local_part = email.split('@')[0]
        parts = local_part.split('.')
        name_parts = []
        for part in parts:
            if any(char.isdigit() for char in part):
                break
            name_parts.append(part.capitalize())
        if name_parts:
            return " ".join(name_parts)
        return parts[0].capitalize()

    @staticmethod
    def parse_email_code(email):
        """
        Parses the raw code segment from a Marian College student email.
        Returns dict with level_char, email_code, batch_year, roll_digits, roll_number, section_hint
        e.g. amal.25pmc114@mariancollege.org ->
             level_char='p', email_code='mc', batch_year=2025, roll_digits='114', roll_number=14
        """
        if not email or '@' not in email:
            return None
        local_part = email.split('@')[0]
        parts = local_part.split('.')
        if len(parts) < 2:
            return None

        # The code segment is the last part that starts with digits
        code_part = None
        for p in reversed(parts):
            if len(p) >= 5 and p[:2].isdigit():
                code_part = p
                break
        if not code_part:
            return None

        batch_year = 2000 + int(code_part[:2])
        level_char = code_part[2].lower()       # 'p' or 'u'
        email_code = code_part[3:5].lower()     # 'mc', 'bc' etc.
        roll_digits = code_part[5:]              # '114', '214'

        # Derive section hint from roll series (100-series -> A, 200-series -> B ...)
        section_hint = ''
        roll_number = None
        if roll_digits.isdigit():
            roll_num = int(roll_digits)
            roll_number = roll_num % 100       # actual roll: last two digits
            series = roll_num // 100
            section_map = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F'}
            section_hint = section_map.get(series, 'A')

        return {
            'level_char': level_char,
            'email_code': email_code,
            'batch_year': batch_year,
            'roll_digits': roll_digits,
            'roll_number': roll_number,
            'section_hint': section_hint,
        }

    @staticmethod
    def get_active_year_start():
        from users.models import AcademicYear
        try:
            active = AcademicYear.objects.filter(is_active=True).order_by('-year').first()
            if active and active.year:
                return int(active.year.split('-')[0])
        except (ValueError, IndexError, Exception):
            pass
        return datetime.now().year

    @staticmethod
    def get_year_roman(year_number):
        mapping = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI'}
        return mapping.get(year_number, str(year_number))

    @classmethod
    def parse_student_email(cls, email):
        """
        Parses Marian College student email format using DB Course/Department lookup.
        Returns a dict with resolved department, class name, batch_year, roll_number etc.
        Falls back to basic inference if no matching Course found in DB.
        """
        from users.models import Course

        parsed_code = cls.parse_email_code(email)
        if not parsed_code:
            return None

        level_char = parsed_code['level_char']
        email_code = parsed_code['email_code']
        batch_year = parsed_code['batch_year']
        roll_number = parsed_code['roll_number']
        roll_digits = parsed_code['roll_digits']
        section_hint = parsed_code['section_hint']

        # Calculate year-in-course from active academic year
        active_year_start = cls.get_active_year_start()
        year_number = active_year_start - batch_year + 1
        year_roman = cls.get_year_roman(year_number)

        # --- Try DB-driven resolution first ---
        try:
            course = Course.objects.select_related('department').get(
                email_code=email_code,
                department__email_prefix=level_char
            )
            dept_obj = course.department
            section = section_hint if course.is_multi_batch else ''
            class_name = f"{year_roman} {course.abbreviation} {section}".strip() if section else f"{year_roman} {course.abbreviation}"
            return {
                'first_name': cls.parse_name_from_email(email),
                'batch_year': batch_year,
                'year_number': year_number,
                'level': 'Postgraduate' if level_char == 'p' else 'Undergraduate',
                'course': course,
                'course_name': course.abbreviation,
                'department_name': dept_obj.name,
                'department_code': dept_obj.code,
                'department_obj': dept_obj,
                'section': section,
                'class_name': class_name,
                'roll_number': roll_number,
                'roll_digits': roll_digits,
                'db_resolved': True,
            }
        except Course.DoesNotExist:
            pass

        # --- Fallback: infer from hardcoded map ---
        course_map = {
            'mc': ('Master of Computer Applications', 'MCA', 'PGDCA'),
            'bc': ('Bachelor of Computer Applications', 'BCA', 'UGDCA'),
            'ba': ('Bachelor of Business Administration', 'BBA', 'UGDBA'),
            'cm': ('Commerce', 'BCom', 'UGCOM'),
            'sw': ('Social Work', 'MSW', 'PGSW'),
        }
        if email_code in course_map:
            full_name, abbr, dept_code = course_map[email_code]
            section = section_hint if level_char == 'u' else ''
            class_name = f"{year_roman} {abbr} {section}".strip() if section else f"{year_roman} {abbr}"
            return {
                'first_name': cls.parse_name_from_email(email),
                'batch_year': batch_year,
                'year_number': year_number,
                'level': 'Postgraduate' if level_char == 'p' else 'Undergraduate',
                'course': None,
                'course_name': abbr,
                'department_name': full_name,
                'department_code': dept_code,
                'department_obj': None,
                'section': section,
                'class_name': class_name,
                'roll_number': roll_number,
                'roll_digits': roll_digits,
                'db_resolved': False,
            }

        return None

    @classmethod
    def allocate_student_from_email(cls, user):
        """
        Allocates student user to the resolved Department and Class objects.
        Uses active AcademicYear + DB Course lookup for accurate class resolution.
        Stores roll_number and batch_year on the user.
        """
        from users.models import Class, Department

        if user.role != 'student' and cls.determine_role_from_email(user.email) != 'student':
            advisor_class = Class.objects.filter(class_teacher=user).first()
            if advisor_class:
                if user.class_name != advisor_class or user.department != advisor_class.department:
                    user.class_name = advisor_class
                    user.department = advisor_class.department
                    user.save(update_fields=['class_name', 'department'])
            elif user.class_name:
                if not Class.objects.filter(class_teacher=user).exists():
                    user.class_name = None
                    user.save(update_fields=['class_name'])
            return user

        parsed = cls.parse_student_email(user.email)
        if not parsed:
            return user

        update_fields = set(['department', 'class_name', 'roll_number', 'batch_year'])

        user.roll_number = parsed.get('roll_number')
        user.batch_year = parsed.get('batch_year')

        if not user.first_name or user.first_name == user.username:
            derived_name = cls.parse_name_from_email(user.email)
            name_parts = derived_name.split(" ", 1)
            user.first_name = name_parts[0]
            update_fields.add('first_name')
            if len(name_parts) > 1:
                user.last_name = name_parts[1]
                update_fields.add('last_name')

        if parsed.get('db_resolved') and parsed.get('department_obj'):
            dept_obj = parsed['department_obj']
            course = parsed['course']
            year_number = parsed['year_number']
            section = parsed.get('section', '')
            class_name = parsed['class_name']

            class_obj, created = Class.objects.get_or_create(
                course=course,
                year_number=year_number,
                section=section,
                defaults={
                    'name': class_name,
                    'department': dept_obj,
                }
            )
            if created or class_obj.department != dept_obj:
                class_obj.department = dept_obj
                class_obj.save()

            user.department = dept_obj
            user.class_name = class_obj

        else:
            dept_code = parsed['department_code']
            dept_name = parsed['department_name']
            class_name = parsed['class_name']

            dept_obj = Department.objects.filter(code=dept_code).first() or Department.objects.filter(name=dept_name).first()
            if not dept_obj:
                try:
                    dept_obj, _ = Department.objects.get_or_create(
                        code=dept_code,
                        defaults={'name': dept_name}
                    )
                except Exception:
                    dept_obj = Department.objects.filter(name=dept_name).first() or Department.objects.filter(code=dept_code).first()

            class_obj = Class.objects.filter(name=class_name).first()
            if not class_obj:
                class_obj = Class.objects.create(name=class_name, department=dept_obj)
            elif dept_obj and class_obj.department != dept_obj:
                class_obj.department = dept_obj
                class_obj.save(update_fields=['department'])

            user.department = dept_obj
            user.class_name = class_obj

        user.save(update_fields=list(update_fields))
        return user

    @staticmethod
    def determine_role_from_email(email):
        """
        Determines user role based on Marian College email format:
        - name.number (e.g. santhosh.25pmc152, amal.25pmc114) -> student
        - name.name (e.g. kochumol.abraham) -> faculty (staff)
        """
        username_part = email.split('@')[0]
        parts = username_part.split('.')
        if len(parts) >= 2:
            second_part = parts[1]
            if any(char.isdigit() for char in second_part):
                return "student"
            else:
                return "faculty"
        return "student"

    @staticmethod
    def is_staff_email(email):
        """
        Validates that the email belongs to a Marian College staff member.
        Staff email format: name.name@mariancollege.org (e.g. kochumol.abraham@mariancollege.org).
        Student email format: name.startingwithnumber@mariancollege.org (e.g. amal.25pmc114@mariancollege.org).
        """
        if not email or not isinstance(email, str):
            return False
        clean = email.strip().lower()
        if not clean.endswith('@mariancollege.org'):
            return False

        local_part = clean[:-len('@mariancollege.org')]
        parts = local_part.split('.')
        if len(parts) < 2:
            return False

        for p in parts:
            if not p or not p.isalpha():
                return False

        if UserService.parse_email_code(clean) is not None:
            return False

        return True

    @staticmethod
    def is_student_email(email):
        """
        Validates that the email belongs to a Marian College student.
        Student email format: name.startingwithnumber@mariancollege.org (e.g. amal.25pmc114@mariancollege.org).
        Staff email format: name.name@mariancollege.org (e.g. kochumol.abraham@mariancollege.org).
        """
        if not email or not isinstance(email, str):
            return False
        clean = email.strip().lower()
        if not clean.endswith('@mariancollege.org'):
            return False

        local_part = clean[:-len('@mariancollege.org')]
        parts = local_part.split('.')
        if len(parts) < 2:
            return False

        if UserService.parse_email_code(clean) is not None:
            return True

        return False

    @staticmethod
    def is_user_dqc_rep(user):
        from users.models import UserGroupModel
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        user_email = (getattr(user, 'email', '') or '').strip().lower()
        if not user_email:
            return False

        dqc_groups = UserGroupModel.objects.filter(
            Q(group_id='grp-dqc-student-rep') |
            Q(group_id__icontains='dqc') |
            Q(name__icontains='dqc') |
            Q(name__icontains='dac')
        )
        for rg in dqc_groups:
            if rg.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in rg.members):
                return True
        return False

    @staticmethod
    def is_user_student_rep(user):
        from users.models import Class, UserGroupModel
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        user_email = (getattr(user, 'email', '') or '').strip().lower()
        if not user_email:
            return False

        if getattr(user, 'is_student_rep', False) or getattr(user, 'is_dqc_member', False):
            return True
        if Class.objects.filter(dqc_member=user).exists():
            return True
        if Class.objects.filter(dqc_member__email__iexact=user_email).exists():
            return True

        rep_groups = UserGroupModel.objects.filter(
            Q(group_id__in=['grp-student-reps', 'grp-dqc-student-rep']) |
            Q(group_id__icontains='rep') | Q(group_id__icontains='dqc') |
            Q(name__icontains='student rep') | Q(name__icontains='representative') | Q(name__icontains='dqc')
        )
        for rg in rep_groups:
            if rg.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in rg.members):
                return True

        if UserService.is_user_dqc_rep(user):
            return True

        return False

    @staticmethod
    def get_student_rep_classes(user):
        from users.models import Class
        if not user or not getattr(user, 'is_authenticated', False):
            return Class.objects.none()

        if not UserService.is_user_student_rep(user):
            return Class.objects.none()

        user_email = (getattr(user, 'email', '') or '').strip().lower()
        q = Q(dqc_member=user)
        if user_email:
            q |= Q(dqc_member__email__iexact=user_email)

        if getattr(user, 'class_name', None):
            q |= Q(id=user.class_name_id)

        if user_email:
            parsed = UserService.parse_student_email(user_email)
            if parsed and parsed.get('class_name'):
                q |= Q(name__iexact=parsed['class_name'])

        classes = Class.objects.filter(q).distinct()
        if not classes.exists() and user_email:
            UserService.allocate_student_from_email(user)
            if getattr(user, 'class_name', None):
                classes = Class.objects.filter(id=user.class_name_id)
        return classes


# Standalone alias exports for backward-compatibility
get_tokens_for_user = UserService.get_tokens_for_user
parse_name_from_email = UserService.parse_name_from_email
parse_email_code = UserService.parse_email_code
get_active_year_start = UserService.get_active_year_start
get_year_roman = UserService.get_year_roman
parse_student_email = UserService.parse_student_email
allocate_student_from_email = UserService.allocate_student_from_email
determine_role_from_email = UserService.determine_role_from_email
is_staff_email = UserService.is_staff_email
is_student_email = UserService.is_student_email
is_user_dqc_rep = UserService.is_user_dqc_rep
is_user_student_rep = UserService.is_user_student_rep
get_student_rep_classes = UserService.get_student_rep_classes

