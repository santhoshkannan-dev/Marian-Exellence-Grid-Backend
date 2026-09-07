# Excellence Grid — Backend API

Django REST Framework backend for the **Marian Best Class Evaluation System** (Marian College Kuttikkanam Autonomous).

---

## 🛠️ Tech Stack
- **Framework**: Django 6.x & Django REST Framework
- **Authentication**: JWT (`rest_framework_simplejwt`), Google OAuth2 Token Verification
- **Database**: PostgreSQL (Production/Staging) / SQLite (Dev)
- **CORS**: `django-cors-headers`

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+ or Python 3.12+
- PostgreSQL (or SQLite for development)

### 2. Setup Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Copy `.env.example` to `.env` and configure your database and Google OAuth credentials:
```bash
copy .env.example .env
```

### 5. Run Migrations & Start Server
```bash
python manage.py migrate
python manage.py runserver 8000
```
API runs on `http://127.0.0.1:8000/api/` and Admin Portal runs on `http://127.0.0.1:8000/admin/`.
