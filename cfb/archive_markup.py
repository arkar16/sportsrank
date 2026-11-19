"""Keep the original archived HTML recoverable when presenting it to readers."""

import re


_UI = re.compile(rb"<!--sr-ui-->.*?<!--/sr-ui-->", re.DOTALL)
_ORIGINAL = re.compile(rb"<!--sr-original:(.*?)-->", re.DOTALL)


def ui(markup: str) -> str:
    return "<!--sr-ui-->" + markup + "<!--/sr-ui-->"


def original(text: str) -> str:
    if "--" in text:
        raise ValueError("archived text contains a comment delimiter")
    return f"<!--sr-original:{text}-->"


def replace_copy(source: str, visible: str = "") -> str:
    return original(source) + (ui(visible) if visible else "")


def strip_ui(raw: bytes) -> bytes:
    return _UI.sub(b"", raw)


def restore_original(raw: bytes) -> bytes:
    return _ORIGINAL.sub(rb"\1", raw)
