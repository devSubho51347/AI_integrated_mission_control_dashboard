# KomputerMechanic OS Dashboard

Single-file KomputerMechanic OS dashboard with a lightweight Python server and SQLite-backed bootstrap data.

## Run

```powershell
python server.py --host 127.0.0.1 --port 3000
```

Then open:

```text
http://127.0.0.1:3000/
```

## Files

- `index.html` - dashboard UI and embedded preview fallback data
- `server.py` - Python HTTP server and SQLite bootstrap API
- `data/dashboard.db` - generated locally by the server at runtime
