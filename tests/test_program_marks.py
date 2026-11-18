import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.response import addinfourl
import zlib

from cfb.program_marks import program_logo_paths
from cfb.public_safety import assert_public_bytes
from cfb.request_meter import RequestMeter
from scripts.fetch_program_marks import main


CDN = "https://a.espncdn.com/i/teamlogos/ncaa/500/"
ASSET_DIR = Path("cfb/assets/program-marks")


def sample_png(width=1, height=1):
    def chunk(name, value):
        data = name + value
        return struct.pack(">I", len(value)) + data + struct.pack(">I", zlib.crc32(data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x01\x02\x03" * width) * height))
        + chunk(b"IEND", b"")
    )


class ProgramMarksTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "private-catalog"
        self.site = self.root / "site"
        self.meter = RequestMeter(self.root / "cfbd_requests.sqlite3")
        self.meter.execute(endpoint="teams", season=2026, purpose="scheduled",
                           cache_decision="miss", transport=lambda: None)
        self.cli = ["--site", str(self.site), "--cache", str(self.cache)]

    def run_cli(self, *options):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = main([*self.cli, *options])
        return status, output.getvalue()

    def test_sdk_catalog_is_metered_once_and_persisted_before_resumable_images(self):
        records = [
            {"id": 101, "school": "Alpha", "conference": "Private raw field",
             "alternateNames": ["Former Alpha", "Shared", "Beta", "https://private.invalid/"],
             "logos": [CDN.replace("https:", "http:") + "101.png", CDN + "unchosen.png"]},
            {"id": 102, "school": "Beta", "alternateNames": ["TEAM-B"], "logos": [CDN + "102.png"]},
            {"id": 103, "school": "Gamma", "alternateNames": ["Shared"], "logos": None},
            {"id": 104, "school": "Delta", "logos": [CDN + "101.png"]},
        ]
        raw = json.dumps(records).encode()
        image_calls = []

        def image_request(request, timeout):
            self.assertEqual((self.cache / "response.json").read_bytes(), raw)
            self.assertEqual(dict(request.header_items()), {"Accept": "image/png"})
            self.assertEqual(timeout, 15)
            image_calls.append(request.full_url)
            if image_calls.count(CDN + "102.png") == 1 and request.full_url.endswith("/102.png"):
                raise TimeoutError("interrupted")
            return self.image_response()

        def sdk_request(rest_client, method, url, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(url, "https://api.collegefootballdata.com/teams")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-only-token")
            self.assertFalse(kwargs["_preload_content"])
            self.assertEqual(self.meter.audit_records()[-1]["outcome"], "started")
            return self.sdk_response(raw)

        acquire = ("--acquire-catalog", "--meter", str(self.meter.path))
        with patch.dict("os.environ", {"CFBD_API": "test-only-token"}), patch(
            "cfbd.rest.RESTClientObject.request", autospec=True, side_effect=sdk_request,
        ) as sdk, patch("cfb.program_marks.build_opener") as opener:
            opener.return_value.open.side_effect = image_request
            status, output = self.run_cli(*acquire)
            self.assertEqual(status, 1, output)
            first = program_logo_paths(self.site)
            self.assertEqual(set(first), {"Alpha", "Former Alpha", "Delta"}, output)
            status, output = self.run_cli(*acquire)
            self.assertEqual(status, 0, output)
            self.assertEqual(self.run_cli()[0], 0)
            sdk.assert_called_once()

        self.assertEqual(image_calls, [CDN + "101.png", CDN + "102.png", CDN + "102.png"])
        self.assertEqual(len(self.meter.audit_records()), 2)
        mark_request = self.meter.audit_records()[-1]
        self.assertEqual((mark_request["endpoint"], mark_request["season"], mark_request["purpose"],
                          mark_request["cache_decision"], mark_request["outcome"], mark_request["budget_impact"]),
                         ("teams-catalog", 0, "historical", "miss", "succeeded", 1))
        manifest_path = self.site / ASSET_DIR / "manifest.json"
        public = manifest_path.read_bytes()
        assert_public_bytes(f"{ASSET_DIR}/manifest.json", public)
        self.assertNotIn(b"https://", public)
        self.assertNotIn(b"Private raw field", public)
        self.assertNotIn(b"test-only-token", public)
        self.assertEqual(len(list((self.site / ASSET_DIR).glob("*.png"))), 2)
        paths = program_logo_paths(self.site)
        self.assertEqual(paths["Alpha"], paths["Delta"])
        self.assertEqual(paths["Beta"], paths["TEAM-B"])
        self.assertNotEqual(paths["Alpha"], paths["Beta"])
        self.assertNotIn("Shared", paths)

        manifest_path.unlink()
        (self.site / paths["Beta"].lstrip("/")).unlink()
        with patch("cfbd.TeamsApi", side_effect=AssertionError), patch(
            "cfb.program_marks.build_opener", side_effect=AssertionError,
        ):
            self.assertEqual(self.run_cli("--offline")[0], 0)
            self.assertEqual(program_logo_paths(self.site), first)

    def test_current_provider_cdn_installs_from_the_retained_response(self):
        url = "https://cdn.collegefootballdata.com/logos/2483.png"
        self.cache.mkdir()
        (self.cache / "response.json").write_text(json.dumps([
            {"id": 2483, "school": "Oregon", "logos": [url]},
            {"id": 193, "school": "Bad path", "logos": [url.replace("logos/", "../")]},
        ]))
        with patch("cfbd.TeamsApi", side_effect=AssertionError), patch(
            "cfb.program_marks.build_opener"
        ) as opener:
            opener.return_value.open.return_value = self.image_response(data=sample_png(4096, 4096))
            self.assertEqual(self.run_cli()[0], 0)
            self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url, url)
        self.assertEqual(set(program_logo_paths(self.site)), {"Oregon"})

    def test_uncertain_catalog_attempt_is_never_replayed(self):
        acquire = ("--acquire-catalog", "--meter", str(self.meter.path))
        with patch("cfb.season_source.create_api_client", side_effect=TimeoutError("private test detail")) as sdk:
            status, output = self.run_cli(*acquire)
            self.assertEqual(status, 1)
            self.assertNotIn("private test detail", output)
            status, output = self.run_cli(*acquire)
            self.assertEqual(status, 1)
            self.assertIn("cache is claimed", output)
            sdk.assert_called_once()
        self.assertEqual(self.meter.audit_records()[-1]["outcome"], "failed")
        self.assertEqual(len(self.meter.audit_records()), 2)
        self.assertEqual(program_logo_paths(self.site), {})

    def test_unsafe_and_unavailable_images_never_enter_public_site(self):
        self.cache.mkdir()
        records = [
            {"id": 1, "school": "Other host", "logos": ["https://other.invalid/img.png"]},
            {"id": 2, "school": "Redirect", "logos": [CDN + "redirect.png"]},
            {"id": 3, "school": "SVG", "logos": [CDN + "svg.png"]},
            {"id": 4, "school": "Oversized", "logos": [CDN + "oversized.png"]},
            {"id": 5, "school": "Corrupt", "logos": [CDN + "corrupt.png"]},
            {"id": 6, "school": "Missing", "logos": [CDN + "missing.png"]},
            {"id": 7, "school": "Query", "logos": [CDN + "query.png?credential=test"]},
        ]
        (self.cache / "response.json").write_text(json.dumps(records), encoding="utf-8")
        replies = {
            CDN + "redirect.png": self.image_response(302, Location="https://other.invalid/redirect.png"),
            CDN + "svg.png": self.image_response(content_type="image/svg+xml"),
            CDN + "oversized.png": self.image_response(**{"Content-Length": str(2 * 1024 * 1024 + 1)}),
            CDN + "corrupt.png": self.image_response(data=sample_png()[:-12]),
            CDN + "missing.png": self.image_response(404),
        }
        with patch("urllib.request.HTTPSHandler.https_open", side_effect=lambda request: replies[request.full_url]) as https:
            self.assertEqual(self.run_cli()[0], 0)
            self.assertEqual(self.run_cli()[0], 0)
            self.assertEqual({call.args[0].full_url for call in https.call_args_list}, set(replies))
            self.assertEqual(https.call_count, len(replies))
        self.assertEqual(list((self.site / ASSET_DIR).glob("*.png")), [])
        self.assertEqual(program_logo_paths(self.site), {})

    @staticmethod
    def sdk_response(raw):
        class SDKResponse:
            status = 200
            data = raw

            def getheaders(self):
                return {"content-type": "application/json"}

        return SDKResponse()

    @staticmethod
    def image_response(status=200, data=None, content_type="image/png", **headers):
        content = Message()
        content["Content-Type"] = content_type
        for name, value in headers.items():
            content[name] = value
        result = addinfourl(io.BytesIO(sample_png() if data is None else data), content, CDN, status)
        result.msg = "test response"
        return result


if __name__ == "__main__":
    unittest.main()
