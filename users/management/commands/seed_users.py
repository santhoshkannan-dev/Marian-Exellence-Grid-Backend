from django.core.management.base import BaseCommand
from users.models import Department, Course, Class, User, AcademicYear
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

        # Purge outdated legacy departments, courses, classes, and non-existing users
        User.objects.filter(email='iqac@mariancollege.org').delete()
        User.objects.all().update(class_name=None, department=None)
        Class.objects.all().delete()
        Course.objects.all().delete()
        Department.objects.all().delete()

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
            obj = Department.objects.create(
                name=dept["name"],
                code=dept["code"],
                level=dept["level"],
                email_prefix=dept["email_prefix"]
            )
            departments[dept["code"]] = obj
            self.stdout.write(f"Created Department: {obj.name} ({obj.code})")

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
            obj = Course.objects.create(
                department=dept,
                name=c["name"],
                abbreviation=c["abbreviation"],
                email_code=c["email_code"],
                is_multi_batch=c["is_multi_batch"],
                duration_years=c["duration_years"],
            )
            courses[c["email_code"]] = obj
            self.stdout.write(f"Created Course: {obj.abbreviation} ({obj.email_code})")

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
            obj = Class.objects.create(
                name=cls["name"],
                department=dept,
                course=course,
                year_number=cls["year_number"],
                section=cls["section"],
            )
            classes[cls["name"]] = obj
            self.stdout.write(f"Created Class: {obj.name}")

        # 5. Seed Users (without IQAC)
        users_data = [
            ("santhosh.25pmc152@mariancollege.org", "student", "PGDCA", "II MCA", False, False, "Santhosh", "Kannan"),
            ("amal.25pmc114@mariancollege.org", "student", "PGDCA", "II MCA", False, False, "Amal", "Thomas"),
            ("santhosh.25ubc154@mariancollege.org", "student", "UGDCA", "II BCA A", False, False, "Santhosh", "Kannan"),
            ("kochumol.abraham@mariancollege.org", "faculty", "PGDCA", None, True, False, "Kochumol", "Abraham"),
            ("allen.george@mariancollege.org", "evaluation", "PGDCA", None, True, False, "Allen", "George"),
            ("admin@mariancollege.org", "admin", None, None, True, True, "System", "Administrator"),
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

        # 7. Seed Official User Groups (strictly 4 official groups)
        from users.models import UserGroupModel, UserGroupMember, EvaluatorCategoryAssignment
        UserGroupModel.objects.exclude(group_id__in=[
            'grp-evaluation-committee',
            'grp-class-teachers',
            'grp-dqc-student-rep',
            'grp-student-reps'
        ]).delete()

        official_groups = [
            {
                "group_id": "grp-evaluation-committee",
                "name": "Evaluation Committee",
                "description": "Evaluator members assigned to review activity submissions.",
            },
            {
                "group_id": "grp-class-teachers",
                "name": "Class Teachers Council",
                "description": "Faculty members acting as class advisors.",
            },
            {
                "group_id": "grp-dqc-student-rep",
                "name": "DQC Student Rep Group",
                "description": "Data Quality Cell student representatives responsible for initial verification of peer submissions, and access to all categories for submissions of class they belong to.",
            },
            {
                "group_id": "grp-student-reps",
                "name": "Student Representatives",
                "description": "Class representatives responsible for initial verification of peer submissions of class they belong to only.",
            },
        ]

        created_groups = {}
        for gdata in official_groups:
            grp, _ = UserGroupModel.objects.update_or_create(
                group_id=gdata["group_id"],
                defaults={
                    "name": gdata["name"],
                    "description": gdata["description"]
                }
            )
            created_groups[gdata["group_id"]] = grp
            self.stdout.write(f"Seeded User Group: {grp.name} ({grp.group_id})")

        # Seed group members with peer details
        mca_class = Class.objects.filter(name="II MCA").first()
        pgdca_dept = Department.objects.filter(code="PGDCA").first()

        # 1. Allen George -> Evaluation Committee
        eval_member, _ = UserGroupMember.objects.update_or_create(
            group=created_groups["grp-evaluation-committee"],
            email="allen.george@mariancollege.org",
            defaults={
                "name": "Allen George",
                "user": seeded_users.get("allen.george@mariancollege.org"),
                "department": pgdca_dept,
            }
        )
        # Assign all categories to Allen George
        for cat in CriteriaCategory.objects.all():
            EvaluatorCategoryAssignment.objects.get_or_create(member=eval_member, category=cat)
            if "allen.george@mariancollege.org" not in cat.evaluators:
                cat.evaluators.append("allen.george@mariancollege.org")
                cat.save(update_fields=["evaluators"])

        # 2. Kochumol Abraham -> Class Teachers Council
        UserGroupMember.objects.update_or_create(
            group=created_groups["grp-class-teachers"],
            email="kochumol.abraham@mariancollege.org",
            defaults={
                "name": "Kochumol Abraham",
                "user": seeded_users.get("kochumol.abraham@mariancollege.org"),
                "department": pgdca_dept,
                "assigned_class": mca_class,
            }
        )

        # 3. Santhosh Kannan -> DQC Student Rep Group & Student Representatives
        UserGroupMember.objects.update_or_create(
            group=created_groups["grp-dqc-student-rep"],
            email="santhosh.25pmc152@mariancollege.org",
            defaults={
                "name": "Santhosh Kannan",
                "user": seeded_users.get("santhosh.25pmc152@mariancollege.org"),
                "department": pgdca_dept,
                "assigned_class": mca_class,
                "badge": "DQC member",
            }
        )
        UserGroupMember.objects.update_or_create(
            group=created_groups["grp-student-reps"],
            email="santhosh.25pmc152@mariancollege.org",
            defaults={
                "name": "Santhosh Kannan",
                "user": seeded_users.get("santhosh.25pmc152@mariancollege.org"),
                "department": pgdca_dept,
                "assigned_class": mca_class,
                "badge": "Student Rep",
            }
        )

        for grp in created_groups.values():
            grp.sync_json_members()

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))

