"""Linkup research: reading what an accessibility statement declares (no network)."""
import unittest
from unittest.mock import patch

from research import is_public, read_statement, site_context

FR = """Le site **Acme** (https://acme.fr) est **partiellement conforme** avec le référentiel général d'amélioration de
l'accessibilité (RGAA), version 4.1.2. L'audit de conformité révèle que **69 % des critères applicables du RGAA (v4.1.2)
sont respectés**. Audit réalisé en auto-évaluation.
### Non-conformités
-   **7.3** – Script non contrôlable au clavier : le menu ne se ferme pas
-   **10.7** – Focus non suffisamment visible sur certains liens
"""
EN = "This website is partially conformant with WCAG 2.1 level AA. 82% of the criteria are met."


class ResearchTests(unittest.TestCase):
    def test_french_rgaa_statement(self):
        got = read_statement(FR)
        self.assertEqual(got["status"], "Partially compliant")
        self.assertEqual(got["rate"], "69%")
        self.assertEqual(got["standard"], "RGAA 4.1.2")
        self.assertTrue(got["self_assessed"])
        self.assertEqual(got["known_issues"][0][:3], "7.3")

    def test_english_wcag_statement(self):
        got = read_statement(EN)
        self.assertEqual(got["status"], "Partially compliant")
        self.assertEqual(got["standard"], "WCAG 2.1 AA")
        self.assertEqual(got["rate"], "82%")

    def test_private_sites_are_not_searched(self):
        for url in ("http://127.0.0.1:8770/", "http://localhost:3000", "https://shop.test/", "http://192.168.1.4/"):
            self.assertFalse(is_public(url), url)
        self.assertTrue(is_public("https://www.mousquetaires.com/"))
        with patch("research._search") as search:
            self.assertIsNone(site_context("http://127.0.0.1:8770/"))
            search.assert_not_called()



class ResilienceTests(unittest.TestCase):
    def test_failed_search_still_reads_the_statement(self):
        with patch("research._search", side_effect=RuntimeError("400")), patch("research.fetch_markdown", return_value=FR):
            got = site_context("https://www.example.fr/")
        self.assertEqual(got["declared_compliance"], "Partially compliant with RGAA 4.1.2 (69% of applicable criteria met)")
        self.assertEqual(got["organisation"], "www.example.fr")


if __name__ == "__main__":
    unittest.main()
