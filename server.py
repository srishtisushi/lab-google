#!/usr/bin/env python3
"""Lab Google MVP: search research projects and enrich likely PI pages."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
USER_AGENT = "LabGooglePrototype/0.1 (research-discovery; contact: local-user)"
REPORTER_URL = "https://api.reporter.nih.gov/v2/projects/search"
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
MAX_RESUME_BYTES = 10_000_000
HOST_REQUEST_INTERVALS = {
    # NIH RePORTER asks API users to post no more than one URL request per second.
    "api.reporter.nih.gov": 1.05,
    # NCBI's ordinary E-utilities limit is three requests per second without an API key.
    "eutils.ncbi.nlm.nih.gov": 0.36,
    # Keep PI email enrichment gentle too; it can fan out over many NIH rows.
    "www.ebi.ac.uk": 0.36,
}
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
PRESTIGIOUS_INSTITUTION_ALIASES = (
    ("Harvard University / Harvard Medical School", ("harvard",)),
    (
        "Massachusetts General Brigham",
        (
            "mass general brigham",
            "massachusetts general hospital",
            "brigham and women",
            "brigham and women's",
            "brigham & women's",
            "brigham and womens",
            "brigham womens",
        ),
    ),
    ("Stanford University / Stanford Medicine", ("stanford",)),
    ("Johns Hopkins University / Johns Hopkins Medicine", ("johns hopkins",)),
    ("Yale University / Yale School of Medicine", ("yale",)),
    ("MIT", ("massachusetts institute of technology", "mit")),
    ("UCSF", ("university of california san francisco", "uc san francisco", "ucsf")),
    (
        "University of Pennsylvania / Perelman School of Medicine",
        ("university of pennsylvania", "perelman", "penn medicine"),
    ),
    (
        "Columbia University / Vagelos College of Physicians and Surgeons",
        ("columbia university", "vagelos"),
    ),
    ("Weill Cornell Medicine", ("weill cornell",)),
    (
        "NYU Grossman School of Medicine / NYU School of Medicine",
        ("nyu grossman", "nyu school of medicine", "new york university school of medicine"),
    ),
    ("Albert Einstein College of Medicine", ("albert einstein college of medicine",)),
    ("Duke University School of Medicine", ("duke university", "duke school of medicine")),
    (
        "Washington University in St. Louis School of Medicine",
        ("washington university in st. louis", "washington university school of medicine"),
    ),
    ("Vanderbilt University Medical Center", ("vanderbilt",)),
    ("University of Michigan Medical School", ("university of michigan",)),
    ("University of Chicago / Pritzker School of Medicine", ("university of chicago", "pritzker")),
    ("Northwestern University Feinberg School of Medicine", ("northwestern", "feinberg")),
    ("Icahn School of Medicine at Mount Sinai", ("icahn", "mount sinai")),
    ("Mayo Clinic / Mayo Clinic Alix School of Medicine", ("mayo clinic",)),
    ("Cleveland Clinic / Lerner College of Medicine", ("cleveland clinic", "lerner")),
    ("University of Pittsburgh School of Medicine", ("university of pittsburgh", "upmc")),
    ("Baylor College of Medicine", ("baylor college of medicine",)),
    ("UT Southwestern Medical Center", ("ut southwestern", "university of texas southwestern")),
    ("Emory University School of Medicine", ("emory",)),
    ("UCLA / David Geffen School of Medicine", ("university of california los angeles", "ucla", "david geffen")),
    ("UC San Diego School of Medicine", ("university of california san diego", "uc san diego", "ucsd")),
    ("University of Washington School of Medicine", ("university of washington",)),
    ("University of Colorado School of Medicine", ("university of colorado",)),
    ("Case Western Reserve University School of Medicine", ("case western",)),
)


@dataclass
class Candidate:
    name: str
    institution: str
    synopsis: str
    project_title: str
    source_url: str
    source_type: str
    title: str = "Unknown"
    student_researchers: str = "Not found"
    email: str = "Not found"
    lab_page: str = ""
    warnings: List[str] = field(default_factory=list)

    def key(self) -> str:
        return re.sub(r"\W+", "", f"{self.name}|{self.institution}".lower())

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "title": self.title,
            "institution": self.institution,
            "student_researchers": self.student_researchers,
            "email": self.email,
            "synopsis": self.synopsis,
            "project_title": self.project_title,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "lab_page": self.lab_page,
            "warnings": self.warnings,
        }


class HostRequestGate:
    def __init__(
        self,
        intervals: Dict[str, float],
        clock: object = time.monotonic,
        sleeper: object = time.sleep,
    ) -> None:
        self.intervals = intervals
        self.clock = clock
        self.sleeper = sleeper
        self.lock = threading.Lock()
        self.next_allowed: Dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urllib.parse.urlparse(url).hostname or ""
        interval = self.intervals.get(host)
        if not interval:
            return
        while True:
            with self.lock:
                now = self.clock()
                wait_for = self.next_allowed.get(host, now) - now
                if wait_for <= 0:
                    self.next_allowed[host] = now + interval
                    return
            self.sleeper(wait_for)


REQUEST_GATE = HostRequestGate(HOST_REQUEST_INTERVALS)


class ResultLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._capture = False
        self._href = ""
        self._text: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[tuple]) -> None:
        attr_map = dict(attrs)
        classes = attr_map.get("class", "")
        if tag == "a" and "result__a" in classes:
            self._capture = True
            self._href = attr_map.get("href", "")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture:
            href = unwrap_duckduckgo_url(self._href)
            if href:
                self.links.append({"url": href, "title": clean_text(" ".join(self._text))})
            self._capture = False


class PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self.mailtos: List[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: Sequence[tuple]) -> None:
        if tag in {"script", "style", "svg", "noscript"}:
            self._hidden_depth += 1
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.lower().startswith("mailto:"):
                self.mailtos.append(urllib.parse.unquote(href[7:].split("?", 1)[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        return clean_text(" ".join(self.parts))


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def request_text(
    url: str,
    data: Optional[bytes] = None,
    timeout: int = 12,
    max_bytes: int = 400_000,
) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xml;q=0.9,*/*;q=0.8",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        REQUEST_GATE.wait(url)
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes)
                charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_CODES or attempt == 2:
                raise
            time.sleep(retry_delay(exc, attempt))
    raise RuntimeError("Request retry loop exited unexpectedly.")


def retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after and retry_after.isdigit():
        return max(float(retry_after), 0.5)
    return 0.75 * (2 ** attempt)


def request_json(url: str, payload: Dict[str, object]) -> Dict[str, object]:
    encoded = json.dumps(payload).encode("utf-8")
    return json.loads(request_text(url, data=encoded, max_bytes=8_000_000))


def post_json(
    url: str,
    payload: Dict[str, object],
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> Dict[str, object]:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(12_000_000).decode("utf-8", errors="replace"))


def synopsis_from_text(text: str, fallback: str) -> str:
    plain = clean_text(re.sub(r"<[^>]+>", " ", text or ""))
    if not plain:
        return fallback
    sentences = [item.strip() for item in SENTENCE_RE.split(plain) if item.strip()]
    synopsis = " ".join(sentences[:2]) if sentences else plain
    if len(synopsis) > 360:
        synopsis = synopsis[:357].rsplit(" ", 1)[0] + "..."
    return synopsis


def name_from_reporter(pi: Dict[str, object]) -> str:
    full_name = clean_text(str(pi.get("full_name") or ""))
    if full_name:
        return full_name
    pieces = [pi.get("first_name"), pi.get("middle_name"), pi.get("last_name")]
    return clean_text(" ".join(str(piece or "") for piece in pieces)) or "Unknown PI"


def reporter_candidates(query: str, limit: int) -> List[Candidate]:
    payload = {
        "criteria": {
            "advanced_text_search": {
                "operator": "and",
                "search_field": "projecttitle,abstracttext,terms",
                "search_text": query,
            }
        },
        "include_fields": [
            "ProjectTitle",
            "AbstractText",
            "PrincipalInvestigators",
            "Organization",
            "ProjectDetailUrl",
        ],
        "limit": max(limit * 2, 10),
        "offset": 0,
        "sort_field": "project_start_date",
        "sort_order": "desc",
    }
    data = request_json(REPORTER_URL, payload)
    candidates: List[Candidate] = []
    for project in data.get("results", []) or []:
        pis = project.get("principal_investigators") or []
        if not pis:
            continue
        organization = project.get("organization") or {}
        institution = clean_text(str(organization.get("org_name") or "Unknown institution"))
        title = clean_text(str(project.get("project_title") or "NIH research project"))
        synopsis = synopsis_from_text(str(project.get("abstract_text") or ""), title)
        source_url = clean_text(str(project.get("project_detail_url") or ""))
        for pi in pis:
            candidates.append(
                Candidate(
                    name=name_from_reporter(pi),
                    institution=institution,
                    synopsis=synopsis,
                    project_title=title,
                    source_url=source_url,
                    source_type="NIH RePORTER",
                    title=clean_text(str(pi.get("title") or "Unknown")),
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def pubmed_candidates(query: str, limit: int) -> List[Candidate]:
    search_params = urllib.parse.urlencode(
        {"db": "pubmed", "term": query, "retmax": max(limit * 2, 8), "retmode": "json"}
    )
    search = json.loads(request_text(f"{PUBMED_SEARCH_URL}?{search_params}"))
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    fetch_params = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    root = ET.fromstring(request_text(f"{PUBMED_FETCH_URL}?{fetch_params}", max_bytes=8_000_000))
    candidates: List[Candidate] = []
    for article in root.findall(".//PubmedArticle"):
        article_title = clean_text("".join(article.findtext(".//ArticleTitle", default="").splitlines()))
        abstract = " ".join(
            clean_text("".join(node.itertext())) for node in article.findall(".//Abstract/AbstractText")
        )
        authors = article.findall(".//AuthorList/Author")
        author = choose_probable_pi(authors)
        if author is None:
            continue
        name = author_name(author)
        affiliations = [
            clean_text("".join(node.itertext()))
            for node in author.findall("./AffiliationInfo/Affiliation")
            if clean_text("".join(node.itertext()))
        ]
        if not affiliations:
            affiliations = [
                clean_text("".join(node.itertext()))
                for node in article.findall(".//AffiliationInfo/Affiliation")
                if clean_text("".join(node.itertext()))
            ]
        affiliation = affiliations[0] if affiliations else "Unknown institution"
        email = extract_email(" ".join(affiliations))
        pmid = article.findtext(".//PMID", default="")
        candidates.append(
            Candidate(
                name=name,
                institution=institution_from_affiliation(affiliation),
                synopsis=synopsis_from_text(abstract, article_title or "PubMed article"),
                project_title=article_title or "PubMed article",
                source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                source_type="PubMed",
                title="Senior author",
                email=email or "Not found",
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def choose_probable_pi(authors: Sequence[ET.Element]) -> Optional[ET.Element]:
    named = [author for author in authors if author_name(author) != "Unknown researcher"]
    return named[-1] if named else None


def author_name(author: ET.Element) -> str:
    collective = clean_text(author.findtext("./CollectiveName", default=""))
    if collective:
        return collective
    first = clean_text(author.findtext("./ForeName", default=""))
    last = clean_text(author.findtext("./LastName", default=""))
    return clean_text(f"{first} {last}") or "Unknown researcher"


def institution_from_affiliation(affiliation: str) -> str:
    pieces = [clean_text(piece) for piece in re.split(r"[.;]", affiliation) if clean_text(piece)]
    for piece in pieces:
        if re.search(r"\b(university|institute|hospital|school|college|center|centre|clinic)\b", piece, re.I):
            return piece
    return pieces[0] if pieces else "Unknown institution"


def unwrap_duckduckgo_url(url: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(url))
    params = urllib.parse.parse_qs(parsed.query)
    if "uddg" in params:
        return params["uddg"][0]
    if parsed.scheme in {"http", "https"}:
        return url
    return ""


def search_web(query: str, limit: int = 4) -> List[Dict[str, str]]:
    params = urllib.parse.urlencode({"q": query})
    parser = ResultLinkParser()
    parser.feed(request_text(f"{DUCKDUCKGO_URL}?{params}"))
    return parser.links[:limit]


def europe_pmc_author_email(candidate: Candidate) -> str:
    if candidate.source_type != "NIH RePORTER":
        return candidate.email if has_email(candidate) else ""
    query = f'AUTH:"{candidate.name}" AND AFF:"{candidate.institution}"'
    params = urllib.parse.urlencode(
        {"query": query, "format": "json", "resultType": "core", "pageSize": 8}
    )
    data = json.loads(request_text(f"{EUROPE_PMC_SEARCH_URL}?{params}", max_bytes=2_000_000))
    for result in data.get("resultList", {}).get("result", []) or []:
        for author in result.get("authorList", {}).get("author", []) or []:
            if not same_researcher(candidate.name, author):
                continue
            for affiliation in author_affiliations(author):
                email_value = extract_email(affiliation)
                if email_value:
                    return email_value
    return ""


def author_affiliations(author: Dict[str, object]) -> List[str]:
    details = author.get("authorAffiliationDetailsList") or {}
    return [
        clean_text(str(item.get("affiliation") or ""))
        for item in details.get("authorAffiliation", []) or []
        if clean_text(str(item.get("affiliation") or ""))
    ]


def name_tokens(value: str) -> List[str]:
    return [token for token in re.findall(r"[A-Za-z]+", value.lower()) if len(token) > 1]


def same_researcher(candidate_name: str, author: Dict[str, object]) -> bool:
    candidate_tokens = name_tokens(candidate_name)
    author_last = name_tokens(str(author.get("lastName") or ""))
    author_first = name_tokens(str(author.get("firstName") or author.get("fullName") or ""))
    if not candidate_tokens or not author_last or not author_first:
        return False
    return candidate_tokens[-1] == author_last[-1] and candidate_tokens[0] == author_first[0]


def looks_like_lab_page(url: str, title: str) -> bool:
    haystack = f"{url} {title}".lower()
    blocked = ("linkedin.com", "researchgate.net", "wikipedia.org", "nih.gov", "pubmed.ncbi.nlm.nih.gov")
    if any(domain in haystack for domain in blocked):
        return False
    preferred = ("lab", "faculty", "profile", "medicine", "research", ".edu", "hospital", "bio")
    return any(token in haystack for token in preferred)


def extract_email(value: str) -> str:
    normalized = html.unescape(value or "")
    normalized = re.sub(r"\s*(?:\[at\]|\(at\)|\sat\s)\s*", "@", normalized, flags=re.I)
    normalized = re.sub(r"\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*", ".", normalized, flags=re.I)
    for email_value in EMAIL_RE.findall(normalized):
        lowered = email_value.lower()
        if not lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            return email_value.strip(".,;:")
    return ""


def student_signal(text: str) -> str:
    lower = text.lower()
    undergrad = bool(re.search(r"\bundergraduate(s)?\b|\bundergrad(s)?\b", lower))
    med_student = bool(re.search(r"\bmedical student(s)?\b|\bmed student(s)?\b", lower))
    if undergrad and med_student:
        return "Mentions undergraduates and medical students"
    if undergrad:
        return "Mentions undergraduates"
    if med_student:
        return "Mentions medical students"
    return "Not found"


def page_text(url: str) -> tuple:
    markup = request_text(url)
    parser = PageTextParser()
    parser.feed(markup)
    return markup, parser.text(), parser.mailtos


def enrich_candidate(candidate: Candidate, include_lab_pages: bool = True) -> Candidate:
    try:
        candidate.email = europe_pmc_author_email(candidate) or candidate.email
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, ValueError):
        candidate.warnings.append("Europe PMC email enrichment unavailable.")
    if not include_lab_pages:
        return candidate
    query = f"{candidate.name} {candidate.institution} lab research email"
    try:
        links = search_web(query)
    except (urllib.error.URLError, socket.timeout, ValueError) as exc:
        candidate.warnings.append(f"Web search enrichment unavailable: {exc.__class__.__name__}")
        return candidate
    for link in links:
        if not looks_like_lab_page(link["url"], link["title"]):
            continue
        candidate.lab_page = link["url"]
        try:
            markup, text, mailtos = page_text(link["url"])
        except (urllib.error.URLError, socket.timeout, UnicodeError, ValueError):
            continue
        candidate.student_researchers = student_signal(text)
        candidate.email = extract_email(" ".join(mailtos)) or extract_email(markup) or candidate.email
        return candidate
    return candidate


def unique_candidates(candidates: Iterable[Candidate], limit: int) -> List[Candidate]:
    rows: List[Candidate] = []
    seen = set()
    for candidate in candidates:
        key = candidate.key()
        if key in seen:
            continue
        seen.add(key)
        rows.append(candidate)
        if len(rows) >= limit:
            break
    return rows


def normalized_institution(institution: str) -> str:
    return f" {re.sub(r'[^a-z0-9]+', ' ', institution.lower()).strip()} "


def is_prestigious_institution(institution: str) -> bool:
    normalized = normalized_institution(institution)
    return any(
        normalized_institution(alias) in normalized
        for _, aliases in PRESTIGIOUS_INSTITUTION_ALIASES
        for alias in aliases
    )


def has_email(candidate: Candidate) -> bool:
    return bool(extract_email(candidate.email)) and candidate.email != "Not found"


def apply_institution_filter(candidates: Iterable[Candidate], prestigious: bool) -> List[Candidate]:
    if not prestigious:
        return list(candidates)
    return [candidate for candidate in candidates if is_prestigious_institution(candidate.institution)]


def prioritize_email_candidates(candidates: Iterable[Candidate]) -> List[Candidate]:
    rows = list(candidates)
    return [row for row in rows if has_email(row)] + [row for row in rows if not has_email(row)]


def enrich_rows(rows: Sequence[Candidate], include_lab_pages: bool) -> List[Candidate]:
    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=min(2, len(rows))) as executor:
        return list(executor.map(lambda row: enrich_candidate(row, include_lab_pages), rows))


def search_labs(
    query: str,
    limit: int = 8,
    enrich: bool = False,
    prestigious: bool = False,
) -> Dict[str, object]:
    query = clean_text(query)
    if len(query) < 3:
        raise ValueError("Describe a condition or research interest with at least three characters.")
    limit = max(1, min(int(limit), 15))
    warnings: List[str] = []
    source_limit = min(max(limit * 8, 40), 80)
    candidates: List[Candidate] = []
    try:
        candidates.extend(reporter_candidates(query, source_limit))
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, ValueError):
        warnings.append("NIH RePORTER search was unavailable for this request.")
    try:
        candidates.extend(pubmed_candidates(query, min(source_limit, max(limit * 4, 20))))
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, ET.ParseError, ValueError):
        warnings.append("PubMed search was unavailable for this request.")
    rows = unique_candidates(apply_institution_filter(candidates, prestigious), source_limit * 2)
    rows = prioritize_email_candidates(rows)
    result_rows = enrich_rows([row for row in rows if has_email(row)][:limit], enrich)
    for start in range(0, len(rows), max(limit, 6)):
        if len(result_rows) >= limit:
            break
        batch = [row for row in rows[start : start + max(limit, 6)] if not has_email(row)]
        result_rows.extend(row for row in enrich_rows(batch, enrich) if has_email(row))
    rows = result_rows[:limit]
    return {
        "query": query,
        "count": len(rows),
        "warnings": warnings,
        "results": [row.to_dict() for row in rows],
    }


def validate_resume_pdf(filename: str, file_data: str) -> tuple[str, bytes]:
    filename = clean_text(filename) or "resume.pdf"
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Upload a PDF resume or CV.")
    if file_data.startswith("data:"):
        file_data = file_data.split(",", 1)[-1]
    try:
        resume_bytes = base64.b64decode(file_data, validate=True)
    except (ValueError, TypeError):
        raise ValueError("The uploaded resume PDF could not be read.") from None
    if not resume_bytes.startswith(b"%PDF"):
        raise ValueError("Upload a valid PDF resume or CV.")
    if len(resume_bytes) > MAX_RESUME_BYTES:
        raise ValueError("Keep the resume PDF under 10 MB.")
    return filename, resume_bytes


def draft_tone(value: str) -> str:
    value = clean_text(value).lower()
    return value if value in {"warm", "friendly", "professional"} else "warm"


def build_draft_prompt(researcher: Dict[str, object], interest: str, tone: str = "warm") -> str:
    name = clean_text(str(researcher.get("name") or "the researcher"))
    title = clean_text(str(researcher.get("title") or ""))
    institution = clean_text(str(researcher.get("institution") or ""))
    email = extract_email(str(researcher.get("email") or ""))
    synopsis = clean_text(str(researcher.get("synopsis") or ""))
    project_title = clean_text(str(researcher.get("project_title") or ""))
    interest = clean_text(interest)
    tone = draft_tone(tone)
    return f"""
