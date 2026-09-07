from rest_framework import serializers
from .models import (
    Department, Course, AcademicYear, Class, User,
    CriteriaCategory, CriteriaItem, CriteriaRule, Submission,
    AcademicGradeBreakdown, WorkflowAuditTrail, ClassIndexResult,
    Champion
)


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = '__all__'


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


class CriteriaRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CriteriaRule
        fields = '__all__'


class CriteriaItemSerializer(serializers.ModelSerializer):
    rules = CriteriaRuleSerializer(many=True, read_only=True)

    class Meta:
        model = CriteriaItem
        fields = ['id', 'category', 'title', 'type', 'marks', 'rules_json', 'rules', 'created_at', 'updated_at']


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


class AcademicGradeBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicGradeBreakdown
        fields = ['s_grade_count', 'a_plus_grade_count', 'a_grade_count', 'failed_count', 'class_pass_percentage', 'total_students']


class SubmissionSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    grade_breakdown = AcademicGradeBreakdownSerializer(read_only=True)

    class Meta:
        model = Submission
        fields = [
            'id', 'user', 'user_email', 'user_name', 'criteria_id',
            'academic_year', 'submission_type', 'description', 'status',
            'remarks', 'marks', 'proof', 'proof_hash', 'certificate_id', 'event_id', 'start_date', 'end_date', 'evaluator_verified',
            'evidence', 'verified_by_name', 'rep_verified_by_name', 'rep_remarks',
            'teacher_verified_by_name', 'teacher_remarks', 'evaluator_verified_by_name',
            'evaluator_remarks', 'grade_breakdown', 'created_at', 'updated_at'
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


class ClassIndexResultSerializer(serializers.ModelSerializer):
    class_name_display = serializers.CharField(source='class_name.name', read_only=True)
    academic_year_display = serializers.CharField(source='academic_year.year', read_only=True)

    class Meta:
        model = ClassIndexResult
        fields = ['id', 'class_name', 'class_name_display', 'academic_year', 'academic_year_display', 'academic_score', 'co_curricular_score', 'extra_curricular_score', 'final_index', 'rank', 'updated_at']


class ChampionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Champion
        fields = '__all__'


