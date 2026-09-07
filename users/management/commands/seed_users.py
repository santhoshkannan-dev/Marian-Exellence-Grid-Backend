from django.core.management.base import BaseCommand
from users.models import Department, Class, User, AcademicYear
from users.views import allocate_student_from_email

class Command(BaseCommand):
    help = 'Seeds departments, classes, academic years, and pre-mapped users'

    def handle(self, *args, **kwargs):
        self.stdout.write("Seeding database...")

        # 1. Seed Academic Years
        academic_years_data = [
            {"year": "2026-2027", "is_active": True},
            {"year": "2025-2026", "is_active": False},
            {"year": "2024-2025", "is_active": False},
            {"year": "2023-2024", "is_active": False},
        ]
        for ay in academic_years_data:
            obj, created = AcademicYear.objects.get_or_create(year=ay["year"], defaults={"is_active": ay["is_active"]})
            if created:
                self.stdout.write(f"Created Academic Year: {obj.year}")
            else:
                obj.is_active = ay["is_active"]
                obj.save()

        # Purge outdated legacy departments and non-matching classes if any
        outdated_dept_codes = ["UGDCA", "PGDCA", "CS"]
        Department.objects.filter(code__in=outdated_dept_codes).delete()

        # 2. Seed Departments in exact specified order
        departments_data = [
            {"name": "Department of Computer Applications", "code": "DCA"},
            {"name": "Department of Commerce", "code": "COMMERCE"},
            {"name": "Department of Business Administration", "code": "BBA_MBA"},
            {"name": "Department of Social Work", "code": "SOCIAL_WORK"},
            {"name": "Department of Physics", "code": "PHYSICS"},
            {"name": "Department of Economics", "code": "ECONOMICS"},
            {"name": "Department of Mathematics", "code": "MATHS"},
            {"name": "Department of English / Communicative English", "code": "BACE"},
            {"name": "Department of Communication & Media Studies", "code": "MCMS"},
            {"name": "Department of Hospitality & Tourism Management", "code": "MHTM"},
            {"name": "Department of Psychology", "code": "PSYCHOLOGY"},
            {"name": "Internal Quality Assurance Cell", "code": "IQAC"},
            {"name": "Administration", "code": "ADMIN"},
        ]

        departments = {}
        for dept in departments_data:
            obj, created = Department.objects.get_or_create(code=dept["code"], defaults={"name": dept["name"]})
            if not created and obj.name != dept["name"]:
                obj.name = dept["name"]
                obj.save()
            departments[dept["code"]] = obj
            if created:
                self.stdout.write(f"Created Department: {obj.name}")

        # 3. Seed Classes in exact specified order
        classes_data = [
            # 1. Department of Computer Applications
            {"name": "I BCA A", "dept_code": "DCA"},
            {"name": "I BCA B", "dept_code": "DCA"},
            {"name": "II BCA A", "dept_code": "DCA"},
            {"name": "II BCA B", "dept_code": "DCA"},
            {"name": "III BCA A", "dept_code": "DCA"},
            {"name": "III BCA B", "dept_code": "DCA"},
            {"name": "I MCA", "dept_code": "DCA"},
            {"name": "II MCA", "dept_code": "DCA"},

            # 2. Department of Commerce
            {"name": "I BCOM A", "dept_code": "COMMERCE"},
            {"name": "I BCOM B", "dept_code": "COMMERCE"},
            {"name": "I BCOM C", "dept_code": "COMMERCE"},
            {"name": "I BCOM (FINTECH)", "dept_code": "COMMERCE"},
            {"name": "II BCOM A", "dept_code": "COMMERCE"},
            {"name": "II BCOM B", "dept_code": "COMMERCE"},
            {"name": "II BCOM C", "dept_code": "COMMERCE"},
            {"name": "III BCOM A", "dept_code": "COMMERCE"},
            {"name": "III BCOM B", "dept_code": "COMMERCE"},
            {"name": "III BCOM C", "dept_code": "COMMERCE"},
            {"name": "I MCOM A", "dept_code": "COMMERCE"},
            {"name": "I MCOM B", "dept_code": "COMMERCE"},
            {"name": "II MCOM A", "dept_code": "COMMERCE"},
            {"name": "II MCOM B", "dept_code": "COMMERCE"},

            # 3. Department of Business Administration
            {"name": "I BBA A", "dept_code": "BBA_MBA"},
            {"name": "I BBA B", "dept_code": "BBA_MBA"},
            {"name": "II BBA A", "dept_code": "BBA_MBA"},
            {"name": "II BBA B", "dept_code": "BBA_MBA"},
            {"name": "III BBA A", "dept_code": "BBA_MBA"},
            {"name": "III BBA B", "dept_code": "BBA_MBA"},
            {"name": "I MBA A", "dept_code": "BBA_MBA"},
            {"name": "I MBA B", "dept_code": "BBA_MBA"},
            {"name": "I MBA C", "dept_code": "BBA_MBA"},
            {"name": "II MBA A", "dept_code": "BBA_MBA"},
            {"name": "II MBA B", "dept_code": "BBA_MBA"},
            {"name": "II MBA C", "dept_code": "BBA_MBA"},

            # 4. Department of Social Work
            {"name": "I BSW A", "dept_code": "SOCIAL_WORK"},
            {"name": "I BSW B", "dept_code": "SOCIAL_WORK"},
            {"name": "II BSW A", "dept_code": "SOCIAL_WORK"},
            {"name": "II BSW B", "dept_code": "SOCIAL_WORK"},
            {"name": "III BSW A", "dept_code": "SOCIAL_WORK"},
            {"name": "III BSW B", "dept_code": "SOCIAL_WORK"},
            {"name": "I MSW", "dept_code": "SOCIAL_WORK"},
            {"name": "II MSW", "dept_code": "SOCIAL_WORK"},

            # 5. Department of Physics (Integrated M.Sc. Physics)
            {"name": "I MSC PHYSICS", "dept_code": "PHYSICS"},
            {"name": "II MSC PHYSICS", "dept_code": "PHYSICS"},
            {"name": "III MSC PHYSICS", "dept_code": "PHYSICS"},
            {"name": "IV MSC PHYSICS", "dept_code": "PHYSICS"},
            {"name": "V MSC PHYSICS", "dept_code": "PHYSICS"},

            # 6. Department of Economics
            {"name": "I ECONOMICS", "dept_code": "ECONOMICS"},
            {"name": "II ECONOMICS", "dept_code": "ECONOMICS"},
            {"name": "III ECONOMICS", "dept_code": "ECONOMICS"},

            # 7. Department of Mathematics
            {"name": "I MATHS", "dept_code": "MATHS"},
            {"name": "II MATHS", "dept_code": "MATHS"},
            {"name": "III MATHS", "dept_code": "MATHS"},

            # 8. Department of English / Communicative English
            {"name": "I BACE", "dept_code": "BACE"},
            {"name": "II BACE", "dept_code": "BACE"},
            {"name": "III BACE", "dept_code": "BACE"},

            # 9. Department of Communication & Media Studies
            {"name": "I MCMS", "dept_code": "MCMS"},
            {"name": "II MCMS", "dept_code": "MCMS"},

            # 10. Department of Hospitality & Tourism Management
            {"name": "I MHTM", "dept_code": "MHTM"},
            {"name": "II MHTM", "dept_code": "MHTM"},

            # 11. Department of Psychology
            {"name": "I PSYCHOLOGY", "dept_code": "PSYCHOLOGY"},
        ]

        valid_class_names = [cls["name"] for cls in classes_data]
        Class.objects.exclude(name__in=valid_class_names).delete()

        classes = {}
        for cls in classes_data:
            dept = departments[cls["dept_code"]]
            obj, created = Class.objects.get_or_create(name=cls["name"], defaults={"department": dept})
            if not created and obj.department != dept:
                obj.department = dept
                obj.save()
            classes[cls["name"]] = obj
            if created:
                self.stdout.write(f"Created Class: {obj.name}")

        # 4. Seed Users
        users_data = [
            ("santhosh.25pmc152@mariancollege.org", "student", "DCA", "II MCA", False, False, "Santhosh", "Kannan"),
            ("amal.25pmc114@mariancollege.org", "student", "DCA", "II MCA", False, False, "Amal", "Thomas"),
            ("santhosh.25ubc154@mariancollege.org", "student", "DCA", "II BCA A", False, False, "Santhosh", "Kannan"),
            ("kochumol.abraham@mariancollege.org", "faculty", "DCA", None, True, False, "Kochumol", "Abraham"),
            ("allen.george@mariancollege.org", "evaluation", "DCA", None, True, False, "Allen", "George"),
            ("iqac@mariancollege.org", "iqac", "IQAC", None, True, False, "IQAC", "Coordinator"),
            ("admin@mariancollege.org", "admin", "ADMIN", None, True, True, "System", "Administrator"),
        ]

        seeded_users = {}
        for email, role, dept_code, class_name, is_staff, is_superuser, first, last in users_data:
            dept = departments.get(dept_code)
            cls = classes.get(class_name) if class_name else None

            username = email.split('@')[0]

            user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.create_user(
                    email=email,
                    username=username,
                    password="MarianPassword@123",
                    role=role,
                    department=dept,
                    class_name=cls,
                    is_staff=is_staff,
                    is_superuser=is_superuser,
                    first_name=first,
                    last_name=last
                )
                self.stdout.write(f"Created pre-registered user: {email} ({role})")
            else:
                user.role = role
                user.department = dept
                user.class_name = cls
                user.is_staff = is_staff
                user.is_superuser = is_superuser
                user.first_name = first
                user.last_name = last
                user.set_password("MarianPassword@123")
                user.save()

            user = allocate_student_from_email(user)
            seeded_users[email] = user

        # 5. Set Class Teacher & DQC member for classes
        mca_class = Class.objects.filter(name__in=["II MCA", "MCA"]).first()
        if mca_class:
            mca_class.class_teacher = seeded_users.get("kochumol.abraham@mariancollege.org")
            mca_class.dqc_member = seeded_users.get("santhosh.25pmc152@mariancollege.org")
            mca_class.save()
            self.stdout.write("Configured MCA Class Teacher and DQC member links")

        # 6. Seed Criteria Catalog (Wipe and recreate clean 12 categories)
        from users.models import CriteriaCategory, CriteriaItem, CriteriaRule
        CriteriaRule.objects.all().delete()
        CriteriaItem.objects.all().delete()
        CriteriaCategory.objects.all().delete()

        criteria_catalog_data = [
            {
                "code": "cat-academics",
                "category": "Academics",
                "access_level": "student_rep_only",
                "items": [
                    {
                        "title": "Class Pass Percentage %",
                        "type": "academic_grades",
                        "marks": 0.0,
                        "rules_json": {
                            "90_above": 5.0,
                            "80_90": 4.0,
                            "70_80": 3.0,
                            "fail": -2.0,
                            "pass_percentage_ranges": [
                                {"min": 90.01, "max": 100.0, "marks": 5.0},
                                {"min": 80.01, "max": 90.0, "marks": 4.0},
                                {"min": 70.01, "max": 80.0, "marks": 3.0},
                                {"min": 60.01, "max": 70.0, "marks": 2.0},
                                {"min": 50.01, "max": 60.0, "marks": 1.0},
                                {"min": 0.0, "max": 50.0, "marks": 0.0}
                            ]
                        }
                    },
                    {
                        "title": "SAVE Sem Result",
                        "type": "academic_grades",
                        "marks": 0.0,
                        "rules_json": {
                            "90_above": 5.0,
                            "80_90": 4.0,
                            "70_80": 3.0,
                            "fail": -2.0,
                            "pass_percentage_ranges": [
                                {"min": 90.01, "max": 100.0, "marks": 5.0},
                                {"min": 80.01, "max": 90.0, "marks": 4.0},
                                {"min": 70.01, "max": 80.0, "marks": 3.0},
                                {"min": 60.01, "max": 70.0, "marks": 2.0},
                                {"min": 50.01, "max": 60.0, "marks": 1.0},
                                {"min": 0.0, "max": 50.0, "marks": 0.0}
                            ]
                        }
                    },
                ]
            },
            {
                "code": "cat-online-courses",
                "category": "Online Courses",
                "access_level": "all_students",
                "items": [
                    {"title": "Swayam / NPTEL Course", "type": "count", "marks": 5.0},
                    {"title": "MOOC Course", "type": "count", "marks": 2.0},
                ]
            },
            {
                "code": "cat-competitive-exams",
                "category": "Competitive Exams",
                "access_level": "all_students",
                "items": [
                    {"title": "JRF Passed", "type": "date", "marks": 20.0},
                    {"title": "NET Passed", "type": "date", "marks": 10.0},
                    {"title": "Any Other Relevant Exam (IELTS, PET, Language Specific, etc.)", "type": "date", "marks": 3.0},
                    {"title": "Participation in Relevant Exam (UPSC / PSC Exams)", "type": "date", "marks": 1.0},
                ]
            },
            {
                "code": "cat-internships",
                "category": "Internships",
                "access_level": "all_students",
                "items": [
                    {"title": "Offline Internship (Min. 1 month)", "type": "range", "marks": 5.0},
                    {"title": "Online Internship (Min. 1 month)", "type": "range", "marks": 3.0},
                ]
            },
            {
                "code": "cat-scholarships",
                "category": "Scholarships",
                "access_level": "all_students",
                "items": [
                    {"title": "International Level Scholarship", "type": "fixed", "marks": 20.0},
                    {"title": "National Level Scholarship", "type": "fixed", "marks": 10.0},
                    {"title": "State Level Scholarship", "type": "fixed", "marks": 5.0},
                    {"title": "District Level Scholarship", "type": "fixed", "marks": 2.0},
                ]
            },
            {
                "code": "cat-research",
                "category": "Research",
                "access_level": "all_students",
                "items": [
                    {
                        "title": "Publications", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "Scopus / Web of Science": 10.0,
                                "Conference Proceeding / Peer reviewed article": 5.0
                            }
                        }
                    },
                    {
                        "title": "Paper Presentation", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "Outside Marian College": 5.0,
                                "Inside Marian College": 3.0
                            }
                        }
                    },
                    {
                        "title": "Patents", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "Utility": 10.0,
                                "Design": 5.0
                            }
                        }
                    },
                    {
                        "title": "Book Publications", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "Book": 10.0,
                                "Book Chapter": 5.0,
                                "Article": 2.0
                            }
                        }
                    },
                    {
                        "title": "Funded Projects", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "International": 20.0,
                                "National": 10.0,
                                "State": 5.0,
                                "Any Other": 3.0
                            }
                        }
                    }
                ]
            },
            {
                "code": "cat-startups",
                "category": "Startups",
                "access_level": "all_students",
                "items": [
                    {"title": "Government-Registered Start-up", "type": "count", "marks": 10.0},
                ]
            },
            {
                "code": "cat-prizes",
                "category": "Prizes",
                "access_level": "all_students",
                "items": [
                    {
                        "title": "From Marian College", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "1st Prize (Individual)": 10.0,
                                "2nd Prize (Individual)": 5.0,
                                "3rd Prize (Individual)": 3.0,
                                "1st Prize (Group)": 5.0,
                                "2nd Prize (Group)": 3.0,
                                "3rd Prize (Group)": 2.0
                            }
                        }
                    },
                    {
                        "title": "Outside Marian College", 
                        "type": "count", 
                        "marks": 0.0,
                        "rules_json": {
                            "subItems": {
                                "1st Prize (Individual)": 15.0,
                                "2nd Prize (Individual)": 10.0,
                                "3rd Prize (Individual)": 5.0,
                                "1st Prize (Group)": 10.0,
                                "2nd Prize (Group)": 5.0,
                                "3rd Prize (Group)": 3.0,
                                "Participation (Individual)": 3.0,
                                "Participation (Group)": 2.0
                            }
                        }
                    }
                ]
            },
            {
                "code": "cat-programs-organized",
                "category": "Programs Organized",
                "access_level": "all_students",
                "items": [
                    {"title": "Intercollegiate", "type": "count", "marks": 5.0},
                    {"title": "Intra - Collegiate", "type": "count", "marks": 3.0},
                    {"title": "Class Magazine", "type": "count", "marks": 5.0},
                ]
            },
            {
                "code": "cat-leadership",
                "category": "Leaderships",
                "access_level": "all_students",
                "items": [
                    {"title": "MCSC Executive Body Position", "type": "fixed", "marks": 5.0},
                    {"title": "SAHYA Executive Body Position", "type": "fixed", "marks": 5.0},
                    {"title": "Clubs & Associations Leadership Position", "type": "fixed", "marks": 5.0},
                    {"title": "Innovative / Sustainable Suggestion", "type": "fixed", "marks": 5.0},
                ]
            },
            {
                "code": "cat-social-responsibility",
                "category": "Social Responsibilities",
                "access_level": "all_students",
                "items": [
                    {"title": "Coordination of Event (Community Action / Outreach)", "type": "count", "marks": 5.0},
                    {"title": "Participation in Event", "type": "count", "marks": 3.0},
                    {"title": "News Media Coverage (Excluding Social Media)", "type": "count", "marks": 3.0},
                ]
            },
            {
                "code": "cat-career-advancement",
                "category": "Career Advancement",
                "access_level": "all_students",
                "items": [
                    {"title": "Library - Regular Footfall (Biometric / Entry)", "type": "count", "marks": 5.0},
                    {"title": "Library - Academic & Career Books Issued/Read", "type": "count", "marks": 5.0},
                    {"title": "Repository Creation (Drive / GitHub / LMS / Website)", "type": "fixed", "marks": 5.0},
                    {"title": "LinkedIn - Profile Completion (Active Profile)", "type": "fixed", "marks": 3.0},
                    {"title": "LinkedIn - Skill Badges Earned", "type": "count", "marks": 1.0},
                    {"title": "LinkedIn - Micro-credentials / Learning Certifications", "type": "count", "marks": 1.0},
                ]
            },
            {
                "code": "cat-documentation",
                "category": "Documentation",
                "access_level": "student_rep_only",
                "items": [
                    {"title": "Class Activity Report & Documents", "type": "fixed", "marks": 10.0},
                ]
            }
        ]

        CriteriaCategory.objects.all().delete()
        for cat_data in criteria_catalog_data:
            cat_obj = CriteriaCategory.objects.create(
                code=cat_data["code"],
                category=cat_data["category"],
                access_level=cat_data["access_level"]
            )
            for item_data in cat_data["items"]:
                item_obj = CriteriaItem.objects.create(
                    category=cat_obj,
                    title=item_data["title"],
                    type=item_data["type"],
                    marks=item_data["marks"],
                    rules_json=item_data.get("rules_json", None)
                )
                CriteriaRule.objects.create(
                    item=item_obj,
                    rule_type=item_data["type"],
                    maximum_marks=item_data["marks"],
                    min_count=1 if item_data["type"] == 'count' else None,
                    is_negative=True if item_data["type"] == 'negative' else False
                )

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))

