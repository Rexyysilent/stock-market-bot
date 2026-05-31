# Publishing Checklist

Use this before making the repository public.

## Must Do

- Confirm `.env` is not committed.
- Use `.env.example` for all shareable configuration.
- Review generated dumps and backups; they should stay ignored.
- Choose and add a license before calling the project open source.
- Run:

```powershell
python -m compileall export_for_gemini.py openinsider_agent.py agents
python test_earnings_calendar.py
python test_pdufa.py
```

## Review Before Public Release

- Keep local reference dumps such as `Gemini Ref.txt` out of git unless they are sanitized examples.
- Keep local `Modelfile.*` experiments out of git unless they are part of the public project story.
- Confirm no personal account names, tokens, broker data, or private notes appear in tracked files.
- Add screenshots only if they do not reveal private data.

## Nice To Have

- Add a sanitized sample export under `examples/`.
- Add CI for compile checks and the non-network regression tests.
- Split local-only dashboard/demo assets from core pipeline code.
- Add a license badge once the license is selected.
