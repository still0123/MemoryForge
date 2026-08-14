"""Canonical storage identifier patterns."""

import re

CHAR_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")
CODE_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$")
CONTENT_SHA256 = re.compile(r"^[a-f0-9]{64}$")
FEISHU_SOURCE_PATH = re.compile(r"^feishu/(?P<document_id>[A-Za-z0-9_-]{8,})\.md$")
ORIGIN_MAIN_SOURCE_ID = re.compile(r"^src_[a-f0-9]{16}$")
