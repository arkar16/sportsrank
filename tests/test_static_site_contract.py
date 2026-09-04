from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class StaticSiteContractTests(unittest.TestCase):
    def test_rankings_use_the_static_html_generator(self):
        calc_source = (REPO_ROOT / "cfb" / "calc.py").read_text()

        self.assertIn("from cors import weekly_cors", calc_source)
        self.assertNotIn("rankings_pipeline", calc_source)

    def test_client_rendering_stack_is_absent(self):
        client_files = (
            "cfb/rankings_pipeline.py",
            "cfb/site_paths.py",
            "frontend/rankings-app.ts",
            "frontend/rankings-app.css",
            "vite.config.ts",
            "tsconfig.json",
            "website/assets/client/rankings-app.js",
            "website/assets/client/rankings-app.css",
        )

        for relative_path in client_files:
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())

if __name__ == "__main__":
    unittest.main()
