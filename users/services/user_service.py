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

    DEPARTMENT_COURSE_MAP = {
        'ce': {'course': 'BACE', 'department': 'Department of English / Languages', 'dept_code': 'ENG', 'level': 'UG', 'multi_batch': False, 'duration': 3, 'display_course': 'BACE'},
        'bm': {'course': 'B.Com', 'department': 'School of Commerce and Professional Studies', 'dept_code': 'SCPS', 'level': 'UG', 'multi_batch': True, 'duration': 3, 'display_course': 'BCOM'},
        'mm': {'course': 'M.Com', 'department': 'School of Commerce and Professional Studies', 'dept_code': 'SCPS', 'level': 'PG', 'multi_batch': True, 'duration': 2, 'display_course': 'MCOM'},
        'bf': {'course': 'B.Com FinTech', 'department': 'School of Commerce and Professional Studies', 'dept_code': 'SCPS', 'level': 'UG', 'multi_batch': False, 'duration': 3, 'display_course': 'BCOM (FINTECH)'},
        'bb': {'course': 'BBA', 'department': 'UG Department of Business Administration', 'dept_code': 'UGBBA', 'level': 'UG', 'multi_batch': True, 'duration': 3, 'display_course': 'BBA'},
        'bc': {'course': 'BCA', 'department': 'UG Department of Computer Applications', 'dept_code': 'UGDCA', 'level': 'UG', 'multi_batch': True, 'duration': 3, 'display_course': 'BCA'},
        'sw': {'course': 'BSW', 'department': 'School of Social Work', 'dept_code': 'SSW', 'level': 'UG', 'multi_batch': True, 'duration': 3, 'display_course': 'BSW'},
        'psw': {'course': 'MSW', 'department': 'School of Social Work', 'dept_code': 'SSW', 'level': 'PG', 'multi_batch': False, 'duration': 2, 'display_course': 'MSW'},
        'ma': {'course': 'B.Sc Mathematics', 'department': 'Department of Mathematics', 'dept_code': 'MATHS', 'level': 'UG', 'multi_batch': False, 'duration': 3, 'display_course': 'MATHS'},
        'cm': {'course': 'MCMS', 'department': 'Department of Communication and Media Studies', 'dept_code': 'MCMS', 'level': 'PG', 'multi_batch': False, 'duration': 2, 'display_course': 'MCMS'},
        'ht': {'course': 'MHTM', 'department': 'Department of Hospitality and Tourism Management', 'dept_code': 'MHTM', 'level': 'PG', 'multi_batch': False, 'duration': 2, 'display_course': 'MHTM'},
        'ph': {'course': 'M.Sc Integrated Physics', 'department': 'Department of Physics', 'dept_code': 'PHYSICS', 'level': 'Integrated', 'multi_batch': False, 'duration': 5, 'display_course': 'MSC PHYSICS'},
        'ec': {'course': 'BA Economics', 'department': 'Department of Economics', 'dept_code': 'ECONOMICS', 'level': 'UG', 'multi_batch': False, 'duration': 3, 'display_course': 'ECONOMICS'},
        'py': {'course': 'B.Sc Psychology', 'department': 'Department of Psychology', 'dept_code': 'PSYCHOLOGY', 'level': 'UG', 'multi_batch': False, 'duration': 3, 'display_course': 'PSYCHOLOGY'},
        'ba': {'course': 'MBA', 'department': 'Masters of Business Administration', 'dept_code': 'MBA', 'level': 'PG', 'multi_batch': True, 'duration': 2, 'display_course': 'MBA'},
        'mc': {'course': 'MCA', 'department': 'PG Department of Computer Applications', 'dept_code': 'PGDCA', 'level': 'PG', 'multi_batch': False, 'duration': 2, 'display_course': 'MCA'},
    }

    SECTION_MAP = {'1': 'A', '2': 'B', '3': 'C'}
    ROMAN_YEARS = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI'}

    @staticmethod
    def parse_name_from_email(email):
        """
        Derives user name from email local part.
        Staff: kochumol.abraham@mariancollege.org -> 'Kochumol Abraham'
        Student: santhosh.25pmc152@mariancollege.org -> 'Santhosh'
                 amal.thomas.25pmc114@mariancollege.org -> 'Amal Thomas'
        """
        if not email or '@' not in email:
            return "User"
        local_part = email.strip().split('@')[0]
        parts = local_part.split('.')
        name_parts = []
        for part in parts:
            if any(char.isdigit() for char in part):
                break
            if part:
                name_parts.append(part.capitalize())
        if name_parts:
            return " ".join(name_parts)
        return parts[0].capitalize()

    @staticmethod
    def parse_email_code(email):
        """
        Parses the code segment from Marian student email:
        Formula: [name].YYLCCDXX@mariancollege.org
        e.g. santhosh.25pmc152@mariancollege.org ->
             batch_year=2025, level_char='p', email_code='mc', section_digit='1', roll_number=52
        """
        if not email or '@' not in email:
            return None
        email_clean = email.strip().lower()
        domain = email_clean.split('@')[1] if '@' in email_clean else ''
        if domain != 'mariancollege.org':
            return None
        local_part = email_clean.split('@')[0]
        parts = local_part.split('.')
        if len(parts) < 2:
            return None

        # Code segment is the last part
        code_part = parts[-1]
        import re
        # Pattern: YY (2 digits) + L (1 char [upi]) + CC (2 chars [a-z]) + D (1 digit) + XX (2 digits)
        match = re.match(r'^(\d{2})([upi])([a-z]{2})(\d)(\d{2})$', code_part)
        if not match:
            # Fallback for roll format variations if any
            match = re.match(r'^(\d{2})([upi])([a-z]{2})(\d)(\d+)$', code_part)
            if not match:
                return None

        batch_str, level_char, course_code_str, section_digit, roll_digits = match.groups()
        batch_year = 2000 + int(batch_str)
        roll_number = int(roll_digits) if roll_digits.isdigit() else None

        # Determine MSW vs BSW by Level ('p' = MSW, 'u' = BSW)
        lookup_code = course_code_str
        if course_code_str == 'sw' and level_char == 'p':
            lookup_code = 'psw'

        class_code = f"{batch_str}{level_char}{course_code_str}{section_digit}"

        return {
            'batch_str': batch_str,
            'batch_year': batch_year,
            'level_char': level_char,
            'course_code_str': course_code_str,
            'lookup_code': lookup_code,
            'section_digit': section_digit,
            'roll_digits': f"{section_digit}{roll_digits}",
            'student_id': roll_digits,
            'roll_number': roll_number,
            'class_code': class_code,
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
        Parses Marian College student email format:
        [name].YYLCCDXX@mariancollege.org
        Returns resolved department, course, class name, batch_year, roll_number.
        """
        from users.models import Course, Department

        parsed_code = cls.parse_email_code(email)
        if not parsed_code:
            return None

        batch_year = parsed_code['batch_year']
        level_char = parsed_code['level_char']
        lookup_code = parsed_code['lookup_code']
        section_digit = parsed_code['section_digit']
        roll_number = parsed_code['roll_number']
        roll_digits = parsed_code['roll_digits']

        # Year number calculation from active academic year
        active_year_start = cls.get_active_year_start()
        year_number = active_year_start - batch_year + 1
        year_roman = cls.get_year_roman(year_number)

        course_info = cls.DEPARTMENT_COURSE_MAP.get(lookup_code)
        if not course_info:
            return None

        display_name = course_info.get('display_course', course_info['course'])
        is_multi_batch = course_info.get('multi_batch', False)

        section = ''
        if is_multi_batch:
            section = cls.SECTION_MAP.get(section_digit, 'A')
            class_name = f"{year_roman} {display_name} {section}".strip()
        else:
            class_name = f"{year_roman} {display_name}".strip()

        # Database lookup
        dept_obj = Department.objects.filter(code=course_info['dept_code']).first() or Department.objects.filter(name=course_info['department']).first()
        course_obj = None
        if dept_obj:
            course_obj = Course.objects.filter(department=dept_obj, email_code=lookup_code).first()
            if not course_obj and lookup_code == 'psw':
                course_obj = Course.objects.filter(department=dept_obj, email_code='sw').first()

        level_name = 'Postgraduate' if level_char == 'p' else ('Integrated' if level_char == 'i' else 'Undergraduate')
        roman_year = cls.ROMAN_YEARS.get(year_number, str(year_number))

        return {
            'name': cls.parse_name_from_email(email),
            'first_name': cls.parse_name_from_email(email),
            'batch_year': batch_year,
            'year_number': year_number,
            'year': roman_year,
            'level': level_name,
            'course': course_obj,
            'course_name': course_info['course'],
            'course_abbreviation': display_name,
            'department': course_info['department'],
            'department_name': course_info['department'],
            'department_code': course_info['dept_code'],
            'department_obj': dept_obj,
            'section': section,
            'class_name': class_name,
            'class_code': parsed_code.get('class_code', ''),
            'academic_year': f"{active_year_start}-{active_year_start + 1}",
            'roll_number': roll_number,
            'roll_digits': roll_digits,
            'db_resolved': bool(dept_obj and course_obj),
        }

    @classmethod
    def allocate_student_from_email(cls, user):
        """
        Allocates student user to the resolved Department and Class objects.
        Uses active AcademicYear + DB Course lookup for accurate class resolution.
        Stores roll_number and batch_year on the user.
        """
        from users.models import Class, Department

        if user.role == 'admin' or user.is_superuser:
            return user

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

        dept_obj = parsed.get('department_obj')
        if not dept_obj and parsed.get('department_code'):
            dept_obj = Department.objects.filter(code=parsed['department_code']).first() or Department.objects.filter(name=parsed['department_name']).first()
            if not dept_obj:
                try:
                    dept_obj = Department.objects.create(
                        code=parsed['department_code'],
                        name=parsed['department_name']
                    )
                except Exception:
                    dept_obj = Department.objects.filter(code=parsed['department_code']).first()

        course = parsed.get('course')
        year_number = parsed.get('year_number')
        section = parsed.get('section', '')
        class_name = parsed.get('class_name')

        class_obj = None
        if course and dept_obj and year_number:
            class_obj = Class.objects.filter(
                course=course,
                year_number=year_number,
                section=section
            ).first()

        if not class_obj and class_name:
            class_obj = Class.objects.filter(name=class_name).first()

        if not class_obj and dept_obj and class_name:
            class_obj, _ = Class.objects.get_or_create(
                name=class_name,
                defaults={
                    'department': dept_obj,
                    'course': course,
                    'year_number': year_number,
                    'section': section,
                    'batch_start_year': parsed.get('batch_year'),
                    'batch_year': parsed.get('batch_year'),
                    'class_code': parsed.get('class_code', ''),
                    'academic_year': parsed.get('academic_year', '')
                }
            )

        if class_obj:
            updated_class_fields = []
            if parsed.get('class_code') and not class_obj.class_code:
                class_obj.class_code = parsed['class_code']
                updated_class_fields.append('class_code')
            if parsed.get('batch_year') and not class_obj.batch_year:
                class_obj.batch_year = parsed['batch_year']
                updated_class_fields.append('batch_year')
            if parsed.get('academic_year') and not class_obj.academic_year:
                class_obj.academic_year = parsed['academic_year']
                updated_class_fields.append('academic_year')
            if dept_obj and class_obj.department != dept_obj:
                class_obj.department = dept_obj
                updated_class_fields.append('department')
            if updated_class_fields:
                class_obj.save(update_fields=updated_class_fields)

        user.department = dept_obj
        user.class_name = class_obj

        user.save(update_fields=list(update_fields))
        return user

    @staticmethod
    def determine_role_from_email(email):
        """
        Determines user role based on Marian College email format:
        - Emails listed in settings.ADMIN_EMAILS → admin
        - name.number (e.g. santhosh.25pmc152, amal.25pmc114, faizah.24uce109) → student
        - name.name (e.g. kochumol.abraham) → faculty (staff)

        Admin addresses are resolved from the ADMIN_EMAILS environment variable
        (parsed in settings.py) to avoid hardcoded credentials in source code.
        """
        if not email or '@' not in email:
            return "student"
        email_lower = email.strip().lower()
        # --- Admin check: compare against env-configured ADMIN_EMAILS list ---
        try:
            from django.conf import settings
            admin_emails = getattr(settings, 'ADMIN_EMAILS', frozenset())
            if email_lower in admin_emails:
                return "admin"
        except Exception:
            pass

        if email_lower.startswith('admin@'):
            return "admin"

        username_part = email_lower.split('@')[0]
        parts = username_part.split('.')
        if len(parts) >= 2:
            last_part = parts[-1]
            if any(char.isdigit() for char in last_part):
                return "student"
            else:
                return "faculty"
        if any(char.isdigit() for char in username_part):
            return "student"
        return "faculty"

    @staticmethod
    def is_user_dqc_rep(user):
        from users.models import Class, UserGroupMember, UserGroupModel
        from django.db.models import Q
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_dqc_member', False):
            return True
        if Class.objects.filter(dqc_member=user).exists():
            return True
        user_email = (getattr(user, 'email', '') or '').strip().lower()
        if not user_email:
            return False
        if UserGroupMember.objects.filter(
            Q(group__group_id='grp-dqc-student-rep') | Q(group__name__iexact='DQC Student Rep Group'),
            email__iexact=user_email
        ).exists():
            return True
        dqc_group = UserGroupModel.objects.filter(
            Q(group_id='grp-dqc-student-rep') | Q(name__iexact='DQC Student Rep Group')
        ).first()
        if dqc_group and dqc_group.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in dqc_group.members):
            return True
        return False

    @staticmethod
    def is_user_student_rep(user):
        from users.models import Class, UserGroupModel, UserGroupMember
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_student_rep', False) or getattr(user, 'is_dqc_member', False):
            return True
        if Class.objects.filter(dqc_member=user).exists():
            return True
        user_email = (getattr(user, 'email', '') or '').strip().lower()
        if user_email:
            if Class.objects.filter(dqc_member__email__iexact=user_email).exists():
                return True
            if UserGroupMember.objects.filter(
                Q(group__group_id__in=['grp-student-reps', 'grp-dqc-student-rep']) |
                Q(group__name__in=['Student Representatives', 'DQC Student Rep Group']),
                email__iexact=user_email
            ).exists():
                return True
            rep_group = UserGroupModel.objects.filter(
                Q(group_id__in=['grp-student-reps', 'grp-dqc-student-rep']) |
                Q(name__icontains='student rep') |
                Q(name__icontains='dqc')
            ).first()
            if rep_group and rep_group.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in rep_group.members):
                return True
        return False

    @staticmethod
    def get_user_group_names(user):
        """
        Returns a sorted list of unique official group names the user is affiliated with:
        'Student Representatives', 'DQC Student Rep Group', 'Class Teachers Council', 'Evaluation Committee'.
        """
        if not user or not getattr(user, 'is_authenticated', False):
            return []

        group_names = set()
        user_email = (getattr(user, 'email', '') or '').strip().lower()

        from users.models import UserGroupMember, UserGroupModel, Class, TeacherClassAssignment, EvaluatorCategoryAssignment

        # 1. From UserGroupMember records
        if user_email:
            memberships = UserGroupMember.objects.filter(
                Q(user=user) | Q(email__iexact=user_email)
            ).select_related('group')
            for m in memberships:
                if m.group and m.group.name:
                    group_names.add(m.group.name)

            # 2. From UserGroupModel JSON members list
            for g in UserGroupModel.objects.all():
                if g.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in g.members):
                    group_names.add(g.name)

        # 3. Direct relational / flag checks
        if getattr(user, 'is_dqc_member', False):
            group_names.add('DQC Student Rep Group')

        if getattr(user, 'is_student_rep', False):
            group_names.add('Student Representatives')

        if user_email and Class.objects.filter(Q(dqc_member=user) | Q(dqc_member__email__iexact=user_email)).exists():
            group_names.add('DQC Student Rep Group')

        user_role = getattr(user, 'role', '')
        if user_role in ('faculty', 'staff', 'teacher'):
            if TeacherClassAssignment.objects.filter(teacher=user, is_active=True).exists() or Class.objects.filter(class_teacher=user).exists():
                group_names.add('Class Teachers Council')
            if user_email and Class.objects.filter(class_teacher__email__iexact=user_email).exists():
                group_names.add('Class Teachers Council')

        if EvaluatorCategoryAssignment.objects.filter(Q(evaluator=user) | Q(member__email__iexact=user_email)).exists():
            group_names.add('Evaluation Committee')

        return sorted(list(group_names))

    @staticmethod
    def is_user_round1_verifier(user):
        """
        Determines if user has Round 1 Verification Authority.
        Allowed roles: 'DQC Student Rep Group' OR 'Student Representatives'.
        """
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'role', '') == 'admin' or getattr(user, 'is_superuser', False):
            return True
        if getattr(user, 'role', '') != 'student':
            return False
        groups = UserService.get_user_group_names(user)
        return (
            'Student Representatives' in groups or
            'DQC Student Rep Group' in groups or
            UserService.is_user_dqc_rep(user) or
            UserService.is_user_student_rep(user)
        )


    @staticmethod
    def get_user_badge(user):
        """
        Returns 'DQC member' if the user is a DQC representative,
        or 'Student Rep' if the user is a class student representative,
        otherwise None.
        """
        if UserService.is_user_dqc_rep(user):
            return "DQC member"
        if UserService.is_user_student_rep(user):
            return "Student Rep"
        return None

    @staticmethod
    def get_user_roles_and_dual_status(user):
        """
        Determines the available roles and whether a staff user has dual roles
        (Class Teachers Council AND Evaluation Committee).
        Returns:
            dict with keys: 'role', 'has_dual_role', 'available_roles', 'badge'
        """
        from users.models import Class, UserGroupMember, UserGroupModel, CriteriaCategory
        email = (getattr(user, 'email', '') or '').strip().lower()
        base_role = getattr(user, 'role', 'student')

        if base_role == 'admin':
            return {
                'role': 'admin',
                'has_dual_role': False,
                'available_roles': ['admin'],
                'badge': None
            }

        badge = UserService.get_user_badge(user)

        # Check if student
        if base_role == 'student' or UserService.determine_role_from_email(email) == 'student':
            return {
                'role': 'student',
                'has_dual_role': False,
                'available_roles': ['student'],
                'badge': badge
            }

        # Staff user: check membership in Class Teachers Council and Evaluation Committee
        in_class_teachers = False
        from users.models import TeacherClassAssignment, EvaluatorCategoryAssignment
        if TeacherClassAssignment.objects.filter(teacher=user, is_active=True).exists():
            in_class_teachers = True
        elif UserGroupMember.objects.filter(group__group_id='grp-class-teachers', email__iexact=email).exists():
            in_class_teachers = True
        elif Class.objects.filter(class_teacher=user).exists() or Class.objects.filter(class_teacher__email__iexact=email).exists():
            in_class_teachers = True
        else:
            ct_group = UserGroupModel.objects.filter(group_id='grp-class-teachers').first()
            if ct_group and ct_group.members and any(isinstance(e, str) and e.strip().lower() == email for e in ct_group.members):
                in_class_teachers = True

        in_evaluation_committee = False
        if EvaluatorCategoryAssignment.objects.filter(Q(evaluator=user) | Q(member__email__iexact=email)).exists():
            in_evaluation_committee = True
        elif UserGroupMember.objects.filter(group__group_id='grp-evaluation-committee', email__iexact=email).exists():
            in_evaluation_committee = True
        elif CriteriaCategory.objects.filter(evaluators__contains=email).exists():
            in_evaluation_committee = True
        else:
            ec_group = UserGroupModel.objects.filter(group_id='grp-evaluation-committee').first()
            if ec_group and ec_group.members and any(isinstance(e, str) and e.strip().lower() == email for e in ec_group.members):
                in_evaluation_committee = True

        if in_class_teachers and in_evaluation_committee:
            return {
                'role': 'teacher',  # Dual role default landing is Class Teachers window
                'has_dual_role': True,
                'available_roles': ['teacher', 'evaluator'],
                'badge': None
            }
        elif in_evaluation_committee:
            return {
                'role': 'evaluator',
                'has_dual_role': False,
                'available_roles': ['evaluator'],
                'badge': None
            }
        elif in_class_teachers:
            return {
                'role': 'teacher',
                'has_dual_role': False,
                'available_roles': ['teacher'],
                'badge': None
            }
        else:
            # General faculty default
            return {
                'role': 'teacher' if base_role == 'faculty' else base_role,
                'has_dual_role': False,
                'available_roles': ['teacher'],
                'badge': None
            }


# Standalone alias exports for backward-compatibility
get_tokens_for_user = UserService.get_tokens_for_user
parse_name_from_email = UserService.parse_name_from_email
parse_email_code = UserService.parse_email_code
get_active_year_start = UserService.get_active_year_start
get_year_roman = UserService.get_year_roman
parse_student_email = UserService.parse_student_email
allocate_student_from_email = UserService.allocate_student_from_email
determine_role_from_email = UserService.determine_role_from_email
is_user_student_rep = UserService.is_user_student_rep
is_user_dqc_rep = UserService.is_user_dqc_rep
get_user_badge = UserService.get_user_badge
get_user_roles_and_dual_status = UserService.get_user_roles_and_dual_status
get_user_group_names = UserService.get_user_group_names
is_user_round1_verifier = UserService.is_user_round1_verifier

