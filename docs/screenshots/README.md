# Screenshots

These images illustrate the interface in [`docs/usage.md`](../usage.md).

The PNGs currently committed are **placeholders**. Generate real screenshots
(seeded demo study, captured via a headless browser) by running, from the repo
root in an environment with a browser:

```bash
uv run python docs/capture_screenshots.py
```

It writes `01-…png` … `05-…png` here, overwriting the placeholders. The
capture uses a throwaway temporary database and never touches your real data —
see the header of [`docs/capture_screenshots.py`](../capture_screenshots.py).
