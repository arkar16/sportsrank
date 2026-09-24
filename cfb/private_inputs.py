"""Owner-only, content-addressed retention for private recovery inputs.

The store keeps the bytes themselves out of Git and exposes only a small
receipt containing a caller supplied role, the pinned SHA-256 digest, and the
byte count.  A reference is deliberately path-free: the store reconstructs
its object path from the digest every time it verifies or restores an input.

This module is intentionally stdlib-only.  It does not decide where an
operator should place the store; the caller supplies an absolute, dedicated
directory and is responsible for keeping that location on the intended
machine.  The implementation fails closed when the store, its objects, or a
restore destination is not owner-only and free of symlinks.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping, Self


_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_ROLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CHUNK_SIZE = 1024 * 1024
_STORE_OBJECTS = "objects"
_DEFAULT_ROLE = "input"
# macOS exposes these conventional aliases as symlinks into ``/private``.
# They are OS plumbing rather than caller-controlled store/input links.  Any
# symlink below them (including the final store, source, or destination path)
# remains rejected.
_SYSTEM_PATH_ALIASES = {Path("/tmp"), Path("/var")}


class PrivateInputError(ValueError):
    """The private-input store cannot establish a safe, pinned input."""


class PrivateInputSecurityError(PrivateInputError):
    """A path, owner, mode, or file type violates the store boundary."""


class PrivateInputIntegrityError(PrivateInputError):
    """Stored or imported bytes do not match their pinned identity."""


def _owner_uid() -> int | None:
    """Return the current account id where the platform exposes one."""

    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else None


def _validate_digest(value: object, *, label: str = "SHA-256 digest") -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise PrivateInputError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _validate_size(value: object, *, label: str = "byte count") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PrivateInputError(f"{label} must be a non-negative integer")
    return value


def _validate_role(value: object) -> str:
    if not isinstance(value, str) or _ROLE_RE.fullmatch(value) is None:
        raise PrivateInputError(
            "role must start with an alphanumeric character and contain only "
            "safe receipt characters"
        )
    return value


def _absolute_path(value: str | os.PathLike[str], *, label: str) -> Path:
    """Validate a path without resolving away an unsafe component."""

    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise PrivateInputSecurityError(f"{label} is not a valid path") from exc
    if isinstance(raw, bytes):
        raise PrivateInputSecurityError(f"{label} must be a text path")
    if "\x00" in raw or "\\" in raw:
        raise PrivateInputSecurityError(f"{label} contains an unsafe path character")
    path = Path(raw)
    if not path.is_absolute():
        raise PrivateInputSecurityError(f"{label} must be absolute")
    if any(part in {".", ".."} for part in path.parts):
        raise PrivateInputSecurityError(f"{label} contains an unsafe path component")
    # A filesystem root is not a dedicated private store and would also make
    # the "outside the store" restore check meaningless.
    if path.parent == path and label == "store root":
        raise PrivateInputSecurityError("store root must be a dedicated directory")
    return path


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        raise PrivateInputSecurityError(f"{label} is missing") from exc
    except OSError as exc:
        raise PrivateInputSecurityError(f"{label} cannot be inspected") from exc


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    """Reject symlinks in every existing component of an absolute path."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            # Callers separately require the components they need to exist.
            # There cannot be an existing descendant after a missing component.
            continue
        except OSError as exc:
            raise PrivateInputSecurityError(f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            if current in _SYSTEM_PATH_ALIASES:
                continue
            raise PrivateInputSecurityError(f"{label} contains a symlink")


def _assert_owner_only(info: os.stat_result, *, label: str) -> None:
    _assert_current_owner(info, label=label)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PrivateInputSecurityError(f"{label} has permissive permissions")


def _assert_current_owner(info: os.stat_result, *, label: str) -> None:
    uid = _owner_uid()
    if uid is not None and info.st_uid != uid:
        raise PrivateInputSecurityError(f"{label} has the wrong owner")


def _assert_secure_dir(path: Path, *, label: str) -> os.stat_result:
    _assert_no_symlink_components(path, label=label)
    info = _lstat(path, label=label)
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateInputSecurityError(f"{label} is not a directory")
    _assert_owner_only(info, label=label)
    return info


def _assert_secure_file(path: Path, *, label: str) -> os.stat_result:
    _assert_no_symlink_components(path, label=label)
    info = _lstat(path, label=label)
    if stat.S_ISLNK(info.st_mode):
        raise PrivateInputSecurityError(f"{label} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise PrivateInputSecurityError(f"{label} is not a regular file")
    _assert_owner_only(info, label=label)
    return info


def _assert_import_source(path: Path) -> os.stat_result:
    """Check the source identity without requiring its pre-import mode.

    Existing downloaded archives commonly have mode ``0644`` before ingress.
    They are copied into the owner-only store immediately; the source still
    must be a current-owner regular file and can never be a symlink.
    """

    _assert_no_symlink_components(path, label="private input source")
    info = _lstat(path, label="private input source")
    if stat.S_ISLNK(info.st_mode):
        raise PrivateInputSecurityError("private input source is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise PrivateInputSecurityError("private input source is not a regular file")
    _assert_current_owner(info, label="private input source")
    return info


def _open_readonly_nofollow(path: Path, *, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except FileNotFoundError as exc:
        raise PrivateInputSecurityError(f"{label} is missing") from exc
    except OSError as exc:
        raise PrivateInputSecurityError(f"{label} cannot be opened safely") from exc


def _copy_fd(source_fd: int, destination_fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(source_fd, _CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:  # pragma: no cover - defensive OS failure guard
                raise OSError("short write while retaining private input")
            view = view[written:]
    return digest.hexdigest(), size


def _fsync_directory(path: Path) -> None:
    """Make a successful atomic link durable where the platform permits it."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # The atomic link is still the correctness boundary.  Some
            # filesystems do not allow fsync on directories.
            return
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class InputReference:
    """A path-free identity for one retained private input."""

    role: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        _validate_role(self.role)
        _validate_digest(self.sha256)
        _validate_size(self.size)

    def public_receipt(self) -> dict[str, str | int]:
        """Return the only fields safe to carry into public evidence."""

        return {"role": self.role, "sha256": self.sha256, "size": self.size}

    # ``to_dict`` is intentionally an alias in behavior, not a path-bearing
    # serialization hook.  Callers commonly use this name for safe records.
    def to_dict(self) -> dict[str, str | int]:
        return self.public_receipt()

    @classmethod
    def from_public_receipt(cls, value: Mapping[str, object]) -> Self:
        if not isinstance(value, Mapping):
            raise PrivateInputError("private-input receipt must be an object")
        if set(value) != {"role", "sha256", "size"}:
            raise PrivateInputError("private-input receipt contains unexpected fields")
        return cls(
            role=value["role"],  # type: ignore[arg-type]
            sha256=value["sha256"],  # type: ignore[arg-type]
            size=value["size"],  # type: ignore[arg-type]
        )


class LocalInputStore:
    """Secure local content-addressed store for pinned input bytes.

    ``root`` must be an absolute dedicated directory.  The constructor creates
    the root and ``objects`` directory with mode ``0700`` when absent.  Stored
    objects use ``objects/<first-two-digest-characters>/<digest>`` and are
    installed through an atomic hard link from a mode ``0600`` temporary file.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = _absolute_path(root, label="store root")
        self._initialize()

    def _initialize(self) -> None:
        _assert_no_symlink_components(self.root, label="store root")
        try:
            root_info = os.lstat(self.root)
        except FileNotFoundError:
            parent = self.root.parent
            _assert_secure_dir(parent, label="store parent")
            try:
                self.root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _assert_secure_dir(self.root, label="store root")
        except OSError as exc:
            raise PrivateInputSecurityError("store root cannot be inspected") from exc
        else:
            if stat.S_ISLNK(root_info.st_mode):
                raise PrivateInputSecurityError("store root is a symlink")
            _assert_secure_dir(self.root, label="store root")
        self._ensure_objects(create=True)

    def _ensure_objects(self, *, create: bool) -> Path:
        _assert_secure_dir(self.root, label="store root")
        objects = self.root / _STORE_OBJECTS
        try:
            os.lstat(objects)
        except FileNotFoundError:
            if not create:
                raise PrivateInputSecurityError("store objects directory is missing")
            try:
                objects.mkdir(mode=0o700)
            except FileExistsError:
                pass
        _assert_secure_dir(objects, label="store objects directory")
        return objects

    def _object_path(self, digest: str, *, create: bool) -> tuple[Path, Path]:
        digest = _validate_digest(digest)
        objects = self._ensure_objects(create=create)
        shard = objects / digest[:2]
        try:
            os.lstat(shard)
        except FileNotFoundError:
            if not create:
                raise PrivateInputSecurityError("private input object shard is missing")
            try:
                shard.mkdir(mode=0o700)
            except FileExistsError:
                pass
        _assert_secure_dir(shard, label="private input object shard")
        return shard, shard / digest

    @staticmethod
    def _reference(
        expected_sha256: str,
        expected_size: int,
        *,
        role: str,
    ) -> InputReference:
        return InputReference(
            role=_validate_role(role),
            sha256=_validate_digest(expected_sha256),
            size=_validate_size(expected_size),
        )

    def _verify_file_without_sink(
        self, path: Path, reference: InputReference, *, label: str
    ) -> None:
        """Verify a file without leaking its bytes into a caller-visible path."""

        before = _assert_secure_file(path, label=label)
        fd = _open_readonly_nofollow(path, label=label)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise PrivateInputSecurityError(f"{label} changed during verification")
            if not stat.S_ISREG(opened.st_mode):
                raise PrivateInputSecurityError(f"{label} is not a regular file")
            _assert_owner_only(opened, label=label)
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(fd, _CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        except OSError as exc:
            if isinstance(exc, PrivateInputError):
                raise
            raise PrivateInputSecurityError(f"{label} cannot be read") from exc
        finally:
            os.close(fd)
        if digest.hexdigest() != reference.sha256 or size != reference.size:
            raise PrivateInputIntegrityError(f"{label} does not match its pinned digest or size")
        # Re-check the complete secure-file contract after reading.  A path
        # can disappear or be replaced between descriptor verification and
        # this boundary; normalize that race to the store's fail-closed error
        # instead of leaking FileNotFoundError/PermissionError to callers.
        after = _assert_secure_file(path, label=label)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise PrivateInputSecurityError(f"{label} changed during verification")
        if after.st_size != reference.size:
            raise PrivateInputIntegrityError(f"{label} has the wrong byte count")

    def retain(
        self,
        source: str | os.PathLike[str],
        expected_sha256: str,
        expected_size: int,
        *,
        role: str = _DEFAULT_ROLE,
    ) -> InputReference:
        """Import one exact pinned file and return a path-free reference.

        A source is checked before it is copied.  The resulting object is
        installed with an atomic no-overwrite link, so an existing object with
        the same digest is idempotent only when its bytes are also correct.
        """

        reference = self._reference(expected_sha256, expected_size, role=role)
        source_path = _absolute_path(source, label="private input source")
        _assert_no_symlink_components(source_path, label="private input source")
        source_info = _assert_import_source(source_path)
        objects = self._ensure_objects(create=True)
        shard_was_present = (objects / reference.sha256[:2]).exists()
        shard, target = self._object_path(reference.sha256, create=True)

        source_fd = _open_readonly_nofollow(source_path, label="private input source")
        temporary_path: Path | None = None
        temporary_fd: int | None = None
        copied = False
        try:
            opened_source = os.fstat(source_fd)
            if (opened_source.st_dev, opened_source.st_ino) != (
                source_info.st_dev,
                source_info.st_ino,
            ):
                raise PrivateInputSecurityError("private input source changed during import")
            _assert_current_owner(opened_source, label="private input source")
            if not stat.S_ISREG(opened_source.st_mode):
                raise PrivateInputSecurityError("private input source is not a regular file")
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".private-input-",
                dir=shard,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(temporary_fd, 0o600)
            actual_digest, actual_size = _copy_fd(source_fd, temporary_fd)
            if actual_digest != reference.sha256 or actual_size != reference.size:
                raise PrivateInputIntegrityError(
                    "private input source does not match its pinned digest or size"
                )
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            copied = True
        except OSError as exc:
            if isinstance(exc, PrivateInputError):
                raise
            raise PrivateInputSecurityError("private input source cannot be retained") from exc
        finally:
            os.close(source_fd)
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_path is not None and not copied:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
            if not copied and not shard_was_present:
                try:
                    shard.rmdir()
                except OSError:
                    # A concurrent writer may have populated the shard; leave
                    # it intact and let normal object verification arbitrate.
                    pass

        try:
            try:
                os.link(temporary_path, target, follow_symlinks=False)
            except FileExistsError:
                # Never replace an existing digest address.  A duplicate is
                # safe only after the existing object independently verifies.
                self._verify_file_without_sink(
                    target,
                    reference,
                    label="existing private input object",
                )
            else:
                _fsync_directory(shard)
            return reference
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def verify(self, reference: InputReference) -> InputReference:
        """Verify an object and return the same reference when it is trusted."""

        if not isinstance(reference, InputReference):
            raise PrivateInputError("private input reference has an invalid type")
        _, object_path = self._object_path(reference.sha256, create=False)
        self._verify_file_without_sink(
            object_path,
            reference,
            label="private input object",
        )
        return reference

    def public_receipt(self, reference: InputReference) -> dict[str, str | int]:
        """Verify and serialize a reference without exposing local paths."""

        self.verify(reference)
        return reference.public_receipt()

    def restore(
        self,
        reference: InputReference,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Restore exact bytes to a new owner-only destination atomically."""

        self.verify(reference)
        destination_path = _absolute_path(destination, label="restore destination")
        if destination_path == self.root or self.root in destination_path.parents:
            raise PrivateInputSecurityError("restore destination is inside the private store")
        _assert_no_symlink_components(destination_path, label="restore destination")
        parent = destination_path.parent
        _assert_secure_dir(parent, label="restore destination parent")

        try:
            existing = os.lstat(destination_path)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                raise PrivateInputSecurityError("restore destination is a symlink")
            self._verify_file_without_sink(
                destination_path,
                reference,
                label="existing restore destination",
            )
            return destination_path

        _, object_path = self._object_path(reference.sha256, create=False)
        source_fd = _open_readonly_nofollow(object_path, label="private input object")
        temporary_path: Path | None = None
        temporary_fd: int | None = None
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".private-restore-",
                dir=parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(temporary_fd, 0o600)
            actual_digest, actual_size = _copy_fd(source_fd, temporary_fd)
            if actual_digest != reference.sha256 or actual_size != reference.size:
                raise PrivateInputIntegrityError(
                    "private input object changed during restore"
                )
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
        except OSError as exc:
            if isinstance(exc, PrivateInputError):
                raise
            raise PrivateInputSecurityError("private input object cannot be restored") from exc
        finally:
            os.close(source_fd)
            if temporary_fd is not None:
                os.close(temporary_fd)

        try:
            try:
                os.link(temporary_path, destination_path, follow_symlinks=False)
            except FileExistsError:
                self._verify_file_without_sink(
                    destination_path,
                    reference,
                    label="existing restore destination",
                )
            else:
                _fsync_directory(parent)
            return destination_path
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


__all__ = [
    "InputReference",
    "LocalInputStore",
    "PrivateInputError",
    "PrivateInputIntegrityError",
    "PrivateInputSecurityError",
]
