from django.core.management.base import BaseCommand
from users.models import Department, Course, Class, User, AcademicYear

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

        # Do not modify or delete existing users.
        # Users are managed through registration, admin, or application workflows.

        # 2. Seed 13 Official Academic Departments in exact order
        departments_data = [
            {"name": "Department of English / Languages", "code": "ENG", "level": "UG", "email_prefix": "u"},
            {"name": "School of Commerce and Professional Studies", "code": "SCPS", "level": "UG", "email_prefix": "u"},
            {"name": "UG Department of Business Administration", "code": "UGBBA", "level": "UG", "email_prefix": "u"},
            {"name": "UG Department of Computer Applications", "code": "UGDCA", "level": "UG", "email_prefix": "u"},
            {"name": "School of Social Work", "code": "SSW", "level": "UG", "email_prefix": "u"},
            {"name": "Department of Mathematics", "code": "MATHS", "level": "UG", "email_prefix": "u"},
            {"name": "Department of Communication and Media Studies", "code": "MCMS", "level": "PG", "email_prefix": "p"},
            {"name": "Department of Hospitality and Tourism Management", "code": "MHTM", "level": "PG", "email_prefix": "p"},
            {"name": "Department of Physics", "code": "PHYSICS", "level": "UG", "email_prefix": "i"},
            {"name": "Department of Economics", "code": "ECONOMICS", "level": "UG", "email_prefix": "u"},
            {"name": "Department of Psychology", "code": "PSYCHOLOGY", "level": "UG", "email_prefix": "u"},
            {"name": "Masters of Business Administration", "code": "MBA", "level": "PG", "email_prefix": "p"},
            {"name": "PG Department of Computer Applications", "code": "PGDCA", "level": "PG", "email_prefix": "p"},
        ]

        departments = {}
        for dept in departments_data:
            obj, created = Department.objects.update_or_create(
                code=dept["code"],
                defaults={
                    "name": dept["name"],
                    "level": dept["level"],
                    "email_prefix": dept["email_prefix"]
                }
            )
            departments[dept["code"]] = obj
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} Department: {obj.name} ({obj.code})")

        # 3. Seed 16 Official Courses
        courses_data = [
            {"dept_code": "ENG", "name": "BA Communicative English", "abbreviation": "BACE", "email_code": "ce", "is_multi_batch": False, "duration_years": 3},
            {"dept_code": "SCPS", "name": "Bachelor of Commerce", "abbreviation": "B.Com", "email_code": "bm", "is_multi_batch": True, "duration_years": 3},
            {"dept_code": "SCPS", "name": "Master of Commerce", "abbreviation": "M.Com", "email_code": "mm", "is_multi_batch": True, "duration_years": 2},
            {"dept_code": "SCPS", "name": "B.Com FinTech with Applied AI", "abbreviation": "B.Com FinTech", "email_code": "bf", "is_multi_batch": False, "duration_years": 3},
            {"dept_code": "UGBBA", "name": "Bachelor of Business Administration", "abbreviation": "BBA", "email_code": "bb", "is_multi_batch": True, "duration_years": 3},
            {"dept_code": "UGDCA", "name": "Bachelor of Computer Applications", "abbreviation": "BCA", "email_code": "bc", "is_multi_batch": True, "duration_years": 3},
            {"dept_code": "SSW", "name": "Bachelor of Social Work", "abbreviation": "BSW", "email_code": "sw", "is_multi_batch": True, "duration_years": 3},
            {"dept_code": "SSW", "name": "Master of Social Work", "abbreviation": "MSW", "email_code": "psw", "is_multi_batch": False, "duration_years": 2},
            {"dept_code": "MATHS", "name": "B.Sc Mathematics", "abbreviation": "MATHS", "email_code": "ma", "is_multi_batch": False, "duration_years": 3},
            {"dept_code": "MCMS", "name": "Master of Communication and Media Studies", "abbreviation": "MCMS", "email_code": "cm", "is_multi_batch": False, "duration_years": 2},
            {"dept_code": "MHTM", "name": "Master of Hospitality and Tourism Management", "abbreviation": "MHTM", "email_code": "ht", "is_multi_batch": False, "duration_years": 2},
            {"dept_code": "PHYSICS", "name": "M.Sc Integrated Physics", "abbreviation": "MSC PHYSICS", "email_code": "ph", "is_multi_batch": False, "duration_years": 5},
            {"dept_code": "ECONOMICS", "name": "BA Economics", "abbreviation": "ECONOMICS", "email_code": "ec", "is_multi_batch": False, "duration_years": 3},
            {"dept_code": "PSYCHOLOGY", "name": "B.Sc Psychology", "abbreviation": "PSYCHOLOGY", "email_code": "py", "is_multi_batch": False, "duration_years": 3},
            {"dept_code": "MBA", "name": "Master of Business Administration", "abbreviation": "MBA", "email_code": "ba", "is_multi_batch": True, "duration_years": 2},
            {"dept_code": "PGDCA", "name": "Master of Computer Applications", "abbreviation": "MCA", "email_code": "mc", "is_multi_batch": False, "duration_years": 2},
        ]

        courses = {}
        for c in courses_data:
            dept = departments[c["dept_code"]]
            obj, created = Course.objects.update_or_create(
                department=dept,
                email_code=c["email_code"],
                defaults={
                    "name": c["name"],
                    "abbreviation": c["abbreviation"],
                    "is_multi_batch": c["is_multi_batch"],
                    "duration_years": c["duration_years"],
                }
            )
            courses[c["email_code"]] = obj
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} Course: {obj.abbreviation} ({obj.email_code})")

        # 4. Seed 65 Official Classes
        classes_data = [
            # 1. BACE (ce)
            {"name": "I BACE", "course_code": "ce", "year_number": 1, "section": ""},
            {"name": "II BACE", "course_code": "ce", "year_number": 2, "section": ""},
            {"name": "III BACE", "course_code": "ce", "year_number": 3, "section": ""},
            # 2. B.Com (bm) - Div A, B, C
            {"name": "I BCOM A", "course_code": "bm", "year_number": 1, "section": "A"},
            {"name": "I BCOM B", "course_code": "bm", "year_number": 1, "section": "B"},
            {"name": "I BCOM C", "course_code": "bm", "year_number": 1, "section": "C"},
            {"name": "II BCOM A", "course_code": "bm", "year_number": 2, "section": "A"},
            {"name": "II BCOM B", "course_code": "bm", "year_number": 2, "section": "B"},
            {"name": "II BCOM C", "course_code": "bm", "year_number": 2, "section": "C"},
            {"name": "III BCOM A", "course_code": "bm", "year_number": 3, "section": "A"},
            {"name": "III BCOM B", "course_code": "bm", "year_number": 3, "section": "B"},
            {"name": "III BCOM C", "course_code": "bm", "year_number": 3, "section": "C"},
            # 3. B.Com FinTech (bf)
            {"name": "I BCOM (FINTECH)", "course_code": "bf", "year_number": 1, "section": ""},
            {"name": "II BCOM (FINTECH)", "course_code": "bf", "year_number": 2, "section": ""},
            {"name": "III BCOM (FINTECH)", "course_code": "bf", "year_number": 3, "section": ""},
            # 4. BBA (bb) - Div A, B
            {"name": "I BBA A", "course_code": "bb", "year_number": 1, "section": "A"},
            {"name": "I BBA B", "course_code": "bb", "year_number": 1, "section": "B"},
            {"name": "II BBA A", "course_code": "bb", "year_number": 2, "section": "A"},
            {"name": "II BBA B", "course_code": "bb", "year_number": 2, "section": "B"},
            {"name": "III BBA A", "course_code": "bb", "year_number": 3, "section": "A"},
            {"name": "III BBA B", "course_code": "bb", "year_number": 3, "section": "B"},
            # 5. BCA (bc) - Div A, B
            {"name": "I BCA A", "course_code": "bc", "year_number": 1, "section": "A"},
            {"name": "I BCA B", "course_code": "bc", "year_number": 1, "section": "B"},
            {"name": "II BCA A", "course_code": "bc", "year_number": 2, "section": "A"},
            {"name": "II BCA B", "course_code": "bc", "year_number": 2, "section": "B"},
            {"name": "III BCA A", "course_code": "bc", "year_number": 3, "section": "A"},
            {"name": "III BCA B", "course_code": "bc", "year_number": 3, "section": "B"},
            # 6. BSW (sw) - Div A, B
            {"name": "I BSW A", "course_code": "sw", "year_number": 1, "section": "A"},
            {"name": "I BSW B", "course_code": "sw", "year_number": 1, "section": "B"},
            {"name": "II BSW A", "course_code": "sw", "year_number": 2, "section": "A"},
            {"name": "II BSW B", "course_code": "sw", "year_number": 2, "section": "B"},
            {"name": "III BSW A", "course_code": "sw", "year_number": 3, "section": "A"},
            {"name": "III BSW B", "course_code": "sw", "year_number": 3, "section": "B"},
            # 7. B.Sc Mathematics (ma)
            {"name": "I MATHS", "course_code": "ma", "year_number": 1, "section": ""},
            {"name": "II MATHS", "course_code": "ma", "year_number": 2, "section": ""},
            {"name": "III MATHS", "course_code": "ma", "year_number": 3, "section": ""},
            # 8. BA Economics (ec)
            {"name": "I ECONOMICS", "course_code": "ec", "year_number": 1, "section": ""},
            {"name": "II ECONOMICS", "course_code": "ec", "year_number": 2, "section": ""},
            {"name": "III ECONOMICS", "course_code": "ec", "year_number": 3, "section": ""},
            # 9. B.Sc Psychology (py)
            {"name": "I PSYCHOLOGY", "course_code": "py", "year_number": 1, "section": ""},
            {"name": "II PSYCHOLOGY", "course_code": "py", "year_number": 2, "section": ""},
            {"name": "III PSYCHOLOGY", "course_code": "py", "year_number": 3, "section": ""},
            # 10. MBA (ba) - Div A, B, C
            {"name": "I MBA A", "course_code": "ba", "year_number": 1, "section": "A"},
            {"name": "I MBA B", "course_code": "ba", "year_number": 1, "section": "B"},
            {"name": "I MBA C", "course_code": "ba", "year_number": 1, "section": "C"},
            {"name": "II MBA A", "course_code": "ba", "year_number": 2, "section": "A"},
            {"name": "II MBA B", "course_code": "ba", "year_number": 2, "section": "B"},
            {"name": "II MBA C", "course_code": "ba", "year_number": 2, "section": "C"},
            # 11. MCA (mc)
            {"name": "I MCA", "course_code": "mc", "year_number": 1, "section": ""},
            {"name": "II MCA", "course_code": "mc", "year_number": 2, "section": ""},
            # 12. M.Com (mm) - Div A, B
            {"name": "I MCOM A", "course_code": "mm", "year_number": 1, "section": "A"},
            {"name": "I MCOM B", "course_code": "mm", "year_number": 1, "section": "B"},
            {"name": "II MCOM A", "course_code": "mm", "year_number": 2, "section": "A"},
            {"name": "II MCOM B", "course_code": "mm", "year_number": 2, "section": "B"},
            # 13. MSW (psw)
            {"name": "I MSW", "course_code": "psw", "year_number": 1, "section": ""},
            {"name": "II MSW", "course_code": "psw", "year_number": 2, "section": ""},
            # 14. MCMS (cm)
            {"name": "I MCMS", "course_code": "cm", "year_number": 1, "section": ""},
            {"name": "II MCMS", "course_code": "cm", "year_number": 2, "section": ""},
            # 15. MHTM (ht)
            {"name": "I MHTM", "course_code": "ht", "year_number": 1, "section": ""},
            {"name": "II MHTM", "course_code": "ht", "year_number": 2, "section": ""},
            # 16. M.Sc Integrated Physics (ph)
            {"name": "I MSC PHYSICS", "course_code": "ph", "year_number": 1, "section": ""},
            {"name": "II MSC PHYSICS", "course_code": "ph", "year_number": 2, "section": ""},
            {"name": "III MSC PHYSICS", "course_code": "ph", "year_number": 3, "section": ""},
            {"name": "IV MSC PHYSICS", "course_code": "ph", "year_number": 4, "section": ""},
            {"name": "V MSC PHYSICS", "course_code": "ph", "year_number": 5, "section": ""},
        ]

        classes = {}
        for cls in classes_data:
            course = courses[cls["course_code"]]
            dept = course.department
            obj, created = Class.objects.update_or_create(
                name=cls["name"],
                defaults={
                    "department": dept,
                    "course": course,
                    "year_number": cls["year_number"],
                    "section": cls["section"],
                }
            )
            classes[cls["name"]] = obj
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} Class: {obj.name}")

        # 5. Seed Admin Users Only
        #
        # Admin accounts are sourced exclusively from the ADMIN_EMAILS
        # environment variable. No dummy student, faculty, or evaluator
        # accounts are created by this command.

        from django.conf import settings as django_settings
        import os

        admin_emails = getattr(django_settings, "ADMIN_EMAILS", frozenset())

        if not admin_emails:
            self.stdout.write(
                self.style.WARNING(
                    "WARNING: ADMIN_EMAILS is not set in your .env file. "
                    "No admin accounts will be seeded. "
                    "Add ADMIN_EMAILS=admin@example.org to your .env file "
                    "if you want to create admin users."
                )
            )

        seed_pwd = (
            os.environ.get("SEED_DEFAULT_PASSWORD")
            or getattr(
                django_settings,
                "SEED_DEFAULT_PASSWORD",
                None,
            )
        )

        for email in sorted(admin_emails):
            username = email.split("@")[0]

            user = User.objects.filter(email=email).first()

            if not user:
                user = User(
                    email=email,
                    username=username,
                    role="admin",
                    department=None,
                    class_name=None,
                    is_staff=True,
                    is_superuser=True,
                    first_name="System",
                    last_name="Administrator",
                )
                if seed_pwd:
                    user.set_password(seed_pwd)
                else:
                    user.set_unusable_password()
                user.save()

                self.stdout.write(
                    f"Created admin user: {email}"
                )
            else:
                user.role = "admin"
                user.department = None
                user.class_name = None
                user.is_staff = True
                user.is_superuser = True
                user.first_name = "System"
                user.last_name = "Administrator"
                if seed_pwd:
                    user.set_password(seed_pwd)
                user.save()

                self.stdout.write(
                    f"Updated admin user: {email}"
                )

        # 6. Seed Criteria Catalog (Idempotent 12 categories)
        from users.models import CriteriaCategory, CriteriaItem, CriteriaRule

        criteria_catalog_data = [
            {
                "code": "cat-academics",
                "category": "Academics",
                "access_level": "student_rep_only",
                "items": [
                    {
                        "title": "Sem Result (End Semester Examination)",
                        "type": "academic_grades",
                        "marks": 0.0,
                        "rules_json": {
                            "90_above": 5.0,
                            "80_90": 4.0,
                            "70_80": 3.0,
                            "fail": -1.0,
                            "pass_percentage_ranges": [
                                {"min": 90.01, "max": 100.0, "marks": 5.0},
                                {"min": 80.01, "max": 90.0, "marks": 4.0},
                                {"min": 70.01, "max": 80.0, "marks": 3.0},
                                {"min": 60.01, "max": 70.0, "marks": 2.0},
                                {"min": 50.01, "max": 60.0, "marks": 1.0},
                                {"min": 0.0, "max": 50.0, "marks": 0.0}
                            ],
                            "fields": {
                                "count_90_above": True,
                                "count_80_90": True,
                                "count_70_80": True,
                                "count_fail": True,
                                "pass_percentage": True,
                                "proof_url": True,
                                "description": True
                            },
                            "max_per_cycle": 1
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
                "category": "Prizes Won",
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
                                "1st Prize (group)": 5.0,
                                "2nd Prize (group)": 3.0,
                                "3rd Prize (group)": 2.0,
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
                                "1st Prize (group)": 10.0,
                                "2nd Prize (group)": 5.0,
                                "3rd Prize (group)": 3.0,
                                "1st Prize (Group)": 10.0,
                                "2nd Prize (Group)": 5.0,
                                "3rd Prize (Group)": 3.0,
                                "participation(Individual)": 3.0,
                                "participation(group)": 2.0,
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
                "access_level": "student_rep_only",
                "items": [
                    {"title": "Intercollegiate", "type": "count", "marks": 5.0},
                    {"title": "Intra - Collegiate", "type": "count", "marks": 3.0},
                    {"title": "Class Magazine", "type": "count", "marks": 5.0},
                ]
            },
            {
                "code": "cat-leadership",
                "category": "Leaderships",
                "access_level": "student_rep_only",
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
                "access_level": "student_rep_only",
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
                "is_manual_eval": True,
                "items": [
                    {"title": "Library - Regular Footfall (Biometric / Entry)", "type": "count", "marks": 5.0, "access_level": "student_rep_only", "is_manual_eval": True},
                    {"title": "Library - Academic & Career Books Issued/Read", "type": "count", "marks": 5.0, "access_level": "student_rep_only", "is_manual_eval": True},
                    {"title": "Repository Creation (Drive / GitHub / LMS / Website)", "type": "fixed", "marks": 5.0, "access_level": "student_rep_only", "is_manual_eval": True},
                    {"title": "LinkedIn - Profile Completion (Active Profile)", "type": "fixed", "marks": 3.0, "access_level": "all_students", "is_manual_eval": True},
                ]
            }
        ]

        # Clean up deprecated categories and items
        CriteriaCategory.objects.filter(code="cat-documentation").delete()
        CriteriaItem.objects.filter(title__in=[
            "LinkedIn - Skill Badges Earned",
            "LinkedIn - Micro-credentials / Learning Certifications",
            "Class Activity Report & Documents"
        ]).delete()

        for cat_data in criteria_catalog_data:
            cat_obj, _ = CriteriaCategory.objects.update_or_create(
                code=cat_data["code"],
                defaults={
                    "category": cat_data["category"],
                    "access_level": cat_data["access_level"],
                    "is_manual_eval": cat_data.get("is_manual_eval", False)
                }
            )
            for item_data in cat_data["items"]:
                item_obj, _ = CriteriaItem.objects.update_or_create(
                    category=cat_obj,
                    title=item_data["title"],
                    defaults={
                        "type": item_data["type"],
                        "marks": item_data["marks"],
                        "access_level": item_data.get("access_level", cat_data["access_level"]),
                        "is_manual_eval": item_data.get("is_manual_eval", cat_data.get("is_manual_eval", False)),
                        "rules_json": item_data.get("rules_json", None)
                    }
                )
                CriteriaRule.objects.update_or_create(
                    item=item_obj,
                    defaults={
                        "rule_type": item_data["type"],
                        "maximum_marks": item_data["marks"],
                        "min_count": 1 if item_data["type"] == 'count' else None,
                        "is_negative": True if item_data["type"] == 'negative' else False
                    }
                )

        # 7. Seed Official User Groups
        from users.models import UserGroupModel

        UserGroupModel.objects.exclude(
            group_id__in=[
                "grp-evaluation-committee",
                "grp-class-teachers",
                "grp-dqc-student-rep",
                "grp-student-reps",
            ]
        ).delete()

        official_groups = [
            {
                "group_id": "grp-evaluation-committee",
                "name": "Evaluation Committee",
                "description": (
                    "Evaluator members assigned to review activity submissions."
                ),
            },
            {
                "group_id": "grp-class-teachers",
                "name": "Class Teachers Council",
                "description": (
                    "Faculty members acting as class advisors."
                ),
            },
            {
                "group_id": "grp-dqc-student-rep",
                "name": "DQC Student Rep Group",
                "description": (
                    "Data Quality Cell student representatives responsible "
                    "for initial verification of peer submissions."
                ),
            },
            {
                "group_id": "grp-student-reps",
                "name": "Student Representatives",
                "description": (
                    "Class representatives responsible for initial verification "
                    "of peer submissions."
                ),
            },
        ]

        for group_data in official_groups:
            group, created = UserGroupModel.objects.update_or_create(
                group_id=group_data["group_id"],
                defaults={
                    "name": group_data["name"],
                    "description": group_data["description"],
                },
            )

            action = "Created" if created else "Updated"

            self.stdout.write(
                f"{action} official group: {group.name}"
            )

        # 8. Seed System Settings
        from users.models import SystemSetting
        SystemSetting.objects.get_or_create(key='smallest_class_size', defaults={'value': '0'})

        # 9. Heal Criteria Submissions
        from django.core.management import call_command
        call_command('heal_criteria_submissions')

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))

