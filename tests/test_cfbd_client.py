import os
import unittest
from unittest.mock import patch


class CfbdClientTests(unittest.TestCase):
    def test_configuration_uses_bearer_token_from_environment(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(os.environ, {"CFBD_API_KEY": "test-token"}, clear=True):
            configuration = create_configuration()

        self.assertEqual(
            configuration.auth_settings()["apiKey"]["value"],
            "Bearer test-token",
        )

    def test_missing_api_key_has_actionable_error(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CFBD_API_KEY"):
                create_configuration()

    def test_division_and_response_classifications_are_normalized(self):
        import cfbd

        from cfb.cfbd_client import classification_value, division_classification

        self.assertEqual(
            division_classification("FBS"),
            cfbd.DivisionClassification.FBS,
        )
        self.assertEqual(
            classification_value(cfbd.DivisionClassification.FCS),
            "fcs",
        )


if __name__ == "__main__":
    unittest.main()
