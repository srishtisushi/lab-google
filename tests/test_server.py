import base64
import unittest
import xml.etree.ElementTree as ET

from server import (
    Candidate,
    HostRequestGate,
    apply_institution_filter,
    build_draft_prompt,
    extract_email,
    has_email,
    institution_from_affiliation,
    is_prestigious_institution,
    same_researcher,
    student_signal,
    synopsis_from_text,
    author_name,
    choose_probable_pi,
    openai_output_text,
    parse_draft_json,
    prioritize_email_candidates,
    draft_tone,
    validate_resume_pdf,
)


class SearchHelpersTest(unittest.TestCase):
    def test_synopsis_uses_two_sentences(self):
        result = synopsis_from_text(
            "We map tumor-immune interactions. We test biomarkers in patient samples. A third sentence stays out.",
            "Fallback",
        )
        self.assertEqual(
            result,
            "We map tumor-immune interactions. We test biomarkers in patient samples.",
        )

    def test_extract_email_handles_obfuscation(self):
        self.assertEqual(extract_email("Contact jane [at] lab (dot) edu today."), "jane@lab.edu")

    def test_student_signal_covers_requested_student_groups(self):
        text = "Our lab welcomes undergraduate researchers and medical students."
        self.assertEqual(student_signal(text), "Mentions undergraduates and medical students")

    def test_affiliation_prefers_institution_phrase(self):
        affiliation = "Department of Medicine; Redwood University School of Medicine; Phoenix, AZ."
        self.assertEqual(institution_from_affiliation(affiliation), "Redwood University School of Medicine")

    def test_pubmed_last_named_author_is_probable_pi(self):
        root = ET.fromstring(
            """
            <AuthorList>
              <Author><ForeName>First</ForeName><LastName>Author</LastName></Author>
              <Author><ForeName>Senior</ForeName><LastName>Scientist</LastName></Author>
            </AuthorList>
            """
        )
        author = choose_probable_pi(root.findall("./Author"))
        self.assertEqual(author_name(author), "Senior Scientist")

    def test_prestigious_institution_matches_named_hospital_alias(self):
        self.assertTrue(is_prestigious_institution("BRIGHAM AND WOMEN'S HOSPITAL"))
        self.assertTrue(is_prestigious_institution("Albert Einstein College of Medicine"))
        self.assertTrue(is_prestigious_institution("NYU School of Medicine"))
        self.assertFalse(is_prestigious_institution("University of Oklahoma"))

    def test_institution_filter_and_email_gate(self):
        rows = [
            Candidate("PI A", "Harvard Medical School", "Synopsis", "Project", "url", "NIH", email="pi@hms.edu"),
            Candidate("PI B", "Regional University", "Synopsis", "Project", "url", "NIH", email="pi@regional.edu"),
        ]
        self.assertEqual([row.name for row in apply_institution_filter(rows, True)], ["PI A"])
        self.assertTrue(has_email(rows[0]))
        rows[0].email = "Not found"
        self.assertFalse(has_email(rows[0]))

    def test_candidates_with_existing_emails_are_prioritized(self):
        rows = [
            Candidate("Needs enrichment", "Example", "Synopsis", "Project", "url", "NIH"),
            Candidate("Ready row", "Example", "Synopsis", "Project", "url", "PubMed", email="pi@example.edu"),
        ]
        self.assertEqual([row.name for row in prioritize_email_candidates(rows)], ["Ready row", "Needs enrichment"])

    def test_europe_pmc_author_match_uses_first_and_last_name(self):
        self.assertTrue(same_researcher("Hui Mao", {"firstName": "Hui", "lastName": "Mao"}))
        self.assertFalse(same_researcher("Hui Mao", {"firstName": "Jing", "lastName": "Mao"}))

    def test_host_request_gate_throttles_only_configured_hosts(self):
        now = [10.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        gate = HostRequestGate({"api.reporter.nih.gov": 1.0}, clock=lambda: now[0], sleeper=sleep)
        gate.wait("https://api.reporter.nih.gov/v2/projects/search")
        gate.wait("https://example.edu/profile")
        gate.wait("https://api.reporter.nih.gov/v2/projects/search")
        self.assertEqual(sleeps, [1.0])

    def test_resume_validation_accepts_pdf_bytes(self):
        encoded = base64.b64encode(b"%PDF-1.4 resume").decode("ascii")
        filename, resume = validate_resume_pdf("student-cv.pdf", encoded)
        self.assertEqual(filename, "student-cv.pdf")
        self.assertTrue(resume.startswith(b"%PDF"))

    def test_resume_validation_rejects_non_pdf(self):
        encoded = base64.b64encode(b"plain text").decode("ascii")
        with self.assertRaisesRegex(ValueError, "valid PDF"):
            validate_resume_pdf("resume.pdf", encoded)

    def test_draft_helpers_parse_responses_payload(self):
        text = openai_output_text(
            {"output": [{"content": [{"type": "output_text", "text": '{"subject":"Hello","body":"Body"}'}]}]}
        )
        self.assertEqual(parse_draft_json(text), {"subject": "Hello", "body": "Body"})

    def test_draft_prompt_uses_pi_and_search_context(self):
        prompt = build_draft_prompt(
            {
                "name": "A. PI",
                "institution": "Example School",
                "email": "pi@example.edu",
                "synopsis": "Studies tumor immunity.",
            },
            "brain tumor biomarkers",
        )
        self.assertIn("A. PI", prompt)
        self.assertIn("brain tumor biomarkers", prompt)

    def test_draft_tone_allows_dropdown_values_only(self):
        self.assertEqual(draft_tone("friendly"), "friendly")
        self.assertEqual(draft_tone("dramatic"), "warm")


if __name__ == "__main__":
    unittest.main()
