import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from users.models import User, CriteriaCategory
from users.permissions import IsAdminRole
from users.services.user_service import UserService

logger = logging.getLogger(__name__)


class EvaluatorManagementView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        from users.models import UserGroupModel
        from django.db.models import Q

        categories = list(CriteriaCategory.objects.all())
        all_evaluator_emails = set()
        for cat in categories:
            if cat.evaluators and isinstance(cat.evaluators, list):
                for e in cat.evaluators:
                    if isinstance(e, str) and e.strip():
                        all_evaluator_emails.add(e.strip().lower())

        eval_users = User.objects.filter(role__in=['evaluation', 'evaluator'])
        for u in eval_users:
            if u.email:
                all_evaluator_emails.add(u.email.strip().lower())

        # Also pull evaluators from all evaluator UserGroups
        eval_groups = UserGroupModel.objects.filter(
            Q(group_id='grp-evaluators') | Q(group_id='grp-evaluation-committee') |
            Q(group_id__icontains='evaluat') | Q(name__icontains='evaluat')
        )
        for g in eval_groups:
            if g.members and isinstance(g.members, list):
                for e in g.members:
                    if isinstance(e, str) and e.strip():
                        all_evaluator_emails.add(e.strip().lower())

        user_map = {u.email.lower(): u for u in User.objects.filter(email__in=all_evaluator_emails).select_related('department')}

        result = []
        for email in sorted(list(all_evaluator_emails)):
            user = user_map.get(email)
            assigned_codes = [
                cat.code for cat in categories
                if cat.evaluators and email in [x.lower() for x in cat.evaluators if isinstance(x, str)]
            ]
            first_name = user.first_name if user else ""
            last_name = user.last_name if user else ""
            full_name = f"{first_name} {last_name}".strip() if (first_name or last_name) else (user.username if user else email.split('@')[0])

            result.append({
                "id": user.id if user else None,
                "email": email,
                "name": full_name,
                "role": "evaluator",
                "department": user.department.name if (user and user.department) else "",
                "is_active": user.is_active if user else True,
                "assigned_categories": assigned_codes,
            })

        return Response(result, status=status.HTTP_200_OK)

    def post(self, request):
        from users.models import UserGroupModel
        email = request.data.get('email', '')
        if not email or not isinstance(email, str):
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        email = email.strip().lower()

        # Strict validation: Evaluators must use staff email only (name.name@mariancollege.org)
        if not UserService.is_staff_email(email):
            return Response(
                {"error": "Evaluators must use staff email address in name.name@mariancollege.org format (e.g. allen.george@mariancollege.org). Student emails (e.g. amal.25pmc114@mariancollege.org) are not permitted."},
                status=status.HTTP_400_BAD_REQUEST
            )

        name = (request.data.get('name') or '').strip()
        assigned_categories = request.data.get('assigned_categories', [])
        if not isinstance(assigned_categories, list):
            assigned_categories = []

        parts = name.split(" ", 1) if name else []
        default_first = email.split("@")[0].split(".")[0].capitalize()
        first_name = parts[0] if parts and parts[0] else default_first
        last_name = parts[1] if len(parts) > 1 else ""

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': email,
                'first_name': first_name,
                'last_name': last_name,
                'role': 'evaluation',
                'is_staff': False,
                'is_active': True,
            }
        )

        if not created:
            if user.role not in ('admin', 'iqac', 'faculty'):
                user.role = 'evaluation'
            user.is_active = True
            if name:
                user.first_name = first_name
                user.last_name = last_name
            user.save()

        # Sync with UserGroupModel
        for gid in ('grp-evaluators', 'grp-evaluation-committee'):
            g = UserGroupModel.objects.filter(group_id=gid).first()
            if g:
                cur_members = list(g.members or [])
                if email not in [m.lower() for m in cur_members if isinstance(m, str)]:
                    cur_members.append(email)
                    g.members = cur_members
                    g.save(update_fields=['members'])

        categories = CriteriaCategory.objects.all()
        assigned_codes = []
        for cat in categories:
            current_evaluators = [e.lower() for e in (cat.evaluators or []) if isinstance(e, str)]
            should_assign = (cat.code in assigned_categories or str(cat.id) in [str(x) for x in assigned_categories])
            if should_assign:
                assigned_codes.append(cat.code)
                if email not in current_evaluators:
                    current_evaluators.append(email)
                    cat.evaluators = current_evaluators
                    cat.save(update_fields=['evaluators'])
            else:
                if email in current_evaluators:
                    current_evaluators = [e for e in current_evaluators if e != email]
                    cat.evaluators = current_evaluators
                    cat.save(update_fields=['evaluators'])

        return Response({
            "id": user.id,
            "email": user.email,
            "name": user.get_full_name() or user.username,
            "role": "evaluator",
            "department": user.department.name if user.department else "",
            "is_active": user.is_active,
            "assigned_categories": assigned_codes,
        }, status=status.HTTP_201_CREATED)


class EvaluatorDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, email):
        return self.patch(request, email)

    def patch(self, request, email):
        if not email or not isinstance(email, str):
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        email = email.strip().lower()

        if 'assigned_categories' in request.data:
            assigned_categories = request.data.get('assigned_categories', [])
            if not isinstance(assigned_categories, list):
                assigned_categories = []

            categories = CriteriaCategory.objects.all()
            for cat in categories:
                current_evaluators = [e.lower() for e in (cat.evaluators or []) if isinstance(e, str)]
                should_assign = (cat.code in assigned_categories or str(cat.id) in [str(x) for x in assigned_categories])
                if should_assign:
                    if email not in current_evaluators:
                        current_evaluators.append(email)
                        cat.evaluators = current_evaluators
                        cat.save(update_fields=['evaluators'])
                else:
                    if email in current_evaluators:
                        current_evaluators = [e for e in current_evaluators if e != email]
                        cat.evaluators = current_evaluators
                        cat.save(update_fields=['evaluators'])

        try:
            user = User.objects.get(email=email)
            if 'name' in request.data and request.data['name']:
                parts = str(request.data['name']).strip().split(' ', 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ""
            if 'is_active' in request.data:
                user.is_active = bool(request.data['is_active'])
            user.save()
        except User.DoesNotExist:
            user = None

        categories = CriteriaCategory.objects.all()
        assigned_codes = [
            cat.code for cat in categories
            if cat.evaluators and email in [x.lower() for x in cat.evaluators if isinstance(x, str)]
        ]

        return Response({
            "id": user.id if user else None,
            "email": email,
            "name": user.get_full_name() if user else email.split('@')[0],
            "role": "evaluator",
            "department": user.department.name if (user and user.department) else "",
            "is_active": user.is_active if user else True,
            "assigned_categories": assigned_codes,
        }, status=status.HTTP_200_OK)

    def delete(self, request, email):
        if not email or not isinstance(email, str):
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        email = email.strip().lower()

        # Remove evaluator from all categories
        categories = CriteriaCategory.objects.all()
        for cat in categories:
            if cat.evaluators:
                current_evaluators = [e.lower() for e in cat.evaluators if isinstance(e, str)]
                if email in current_evaluators:
                    cat.evaluators = [e for e in current_evaluators if e != email]
                    cat.save(update_fields=['evaluators'])

        # Remove from UserGroupModel evaluator groups
        from users.models import UserGroupModel
        for gid in ('grp-evaluators', 'grp-evaluation-committee'):
            g = UserGroupModel.objects.filter(group_id=gid).first()
            if g and g.members:
                cur_members = [m for m in g.members if isinstance(m, str) and m.lower() != email]
                if len(cur_members) != len(g.members):
                    g.members = cur_members
                    g.save(update_fields=['members'])

        # Delete user if role is evaluation/evaluator
        try:
            user = User.objects.get(email=email)
            if user.role in ('evaluation', 'evaluator'):
                user.delete()
        except User.DoesNotExist:
            pass

        return Response({"success": True}, status=status.HTTP_200_OK)
