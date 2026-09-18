# Dashboard Start

Windows:

```powershell
cd "C:\Users\X1\Documents\Terminal 2026"
.\start_dashboard.bat
```

If you want to run it directly in PowerShell:

```powershell
.\start_dashboard.ps1
```

After startup, open:

```text
http://127.0.0.1:8000
```

What this launcher does:

- finds a usable Python 3.11+ runtime;
- checks whether the project dependencies are importable;
- starts `macro_replay/streamlit_app.py` directly with Streamlit;
- does not refresh data or run legacy migration logic.

If it says dependencies are missing, run:

```powershell
<python> -m pip install .
```

On this machine, the bundled runtime path is:

```text
C:\Users\X1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```
