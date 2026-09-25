import os
import unittest
from unittest.mock import patch


class CfbdClientTests(unittest.TestCase):
    def test_configuration_uses_bearer_token_from_environment(self):
        from cfb.cfbd_client import API_KEY_ENV_VAR, create_configuration

        self.assertEqual(API_KEY_ENV_VAR, "CFBD_API")
        with patch.dict(os.environ, {"CFBD_API": "test-token"}, clear=True):
            configuration = create_configuration()

        self.assertEqual(
            configuration.auth_settings()["apiKey"]["value"],
            "Bearer test-token",
        )

    def test_missing_api_key_has_actionable_error(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CFBD_API"):
                create_configuration()

    def test_blank_api_key_is_rejected(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(os.environ, {"CFBD_API": "  \t"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CFBD_API"):
                create_configuration()

    def test_old_api_key_name_alone_is_rejected(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(os.environ, {"CFBD_API_KEY": "old-name-only"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CFBD_API"):
                create_configuration()

    def test_new_api_key_wins_when_both_names_are_present(self):
        from cfb.cfbd_client import create_configuration

        with patch.dict(
            os.environ,
            {"CFBD_API_KEY": "old-name", "CFBD_API": "new-name"},
            clear=True,
        ):
            configuration = create_configuration()

        self.assertEqual(
            configuration.auth_settings()["apiKey"]["value"],
            "Bearer new-name",
        )

    def test_redaction_uses_normalized_new_api_key(self):
        import cfb.main as legacy_main
        import cfb.recovery as recovery

        with patch.dict(
            os.environ,
            {"CFBD_API": "  padded-test-token \t"},
            clear=True,
        ):
            for redact in (legacy_main._safe_message, recovery._safe_message):
                self.assertEqual(
                    redact(RuntimeError("provider failed padded-test-token")),
                    "provider failed [redacted]",
                )

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
