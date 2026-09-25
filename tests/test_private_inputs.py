"""Focused offline checks for the owner-only private input store."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from cfb import private_inputs as private_inputs_module
from cfb.private_inputs import (
    InputReference,
    LocalInputStore,
    PrivateInputError,
    PrivateInputIntegrityError,
    PrivateInputSecurityError,
)


class LocalInputStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary.name)
        os.chmod(self.workspace, 0o700)
        self.store = LocalInputStore(self.workspace / "store")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _source(self, payload: bytes = b"private source bytes\n", *, mode: int = 0o644) -> Path:
        source = self.workspace / "incoming.bin"
        source.write_bytes(payload)
        os.chmod(source, mode)
        return source

    @staticmethod
    def _pin(payload: bytes) -> tuple[str, int]:
        return hashlib.sha256(payload).hexdigest(), len(payload)

    def test_retain_restore_and_receipt_round_trip_exact_bytes(self) -> None:
        payload = b"source input with a newline\n\x00"
        source = self._source(payload)
        digest, size = self._pin(payload)

        reference = self.store.retain(
            source,
            digest,
            size,
            role="source-input",
        )
        self.assertEqual(reference.public_receipt(), {
            "role": "source-input",
            "sha256": digest,
            "size": size,
        })
        self.assertEqual(set(reference.to_dict()), {"role", "sha256", "size"})
        self.assertNotIn(str(self.store.root), json.dumps(reference.public_receipt()))
        self.assertEqual(InputReference.from_public_receipt(reference.public_receipt()), reference)
        self.assertIs(self.store.verify(reference), reference)

        destination_parent = self.workspace / "restored"
        destination_parent.mkdir(mode=0o700)
        destination = self.store.restore(reference, destination_parent / "input.bin")
        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

        object_path = self.store.root / "objects" / digest[:2] / digest
        self.assertEqual(object_path.read_bytes(), payload)
        self.assertEqual(stat.S_IMODE(self.store.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.store.root / "objects").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(object_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(object_path.stat().st_mode), 0o600)

    def test_empty_input_is_retained_and_restored_with_zero_size(self) -> None:
        payload = b""
        source = self._source(payload)
        digest, size = self._pin(payload)

        reference = self.store.retain(source, digest, size)
        destination_parent = self.workspace / "empty-restored"
        destination_parent.mkdir(mode=0o700)
        destination = self.store.restore(reference, destination_parent / "input.bin")

        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(destination.stat().st_size, 0)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_duplicate_digest_is_idempotent_and_does_not_replace_object(self) -> None:
        payload = b"same content"
        source = self._source(payload)
        digest, size = self._pin(payload)
        first = self.store.retain(source, digest, size)
        object_path = self.store.root / "objects" / digest[:2] / digest
        first_inode = object_path.stat().st_ino

        duplicate_source = self._source(payload, mode=0o600)
        second = self.store.retain(duplicate_source, digest, size)

        self.assertEqual(first, second)
        self.assertEqual(object_path.stat().st_ino, first_inode)

    def test_existing_digest_with_mismatched_bytes_is_never_overwritten(self) -> None:
        payload = b"original bytes"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)
        object_path = self.store.root / "objects" / digest[:2] / digest
        object_path.write_bytes(b"tampered bytes")
        os.chmod(object_path, 0o600)

        with self.assertRaises(PrivateInputIntegrityError):
            self.store.retain(source, digest, size)
        self.assertEqual(object_path.read_bytes(), b"tampered bytes")
        with self.assertRaises(PrivateInputIntegrityError):
            self.store.verify(reference)

    def test_wrong_digest_or_size_is_rejected_before_storage(self) -> None:
        payload = b"pinned payload"
        source = self._source(payload)
        digest, size = self._pin(payload)

        with self.assertRaises(PrivateInputIntegrityError):
            self.store.retain(source, "0" * 64, size)
        with self.assertRaises(PrivateInputIntegrityError):
            self.store.retain(source, digest, size + 1)
        self.assertEqual(list((self.store.root / "objects").iterdir()), [])

    def test_missing_or_corrupt_or_symlinked_objects_fail_closed(self) -> None:
        payload = b"object state"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)
        object_path = self.store.root / "objects" / digest[:2] / digest

        object_path.unlink()
        with self.assertRaises(PrivateInputSecurityError):
            self.store.verify(reference)

        self.store.retain(source, digest, size)
        object_path.unlink()
        object_path.symlink_to(source)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.verify(reference)

    def test_verify_normalizes_object_disappearance_at_final_boundary(self) -> None:
        payload = b"final boundary race"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)
        object_path = self.store.root / "objects" / digest[:2] / digest
        original_secure_file = private_inputs_module._assert_secure_file
        calls = 0

        def disappear_on_final_check(path: Path, *, label: str):
            nonlocal calls
            calls += 1
            info = original_secure_file(path, label=label)
            if calls == 2:
                path.unlink()
                return original_secure_file(path, label=label)
            return info

        with patch(
            "cfb.private_inputs._assert_secure_file",
            side_effect=disappear_on_final_check,
        ):
            with self.assertRaises(PrivateInputSecurityError):
                self.store.verify(reference)
        self.assertEqual(calls, 2)

    def test_permissive_modes_and_wrong_owner_are_rejected(self) -> None:
        payload = b"security checks"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)

        os.chmod(self.store.root, 0o755)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.verify(reference)
        os.chmod(self.store.root, 0o700)

        object_path = self.store.root / "objects" / digest[:2] / digest
        os.chmod(object_path, 0o644)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.verify(reference)
        os.chmod(object_path, 0o600)

        with patch("cfb.private_inputs.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(PrivateInputSecurityError):
                self.store.verify(reference)

    def test_restore_requires_secure_parent_and_does_not_overwrite_mismatch(self) -> None:
        payload = b"restore target"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)

        destination_parent = self.workspace / "restored"
        destination_parent.mkdir(mode=0o700)
        destination = destination_parent / "input.bin"
        destination.write_bytes(b"existing different bytes")
        os.chmod(destination, 0o600)
        with self.assertRaises(PrivateInputIntegrityError):
            self.store.restore(reference, destination)
        self.assertEqual(destination.read_bytes(), b"existing different bytes")

        os.chmod(destination_parent, 0o755)
        destination.unlink()
        with self.assertRaises(PrivateInputSecurityError):
            self.store.restore(reference, destination)

    def test_mismatched_hardlink_destination_is_not_overwritten(self) -> None:
        payload = b"restore hardlink target"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)

        destination_parent = self.workspace / "hardlink-restored"
        destination_parent.mkdir(mode=0o700)
        unrelated = self.workspace / "unrelated.bin"
        unrelated.write_bytes(b"unrelated bytes")
        os.chmod(unrelated, 0o600)
        destination = destination_parent / "input.bin"
        os.link(unrelated, destination)

        with self.assertRaises(PrivateInputIntegrityError):
            self.store.restore(reference, destination)
        self.assertEqual(destination.read_bytes(), b"unrelated bytes")
        self.assertEqual(unrelated.read_bytes(), b"unrelated bytes")

    def test_symlink_sources_and_destinations_are_rejected(self) -> None:
        payload = b"symlink rejection"
        real_source = self._source(payload)
        symlink_source = self.workspace / "source-link"
        symlink_source.symlink_to(real_source)
        digest, size = self._pin(payload)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.retain(symlink_source, digest, size)

        destination_parent = self.workspace / "restored"
        destination_parent.mkdir(mode=0o700)
        reference = self.store.retain(real_source, digest, size)
        symlink_destination = destination_parent / "output"
        symlink_destination.symlink_to(real_source)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.restore(reference, symlink_destination)

    def test_unsafe_paths_and_receipt_fields_are_rejected(self) -> None:
        with self.assertRaises(PrivateInputSecurityError):
            LocalInputStore("relative-store")

        payload = b"path checks"
        source = self._source(payload)
        digest, size = self._pin(payload)
        reference = self.store.retain(source, digest, size)
        with self.assertRaises(PrivateInputSecurityError):
            self.store.restore(reference, self.workspace / ".." / "outside")
        with self.assertRaises(PrivateInputSecurityError):
            self.store.restore(reference, "relative-destination")
        with self.assertRaises(PrivateInputError):
            InputReference.from_public_receipt({**reference.public_receipt(), "path": "secret"})
        with self.assertRaises(PrivateInputError):
            InputReference("../private", digest, size)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
