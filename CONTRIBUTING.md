# Contributing

## Workflow

1. Create a feature branch from the current working branch.
2. Set up the Python virtual environment and install frontend dev dependencies.
3. Make focused changes with tests alongside the affected area.
4. Run the Python unit suite and browser smoke suite before opening a pull request.

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
```

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
npm run smoke
```

## Pull Requests

- Keep refactors non-destructive unless a behavior change is explicitly intended
- Document any new environment variables in `.env.example`
- Update folder README files when introducing new directories
- Include screenshots when the frontend behavior changes materially
