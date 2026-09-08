from rest_framework import serializers
from .models import (
    Department, Course, AcademicYear, Class, User,
    CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion, Submission,
    AcademicGradeBreakdown, WorkflowAuditTrail, ClassIndexResult,
    Champion, BugReport
)


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = '__all__'

    def validate_year(self, value):
        import re
        if not re.match(r'^\d{4}-\d{4}$', str(value).strip()):
            raise serializers.ValidationError("Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026').")
        return str(value).strip()


class CourseSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    department_code = serializers.CharField(source='department.code', read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'department', 'department_name', 'department_code',
            'name', 'abbreviation', 'email_code',
            'is_multi_batch', 'duration_years',
            'created_at', 'updated_at'
        ]

    def validate_duration_years(self, value):
        if value < 1 or value > 6:
            raise serializers.ValidationError("Course duration must be between 1 and 6 years.")
        return value


class ClassSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    course_abbreviation = serializers.CharField(source='course.abbreviation', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    class_teacher_name = serializers.SerializerMethodField()
    class_teacher_email = serializers.CharField(source='class_teacher.email', read_only=True)
    dqc_member_name = serializers.SerializerMethodField()
    dqc_member_email = serializers.CharField(source='dqc_member.email', read_only=True)

    def get_class_teacher_name(self, obj):
        if obj.class_teacher:
            return obj.class_teacher.get_full_name() or obj.class_teacher.email
        return None

    def get_dqc_member_name(self, obj):
        if obj.dqc_member:
            return obj.dqc_member.get_full_name() or obj.dqc_member.email
        return None

    class Meta:
        model = Class
        fields = [
            'id', 'name', 'department', 'department_name',
            'course', 'course_name', 'course_abbreviation',
            'year_number', 'section', 'batch_start_year',
            'class_teacher', 'class_teacher_name', 'class_teacher_email',
            'dqc_member', 'dqc_member_name', 'dqc_member_email',
            'num_students', 'negative_points',
            'created_at', 'updated_at'
        ]

    def validate_num_students(self, value):
        if value < 0 or value > 1000:
            raise serializers.ValidationError("Number of students must be between 0 and 1000.")
        return value

    def validate_negative_points(self, value):
        if value < 0 or value > 10000:
            raise serializers.ValidationError("Negative points must be between 0 and 10000.")
        return value

    def validate_year_number(self, value):
        if value is not None and (value < 1 or value > 6):
            raise serializers.ValidationError("Year number must be between 1 and 6.")
        return value


class DepartmentSerializer(serializers.ModelSerializer):
    courses = CourseSerializer(many=True, read_only=True)
    classes = ClassSerializer(many=True, read_only=True)

    class Meta:
        model = Department
        fields = [
            'id', 'name', 'code', 'email_prefix', 'level',
            'courses', 'classes',
            'created_at', 'updated_at'
        ]

    def validate_level(self, value):
        allowed = [c[0] for c in Department.LEVEL_CHOICES]
        if value not in allowed:
            raise serializers.ValidationError(f"Invalid level '{value}'. Allowed: {', '.join(allowed)}.")
        return value


class CriteriaVersionSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(source='items.count', read_only=True)

    class Meta:
        model = CriteriaVersion
        fields = ['id', 'academic_year', 'version', 'name', 'created_at', 'published_at', 'is_locked', 'item_count']


class CriteriaRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CriteriaRule
        fields = '__all__'


class CriteriaItemSerializer(serializers.ModelSerializer):
    rules = CriteriaRuleSerializer(many=True, read_only=True)

    class Meta:
        model = CriteriaItem
        fields = ['id', 'category', 'version', 'title', 'type', 'marks', 'rules_json', 'rules', 'created_at', 'updated_at']


class CriteriaCategorySerializer(serializers.ModelSerializer):
    items = CriteriaItemSerializer(many=True, read_only=True)

    class Meta:
        model = CriteriaCategory
        fields = ['id', 'code', 'category', 'access_level', 'evaluators', 'items', 'created_at']


class UserSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    class_name_display = serializers.CharField(source='class_name.name', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'department', 'department_name', 'class_name',
            'class_name_display', 'roll_number', 'batch_year',
            'is_student_rep', 'is_staff', 'is_superuser', 'is_active'
        ]
        read_only_fields = ['is_staff', 'is_superuser']


class AcademicGradeBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicGradeBreakdown
        fields = ['s_grade_count', 'a_plus_grade_count', 'a_grade_count', 'other_pass_count', 'failed_count', 'class_pass_percentage', 'total_students']


class SubmissionSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    grade_breakdown = AcademicGradeBreakdownSerializer(read_only=True)

    class Meta:
        model = Submission
        fields = [
            'id', 'user', 'user_email', 'user_name', 'criteria_id', 'criteria_version',
            'academic_year', 'submission_type', 'description', 'status',
            'remarks', 'marks', 'proof', 'proof_hash', 'certificate_id', 'event_id', 'start_date', 'end_date', 'evaluator_verified',
            'evidence', 'verified_by_name', 'rep_verified_by_name', 'rep_remarks',
            'teacher_verified_by_name', 'teacher_remarks', 'evaluator_verified_by_name',
            'evaluator_remarks', 'grade_breakdown', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'user', 'verified_by_name', 'rep_verified_by_name',
            'teacher_verified_by_name', 'evaluator_verified_by_name',
            'evaluator_verified', 'created_at', 'updated_at'
        ]


class WorkflowAuditTrailSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source='actor.email', read_only=True)

    class Meta:
        model = WorkflowAuditTrail
        fields = [
            'id', 'submission', 'actor', 'actor_email', 'stage',
            'stage_name', 'previous_status', 'new_status', 'comments',
            'ip_address', 'user_agent', 'request_id', 'previous_hash',
            'record_hash', 'created_at'
        ]
        read_only_fields = '__all__'


class ClassIndexResultSerializer(serializers.ModelSerializer):
    class_name_display = serializers.CharField(source='class_name.name', read_only=True)
    academic_year_display = serializers.CharField(source='academic_year.year', read_only=True)

    class Meta:
        model = ClassIndexResult
        fields = ['id', 'class_name', 'class_name_display', 'academic_year', 'academic_year_display', 'academic_score', 'co_curricular_score', 'extra_curricular_score', 'final_index', 'rank', 'updated_at']
        read_only_fields = '__all__'


class ChampionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Champion
        fields = '__all__'
        read_only_fields = ['created_at']

    def validate_rank(self, value):
        if value < 1 or value > 100:
            raise serializers.ValidationError("Rank must be between 1 and 100.")
        return value


class BugReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = BugReport
        fields = '__all__'
        read_only_fields = ['status', 'whatsapp_numbers', 'created_at', 'updated_at']

    def validate_title(self, value):
        clean_val = str(value).strip()
        if len(clean_val) < 3:
            raise serializers.ValidationError("Title must be at least 3 characters long.")
        if len(clean_val) > 255:
            raise serializers.ValidationError("Title cannot exceed 255 characters.")
        return clean_val

    def validate_description(self, value):
        clean_val = str(value).strip()
        if len(clean_val) < 5:
            raise serializers.ValidationError("Description must be at least 5 characters long.")
        if len(clean_val) > 5000:
            raise serializers.ValidationError("Description cannot exceed 5000 characters.")
        return clean_val



