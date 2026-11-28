# Green Life — Python Stack (FastAPI + Flet + Firebase)

Uses the exact same Firebase project (`green-life-1436d`), Firestore Enterprise database (`greenlife-db`), and Email/Password Authentication as `web/`.

## Configuration
`python_app/.env` is already populated from `web/.env.local`. Add your `GEMINI_API_KEY` there when using `/api/predict`.

## Run Backend (FastAPI)
```powershell
cd python_app
.\.venv\Scripts\uvicorn.exe backend:app --reload --port 8000
```

## Run Frontend (Flet Web Site)
In a second terminal:
```powershell
cd python_app
.\.venv\Scripts\python.exe frontend.py
```
Opens the Flet web UI at `http://localhost:8550` (set `FLET_VIEW=desktop` for native desktop mode).

## Run Tests
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
