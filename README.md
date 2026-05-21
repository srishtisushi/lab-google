# Lab Google

`Lab Google` is a local prototype for turning a condition or short research-interest description into a PI table.

The first pass uses:

- NIH RePORTER project search for funded project titles, PI names, organizations, and abstracts.
- PubMed paper records as an additional source of recent author affiliations and email-bearing rows.
- Europe PMC author-affiliation enrichment for NIH PI email discovery.
- A one-time PDF resume/CV upload in the browser page session for LLM-tailored outreach email drafts.

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
6. Optional: add an environment variable in Render if you want the server to use your own key instead of asking each drafter for one:

   ```text
   OPENAI_API_KEY=your_api_key
   ```

   Without `OPENAI_API_KEY`, the draft dialog asks for an OpenAI API key for each draft request. The optional `OPENAI_DRAFT_MODEL` environment variable defaults to `gpt-4o-mini`.

## Test

```bash
python3 -m unittest discover -s tests
```

## Notes

- Rows are only returned when an email is found. NIH RePORTER exposes PI titles in its search/project metadata, but its project-page email reveal is protected behind its `View Email` reCAPTCHA flow, so this prototype uses Europe PMC/PubMed author affiliations for email discovery.
- Shared upstream request gates keep NIH RePORTER below one request per second and keep PubMed/Europe PMC enrichment at or below three requests per second while still allowing the UI to request up to 15 final rows.
- A row can be sourced from a funded project or a paper record. The table links the source record.
- The title and student-researcher columns report `Unknown` or `Not found` when a source page does not expose enough evidence.
- Email drafting accepts a PDF resume/CV under 10 MB. The browser keeps that upload ready while the page stays open, so a changed search does not require another upload; the app does not save uploaded resumes or user-entered API keys to disk.
