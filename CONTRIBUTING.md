# Contributing

This project is a local-first research pipeline. Contributions should preserve that bias:

- Prefer readable source adapters over clever abstractions.
- Keep generated exports, secrets, and local databases out of git.
- Add health metadata whenever a source can fail, rate-limit, or silently degrade.
- Keep network-dependent tests as smoke scripts; keep deterministic parser/date tests separate when possible.

Before opening a PR:

```powershell
python -m compileall export_for_gemini.py openinsider_agent.py agents
python test_earnings_calendar.py
```

For data-source changes, include a short note explaining how the source fails and how the exporter surfaces that failure in `health`.
