# Lab Google

`Lab Google` is a local prototype for turning a condition or short research-interest description into a PI table.

The first pass uses:

- NIH RePORTER project search for funded project titles, PI names, organizations, and abstracts.
- PubMed paper records as an additional source of recent author affiliations and email-bearing rows.
- Europe PMC author-affiliation enrichment for NIH PI email discovery.
- Best-effort lab-page enrichment to look for additional email addresses and text that mentions undergraduate or medical student researchers.

## Run

```bash
python3 server.py
```

Open `http://127.0.0.1:8765`.

## Deploy On Render

This repo includes a Render-ready Flask entrypoint in `app.py`.

1. Push the project to GitHub.
2. In Render, create a Python `Web Service` from that repo.
3. Use build command:

   ```bash
   pip install -r requirements.txt
   ```

4. Use start command:

   ```bash
   gunicorn app:app --timeout 180
   ```

5. Choose the `Free` instance type for hobby/testing use.

## Test

```bash
python3 -m unittest discover -s tests
```

## Notes

- Rows are only returned when an email is found. NIH RePORTER exposes PI titles in its search/project metadata, but its project-page email reveal is protected behind its `View Email` reCAPTCHA flow, so this prototype uses Europe PMC/PubMed author affiliations and public institutional pages for email discovery.
- Shared upstream request gates keep NIH RePORTER below one request per second and keep PubMed/Europe PMC enrichment at or below three requests per second while still allowing the UI to request up to 15 final rows.
- Lab-page enrichment is intentionally inspectable and imperfect. Search results and institutional pages change, some pages block automated requests, and not every page publishes a PI email or trainee language.
- A row can be sourced from a funded project or a paper record. The table links the source record and links the lab page when enrichment finds one.
- The title and student-researcher columns report `Unknown` or `Not found` when a source page does not expose enough evidence.