Draft a concise cold outreach email from a student to a research PI.

Use the attached resume/CV as evidence about the student's background. Tailor the email to this researcher without inventing credentials, papers read, clinical experiences, or prior relationships. Keep the body around 140 to 220 words. Use a {tone} tone that remains appropriate for research outreach and include a clear ask about research opportunities. Do not use bracket placeholders except for the student's name if the resume does not make it clear.

Researcher:
- Name: {name}
- Title: {title or "Unknown"}
- Institution: {institution or "Unknown"}
- Email: {email or "Unknown"}
- Research synopsis: {synopsis or "Unknown"}
- Relevant source/project: {project_title or "Unknown"}
- Student search interest: {interest or "Not provided"}

Return only JSON with this exact shape:
{{"subject":"...", "body":"..."}}
""".strip()


def openai_output_text(response: Dict[str, object]) -> str:
    top_level = clean_text(str(response.get("output_text") or ""))
    if top_level:
        return top_level
    chunks: List[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"}:
                chunks.append(str(content.get("text") or ""))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def parse_draft_json(text: str) -> Dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("The draft response was not readable. Try drafting again.") from exc
    subject = clean_text(str(payload.get("subject") or ""))
    body = str(payload.get("body") or "").strip()
    if not subject or not body:
        raise ValueError("The draft response was incomplete. Try drafting again.")
    return {"subject": subject, "body": body}


def draft_outreach_email(payload: Dict[str, object]) -> Dict[str, str]:
    api_key = clean_text(str(payload.get("api_key") or os.environ.get("OPENAI_API_KEY", "")))
    if not api_key:
        raise ValueError("Enter an OpenAI API key before drafting.")
    resume = payload.get("resume") or {}
    if not isinstance(resume, dict):
        raise ValueError("Upload a PDF resume or CV before drafting.")
    filename, resume_bytes = validate_resume_pdf(
        str(resume.get("filename") or ""),
        str(resume.get("file_data") or ""),
    )
    researcher = payload.get("researcher") or {}
    if not isinstance(researcher, dict) or not extract_email(str(researcher.get("email") or "")):
        raise ValueError("Choose a researcher row with an email.")
    response = post_json(
        OPENAI_RESPONSES_URL,
        {
            "model": os.environ.get("OPENAI_DRAFT_MODEL", "gpt-4o-mini"),
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": base64.b64encode(resume_bytes).decode("ascii"),
                        },
                        {
                            "type": "input_text",
                            "text": build_draft_prompt(
                                researcher,
                                str(payload.get("interest") or ""),
                                str(payload.get("tone") or ""),
                            ),
                        },
                    ],
                }
            ],
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return parse_draft_json(openai_output_text(response))


class LabGoogleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_POST(self) -> None:
        if self.path not in {"/api/search", "/api/draft-email"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if self.path == "/api/draft-email":
                response = draft_outreach_email(payload)
            else:
                response = search_labs(
                    str(payload.get("query") or ""),
                    int(payload.get("limit") or 8),
                    bool(payload.get("enrich", False)),
                    bool(payload.get("prestigious", False)),
                )
            self.send_json(response)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:  # Keep the prototype UI readable if upstream pages misbehave.
            action = "Drafting" if self.path == "/api/draft-email" else "Search"
            self.send_json({"error": f"{action} failed: {exc.__class__.__name__}"}, HTTPStatus.BAD_GATEWAY)

    def send_json(self, body: Dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Lab Google prototype.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), LabGoogleHandler)
    print(f"Lab Google running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
