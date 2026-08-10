#!/usr/bin/env python3
"""Validate registered benchmark identities, artifacts, and public claims."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from collections import defaultdict
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_GIT_ROOT = REPO_ROOT
DEFAULT_REGISTRY = REPO_ROOT / "demo/evaluation/registry.json"
SUMMARY_SCHEMA_2_ANCESTOR = "2451f2dae8845b490db1cb46727c7828f0d227f7"
SUITE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SPLITS = {"development", "confirmation", "holdout"}
SUITE_TYPES = {
    "document_wiki_qa",
    "code_wiki_structure",
    "code_wiki_qa",
    "source_lifecycle",
}
CODE_WIKI_METRICS = {
    "expected_source_coverage",
    "symbol_recall",
    "core_relation_recall",
    "known_gap_relation_recall",
    "overall_relation_recall",
    "module_assignment_accuracy",
    "citation_grounding_accuracy",
    "architecture_edge_grounding",
    "mermaid_edge_coverage",
    "architecture_citation_coverage",
    "architecture_mermaid_determinism",
    "deterministic_replay",
}
CODE_WIKI_GATES = {
    "core_symbols",
    "core_relations",
    "module_assignment",
    "citations",
    "architecture_wiki",
    "deterministic",
}
CODE_WIKI_INCREMENTAL = {
    "changed_symbols": ["py.app", "py.app.run_local"],
    "expected_changed_symbols": ["py.app", "py.app.run_local"],
    "changed_pages": ["wiki/pages/code/<repository-id-prefix>/py/app.md"],
    "expected_changed_pages": ["wiki/pages/code/<repository-id-prefix>/py/app.md"],
    "changed_page_ratio": 0.2,
    "max_changed_page_ratio": 0.25,
    "stable_symbol_ids": True,
    "passed": True,
}
CODE_WIKI_COUNTS = {
    "symbols": 20,
    "relations": 24,
    "modules": 8,
    "architecture_edges": 4,
}
CODE_WIKI_FIXTURE_COMMITS = {
    "initial_commit": "c7a312631c0f4122e82e08d23d69498b1c2881c4",
    "updated_commit": "eebf184cd82509b0a1c48901fdf643b63f714828",
}
RELEASE_DRILL_FIXTURE = {
    "repository_commit": "92c1236e57b9b059ae23c95452c916cccc732af0",
    "repository_id": "0bc5bbece6a54d67e12840727ce8663c895b69d203f45eaa1efc940381e86bc0",
    "source_id": "8864360214e8fa15d97f8019b8392729520e066c469d2e8475ec3e07c8734c68",
}
RELEASE_DRILL_WORKSPACE_COMMIT = "682b277b30ae3f8963f0eb1276c888215cb1c8c3"
_RESULTS = "demo/results/"
HISTORICAL_REVIEW_SCOPES = {
    _RESULTS + "artifacts/release_candidate_review_candidate_5/review-scope.json": {
        "sha256": "3da236623b9ba45ce4704b1d332c441e59b24a99ed4ce08f348b9bb09cb37e85",
        "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
        "source_commit": "26767333bc20a6367bc87f239cdc956cd40e7f4e",
        "reviewed_files": 48,
        "diff_files": 82,
        "added_lines": 7835,
        "deleted_lines": 30,
        "changed_lines": 7865,
    },
    _RESULTS + "artifacts/release_candidate_review_candidate_6/review-scope.json": {
        "sha256": "19b308f5fb463821eee729aac06bc8f7d03efb8fabd80fa0239df0fec0a029ce",
        "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
        "source_commit": "9588c2fb6a41225515165f0114ce61f23f51d921",
        "reviewed_files": 62,
        "diff_files": 105,
        "added_lines": 10918,
        "deleted_lines": 35,
        "changed_lines": 10953,
    },
    _RESULTS + "artifacts/release_candidate_review_candidate_7/review-scope.json": {
        "sha256": "55dc5e0dd5512dd34c82f5e886f577f9ec098bb4b5f06d5231c56d8776be16ed",
        "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
        "source_commit": "a044337347b9c6884ea660c7568c4e3911c84521",
        "reviewed_files": 79,
        "diff_files": 132,
        "added_lines": 14086,
        "deleted_lines": 39,
        "changed_lines": 14125,
        "scope_fields": ("reviewed_files", "changed_lines"),
    },
    _RESULTS + "artifacts/release_candidate_review_candidate_8/review-scope.json": {
        "sha256": "b838d75cdd3d75f44f99c85cd67cfb080063dea90aa209e4340b44242109a2f2",
        "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
        "source_commit": "f4dde0904e5bcaeb78be6d7a32e74a6beae5679a",
        "reviewed_files": 97,
        "diff_files": 161,
        "added_lines": 17612,
        "deleted_lines": 192,
        "changed_lines": 17804,
    },
}
REQUIRED_EXPERIMENT_EVIDENCE = {
    "exact-symbol-routing.learn-claude-code": {
        _RESULTS + "exact_symbol_routing_candidate_1_rejected.json": (
            1,
            "rejected",
            "6dad9f745ec4477dbb5990113f043a0cffa47e14cc0ccf1490854100789e89e1",
            "1cf35d67b5eb53255399dbf3bd0217dfb0937402",
        ),
        _RESULTS + "exact_symbol_routing_development.json": (
            2,
            "development_passed_regression_failed",
            "3769f91c17687228f4a509f5b7ecc49b5fa23b10b5cb17fdfe8475d1e770d2b8",
            "8af4198e8bc625a52c5016f5cd3b19f7c790f653",
        ),
        _RESULTS + "exact_symbol_routing_development_accepted.json": (
            3,
            "accepted_development_superseded",
            "748e9fd7bf76c7ca1a7a3e5d4416db8f357fe363efd77df86c1894e5f53c9145",
            "8e95ddb8aa7b29b97a5c6aa884f715c3b2006051",
        ),
        _RESULTS + "exact_symbol_routing_development_final.json": (
            4,
            "accepted_development",
            "a72494d69964a1c93b228fb73ffa1d8608bf714153cc54efa1096984addc0fc7",
            "5308078614e83ad90e3eae9ccdd490bb156f9948",
        ),
    },
    "support-score.learn-claude-code": {
        _RESULTS + "support_score_development.json": (
            1,
            "development_passed_regression_failed",
            "d6699dd57109b5bbc573fdf17355084a3ac045e43f5766f4c539c42621208a75",
            "99d20a259350693292ada852f3b18ab98aa1c172",
        ),
        _RESULTS + "support_score_development_final.json": (
            2,
            "development_passed_regression_failed",
            "4592fb565ae53596dd69b7ca80a4870f2b7430b03f9a3a2e5d9fa4660902aa29",
            "807e8e76e0b787b7c7eae08f405afbeca6c9a783",
        ),
        _RESULTS + "support_score_development_candidate_3.json": (
            3,
            "accepted_development_superseded",
            "a04876a4565cc3fe715dcfa3a479a1b662b3c2050dc81186deedbbea58ca43c0",
            "ec83a05c46bef6882658b314fd3aedf8ab2cc161",
        ),
        _RESULTS + "support_score_development_candidate_4.json": (
            4,
            "accepted_development_superseded",
            "c4fab97ed96cdc35d61540052b49b5ab18764fd0d98e641ae3a11f3deb2c258e",
            "80ac72c0fbfbf393c137aa1c25e5a44c91ae5325",
        ),
        _RESULTS + "support_score_development_candidate_5.json": (
            5,
            "accepted_development_superseded",
            "618f856f02ab077f63e3ca31cc6fb614f4c1b985ae0eecaee1f9d54c8060c42f",
            "655df04b1b90a0892c1a815a503548014d10d8ee",
        ),
        _RESULTS + "support_score_development_candidate_6.json": (
            6,
            "accepted_development_superseded",
            "f872d3e3a7fe1cf2a18f9740734c0d560e2413188d9b8984c89b6d4ecb31af2b",
            "f080b3a71132a453b61420e1fae1ab333824bc46",
        ),
        _RESULTS + "support_score_development_candidate_7.json": (
            7,
            "accepted_development_superseded",
            "49b789f110684b9f0e6331fe74b2cb186c1394c16e0d970ca750b5b6d04497b0",
            "e8c4c6587051ee83e8c42be0604281814cbcc6d6",
        ),
        _RESULTS + "support_score_development_candidate_8.json": (
            8,
            "accepted_development_superseded",
            "e8c3da42d39ab2ce3ff471761cd32a06f79a5e0ad330589c97da509cc8457ed7",
            "9667b0585a865ba6af9ef13f5eafb674b0c52026",
        ),
        _RESULTS + "support_score_development_candidate_9.json": (
            9,
            "accepted_development_superseded",
            "e3eb0f0568b5688442be314abe3b81b3d9a69f48787c78afea92f61fd993640a",
            "63972b0eee534c258a4054f40876de4da7f54880",
        ),
        _RESULTS + "support_score_development_candidate_10_rejected.json": (
            10,
            "rejected",
            "f040c4760a301143e79c8919e174f34d04d689c0f173738b0d2b7ffa8b5f9c37",
            "2729e37529fd7918f2591e5340dcaba2e90f8267",
        ),
        _RESULTS + "support_score_development_candidate_11.json": (
            11,
            "accepted_development_superseded",
            "aa4f6625025038e6aac097a510fb7ef1773cdf8168c3ade03870423b1806fe44",
            "c1cbd94d0a92e3b346afeb32895a4901cb249951",
        ),
        _RESULTS + "support_score_development_candidate_12.json": (
            12,
            "accepted_development_superseded",
            "9f0aa5793ad9cc846795b838922a1c0c9fe91ccce3a856adc4057f4ab38313c6",
            "3b7065856de66b9cf1c74b8f99637a07bfca387f",
        ),
        _RESULTS + "support_score_development_candidate_13.json": (
            13,
            "accepted_development_superseded",
            "8b1d45c721b4ecf55a25510ee8851b7891474739186f0c428eb7098b0a894b34",
            "3e20ad8ca0d54fa06fa8ca0aaed4dad959a63009",
        ),
        _RESULTS + "support_score_development_candidate_14.json": (
            14,
            "accepted_development",
            "7159992bf66e87e4cfd4e6fde70e65fa50d169002276d444bd6255223a93254a",
            "4f2f15ddc06882612a547aaad81d4b67b4ffbf8b",
        ),
    },
    "multi-source-coverage-selection": {
        _RESULTS + "multi_source_coverage_baseline_rejected.json": (
            1,
            "rejected",
            "6fa99baefed3cfa2b50bb044b3293c24ee90e697f0ff1a7f6636e91b3c548f81",
            "79c41bc5fdb08de18351546f7869b8083da3a1b2",
        ),
        _RESULTS + "multi_source_coverage_development_candidate_1.json": (
            2,
            "accepted_development",
            "ef584ff97687ff4a09644f203b9b8d135c420918e4a19e27a8f7354a3e9b5197",
            "4303c159caac6c8bded2eeb9e0e3cba625f61dc2",
        ),
    },
    "folder-import-lifecycle": {
        _RESULTS + "folder_import_baseline_rejected.json": (
            1,
            "rejected",
            "6dee68b6723d483fc308a6f09dbc5a1e06e58b7d5793eca92ec5ab08c53481ec",
            "9242478a7108e001bf6fafbd5567a01718f51716",
        ),
        _RESULTS + "folder_import_development_candidate_1.json": (
            2,
            "accepted_development_superseded",
            "9fe364c14008054f670c8ca5db6cf4489f582367db1cc55a51d7e66afb6eb435",
            "3990df2bd69988793629bcb6985cf013049c90ab",
        ),
        _RESULTS + "folder_import_development_candidate_2.json": (
            3,
            "accepted_development",
            "d6a2c7ee8d9d74f75eea88b276db09c9dd2846e143702a3e30ee0ca858f2780d",
            "3d056c9d71a4caf6a625449ac3057f74ff98148c",
        ),
    },
    "github-thread-import-lifecycle": {
        _RESULTS + "github_thread_import_baseline_rejected.json": (
            1,
            "rejected",
            "028d1d71db3e92b4b183a0fa3147d6eb581bb1274679a4d2c669d3e1f46b7255",
            "271ec3490aa6cd14120913e7fa93f259bd9999fa",
        ),
        _RESULTS + "github_thread_import_development_candidate_1.json": (
            2,
            "accepted_development_superseded",
            "0b3e8304467d6ee38cd826d225e6c19a69a40cf1c1ed5fd6f89884920fc84ee5",
            "8be9a05da49e4da1efe742dd8406f2f4706cb3f0",
        ),
        _RESULTS + "github_thread_import_development_candidate_2.json": (
            3,
            "accepted_development_superseded",
            "51963aab72b7462454544d4379ad46f44d9ae6c76924e68ffde9c155dfdce1fc",
            "fc1504488cc7735c2bfbf03f030ce6de0c946ddb",
        ),
        _RESULTS + "github_thread_import_development_candidate_3.json": (
            4,
            "accepted_development",
            "3c32675802191dbeec6c8477e0b1abcb618b115120575abc0e6509f8dc565b2c",
            "c6f329152dac002ecead2f8d8bebcb002865aff6",
        ),
    },
    "static-showcase": {
        _RESULTS + "static_showcase_baseline_rejected.json": (
            1,
            "rejected",
            "c8448999fad2f5ee50cd249688981498eb30fd9b52d8d4f8dde27c72fafb4952",
            "cf8a47eb2e62b0aff7bf5e056efd848902df3e06",
        ),
        _RESULTS + "static_showcase_candidate_0_fixture_rejected.json": (
            2,
            "rejected",
            "5afefb3f7312007c9ca3c1f3c2ab08cc369df79a025b55112f9ecd3e28f60f5d",
            "bec8c0edea1fa7a126fa15db8078065bb45e2eae",
        ),
        _RESULTS + "static_showcase_candidate_1_fixture_rejected.json": (
            3,
            "rejected",
            "5f7841001452563bb5963c71f605f35ef587b56a868d40febbb0236026635e8b",
            "a84f777f3e701aa5f67175445c20a3b656bcac0a",
        ),
        _RESULTS + "static_showcase_development_candidate_1.json": (
            4,
            "accepted_development_superseded",
            "22f58560b5597dfc4f643656c918a466ee62e1d9e19ac2ec1f2d92740b5057c0",
            "649f1ca35cf363f6eda9e4cd563c2ba6e29fecff",
        ),
        _RESULTS + "static_showcase_development_candidate_2.json": (
            5,
            "accepted_development_superseded",
            "0ae35723c8f6f30c4edcfabae89c3a078b965a2d89563e3006b08eaee17573eb",
            "1962f19a399ba0805a9c107fd14741fdfcb34759",
        ),
        _RESULTS + "static_showcase_development_candidate_3.json": (
            6,
            "accepted_development_superseded",
            "3cb6113516a5d6b3e0b5e2c357bde6470350c0a8be0aecfaa563253135ed7da7",
            "c07d2d275caa4449ffc6cc20f3a709ebf4c59b0b",
        ),
        _RESULTS + "static_showcase_development_candidate_4.json": (
            7,
            "accepted_development_superseded",
            "2340e3ecd313a85fdcdb2384ea5c936dd564b4ca8c0e6e1440af2a49c5661234",
            "77b9026c1b88fbfa1138dd74c9efb3f9abf23d90",
        ),
        _RESULTS + "static_showcase_development_candidate_5.json": (
            8,
            "accepted_development",
            "5e644b467738bcb8121a0a224a7e7ca31b11b37d4ee6962b7b5659b72544101e",
            "12d0ecadb4d8d310e2fa3b22f71dbfe770bd2567",
        ),
    },
    "cross-platform-delivery": {
        _RESULTS + "cross_platform_delivery_baseline_rejected.json": (
            1,
            "rejected",
            "f169486f1fc757abaaf3728187703834510726c2cae11736c2e82ba220e369ac",
            "bef6c7e35e9d8e282d2b3b0e0c4b3874a12f9e8a",
        ),
        _RESULTS + "cross_platform_delivery_candidate_1.json": (
            2,
            "accepted_development_superseded",
            "f2bd8afa6759c3d1ddbc796444cfb58d968fbab82818c27f1c0f24590013801e",
            "96c720cf49ed0bfc97fd765e9af025ab6f4ae9ea",
        ),
        _RESULTS + "cross_platform_delivery_candidate_2.json": (
            3,
            "accepted_development_superseded",
            "22a309f133008268e857e4331f70967d58f0c06adc45ab1988f8a99ee3c34775",
            "7d0a296ffbbb73863b63ec732608a6e3c0bab35b",
        ),
        _RESULTS + "cross_platform_delivery_candidate_3.json": (
            4,
            "accepted_development_superseded",
            "1584be87a25356d6189c55a696c35d9b679c4c56c654da261c1caf6d185abb31",
            "79188650e953c6c183b631fd41432e795bde0eaa",
        ),
        _RESULTS + "cross_platform_delivery_candidate_4.json": (
            5,
            "accepted_development_superseded",
            "198c3654291ba762b591891f894f87c3ebc41764faa8e7e24cfc5c484a4c39cb",
            "2bb3505ce2c5b32ec1ce6e2b4dcd1a12638cc93f",
        ),
        _RESULTS + "cross_platform_delivery_candidate_5.json": (
            6,
            "accepted_development_superseded",
            "4f1cb0fcad903e7e525670d9efde02148b2cdf2a9bef532210997f9ca8102106",
            "cba84d7a6b01d20abfb353e85ae2733210bde98b",
        ),
        _RESULTS + "cross_platform_delivery_candidate_6.json": (
            7,
            "accepted_development_superseded",
            "17bdad1bb9ce7b1cfeff779e4c096d5c981248326493088a2bbee43898fbb706",
            "b3dab407db3f3103456dcbe79d704e4a72c6b656",
        ),
        _RESULTS + "cross_platform_delivery_candidate_7.json": (
            8,
            "accepted_development_superseded",
            "dd48d59e149f9195410f793edacacb8ca90c899ee4691b6e214fcb8ebedc567a",
            "5e7c50ca377622a21600a7fa877046af92fefc4c",
        ),
        _RESULTS + "cross_platform_delivery_candidate_8.json": (
            9,
            "accepted_development_superseded",
            "e86b08906ddd99bf8cf14089cc9e2e873c902d6855c33596c2cc973352f2d106",
            "beb4bd0f41afc804136ce1e96b8b9857d88be30b",
        ),
        _RESULTS + "cross_platform_delivery_candidate_9.json": (
            10,
            "accepted_development",
            "425f3047bae7df586a9a529a33024f667f4c98d096bce8edb101676e04135b0c",
            "70f76ebfcc7a7bd64f926955e09cfa0a6f45766d",
        ),
    },
    "release-candidate-delivery": {
        _RESULTS + "release_candidate_baseline_rejected.json": (
            1,
            "rejected",
            "2e2674ce071489f92d4a480b4f0c0018e0b416764e9a2df1a7ff8a2fc7640740",
            "4d834679ec61355e285fb36a0cceef8f489a9083",
        ),
        _RESULTS + "release_candidate_development_candidate_1.json": (
            2,
            "accepted_development_superseded",
            "9c9a40dcd491613ca55f54e7b25ba78993be0eef0ee7fff4dbccf6f65fca3695",
            "b51a90d9603c2558ae72817bfbc8f291c3933812",
        ),
        _RESULTS + "release_candidate_development_candidate_2.json": (
            3,
            "development_passed_review_failed",
            "84a5a2e3eefb6894d512a0aea6ccc4626844ceaaab55a28b3a96f733f84b0792",
            "4972b3c2223c5e6fe7248090a9d8ee006c1c271b",
        ),
        _RESULTS + "release_candidate_development_candidate_3.json": (
            4,
            "accepted_development_superseded",
            "c61e5817c9a55e2bda780a7381512087c0a37943d8d34bb0e0a54a880a074349",
            "5005f1511301797d7d1a9ce25c3a885ab6ba85ba",
        ),
        _RESULTS + "release_candidate_development_candidate_4_rejected.json": (
            5,
            "rejected",
            "2b55864983c0b5cc7fa9b3819b9a94b68cdd3192bd55b156fbcc5f564df48fbc",
            "7e998d509ee7a4aba31f269e16699d18343ec978",
        ),
        _RESULTS + "release_candidate_development_candidate_5.json": (
            6,
            "development_passed_review_failed",
            "a64d26d8103c5bc7c0e2f61627fe978f57920a31d4be5252866cbbe354e6d861",
            "b42d6a887053464f138f87dd45922d22dc58baa0",
        ),
        _RESULTS + "release_candidate_development_candidate_6.json": (
            7,
            "development_passed_review_failed",
            "8d1f1a07f218581048e3860556e12dea0a900f2189876f28265afeffbf8093a3",
            "9a6c145a3f052c78b47c4d8f882d4a3191c4a2f4",
        ),
        _RESULTS + "release_candidate_development_candidate_7.json": (
            8,
            "development_passed_review_failed",
            "337393de3ea54605055fd08f29fa92679ca3db52470879080cc0c92c5dd5ff10",
            "80b111bbd472cacd16ceb773a4c141e70ee97a4a",
        ),
        _RESULTS + "release_candidate_development_candidate_8.json": (
            9,
            "development_passed_review_failed",
            "37b0270bba89da81815f2ac00fbeec10e766c8a16436e28ab1e7a2fd449afe83",
            "2451f2dae8845b490db1cb46727c7828f0d227f7",
        ),
        _RESULTS + "release_candidate_development_candidate_9.json": (
            10,
            "development_passed_review_failed",
            "cabe2c738be47c5e3d73b371c78a32b1dbaea899f1ce743234327316f67ded0b",
            "63326fb2f123c336c31bcebf68c76c90dfac86e6",
        ),
        _RESULTS + "release_candidate_development_candidate_10_rejected.json": (
            11,
            "rejected",
            "6a52f9ffda29b8c49dd2e428683294d4d108fee22bebebafec91552f126a8b14",
            "a4c74bdb8047bb6267955624c7d054d17bb5e722",
        ),
        _RESULTS + "release_candidate_development_candidate_11.json": (
            12,
            "development_passed_review_failed",
            "690b4d46e4191e3e0b267e9100fd3f61fa0cbdd8f9661784b2dd82405a2ac396",
            "f174d1de4e459c4b324f0ba5f58e8df62263fa00",
        ),
        _RESULTS + "release_candidate_development_candidate_12.json": (
            13,
            "development_passed_review_failed",
            "d66093fdd468501ef5c43045cb2d72579bf97a83d0a29602d51447d3e92d816c",
            "bced2a660ef38dfc4c0c6a0f994897d6af895574",
        ),
        _RESULTS + "release_candidate_development_candidate_13.json": (
            14,
            "development_passed_review_failed",
            "df39214604a3f211507a4b0079a21e34a0fd5836e7f813611c4639ae72631a43",
            "5093faea584b42739a04721e63adfad3d914d460",
        ),
    },
}
RELEASE_CANDIDATE_REJECTED_FAILURES = {
    _RESULTS + "release_candidate_baseline_rejected.json": {
        "package-version-consistency": "version_mismatch",
        "registry-and-benchmark-summary": "benchmark_summary_mismatch",
        "local-reproducible-artifacts": "artifact_missing",
        "workspace-release-drill": "workspace_drill_failure",
        "release-document-consistency": "release_document_mismatch",
    },
    _RESULTS + "release_candidate_development_candidate_4_rejected.json": {
        "release-document-consistency": "release_document_mismatch",
    },
    _RESULTS + "release_candidate_development_candidate_10_rejected.json": {
        "workspace-release-drill": "workspace_drill_failure",
    },
}
STATIC_SHOWCASE_REJECTED_CONTRACTS = {
    _RESULTS + "static_showcase_baseline_rejected.json": {
        "suite_revision": 1,
        "development_sha256": "ff6e74e31ac0ce4891e04c4320874e503ec6dd935bf55da29ecf0243f24bf63f",
        "test_sha256": "5ac08a2fc24e005737eddc094ef372baa317775b58a88e067d20e93287ab33ae",
        "confirmation_sha256": "1a75c146f884459a7bad5a995bda9eed32b91f043332d3ce735aadc73ccdf555",
        "pytest": {"passed": 0, "failed": 0, "errors": 1, "exit_code": 2},
        "failures": [("test-collection", "FEATURE_NOT_IMPLEMENTED")],
    },
    _RESULTS + "static_showcase_candidate_0_fixture_rejected.json": {
        "suite_revision": 1,
        "development_sha256": "ff6e74e31ac0ce4891e04c4320874e503ec6dd935bf55da29ecf0243f24bf63f",
        "test_sha256": "5ac08a2fc24e005737eddc094ef372baa317775b58a88e067d20e93287ab33ae",
        "confirmation_sha256": "1a75c146f884459a7bad5a995bda9eed32b91f043332d3ce735aadc73ccdf555",
        "pytest": {"passed": 0, "failed": 4, "errors": 0, "exit_code": 1},
        "failures": [
            ("complete-public-readonly-snapshot", "FIXTURE_PRECONDITION"),
            ("owned-deterministic-rebuild", "FIXTURE_PRECONDITION"),
            ("unsafe-output-rejection", "FIXTURE_PRECONDITION"),
            ("zero-key-nested-cli", "FIXTURE_PRECONDITION"),
        ],
    },
    _RESULTS + "static_showcase_candidate_1_fixture_rejected.json": {
        "suite_revision": 2,
        "development_sha256": "773ceef802964b197bab56cf7fbfadd255f32bce776e1c99029bec9b23c91cf6",
        "test_sha256": "1353c6e571df641617a6f906dcf5f6afcc1e61d9409d1392c3aee1be593fb0ec",
        "confirmation_sha256": "cdc59f2eccfe920204be0a5875dbbaf3eefb2d517ff098419bed6d47ecb8e0d8",
        "pytest": {"passed": 0, "failed": 4, "errors": 0, "exit_code": 1},
        "failures": [
            ("complete-public-readonly-snapshot", "FIXTURE_PRECONDITION"),
            ("owned-deterministic-rebuild", "FIXTURE_PRECONDITION"),
            ("unsafe-output-rejection", "FIXTURE_PRECONDITION"),
            ("zero-key-nested-cli", "FIXTURE_PRECONDITION"),
        ],
    },
}
REQUIRED_REGRESSION_EVIDENCE = {
    "exact-symbol-routing.learn-claude-code": {
        _RESULTS + "exact_symbol_routing_development.json": (
            _RESULTS + "exact_symbol_routing_candidate_2_regression_rejected.json",
            "d07b5a54ec11dc044fbb091b7372461885a02e68fde0803b004dbe8cf3fb60f8",
            "9956343013d33531560a437fc61fef3f864ec319",
        ),
    },
    "support-score.learn-claude-code": {
        _RESULTS + "support_score_development.json": (
            _RESULTS + "support_score_candidate_1_regression_rejected.json",
            "2da64ec8a359162e531153bd28f37c185a0f23fae85aa95c8efc22b852dd87ef",
            "e41bb48a63d40e9bcecab74c25a8a7061f2464a5",
        ),
        _RESULTS + "support_score_development_final.json": (
            _RESULTS + "support_score_candidate_2_regression_rejected.json",
            "4ef87510f5a1780aa90ffdd184742fa35ca45ed58d4eac5e5127137e8e64f867",
            "dd7ac2df2af8da205f46044dd39cc4d2e1e41604",
        ),
    },
    "multi-source-coverage-selection": {},
    "folder-import-lifecycle": {},
    "github-thread-import-lifecycle": {},
    "static-showcase": {},
    "cross-platform-delivery": {},
    "release-candidate-delivery": {
        _RESULTS + "release_candidate_development_candidate_2.json": (
            _RESULTS + "release_candidate_sdist_probe_regression_rejected.json",
            "b0c18c7e2d23d47e3cb8cb1200c3511dc9a4bb560ac81e531e4492c5f1353d5b",
            "94b136e0ddda947c14e4ab0297b6505e00b9c63f",
        ),
        _RESULTS + "release_candidate_development_candidate_7.json": (
            _RESULTS + "release_candidate_candidate_7_local_gate_contract_rejected.json",
            "921d5595531bc3b8427b4080264f366f02b40909e01312fc340ce417c298aa57",
            "0da3092733e0cf549d3e55ed50ed2413374a5cfb",
        ),
    },
}
REQUIRED_REVIEW_EVIDENCE = {
    "exact-symbol-routing.learn-claude-code": {},
    "support-score.learn-claude-code": {},
    "multi-source-coverage-selection": {},
    "folder-import-lifecycle": {},
    "github-thread-import-lifecycle": {},
    "static-showcase": {},
    "cross-platform-delivery": {},
    "release-candidate-delivery": {
        _RESULTS + "release_candidate_development_candidate_2.json": (
            _RESULTS + "release_candidate_candidate_2_static_review_rejected.json",
            "475b6e5981bc43438107c67a7fd3ab05fc95888bfb30c391f2e7ae2275c23d45",
            "433f33c001c963cd69dd507346ac836895b7c36b",
        ),
        _RESULTS + "release_candidate_development_candidate_5.json": (
            _RESULTS + "release_candidate_candidate_5_static_review_rejected.json",
            "7973225fac1123040b93674bbcb2d5df38872229772e9711015af064cbd39913",
            "26767333bc20a6367bc87f239cdc956cd40e7f4e",
        ),
        _RESULTS + "release_candidate_development_candidate_6.json": (
            _RESULTS + "release_candidate_candidate_6_static_review_rejected.json",
            "cc3ae3f9a5f99f5d420e2b4cfce1f12cc260b46d99b03b34f93632f5c47dcacc",
            "9588c2fb6a41225515165f0114ce61f23f51d921",
        ),
        _RESULTS + "release_candidate_development_candidate_7.json": (
            _RESULTS + "release_candidate_candidate_7_static_review_rejected.json",
            "94b841b8148f40049e3b226b705294527767acf7567a5a456b8706edcde3b501",
            "a044337347b9c6884ea660c7568c4e3911c84521",
        ),
        _RESULTS + "release_candidate_development_candidate_8.json": (
            _RESULTS + "release_candidate_candidate_8_static_review_rejected.json",
            "f2456842b969565a221d962fc48f95264cbe22ccce13fca960b21b1a155f043a",
            "f4dde0904e5bcaeb78be6d7a32e74a6beae5679a",
        ),
        _RESULTS + "release_candidate_development_candidate_9.json": (
            _RESULTS + "release_candidate_candidate_9_static_review_rejected.json",
            "bda916939f33f5ca10ad0c78733f01197fd8fc5924712d2b9aa1f37e4bebda2d",
            "c23de5915e6237700c0aa8e03a14e44583ab1049",
        ),
        _RESULTS + "release_candidate_development_candidate_11.json": (
            _RESULTS + "release_candidate_candidate_11_static_review_rejected.json",
            "45ab7e09a98b6e888332a16ed03be1944c3f14ea5851ac4d700fde986e02b1f6",
            "43d5c80852595c7b49e46c66f70ced82c53cf7d0",
        ),
        _RESULTS + "release_candidate_development_candidate_12.json": (
            _RESULTS + "release_candidate_candidate_12_static_review_rejected.json",
            "168e565b495872b2e663528bb2ca214482e999011e957fab8ddfc577ddb2aac1",
            "e7d3fef9312b4b6c1683e12984ae00cd1b21b343",
        ),
        _RESULTS + "release_candidate_development_candidate_13.json": (
            _RESULTS + "release_candidate_candidate_13_static_review_rejected.json",
            "6d442587d6502704b274d51ec5d94b14076fcac77dc20110f9b81b7705532b65",
            "2fff47c47f0cc3c4bbb98eb02c0acb77bce1718e",
        ),
    },
}
REQUIRED_ACCEPTANCE_EVIDENCE = {
    "exact-symbol-routing.learn-claude-code": {
        _RESULTS + "exact_symbol_routing_development_final.json": (
            _RESULTS + "exact_symbol_routing_candidate_4_local_gate.json",
            "9a5d749e32816253c1412c78fdea436a012067e524afc888273738f38d4a2194",
            "5308078614e83ad90e3eae9ccdd490bb156f9948",
        ),
    },
    "support-score.learn-claude-code": {
        _RESULTS + "support_score_development_candidate_3.json": (
            _RESULTS + "support_score_candidate_3_local_gate.json",
            "6d1345e9a1ab2c20b01b6adbac0136c05d772e45381ebf6636296895a23713af",
            "14618ac3a626dc925375fb600727bca83b46cc0a",
        ),
        _RESULTS + "support_score_development_candidate_4.json": (
            _RESULTS + "support_score_candidate_4_local_gate.json",
            "1b9db95d3950150dc83abfafea4b4e226d7a4b02d50852b3f04c1103a15cd085",
            "e86d23ef34260608e1dbca46047a812c479454f1",
        ),
        _RESULTS + "support_score_development_candidate_5.json": (
            _RESULTS + "support_score_candidate_5_local_gate.json",
            "94ec66a56ae56ebd9c60b2cc30a9784f899b04cb4b7e983bc1a3ac7f321a4123",
            "e13b3515189a8428344399fc36c0df7622e4a7f0",
        ),
        _RESULTS + "support_score_development_candidate_6.json": (
            _RESULTS + "support_score_candidate_6_local_gate.json",
            "971f88d55a4fbee6e2651b94baf6889b8f40d7f2b4b659adb99cd69504f5dfb9",
            "350fc0883c4b818fa9d57f3acbeb07e23920fdf5",
        ),
        _RESULTS + "support_score_development_candidate_7.json": (
            _RESULTS + "support_score_candidate_7_local_gate.json",
            "1e0ad1a190d5051f6c9ebdc4dcd71cf14bde007e12fef7289ab6af6d8b7733e1",
            "3b5447a9f8fa592adc2e5ac48930f9da9a56be5f",
        ),
        _RESULTS + "support_score_development_candidate_8.json": (
            _RESULTS + "support_score_candidate_8_local_gate.json",
            "2396ca6b9b4bfd401d313e96d1551bf24dd94aabd3a4c24d37048fc0f8c61ae0",
            "dc6c70efa3edec8782b9514764efbf37cf20e22b",
        ),
        _RESULTS + "support_score_development_candidate_9.json": (
            _RESULTS + "support_score_candidate_9_local_gate.json",
            "2939506d21e3ffd42777ebc1ac7cfb0f4ae3f13bbcbe51cdcf2415f5ab141d9c",
            "fc2f15f7da52703061b31705d553e211deaa4e97",
        ),
        _RESULTS + "support_score_development_candidate_11.json": (
            _RESULTS + "support_score_candidate_11_local_gate.json",
            "686f4b799d2f7a07bc7bff0a732ddc538d8a3584e2c8962e17f2da39ac9fbe71",
            "ad67aa09341d451a7661634c0bf714291f0ad452",
        ),
        _RESULTS + "support_score_development_candidate_12.json": (
            _RESULTS + "support_score_candidate_12_local_gate.json",
            "633021bf534670cb78aca392815799d1d1ca60624049fba09fae0796c4122de9",
            "83f39183e68c9d7ebd4c3f8e80e063555cfcb460",
        ),
        _RESULTS + "support_score_development_candidate_13.json": (
            _RESULTS + "support_score_candidate_13_local_gate.json",
            "636ca38fce142e379f2686d2f0be629666eb8221898ea4d1c9e54c22123e0bd6",
            "d950911524962e92e050e6a20734fe20d25c7b2a",
        ),
        _RESULTS + "support_score_development_candidate_14.json": (
            _RESULTS + "support_score_candidate_14_local_gate.json",
            "6f36800dbb453fec0b066fae3ecf72d16f1e5ce87822e53c248257e6b26b9b83",
            "8d5b9cbf297b8f45f209096021360f6c8e37c5bf",
        ),
    },
    "multi-source-coverage-selection": {
        _RESULTS + "multi_source_coverage_development_candidate_1.json": (
            _RESULTS + "multi_source_coverage_candidate_1_local_gate.json",
            "6762a919accee61979507842bd912c9c9921259eeeeaeb0751e905fe63ef4bf6",
            "73fa41087d222833b5025f5406ea3089b3f4519a",
        ),
    },
    "folder-import-lifecycle": {
        _RESULTS + "folder_import_development_candidate_2.json": (
            _RESULTS + "folder_import_candidate_2_local_gate.json",
            "e4fa0230d4d84d4a428dc95e6732e0e0e3ce6c6823884274a70bc65b761f8997",
            "63e34ec0b22c6aee7e7a17426b984ffb205b4188",
        ),
    },
    "github-thread-import-lifecycle": {
        _RESULTS + "github_thread_import_development_candidate_3.json": (
            _RESULTS + "github_thread_import_candidate_3_local_gate.json",
            "577d302250d9572b0fa295e3258d974b7f99f06b2a0f1fdbb34c1f6debb544fb",
            "73242bc085e6a170d459b10324dabc57aed4bc50",
        ),
    },
    "static-showcase": {
        _RESULTS + "static_showcase_development_candidate_5.json": (
            _RESULTS + "static_showcase_candidate_5_local_gate.json",
            "c17d2ebe838ac9d43e58825bd90c07d504cea5850f0d51995718b7827aafd9df",
            "5c719c387addc6a415658727597400cfd1af7846",
        ),
    },
    "cross-platform-delivery": {
        _RESULTS + "cross_platform_delivery_candidate_2.json": (
            _RESULTS + "cross_platform_delivery_candidate_2_local_gate.json",
            "6318d9bf999163917441c65e8085bce3548424b6a7183b4c284e7f9c43b9b2d7",
            "7d0a296ffbbb73863b63ec732608a6e3c0bab35b",
        ),
        _RESULTS + "cross_platform_delivery_candidate_3.json": (
            _RESULTS + "cross_platform_delivery_candidate_3_local_gate.json",
            "a8ce5385fecdbef45660dc809ae8a4a20ed197aec6aa96ab1464877dd66b018d",
            "31f51cd121559654f4e129b96921f2d81e991e6e",
        ),
        _RESULTS + "cross_platform_delivery_candidate_5.json": (
            _RESULTS + "cross_platform_delivery_candidate_5_local_gate.json",
            "20d2ea86f04120b4f27c8ba39ef8e613de2c11acedcb46961284ad56a72f9240",
            "c9af1ed22c5aef64a6b888b494fb27872c7d6ad9",
        ),
        _RESULTS + "cross_platform_delivery_candidate_6.json": (
            _RESULTS + "cross_platform_delivery_candidate_6_local_gate.json",
            "21e0c9f5752030fc7e3a94bedb45c2868d803c50b93d67866e2af2bd554dd593",
            "271a788b51ce1e5a6072362d7dea0a13e1c31fad",
        ),
        _RESULTS + "cross_platform_delivery_candidate_7.json": (
            _RESULTS + "cross_platform_delivery_candidate_7_local_gate.json",
            "7d047d5d3a360450c046adf90fcd3165c6bab4a5417c7e2a71ff70b06dba9ed1",
            "569451d7f56d5606e8b000f15e34e04b87cb62a4",
        ),
        _RESULTS + "cross_platform_delivery_candidate_8.json": (
            _RESULTS + "cross_platform_delivery_candidate_8_local_gate.json",
            "99d534741eebb1a10121d753953d420edde8e6381ec454e660a90dba3a338c7d",
            "04f246f815f0c80f74a3aa20caf5af3a31ff5c92",
        ),
        _RESULTS + "cross_platform_delivery_candidate_9.json": (
            _RESULTS + "cross_platform_delivery_candidate_9_local_gate.json",
            "4e5ec614f020503eba1639fe6807f93ff6386024636912c9742775daa7c1e406",
            "9779fb4624e21575de8b0de359cc199cecb88589",
        ),
    },
    "release-candidate-delivery": {
        _RESULTS + "release_candidate_development_candidate_1.json": (
            _RESULTS + "release_candidate_candidate_1_local_gates.json",
            "94924487672989ea216a1728caf77f94bfeb094cae496c78f832ec2838f65d9b",
            "3980b47fec0a8abc001c4df740b6924d3f32223a",
        ),
        _RESULTS + "release_candidate_development_candidate_2.json": (
            _RESULTS + "release_candidate_candidate_2_local_gates.json",
            "fd6c6f2475848b79cf7c89a212d0f5ba2e0c52846cec7e572cc406dd3d8de092",
            "926832e28503b69d83c2ca760d3ad0065615b5a8",
        ),
        _RESULTS + "release_candidate_development_candidate_3.json": (
            _RESULTS + "release_candidate_candidate_3_local_gates.json",
            "54e6bbd11d952c7f18afb7f4d637c5285daf6f4ac7596f649eeb5710af08bfcc",
            "40cabe1dc5c3869ce67da60d3ce8bdbf883bc1a6",
        ),
        _RESULTS + "release_candidate_development_candidate_5.json": (
            _RESULTS + "release_candidate_candidate_5_local_gates.json",
            "0eb8fe924537f6fc8ecdf6657e117abda3771705678364835b7e327fea43a808",
            "88a0b101aad708190331d42ac1557e1cd44be114",
        ),
        _RESULTS + "release_candidate_development_candidate_6.json": (
            _RESULTS + "release_candidate_candidate_6_local_gates.json",
            "37b1df73117f5e9260861a76abc79af6f614fba4399cab4ba6a3d1c567ce394d",
            "4440b05e4200ceb939d5668f7a8dd73a77a69287",
        ),
        _RESULTS + "release_candidate_development_candidate_7.json": (
            _RESULTS + "release_candidate_candidate_7_local_gates.json",
            "ce550b6ff8cccb17f4fb3bf2dff758d8755c4d05221ccd275b1b030c34e961f3",
            "249a89b36518452d64a56d902c41c81027976c1b",
        ),
        _RESULTS + "release_candidate_development_candidate_8.json": (
            _RESULTS + "release_candidate_candidate_8_local_gates.json",
            "65486449522da88a80a734f30af81cf8881cdd41b12766440f9a50aac7d0930e",
            "4c6f8e64e4dcda725966d7982ad3f2630814432f",
        ),
        _RESULTS + "release_candidate_development_candidate_9.json": (
            _RESULTS + "release_candidate_candidate_9_local_gates.json",
            "a624fd50375639da4b4727fdac656e3d73074a1c3d845f0204356da0bf1ea48c",
            "53caa517ac4e11079767ee26633f8f3be9f55d0d",
        ),
        _RESULTS + "release_candidate_development_candidate_11.json": (
            _RESULTS + "release_candidate_candidate_11_local_gates.json",
            "c9b61fbc81951aa87944e2866a2693c97838b40b26fea3baed7ff92383f539a3",
            "ee298f07a66c4889b999752fb07555993c16716c",
        ),
        _RESULTS + "release_candidate_development_candidate_12.json": (
            _RESULTS + "release_candidate_candidate_12_local_gates.json",
            "2bc1b23b21613ec3ca20d66ad78c829181106422964b17ff4f5e2e6b718fdaa6",
            "f16d2735009519929e82e250668a72877986aed1",
        ),
        _RESULTS + "release_candidate_development_candidate_13.json": (
            _RESULTS + "release_candidate_candidate_13_local_gates.json",
            "1ad1a1e7036390c85ad23f9498a7839e50ee3161acc7463651fe2ef362addadf",
            "94db14e619cb912910321b7a10cfe023d28da2fb",
        ),
    },
}
REQUIRED_LINUX_EVIDENCE = {
    "cross-platform-delivery": {
        _RESULTS + "cross_platform_delivery_candidate_3.json": (
            _RESULTS + "cross_platform_delivery_candidate_3_linux_gate.json",
            "efd898c2a3c9eb4807b0610bf5c2979ccc5a86b48fd81735f4b72a6ce6360824",
            "79188650e953c6c183b631fd41432e795bde0eaa",
        ),
        _RESULTS + "cross_platform_delivery_candidate_4.json": (
            _RESULTS + "cross_platform_delivery_candidate_4_linux_gate.json",
            "f09affaa4dd7633e10cd37752f9ce1ed7e7258a12bc747cda2bc20c805beadc1",
            "1ed10462e0585be8fdafa34e6c42de6e2a0ba784",
        ),
        _RESULTS + "cross_platform_delivery_candidate_5.json": (
            _RESULTS + "cross_platform_delivery_candidate_5_linux_gate.json",
            "d58dacc1bd4a34f6230e3045db058e4f2542e671d1f017311c02147ee76e3a8f",
            "c9af1ed22c5aef64a6b888b494fb27872c7d6ad9",
        ),
        _RESULTS + "cross_platform_delivery_candidate_6.json": (
            _RESULTS + "cross_platform_delivery_candidate_6_linux_gate.json",
            "ef31991c6efc21cbdeec6ab656937961e0df3bfbcecaf4b92de6f97c8b59575f",
            "271a788b51ce1e5a6072362d7dea0a13e1c31fad",
        ),
        _RESULTS + "cross_platform_delivery_candidate_7.json": (
            _RESULTS + "cross_platform_delivery_candidate_7_linux_gate.json",
            "263ff1e5ec752d5bb2f18372ee70c6dd3e1f5a6d80ee7719cc15d5ab092a5bc6",
            "569451d7f56d5606e8b000f15e34e04b87cb62a4",
        ),
        _RESULTS + "cross_platform_delivery_candidate_8.json": (
            _RESULTS + "cross_platform_delivery_candidate_8_linux_gate.json",
            "14553130cb03afaa220a476f5be2d1b170c89afb9fcf74af4730c93a6cae65b6",
            "04f246f815f0c80f74a3aa20caf5af3a31ff5c92",
        ),
        _RESULTS + "cross_platform_delivery_candidate_9.json": (
            _RESULTS + "cross_platform_delivery_candidate_9_linux_gate.json",
            "a724e17659ecfd5c8a5057cc9f50ed03abd5d6624934dc5920f016905483ed22",
            "9779fb4624e21575de8b0de359cc199cecb88589",
        ),
    },
}
LINUX_EVIDENCE_CONTRACTS = {
    _RESULTS + "cross_platform_delivery_candidate_3_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 75,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 536,
            "skipped": 2,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_4_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 79,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 542,
            "skipped": 2,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_5_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 81,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 542,
            "skipped": 2,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_6_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 84,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 545,
            "skipped": 2,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_7_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 87,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 554,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        },
        "bound_artifacts": True,
    },
    _RESULTS + "cross_platform_delivery_candidate_8_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 90,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 555,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        },
        "bound_artifacts": True,
    },
    _RESULTS + "cross_platform_delivery_candidate_9_linux_gate.json": {
        "runtime": {
            "virtualization": "Lima 2.2.0 local VM",
            "distribution": "Debian GNU/Linux 12",
            "kernel": "Linux 6.1.0-50-cloud-arm64",
            "architecture": "aarch64",
            "implementation": "CPython",
            "python": "3.11.2",
            "hosted_runner": False,
        },
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 93,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 556,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        },
        "bound_artifacts": True,
        "clean_sdist": True,
    },
}
FINAL_ACCEPTANCE_REGISTRY_COUNTS = {
    "exact-symbol-routing.learn-claude-code": {
        "suite_count": 12,
        "experiment_count": 1,
        "evidence_count": 20,
        "qa_case_count": 121,
    },
    "support-score.learn-claude-code": {
        "suite_count": 12,
        "experiment_count": 2,
        "evidence_count": 47,
        "qa_case_count": 121,
    },
    "multi-source-coverage-selection": {
        "suite_count": 12,
        "experiment_count": 2,
        "evidence_count": 49,
        "qa_case_count": 121,
    },
    "folder-import-lifecycle": {
        "suite_count": 12,
        "experiment_count": 3,
        "evidence_count": 53,
        "qa_case_count": 121,
    },
    "github-thread-import-lifecycle": {
        "suite_count": 12,
        "experiment_count": 5,
        "evidence_count": 62,
        "qa_case_count": 121,
    },
    "static-showcase": {
        "suite_count": 12,
        "experiment_count": 6,
        "evidence_count": 71,
        "qa_case_count": 121,
    },
    "cross-platform-delivery": {
        "suite_count": 12,
        "experiment_count": 7,
        "evidence_count": 93,
        "qa_case_count": 121,
    },
}
DEVELOPMENT_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "source_manifest",
    "source_repository",
    "development",
    "baseline_evidence",
    "confirmation",
    "runs",
    "gates",
    "passed",
}
MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "development",
    "confirmation",
    "runs",
    "gates",
    "passed",
}
CROSS_PLATFORM_DEVELOPMENT_EVIDENCE_KEYS = MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS | {"runtime"}
RELEASE_CANDIDATE_DEVELOPMENT_EVIDENCE_KEYS = MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS | {"holdout"}
CROSS_PLATFORM_DEVELOPMENT_RUNTIME_CONTRACTS = {
    _RESULTS + "cross_platform_delivery_candidate_3.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_4.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_5.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_6.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_7.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_8.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
    _RESULTS + "cross_platform_delivery_candidate_9.json": {
        "implementation": "CPython",
        "python": "3.11.15",
        "system": "Darwin",
        "machine": "arm64",
    },
}
LOCAL_GATE_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "development_evidence",
    "local_gate",
    "confirmation",
    "passed",
}
REGRESSION_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "development_evidence",
    "regression",
    "root_cause",
    "confirmation",
    "passed",
}
FINAL_EXPERIMENT_GATE_KEYS = {
    "exact-symbol-routing.learn-claude-code": {
        "answer_accuracy_at_least_90",
        "citation_grounding_accuracy",
        "confirmation_not_run",
        "deferred_abstention_gap_visible",
        "deterministic_replay",
        "exact_symbol_answer_accuracy",
        "fact_selection_accuracy",
        "multi_source_coverage",
        "page_route_recall_at_3",
        "repository_path_isolation_accuracy",
        "source_recall_at_3",
        "structural_benchmark",
    },
    "support-score.learn-claude-code": {
        "abstention_accuracy",
        "answer_accuracy",
        "citation_grounding_accuracy",
        "clean_source_worktree_after_run",
        "clean_worktree_after_run",
        "confirmation_not_run",
        "coverage",
        "deterministic_replay",
        "fact_selection_accuracy",
        "multi_source_coverage",
        "no_failed_cases",
        "page_route_recall_at_3",
        "per_case_support",
        "repository_path_isolation_accuracy",
        "risk",
        "selective_accuracy",
        "source_recall_at_3",
        "stable_memoryforge_commit",
        "stable_source_commit",
        "structural_benchmark",
        "unsupported_question_abstains",
    },
    "multi-source-coverage-selection": {
        "selection_accuracy",
        "source_coverage_accuracy",
        "term_coverage_accuracy",
        "single_source_rank_preservation",
        "duplicate_source_rate",
        "deterministic_replay",
        "selector_supports_required_sources",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
        "confirmation_not_run",
    },
    "folder-import-lifecycle": {
        "pass_rate",
        "failed_cases",
        "deterministic_replay",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
        "confirmation_not_run",
    },
    "github-thread-import-lifecycle": {
        "pass_rate",
        "failed_cases",
        "deterministic_replay",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
        "confirmation_not_run",
    },
    "static-showcase": {
        "pass_rate",
        "failed_cases",
        "required_sections",
        "local_detail_leaks",
        "workspace_mutations",
        "deterministic_replay",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
        "confirmation_not_run",
    },
    "cross-platform-delivery": {
        "pass_rate",
        "failed_cases",
        "direct_platform_imports",
        "windows_lock_offset",
        "windows_lock_bytes",
        "local_smoke",
        "deterministic_replay",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
        "confirmation_not_run",
    },
    "release-candidate-delivery": {
        "pass_rate",
        "failed_cases",
        "reproducible_artifacts",
        "private_detail_leaks",
        "confirmation_not_run",
        "holdout_not_run",
        "deterministic_replay",
        "stable_memoryforge_commit",
        "clean_worktree_after_run",
    },
}
LOCAL_GATE_KEYS = {
    "command",
    "ruff_check",
    "ruff_format",
    "strict_mypy",
    "registry_validation",
    "dependency_check",
    "pytest",
    "wheel_clean_room",
    "sdist_clean_room",
    "pip_check",
    "cli_version_smoke",
}
CROSS_PLATFORM_MAC_RUNTIME = {
    "system": "Darwin",
    "machine": "arm64",
    "implementation": "CPython",
    "python": "3.11.15",
    "hosted_runner": False,
}
CROSS_PLATFORM_MAC_GATE_CONTRACTS = {
    _RESULTS + "cross_platform_delivery_candidate_5_local_gate.json": {
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 81,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 544,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_6_local_gate.json": {
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 84,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 547,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_7_local_gate.json": {
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 87,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 557,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_8_local_gate.json": {
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 90,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 558,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
    _RESULTS + "cross_platform_delivery_candidate_9_local_gate.json": {
        "registry_validation": {
            "suite_count": 12,
            "experiment_count": 7,
            "evidence_count": 93,
            "qa_case_count": 121,
        },
        "pytest": {
            "passed": 559,
            "failed": 0,
            "coverage_percent": 88,
        },
    },
}
MULTI_SOURCE_SUPPORT_REGRESSION = (
    _RESULTS + "support_score_multi_source_coverage_regression.json",
    "631d6aace75de30fa7c68badd8f040163f8480db7b7a40a1eb60eae5fabc0b88",
    "1fb2a1263f82ac9720b49543d177345567505c6d",
    "1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96",
)
MULTI_SOURCE_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "79c41bc5fdb08de18351546f7869b8083da3a1b2",
    "license": "MIT",
    "source_paths": [
        "src/memoryforge/query.py",
        "demo/evaluation/multi_source_coverage_development.json",
        "demo/evaluation/multi_source_coverage_confirmation.json",
    ],
}
FOLDER_IMPORT_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "9242478a7108e001bf6fafbd5567a01718f51716",
    "license": "MIT",
    "source_paths": [
        "tests/test_folder_import.py",
        "demo/evaluation/folder_import_development.json",
        "demo/evaluation/folder_import_confirmation.json",
    ],
}
GITHUB_THREAD_IMPORT_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "271ec3490aa6cd14120913e7fa93f259bd9999fa",
    "license": "MIT",
    "source_paths": [
        "tests/test_github_thread_import.py",
        "demo/evaluation/github_thread_import_development.json",
        "demo/evaluation/github_thread_import_confirmation.json",
    ],
}
STATIC_SHOWCASE_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "cf8a47eb2e62b0aff7bf5e056efd848902df3e06",
    "license": "MIT",
    "source_paths": [
        "tests/test_showcase.py",
        "demo/evaluation/static_showcase_development.json",
        "demo/evaluation/static_showcase_confirmation.json",
    ],
}
CROSS_PLATFORM_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "bef6c7e35e9d8e282d2b3b0e0c4b3874a12f9e8a",
    "license": "MIT",
    "source_paths": [
        "tests/test_cross_platform_delivery.py",
        "demo/evaluation/cross_platform_delivery_development.json",
        "demo/evaluation/cross_platform_delivery_confirmation.json",
    ],
}
RELEASE_CANDIDATE_REPOSITORY = {
    "repository": "still0123/MemoryForge",
    "remote_url": "https://github.com/still0123/MemoryForge.git",
    "commit": "4d834679ec61355e285fb36a0cceef8f489a9083",
    "license": "MIT",
    "source_paths": [
        "demo/run_release_candidate_benchmark.py",
        "demo/evaluation/release_candidate_development.json",
        "demo/evaluation/release_candidate_confirmation.json",
        "demo/evaluation/release_candidate_holdout.json",
        "tests/test_release_candidate_contract.py",
    ],
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    summary = validate_registry(args.registry.resolve())
    print(json.dumps(summary, indent=2, sort_keys=True))


def validate_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, object]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    return validate_registry_payload(registry)


def validate_registry_payload(registry: dict[str, Any]) -> dict[str, object]:
    if type(registry.get("schema_version")) is not int or registry.get("schema_version") != 1:
        raise ValueError("unsupported benchmark registry schema")
    if registry.get("package_release_target") != "0.3.0":
        raise ValueError("benchmark registry must target package 0.3.0")
    suites = registry.get("suites")
    if not isinstance(suites, list) or not suites:
        raise ValueError("benchmark registry requires suites")
    experiments = registry.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("benchmark registry requires experiments")
    _validate_historical_review_scopes()

    suite_ids = [
        artifact.get("suite_id") for collection in (suites, experiments) for artifact in collection
    ]
    if len(suite_ids) != len(set(suite_ids)):
        raise ValueError("benchmark suite IDs must be unique")

    taxonomy_evidence = registry.get("taxonomy_evidence")
    if not isinstance(taxonomy_evidence, dict):
        raise ValueError("benchmark registry requires taxonomy evidence")
    _validate_artifact(taxonomy_evidence, "benchmark-taxonomy")
    if COMMIT.fullmatch(str(taxonomy_evidence.get("evaluation_commit"))) is None:
        raise ValueError("benchmark taxonomy Commit is invalid")
    taxonomy_payload = json.loads(
        (REPO_ROOT / taxonomy_evidence["path"]).read_text(encoding="utf-8")
    )
    if (
        taxonomy_payload.get("evaluation_commit") != taxonomy_evidence["evaluation_commit"]
        or taxonomy_payload.get("evaluation_worktree_dirty") is not False
        or taxonomy_payload.get("evaluated_case_count") != taxonomy_evidence["evaluated_case_count"]
        or taxonomy_payload.get("unrun_frozen_split", {}).get("status")
        != taxonomy_evidence["frozen_confirmation_status"]
    ):
        raise ValueError("benchmark taxonomy evidence contract failed")

    qa_case_count = 0
    qa_case_types: set[str] = set()
    registered_cases: dict[tuple[str, str], set[str]] = {}
    suite_types: set[str] = set()
    experiment_evidence_count = _validate_experiments(experiments)
    evidence_count = 1 + experiment_evidence_count
    for suite in suites:
        suite_id = suite.get("suite_id")
        if not isinstance(suite_id, str) or SUITE_ID.fullmatch(suite_id) is None:
            raise ValueError(f"invalid suite_id: {suite_id}")
        if re.search(r"(?:^|[._-])v\d", suite_id):
            raise ValueError(f"suite_id contains a package-like version: {suite_id}")
        revision = suite.get("suite_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"invalid suite_revision: {suite_id}")
        suite_type = suite.get("suite_type")
        if suite_type not in SUITE_TYPES:
            raise ValueError(f"invalid suite_type: {suite_id}")
        suite_types.add(suite_type)
        if suite.get("model_judge") is not False:
            raise ValueError(f"model judge must be disabled: {suite_id}")

        repositories = suite.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise ValueError(f"suite requires repositories: {suite_id}")
        for repository in repositories:
            _validate_repository(suite_id, repository)

        source_manifest = suite.get("source_manifest")
        if source_manifest is not None:
            _validate_artifact(source_manifest, suite_id)

        splits = suite.get("splits")
        if not isinstance(splits, dict) or set(splits) != SPLITS:
            raise ValueError(f"suite must declare all split keys: {suite_id}")
        for split_name, split in splits.items():
            if split is None:
                continue
            _validate_artifact(split, suite_id)
            case_count, case_types, case_ids = _suite_cases(split)
            if case_count != split.get("case_count"):
                raise ValueError(f"case count mismatch: {suite_id}/{split_name}")
            registered_cases[(suite_id, split_name)] = case_ids
            if suite_type in {"document_wiki_qa", "code_wiki_qa"}:
                qa_case_count += case_count
                qa_case_types.update(case_types)

        evidence = suite.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"suite requires generated evidence: {suite_id}")
        for artifact in evidence:
            _validate_artifact(artifact, suite_id)
            if artifact.get("split") not in SPLITS:
                raise ValueError(f"invalid evidence split: {suite_id}")
            evidence_revision = artifact.get("evidence_revision")
            if (
                isinstance(evidence_revision, bool)
                or not isinstance(evidence_revision, int)
                or evidence_revision < 1
            ):
                raise ValueError(f"invalid evidence revision: {suite_id}")
            if COMMIT.fullmatch(str(artifact.get("memoryforge_commit"))) is None:
                raise ValueError(f"invalid evidence Commit: {suite_id}")
            _validate_metrics(suite, artifact)
            evidence_count += 1

    if suite_types != SUITE_TYPES:
        raise ValueError("registry must contain all four benchmark suite types")
    overlay = registry.get("case_type_overlay")
    if not isinstance(overlay, dict):
        raise ValueError("benchmark registry requires a case type overlay")
    _validate_artifact(overlay, "benchmark-case-types")
    overlay_payload = json.loads((REPO_ROOT / overlay["path"]).read_text(encoding="utf-8"))
    mappings = overlay_payload.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("case type overlay requires mappings")
    mapping_keys = {
        (mapping.get("suite_id"), mapping.get("split"), mapping.get("case_id"))
        for mapping in mappings
    }
    if len(mapping_keys) != len(mappings):
        raise ValueError("case type overlay mappings must be unique")
    for mapping in mappings:
        key = (mapping.get("suite_id"), mapping.get("split"))
        if mapping.get("case_id") not in registered_cases.get(key, set()):
            raise ValueError("case type overlay references an unknown case")
        qa_case_types.add(str(mapping.get("case_type")))
    if qa_case_count != registry.get("qa_case_count") or not 100 <= qa_case_count <= 140:
        raise ValueError("registered QA case count must stay within 100-140")
    if qa_case_types != set(registry.get("qa_case_types_present", [])):
        raise ValueError("registered QA case types do not match suite contents")
    if qa_case_types != set(registry.get("qa_case_types_required", [])):
        raise ValueError("registered QA case types do not cover the required taxonomy")
    return {
        "status": "valid",
        "suite_count": len(suites),
        "experiment_count": len(experiments),
        "evidence_count": evidence_count,
        "qa_case_count": qa_case_count,
        "qa_case_types_present": sorted(qa_case_types),
        "suite_types": sorted(suite_types),
    }


def _validate_historical_review_scopes() -> None:
    for path, expected in HISTORICAL_REVIEW_SCOPES.items():
        artifact = {"path": path, "sha256": expected["sha256"]}
        _validate_artifact(artifact, "release-candidate review scope")
        payload = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
        expected_payload = {
            "schema_version": 1,
            "base_commit": expected["base_commit"],
            "source_commit": expected["source_commit"],
            "diff_mode": "merge_base_to_source",
            **{
                key: expected[key]
                for key in expected.get(
                    "scope_fields",
                    (
                        "diff_files",
                        "reviewed_files",
                        "added_lines",
                        "deleted_lines",
                        "changed_lines",
                    ),
                )
            },
        }
        actual_stats = _git_diff_stats(
            str(expected["base_commit"]),
            str(expected["source_commit"]),
        )
        expected_stats = {
            key: expected[key]
            for key in (
                "diff_files",
                "reviewed_files",
                "added_lines",
                "deleted_lines",
                "changed_lines",
            )
        }
        if (
            not _strict_mapping(payload, expected_payload)
            or actual_stats != expected_stats
            or not _git_commit_descends_from(
                str(expected["source_commit"]),
                str(expected["base_commit"]),
            )
        ):
            raise ValueError("release-candidate review scope changed")


def _git_diff_stats(base_commit: str, source_commit: str) -> dict[str, int]:
    output = subprocess.run(
        ["git", "diff", "--numstat", f"{base_commit}...{source_commit}"],
        cwd=SOURCE_GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    diff_files = reviewed_files = added_lines = deleted_lines = 0
    for line in output.splitlines():
        added, deleted, path = line.split("\t", 2)
        diff_files += 1
        if added.isdigit() and deleted.isdigit():
            file_added = int(added)
            file_deleted = int(deleted)
            added_lines += file_added
            deleted_lines += file_deleted
            if _historical_review_path_included(path, file_added, file_deleted):
                reviewed_files += 1
    return {
        "diff_files": diff_files,
        "reviewed_files": reviewed_files,
        "added_lines": added_lines,
        "deleted_lines": deleted_lines,
        "changed_lines": added_lines + deleted_lines,
    }


def _historical_review_path_included(path: str, added: int, deleted: int) -> bool:
    parts = path.split("/")
    return added + deleted > 0 and "tests" not in parts and not path.endswith((".gz", ".md"))


def _validate_experiments(experiments: list[dict[str, Any]]) -> int:
    evidence_count = 0
    for experiment in experiments:
        suite_id = str(experiment.get("suite_id"))
        if SUITE_ID.fullmatch(suite_id) is None or re.search(r"(?:^|[._-])v\d", suite_id):
            raise ValueError(f"invalid experiment suite_id: {suite_id}")
        revision = experiment.get("suite_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"invalid experiment suite_revision: {suite_id}")
        if experiment.get("suite_type") not in SUITE_TYPES:
            raise ValueError(f"invalid experiment suite_type: {suite_id}")
        if experiment.get("model_judge") is not False:
            raise ValueError(f"experiment model judge must be disabled: {suite_id}")
        if suite_id == "multi-source-coverage-selection":
            _validate_multi_source_experiment_metadata(experiment)
        elif suite_id == "folder-import-lifecycle":
            _validate_folder_import_experiment_metadata(experiment)
        elif suite_id == "github-thread-import-lifecycle":
            _validate_github_thread_experiment_metadata(experiment)
        elif suite_id == "static-showcase":
            _validate_static_showcase_experiment_metadata(experiment)
        elif suite_id == "cross-platform-delivery":
            _validate_cross_platform_experiment_metadata(experiment)
        elif suite_id == "release-candidate-delivery":
            _validate_release_candidate_experiment_metadata(experiment)

        repositories = experiment.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise ValueError(f"experiment requires repositories: {suite_id}")
        for repository in repositories:
            _validate_repository(suite_id, repository)
        source_manifest = experiment.get("source_manifest")
        if suite_id in {
            "multi-source-coverage-selection",
            "folder-import-lifecycle",
            "github-thread-import-lifecycle",
            "static-showcase",
            "cross-platform-delivery",
            "release-candidate-delivery",
        }:
            if "source_manifest" in experiment:
                raise ValueError(f"component experiment cannot declare source manifest: {suite_id}")
        else:
            if not isinstance(source_manifest, dict):
                raise ValueError(f"experiment requires a source manifest: {suite_id}")
            _validate_artifact(source_manifest, suite_id)

        splits = experiment.get("splits")
        if not isinstance(splits, dict) or set(splits) != SPLITS:
            raise ValueError(f"experiment must declare all split keys: {suite_id}")
        development = splits["development"]
        confirmation = splits["confirmation"]
        if not isinstance(development, dict) or not isinstance(confirmation, dict):
            raise ValueError(f"experiment requires frozen development and confirmation: {suite_id}")
        _validate_artifact(development, suite_id)
        _validate_artifact(confirmation, suite_id)
        development_count, _, _ = _suite_cases(development)
        release_candidate = suite_id == "release-candidate-delivery"
        if release_candidate:
            _validate_release_split_manifest(development, split_name="development")
            confirmation_count = _release_confirmation_case_count(confirmation, suite_id)
            holdout = splits["holdout"]
            if not isinstance(holdout, dict):
                raise ValueError(f"release experiment requires frozen holdout: {suite_id}")
            _validate_artifact(holdout, suite_id)
            _validate_release_split_manifest(holdout, split_name="holdout")
            holdout_count, _, _ = _suite_cases(holdout)
            holdout_valid = (
                type(holdout.get("case_count")) is int
                and holdout_count == holdout.get("case_count")
                and holdout.get("status") == "not_run"
            )
        else:
            confirmation_count, _, _ = _suite_cases(confirmation)
            holdout_valid = splits["holdout"] is None
        if (
            type(development.get("case_count")) is not int
            or type(confirmation.get("case_count")) is not int
            or development_count != development.get("case_count")
            or confirmation_count != confirmation.get("case_count")
            or confirmation.get("status") != "not_run"
            or not holdout_valid
        ):
            raise ValueError(f"experiment split contract failed: {suite_id}")

        expected_metrics = experiment.get("expected_metrics")
        if not isinstance(expected_metrics, dict) or not isinstance(
            expected_metrics.get("development"), dict
        ):
            raise ValueError(f"experiment expected metrics missing: {suite_id}")
        evidence = experiment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"experiment requires generated evidence: {suite_id}")
        required_evidence = REQUIRED_EXPERIMENT_EVIDENCE.get(suite_id)
        evidence_paths = [artifact.get("path") for artifact in evidence]
        if (
            required_evidence is None
            or len(evidence_paths) != len(set(evidence_paths))
            or set(evidence_paths) != set(required_evidence)
        ):
            raise ValueError(f"experiment Evidence history is incomplete: {suite_id}")
        required_statuses = experiment.get("required_evidence_statuses")
        allowed_statuses = {
            "rejected",
            "development_passed_regression_failed",
            "development_passed_review_failed",
            "development_passed_gate_pending",
            "local_gates_passed_review_pending",
            "accepted_development_superseded",
            "accepted_development",
        }
        expected_statuses = {identity[1] for identity in required_evidence.values()}
        if (
            not isinstance(required_statuses, list)
            or not required_statuses
            or any(status not in allowed_statuses for status in required_statuses)
            or len(required_statuses) != len(set(required_statuses))
            or set(required_statuses) != expected_statuses
        ):
            raise ValueError(f"invalid required experiment Evidence statuses: {suite_id}")
        revisions: set[int] = set()
        statuses: set[str] = set()
        required_regression = REQUIRED_REGRESSION_EVIDENCE.get(suite_id)
        required_review = REQUIRED_REVIEW_EVIDENCE.get(suite_id)
        required_acceptance = REQUIRED_ACCEPTANCE_EVIDENCE.get(suite_id)
        required_linux = REQUIRED_LINUX_EVIDENCE.get(suite_id, {})
        if required_regression is None or required_review is None or required_acceptance is None:
            raise ValueError(f"experiment acceptance Evidence history is missing: {suite_id}")
        for artifact in evidence:
            _validate_artifact(artifact, suite_id)
            if artifact.get("split") != "development":
                raise ValueError(f"experiment Evidence split changed: {suite_id}")
            evidence_revision = artifact.get("evidence_revision")
            if (
                isinstance(evidence_revision, bool)
                or not isinstance(evidence_revision, int)
                or evidence_revision < 1
                or evidence_revision in revisions
            ):
                raise ValueError(f"invalid experiment evidence revision: {suite_id}")
            revisions.add(evidence_revision)
            status = str(artifact.get("status"))
            if status not in allowed_statuses:
                raise ValueError(f"invalid experiment evidence status: {suite_id}")
            path = str(artifact.get("path"))
            commit = str(artifact.get("memoryforge_commit"))
            if required_evidence.get(path) != (
                evidence_revision,
                status,
                artifact.get("sha256"),
                commit,
            ):
                raise ValueError(f"experiment Evidence identity changed: {suite_id}")
            expected_regression = required_regression.get(path)
            regression_evidence = artifact.get("regression_evidence")
            if expected_regression is None:
                if regression_evidence is not None:
                    raise ValueError(f"unexpected experiment regression Evidence: {suite_id}")
            elif (
                not isinstance(regression_evidence, dict)
                or (
                    regression_evidence.get("path"),
                    regression_evidence.get("sha256"),
                    regression_evidence.get("memoryforge_commit"),
                )
                != expected_regression
            ):
                raise ValueError(
                    f"experiment regression Evidence history is incomplete: {suite_id}"
                )
            expected_review = required_review.get(path)
            review_evidence = artifact.get("review_evidence")
            if expected_review is None:
                if review_evidence is not None:
                    raise ValueError(f"unexpected experiment review Evidence: {suite_id}")
            elif (
                not isinstance(review_evidence, dict)
                or (
                    review_evidence.get("path"),
                    review_evidence.get("sha256"),
                    review_evidence.get("memoryforge_commit"),
                )
                != expected_review
            ):
                raise ValueError(f"experiment review Evidence history is incomplete: {suite_id}")
            expected_acceptance = required_acceptance.get(path)
            acceptance_evidence = artifact.get("acceptance_evidence")
            if expected_acceptance is None:
                if acceptance_evidence is not None:
                    raise ValueError(f"unexpected experiment acceptance Evidence: {suite_id}")
            elif (
                not isinstance(acceptance_evidence, dict)
                or (
                    acceptance_evidence.get("path"),
                    acceptance_evidence.get("sha256"),
                    acceptance_evidence.get("memoryforge_commit"),
                )
                != expected_acceptance
            ):
                raise ValueError(
                    f"experiment acceptance Evidence history is incomplete: {suite_id}"
                )
            if suite_id == "release-candidate-delivery" and not _release_review_state_valid(
                status,
                acceptance_evidence,
                review_evidence,
            ):
                raise ValueError("release candidate review state is inconsistent")
            expected_linux = required_linux.get(path)
            linux_evidence = artifact.get("linux_evidence")
            if expected_linux is None:
                if linux_evidence is not None:
                    raise ValueError(f"unexpected Linux Evidence: {suite_id}")
            elif (
                not isinstance(linux_evidence, dict)
                or (
                    linux_evidence.get("path"),
                    linux_evidence.get("sha256"),
                    linux_evidence.get("memoryforge_commit"),
                )
                != expected_linux
            ):
                raise ValueError(f"Linux Evidence history is incomplete: {suite_id}")
            references = {
                "regression_evidence": (regression_evidence, expected_regression),
                "review_evidence": (review_evidence, expected_review),
                "acceptance_evidence": (acceptance_evidence, expected_acceptance),
                "linux_evidence": (linux_evidence, expected_linux),
            }
            expected_artifact_keys = {
                "split",
                "evidence_revision",
                "status",
                "path",
                "sha256",
                "memoryforge_commit",
                "passed",
                *(name for name, (_, expected) in references.items() if expected is not None),
            }
            if (
                set(artifact) != expected_artifact_keys
                or _payload_private_detail_leaks(artifact) != 0
                or any(
                    expected is not None and not _evidence_reference_valid(reference)
                    for reference, expected in references.values()
                )
            ):
                raise ValueError(f"experiment Evidence schema changed: {suite_id}")
            statuses.add(status)
            if COMMIT.fullmatch(commit) is None:
                raise ValueError(f"invalid experiment Evidence Commit: {suite_id}")
            payload = cast(
                dict[str, Any],
                json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
            )
            _validate_experiment_payload(
                experiment,
                artifact,
                payload,
                development,
                confirmation,
            )
            if expected_regression is not None:
                evidence_count += _validate_regression_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            if expected_review is not None:
                evidence_count += _validate_review_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            if expected_acceptance is not None:
                evidence_count += _validate_acceptance_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            if expected_linux is not None:
                evidence_count += _validate_linux_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            evidence_count += 1
        if statuses != set(required_statuses):
            raise ValueError(f"experiment must retain rejected and accepted Evidence: {suite_id}")
    return evidence_count


def _validate_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    if experiment["suite_id"] == "release-candidate-delivery":
        _validate_release_candidate_experiment_payload(
            experiment,
            artifact,
            payload,
            development,
            confirmation,
        )
        return
    if experiment["suite_id"] == "multi-source-coverage-selection":
        _validate_multi_source_experiment_payload(
            experiment,
            artifact,
            payload,
            development,
            confirmation,
        )
        return
    if experiment["suite_id"] in {
        "folder-import-lifecycle",
        "github-thread-import-lifecycle",
        "cross-platform-delivery",
    }:
        _validate_pytest_component_experiment_payload(
            experiment,
            artifact,
            payload,
            development,
            confirmation,
        )
        return
    if experiment["suite_id"] == "static-showcase":
        _validate_static_showcase_experiment_payload(
            experiment,
            artifact,
            payload,
            development,
            confirmation,
        )
        return
    repository = experiment["repositories"][0]
    if (
        payload.get("schema_version") != 1
        or set(payload) != DEVELOPMENT_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or payload.get("source_manifest") != experiment["source_manifest"]
        or payload.get("source_repository", {}).get("commit") != repository["commit"]
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
    ):
        raise ValueError(f"experiment Evidence contract failed: {experiment['suite_id']}")
    if artifact["status"] == "rejected":
        if artifact["passed"] is not False:
            raise ValueError("rejected experiment Evidence must fail")
        return
    if artifact["passed"] is not True:
        raise ValueError("accepted experiment Evidence must pass")
    if artifact["status"] == "accepted_development":
        gates = payload.get("gates")
        required_gates = FINAL_EXPERIMENT_GATE_KEYS.get(str(experiment["suite_id"]))
        if (
            required_gates is None
            or not isinstance(gates, dict)
            or set(gates) != required_gates
            or not all(value is True for value in gates.values())
        ):
            raise ValueError(f"experiment Evidence gates failed: {experiment['suite_id']}")
        if experiment["suite_id"] == "support-score.learn-claude-code":
            source_manifest = json.loads(
                (REPO_ROOT / experiment["source_manifest"]["path"]).read_text(encoding="utf-8")
            )
            threshold = source_manifest.get("support_threshold")
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or payload.get("development", {}).get("support_threshold") != threshold
            ):
                raise ValueError("support-score threshold does not match frozen manifest")
            if not _support_runs_are_deterministic(payload.get("runs")):
                raise ValueError("support-score deterministic replay Evidence is invalid")
            cases = payload.get("development", {}).get("evaluation", {}).get("cases")
            if (
                not isinstance(cases, list)
                or len(cases) != development["case_count"]
                or not _support_case_identities_match(cases, development)
            ):
                raise ValueError("support-score case Evidence is incomplete")
            for case in cases:
                if not _support_benchmark_module()._valid_case_support(
                    case,
                    float(threshold),
                ):
                    raise ValueError("support-score case Evidence contract failed")
    actual = payload["development"]["metrics"]
    for metric, expected in experiment["expected_metrics"]["development"].items():
        if actual.get(metric) != expected:
            raise ValueError(
                f"experiment metric mismatch: {experiment['suite_id']}/development/{metric}"
            )


def _validate_multi_source_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    evaluation = payload.get("development", {}).get("evaluation", {})
    cases = evaluation.get("cases", {}) if isinstance(evaluation, dict) else {}
    frozen = json.loads((REPO_ROOT / development["path"]).read_text(encoding="utf-8"))
    frozen_ids = [case.get("id") for case in frozen.get("cases", [])]
    runs = payload.get("runs")
    evaluation_sha256 = hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if (
        payload.get("schema_version") != 1
        or set(payload) != MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or payload.get("development", {}).get("case_count") != development["case_count"]
        or not isinstance(evaluation, dict)
        or set(evaluation) != {"case_count", "metrics", "cases"}
        or evaluation.get("case_count") != development["case_count"]
        or not isinstance(cases, list)
        or [case.get("id") for case in cases] != frozen_ids
        or len(set(frozen_ids)) != len(frozen_ids)
        or not isinstance(runs, list)
        or len(runs) != 2
        or any(
            not isinstance(run, dict)
            or set(run) != {"name", "evaluation_sha256"}
            or SHA256.fullmatch(str(run.get("evaluation_sha256"))) is None
            for run in runs
        )
        or [run["name"] for run in runs] != ["first", "second"]
        or any(run["evaluation_sha256"] != evaluation_sha256 for run in runs)
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
    ):
        raise ValueError("multi-source experiment Evidence contract failed")
    if artifact["status"] == "rejected":
        if artifact["passed"] is not False or all(payload["gates"].values()):
            raise ValueError("rejected multi-source Evidence must fail")
        return
    gates = payload.get("gates")
    if (
        artifact["status"] != "accepted_development"
        or artifact["passed"] is not True
        or not isinstance(gates, dict)
        or set(gates) != FINAL_EXPERIMENT_GATE_KEYS[experiment["suite_id"]]
        or not all(value is True for value in gates.values())
    ):
        raise ValueError("accepted multi-source Evidence gates failed")
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("multi-source experiment metrics missing")
    for metric, expected in experiment["expected_metrics"]["development"].items():
        if metrics.get(metric) != expected:
            raise ValueError(f"multi-source experiment metric mismatch: {metric}")


def _validate_release_candidate_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    holdout = experiment["splits"]["holdout"]
    evaluation = payload.get("development", {}).get("evaluation", {})
    cases = evaluation.get("cases") if isinstance(evaluation, dict) else None
    frozen = json.loads((REPO_ROOT / development["path"]).read_text(encoding="utf-8"))
    frozen_ids = [case.get("id") for case in frozen.get("cases", [])]
    runs = payload.get("runs")
    gates = payload.get("gates")
    expected_payload_keys = RELEASE_CANDIDATE_DEVELOPMENT_EVIDENCE_KEYS | (
        {"release_artifacts"} if artifact["evidence_revision"] >= 5 else set()
    )
    evaluation_sha256 = hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if (
        set(payload) != expected_payload_keys
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or set(payload.get("development", {})) != {"path", "sha256", "case_count", "evaluation"}
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or type(payload.get("development", {}).get("case_count")) is not int
        or payload.get("development", {}).get("case_count") != development["case_count"]
        or not isinstance(evaluation, dict)
        or set(evaluation) != {"case_count", "metrics", "cases"}
        or type(evaluation.get("case_count")) is not int
        or evaluation.get("case_count") != development["case_count"]
        or not isinstance(cases, list)
        or [case.get("id") for case in cases if isinstance(case, dict)] != frozen_ids
        or any(
            not isinstance(case, dict) or set(case) != {"id", "status", "error_classification"}
            for case in cases
        )
        or not isinstance(runs, list)
        or len(runs) != 2
        or [run.get("name") for run in runs if isinstance(run, dict)] != ["first", "second"]
        or any(
            not isinstance(run, dict)
            or set(run) != {"name", "evaluation_sha256"}
            or run.get("evaluation_sha256") != evaluation_sha256
            for run in runs
        )
        or payload.get("confirmation")
        != {
            "path": confirmation["path"],
            "sha256": confirmation["sha256"],
            "status": "not_run",
        }
        or not isinstance(holdout, dict)
        or payload.get("holdout")
        != {
            "path": holdout["path"],
            "sha256": holdout["sha256"],
            "status": "not_run",
        }
        or not isinstance(gates, dict)
        or set(gates) != FINAL_EXPERIMENT_GATE_KEYS[experiment["suite_id"]]
    ):
        raise ValueError("release-candidate experiment Evidence contract failed")

    if artifact["evidence_revision"] >= 5 and not _validate_release_development_artifacts(
        payload.get("release_artifacts"),
        artifact["memoryforge_commit"],
        evidence_revision=artifact["evidence_revision"],
    ):
        raise ValueError("release-candidate retained build Evidence changed")

    metrics = evaluation.get("metrics")
    if artifact["status"] == "rejected":
        failures = RELEASE_CANDIDATE_REJECTED_FAILURES.get(str(artifact["path"]))
        if failures is None:
            raise ValueError("unknown rejected release-candidate Evidence")
        failed_cases = len(failures)
        reproducible_artifacts = "local-reproducible-artifacts" not in failures
        expected_metrics = {
            "pass_rate": round(100 * (len(frozen_ids) - failed_cases) / len(frozen_ids), 1),
            "failed_cases": failed_cases,
            "reproducible_artifacts": reproducible_artifacts,
            "private_detail_leaks": 0,
            "confirmation_not_run": True,
            "holdout_not_run": True,
        }
        expected_cases = [
            (
                case_id,
                "failed" if case_id in failures else "passed",
                failures.get(case_id, "none"),
            )
            for case_id in frozen_ids
        ]
        expected_gates = {
            "pass_rate": False,
            "failed_cases": False,
            "reproducible_artifacts": reproducible_artifacts,
            "private_detail_leaks": True,
            "confirmation_not_run": True,
            "holdout_not_run": True,
            "deterministic_replay": True,
            "stable_memoryforge_commit": True,
            "clean_worktree_after_run": True,
        }
        actual_cases = [
            (case["id"], case["status"], case["error_classification"]) for case in cases
        ]
        if (
            artifact["passed"] is not False
            or not _strict_mapping(metrics, expected_metrics)
            or actual_cases != expected_cases
            or not _strict_mapping(gates, expected_gates)
        ):
            raise ValueError("rejected release-candidate Evidence changed")
        return

    if (
        artifact["status"]
        not in {
            "accepted_development",
            "accepted_development_superseded",
            "development_passed_regression_failed",
            "development_passed_review_failed",
            "development_passed_gate_pending",
            "local_gates_passed_review_pending",
        }
        or artifact["passed"] is not True
        or not _strict_mapping(metrics, experiment["expected_metrics"]["development"])
        or any(
            case["status"] != "passed" or case["error_classification"] != "none" for case in cases
        )
        or not all(value is True for value in gates.values())
    ):
        raise ValueError("accepted release-candidate Evidence gates failed")


def _validate_release_development_artifacts(
    payload: object,
    commit: str,
    *,
    evidence_revision: int = 5,
) -> bool:
    base_keys = {
        "package",
        "builds",
        "sha256sums_sha256",
        "artifact_root",
    }
    support_digest_keys = {
        "provenance_sha256",
        "benchmark_summary_sha256",
        "workspace_drill_sha256",
    }
    payload_keys = frozenset(payload) if isinstance(payload, dict) else frozenset()
    if (
        not isinstance(payload, dict)
        or payload_keys not in {frozenset(base_keys), frozenset(base_keys | support_digest_keys)}
        or (evidence_revision >= 7 and set(payload) != base_keys | support_digest_keys)
    ):
        return False
    expected_root = f"demo/results/artifacts/release_candidate_development/{commit}"
    if payload.get("artifact_root") != expected_root:
        return False
    declared_root = REPO_ROOT / expected_root
    cursor = REPO_ROOT
    for part in Path(expected_root).parts:
        cursor /= part
        if cursor.is_symlink():
            return False
    root = declared_root.resolve()
    if not root.is_relative_to(REPO_ROOT.resolve()) or not root.is_dir():
        return False
    package = payload.get("package")
    builds = payload.get("builds")
    sums = root / "SHA256SUMS"
    if (
        not isinstance(package, dict)
        or set(package)
        != {
            "version",
            "wheel",
            "wheel_sha256",
            "sdist",
            "sdist_sha256",
        }
        or package.get("version") != "0.3.0"
        or package.get("wheel") != "memoryforge-0.3.0-py3-none-any.whl"
        or package.get("sdist") != "memoryforge-0.3.0.tar.gz"
        or any(
            SHA256.fullmatch(str(package.get(f"{kind}_sha256"))) is None
            for kind in ("wheel", "sdist")
        )
        or not isinstance(builds, list)
        or len(builds) != 2
        or not sums.is_file()
        or hashlib.sha256(sums.read_bytes()).hexdigest() != payload.get("sha256sums_sha256")
    ):
        return False
    registered_sums = _read_registered_sha256sums(root)
    if registered_sums is None:
        return False
    retained: set[Path] = set()
    for index, build in enumerate(builds):
        name = ("first", "second")[index]
        if (
            not isinstance(build, dict)
            or set(build) != {"name", "wheel", "sdist"}
            or build.get("name") != name
        ):
            return False
        for kind in ("wheel", "sdist"):
            record = build.get(kind)
            if (
                not isinstance(record, dict)
                or set(record) != {"path", "sha256", "size", "retained_path"}
                or record.get("path") != package.get(kind)
                or record.get("sha256") != package.get(f"{kind}_sha256")
                or type(record.get("size")) is not int
                or record["size"] < 1
            ):
                return False
            relative = record.get("retained_path")
            if (
                not isinstance(relative, str)
                or Path(relative).name != relative
                or relative != f"reproducibility-{name}-{package[kind]}"
            ):
                return False
            path = (root / relative).resolve()
            if (
                not path.is_relative_to(root.resolve())
                or path in retained
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256")
                or path.stat().st_size != record.get("size")
            ):
                return False
            retained.add(path)
    for kind in ("wheel", "sdist"):
        path = (root / str(package[kind])).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != package[f"{kind}_sha256"]
        ):
            return False
    if not _package_archives_match(
        root / str(package["wheel"]),
        root / str(package["sdist"]),
        commit,
    ):
        return False
    expected_files = {
        "benchmark-summary.json",
        "release-provenance.json",
        "workspace-drill.json",
        str(package["wheel"]),
        str(package["sdist"]),
        *(
            f"reproducibility-{name}-{package[kind]}"
            for name in ("first", "second")
            for kind in ("wheel", "sdist")
        ),
    }
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "SHA256SUMS"
    }
    if (
        set(registered_sums) != expected_files
        or actual_files != expected_files
        or any(path.is_symlink() for path in root.rglob("*"))
    ):
        return False
    support = {
        "provenance_sha256": root / "release-provenance.json",
        "benchmark_summary_sha256": root / "benchmark-summary.json",
        "workspace_drill_sha256": root / "workspace-drill.json",
    }
    if any(not path.is_file() for path in support.values()):
        return False
    if evidence_revision >= 7 and any(
        hashlib.sha256(path.read_bytes()).hexdigest() != payload.get(key)
        for key, path in support.items()
    ):
        return False
    try:
        provenance = json.loads(support["provenance_sha256"].read_text(encoding="utf-8"))
        summary = json.loads(support["benchmark_summary_sha256"].read_text(encoding="utf-8"))
        drill = json.loads(support["workspace_drill_sha256"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        _release_provenance_contract(provenance, commit, package, builds)
        and _release_summary_contract(summary, commit, evidence_revision=evidence_revision)
        and _release_drill_contract(drill, commit, evidence_revision=evidence_revision)
    )


def _package_archives_match(wheel: Path, sdist: Path, commit: str) -> bool:
    wheel_metadata_path = "memoryforge-0.3.0.dist-info/METADATA"
    wheel_record_path = "memoryforge-0.3.0.dist-info/RECORD"
    sdist_metadata_path = "memoryforge-0.3.0/PKG-INFO"
    try:
        with zipfile.ZipFile(wheel) as archive:
            wheel_names = archive.namelist()
            if len(wheel_names) != len(set(wheel_names)):
                return False
            metadata_names = [name for name in wheel_names if name.endswith(".dist-info/METADATA")]
            if metadata_names != [wheel_metadata_path]:
                return False
            wheel_payload = archive.read(wheel_metadata_path)
            wheel_sources = {
                f"src/{name}": archive.read(name)
                for name in wheel_names
                if name.startswith("memoryforge/") and not name.endswith("/")
            }
            record_names = [name for name in wheel_names if name.endswith(".dist-info/RECORD")]
            if record_names != [wheel_record_path] or not _wheel_record_valid(
                archive.read(wheel_record_path),
                {name: archive.read(name) for name in wheel_names if not name.endswith("/")},
                wheel_record_path,
            ):
                return False
        with tarfile.open(sdist, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != len({member.name for member in members}):
                return False
            metadata_members = [member for member in members if member.name.endswith("/PKG-INFO")]
            if [member.name for member in metadata_members] != [sdist_metadata_path]:
                return False
            extracted = archive.extractfile(metadata_members[0])
            if extracted is None:
                return False
            sdist_payload = extracted.read()
            sdist_sources: dict[str, bytes] = {}
            prefix = "memoryforge-0.3.0/src/"
            for member in members:
                if not member.name.startswith(prefix) or member.isdir():
                    continue
                if not member.isfile():
                    return False
                source = archive.extractfile(member)
                if source is None:
                    return False
                sdist_sources[member.name.removeprefix("memoryforge-0.3.0/")] = source.read()
    except (OSError, KeyError, tarfile.TarError, zipfile.BadZipFile):
        return False
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "src/memoryforge"],
        cwd=SOURCE_GIT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        return False
    tracked_paths = tracked.stdout.splitlines()
    committed_sources: dict[str, bytes] = {}
    for path in tracked_paths:
        blob = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=SOURCE_GIT_ROOT,
            check=False,
            capture_output=True,
        )
        if blob.returncode != 0:
            return False
        committed_sources[path] = blob.stdout
    metadata = BytesParser(policy=default).parsebytes(wheel_payload)
    return (
        bool(committed_sources)
        and wheel_payload == sdist_payload
        and wheel_sources == sdist_sources == committed_sources
        and metadata.get("Name", "").casefold() == "memoryforge"
        and metadata.get("Version") == "0.3.0"
    )


def _wheel_record_valid(payload: bytes, files: dict[str, bytes], record_path: str) -> bool:
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"))))
    except (UnicodeError, csv.Error):
        return False
    if any(len(row) != 3 for row in rows) or len(rows) != len(files):
        return False
    records = {row[0]: (row[1], row[2]) for row in rows}
    if len(records) != len(rows) or set(records) != set(files):
        return False
    for path, content in files.items():
        digest, size = records[path]
        if path == record_path:
            if digest or size:
                return False
            continue
        expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        if digest != f"sha256={expected}" or size != str(len(content)):
            return False
    return True


def _read_registered_sha256sums(root: Path) -> dict[str, str] | None:
    try:
        lines = (root / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    sums: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2:
            return None
        digest, relative = parts
        path = (root / relative).resolve()
        if (
            SHA256.fullmatch(digest) is None
            or relative in sums
            or not path.is_relative_to(root)
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            return None
        sums[relative] = digest
    return sums


def _release_provenance_contract(
    payload: object,
    commit: str,
    package: dict[str, Any],
    builds: list[Any],
) -> bool:
    if not isinstance(payload, dict):
        return False
    runtime = payload.get("runtime")
    checks = payload.get("checks")
    dependencies = payload.get("dependencies")
    return (
        set(payload)
        == {
            "schema_version",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "package",
            "builds",
            "reproducible_artifacts",
            "runtime",
            "checks",
            "dependencies",
            "confirmation",
            "holdout",
        }
        and type(payload.get("schema_version")) is int
        and payload.get("schema_version") == 1
        and payload.get("memoryforge_commit") == commit
        and payload.get("memoryforge_worktree_dirty") is False
        and payload.get("reproducible_artifacts") is True
        and _payload_private_detail_leaks(payload) == 0
        and _strict_mapping(payload.get("package"), package)
        and _strict_json_value(payload.get("builds"), builds)
        and isinstance(runtime, dict)
        and set(runtime) == {"implementation", "python", "system", "machine"}
        and runtime.get("implementation") == "CPython"
        and str(runtime.get("python", "")).startswith("3.11.")
        and all(isinstance(runtime.get(key), str) and runtime[key] for key in ("system", "machine"))
        and isinstance(checks, dict)
        and set(checks)
        == {
            "wheel_clean_room",
            "sdist_clean_room",
            "workspace_drill",
            "benchmark_summary",
            "sdist_members",
        }
        and _strict_mapping(
            checks.get("wheel_clean_room"),
            {
                "pip_check": "passed",
                "cli_help": "passed",
                "cli_version": "passed",
                "code_wiki_benchmark": "passed",
                "public_demo": "not_run",
            },
        )
        and _strict_mapping(
            checks.get("sdist_clean_room"),
            {
                "install": "passed",
                "pip_check": "passed",
                "import": "passed",
                "cli_version": "passed",
            },
        )
        and all(
            checks.get(key) == "passed"
            for key in ("workspace_drill", "benchmark_summary", "sdist_members")
        )
        and isinstance(dependencies, dict)
        and bool(dependencies)
        and all(
            isinstance(key, str) and isinstance(value, str) for key, value in dependencies.items()
        )
        and _strict_mapping(payload.get("confirmation"), {"status": "not_run"})
        and _strict_mapping(payload.get("holdout"), {"status": "not_run"})
    )


def build_benchmark_summary(
    registry: dict[str, Any],
    registry_summary: dict[str, object],
    *,
    package_version: str,
    memoryforge_commit: str,
    schema_version: int = 3,
) -> dict[str, Any]:
    if type(schema_version) is not int or schema_version not in {1, 2, 3}:
        raise ValueError("unsupported benchmark summary schema")
    if schema_version >= 3:
        if not _git_commit_descends_from(memoryforge_commit, memoryforge_commit):
            raise ValueError("benchmark summary Commit does not exist")
        try:
            committed_registry = json.loads(
                subprocess.run(
                    [
                        "git",
                        "show",
                        f"{memoryforge_commit}:demo/evaluation/registry.json",
                    ],
                    cwd=SOURCE_GIT_ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            committed_project = tomllib.loads(
                subprocess.run(
                    ["git", "show", f"{memoryforge_commit}:pyproject.toml"],
                    cwd=SOURCE_GIT_ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
        except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            raise ValueError("benchmark summary Registry snapshot is unreadable") from exc
        if not _strict_json_value(registry, committed_registry) or not _strict_mapping(
            registry_summary,
            _registry_snapshot_summary(committed_registry),
        ):
            raise ValueError("benchmark summary Registry does not match its Commit")
        if (
            committed_project.get("project", {}).get("version") != package_version
            or committed_registry.get("package_release_target") != package_version
        ):
            raise ValueError("benchmark summary package version does not match its Commit")
        for experiment in registry["experiments"]:
            if experiment["suite_id"] != "release-candidate-delivery":
                continue
            for evidence in experiment["evidence"]:
                if evidence["status"] != "accepted_development":
                    continue
                acceptance = evidence.get("acceptance_evidence")
                review = evidence.get("review_evidence")
                if (
                    not isinstance(acceptance, dict)
                    or not isinstance(review, dict)
                    or review.get("passed") is not True
                    or not _git_commit_descends_from(
                        memoryforge_commit,
                        str(evidence["memoryforge_commit"]),
                    )
                    or not _git_commit_descends_from(
                        memoryforge_commit,
                        str(acceptance.get("memoryforge_commit")),
                    )
                    or not _git_commit_descends_from(
                        memoryforge_commit,
                        str(review.get("memoryforge_commit")),
                    )
                ):
                    raise ValueError("benchmark summary acceptance ancestry is invalid")
    suites = [_benchmark_suite_summary(suite) for suite in registry["suites"]]
    experiments = [
        _benchmark_experiment_summary(experiment, schema_version=schema_version)
        for experiment in registry["experiments"]
    ]
    return {
        "schema_version": schema_version,
        "package_version": package_version,
        "memoryforge_commit": memoryforge_commit,
        "registry": registry_summary,
        "macro": _benchmark_macro_metrics(suites),
        "suites": suites,
        "experiments": experiments,
        "negative_results": _benchmark_negative_results(
            registry,
            schema_version=schema_version,
        ),
    }


def _benchmark_suite_summary(suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "suite_id": suite["suite_id"],
        "suite_revision": suite["suite_revision"],
        "suite_type": suite["suite_type"],
        "repositories": [
            {
                "repository": repository["repository"],
                "commit": repository["commit"],
                "license": repository["license"],
            }
            for repository in suite["repositories"]
        ],
        "splits": suite["splits"],
        "metrics": suite["expected_metrics"],
        "evidence": [
            {
                "split": evidence["split"],
                "evidence_revision": evidence["evidence_revision"],
                "path": evidence["path"],
                "sha256": evidence["sha256"],
                "memoryforge_commit": evidence["memoryforge_commit"],
            }
            for evidence in suite["evidence"]
        ],
    }


def _benchmark_experiment_summary(
    experiment: dict[str, Any],
    *,
    schema_version: int,
) -> dict[str, Any]:
    accepted = [
        evidence
        for evidence in experiment["evidence"]
        if evidence["status"] == "accepted_development"
    ]
    if schema_version == 1:
        accepted_evidence = [
            {
                "path": evidence["path"],
                "sha256": evidence["sha256"],
                "memoryforge_commit": evidence["memoryforge_commit"],
            }
            for evidence in accepted
        ]
    else:
        accepted_evidence = []
        for evidence in accepted:
            accepted_summary = {
                "status": evidence["status"],
                "development": {
                    "path": evidence["path"],
                    "sha256": evidence["sha256"],
                    "memoryforge_commit": evidence["memoryforge_commit"],
                    "passed": evidence["passed"],
                },
                "acceptance": evidence["acceptance_evidence"],
            }
            if "review_evidence" in evidence:
                accepted_summary["review"] = evidence["review_evidence"]
            accepted_evidence.append(accepted_summary)
    summary = {
        "suite_id": experiment["suite_id"],
        "suite_revision": experiment["suite_revision"],
        "confirmation": experiment["splits"]["confirmation"],
        "holdout": experiment["splits"]["holdout"],
        "accepted_evidence": accepted_evidence,
    }
    if schema_version >= 3:
        summary.update(
            {
                "repositories": experiment["repositories"],
                "development": experiment["splits"]["development"],
                "expected_metrics": experiment["expected_metrics"],
                "evidence": [
                    {
                        key: value
                        for key, value in evidence.items()
                        if key
                        in {
                            "split",
                            "evidence_revision",
                            "status",
                            "path",
                            "sha256",
                            "memoryforge_commit",
                            "passed",
                            "regression_evidence",
                            "review_evidence",
                            "acceptance_evidence",
                            "linux_evidence",
                        }
                    }
                    for evidence in experiment["evidence"]
                ],
            }
        )
    return summary


def _benchmark_macro_metrics(suites: list[dict[str, Any]]) -> dict[str, float]:
    values: defaultdict[str, list[float]] = defaultdict(list)
    for suite in suites:
        if suite["suite_type"] not in {"document_wiki_qa", "code_wiki_qa"}:
            continue
        for metrics in suite["metrics"].values():
            if not isinstance(metrics, dict):
                continue
            for name, value in metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[name].append(float(value))
    return {
        name: round(sum(metric_values) / len(metric_values), 1)
        for name, metric_values in sorted(values.items())
        if metric_values
    }


def _benchmark_negative_results(
    registry: dict[str, Any],
    *,
    schema_version: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for experiment in registry["experiments"]:
        for evidence in experiment["evidence"]:
            if evidence["status"] in {
                "rejected",
                "development_passed_regression_failed",
                "development_passed_review_failed",
            }:
                result = {
                    "suite_id": experiment["suite_id"],
                    "status": evidence["status"],
                    "path": evidence["path"],
                    "sha256": evidence["sha256"],
                }
                if schema_version >= 2:
                    result["memoryforge_commit"] = evidence["memoryforge_commit"]
                results.append(result)
            for field, status in (
                ("regression_evidence", "regression_rejected"),
                ("review_evidence", "review_rejected"),
            ):
                negative = evidence.get(field)
                if (
                    not isinstance(negative, dict)
                    or field == "review_evidence"
                    and negative.get("passed") is not False
                ):
                    continue
                result = {
                    "suite_id": experiment["suite_id"],
                    "status": status,
                    "path": negative["path"],
                    "sha256": negative["sha256"],
                }
                if schema_version >= 2:
                    result["memoryforge_commit"] = negative["memoryforge_commit"]
                results.append(result)
    for suite in registry["suites"]:
        for split, metrics in suite["expected_metrics"].items():
            if (
                isinstance(metrics, dict)
                and isinstance(metrics.get("answer_accuracy"), (int, float))
                and metrics["answer_accuracy"] < 100
            ):
                result = {
                    "suite_id": suite["suite_id"],
                    "status": "retained_metric_gap",
                    "split": split,
                    "answer_accuracy": metrics["answer_accuracy"],
                }
                if schema_version >= 2:
                    result["repositories"] = [
                        {
                            "repository": repository["repository"],
                            "commit": repository["commit"],
                        }
                        for repository in suite["repositories"]
                    ]
                results.append(result)
    return sorted(
        results,
        key=lambda item: (item["suite_id"], item["status"], item.get("split", "")),
    )


def _registry_snapshot_summary(registry: dict[str, Any]) -> dict[str, object]:
    suites = cast(list[dict[str, Any]], registry["suites"])
    experiments = cast(list[dict[str, Any]], registry["experiments"])
    sidecar_fields = {
        "regression_evidence",
        "review_evidence",
        "acceptance_evidence",
        "linux_evidence",
    }
    experiment_evidence_count = sum(
        1 + sum(field in evidence for field in sidecar_fields)
        for experiment in experiments
        for evidence in experiment["evidence"]
    )
    embedded_acceptance_artifacts = sum(
        1
        for experiment in experiments
        if experiment["suite_id"] == "multi-source-coverage-selection"
        for evidence in experiment["evidence"]
        if "acceptance_evidence" in evidence
    )
    return {
        "status": "valid",
        "suite_count": len(suites),
        "experiment_count": len(experiments),
        "evidence_count": (
            1
            + experiment_evidence_count
            + embedded_acceptance_artifacts
            + sum(len(cast(list[object], suite["evidence"])) for suite in suites)
        ),
        "qa_case_count": registry["qa_case_count"],
        "qa_case_types_present": sorted(registry["qa_case_types_present"]),
        "suite_types": sorted({str(suite["suite_type"]) for suite in suites}),
    }


def _release_summary_contract(
    payload: object,
    commit: str,
    *,
    evidence_revision: int = 10,
) -> bool:
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
        return False
    schema_version = payload["schema_version"]
    if schema_version not in {1, 2, 3}:
        return False
    try:
        schema_2_required = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", SUMMARY_SCHEMA_2_ANCESTOR, commit],
                cwd=SOURCE_GIT_ROOT,
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
        expected_schema = 3 if evidence_revision >= 11 else (2 if schema_2_required else 1)
        if schema_version != expected_schema:
            return False
        snapshot = json.loads(
            subprocess.run(
                ["git", "show", f"{commit}:demo/evaluation/registry.json"],
                cwd=SOURCE_GIT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        expected = build_benchmark_summary(
            snapshot,
            _registry_snapshot_summary(snapshot),
            package_version="0.3.0",
            memoryforge_commit=commit,
            schema_version=schema_version,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError):
        return False
    return _strict_json_value(payload, expected)


def _release_drill_contract(
    payload: object,
    commit: str,
    *,
    evidence_revision: int = 10,
) -> bool:
    if not isinstance(payload, dict):
        return False
    expected_checks = {
        key: "passed"
        for key in {
            "refresh",
            "review",
            "approve",
            "apply",
            "lint",
            "no_pending_ingest",
            "backup",
            "restore",
            "query",
            "showcase",
        }
    }
    base_valid = (
        payload.get("memoryforge_commit") == commit
        and _strict_mapping(payload.get("checks"), expected_checks)
        and type(payload.get("private_detail_leaks")) is int
        and payload.get("private_detail_leaks") == 0
        and _payload_private_detail_leaks(payload) == 0
        and payload.get("passed") is True
    )
    if evidence_revision < 11:
        return (
            set(payload)
            == {
                "schema_version",
                "memoryforge_commit",
                "checks",
                "private_detail_leaks",
                "passed",
            }
            and type(payload.get("schema_version")) is int
            and payload.get("schema_version") == 1
            and base_valid
        )

    evaluation = payload.get("evaluation")
    fixture = payload.get("fixture")
    queries = payload.get("queries")
    workspace = payload.get("workspace")
    expected_keys = {
        "schema_version",
        "memoryforge_commit",
        "checks",
        "evaluation",
        "queries",
        "workspace",
        "private_detail_leaks",
        "passed",
    }
    if evidence_revision >= 14:
        expected_keys.add("fixture")
    if (
        set(payload) != expected_keys
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 2
        or not base_valid
        or not _strict_mapping(
            evaluation,
            {
                "metrics": {
                    "answer_accuracy": 100.0,
                    "citation_grounding_accuracy": 100.0,
                    "abstention_accuracy": 100.0,
                },
                "cases": [
                    {
                        "id": "exact-code-signature",
                        "category": "exact_symbol",
                        "error_classification": "none",
                        "memoryforge": {
                            "answer_correct": True,
                            "abstention_correct": False,
                        },
                    },
                    {
                        "id": "unsupported-symbol-abstention",
                        "category": "unanswerable",
                        "error_classification": "none",
                        "memoryforge": {
                            "answer_correct": True,
                            "abstention_correct": True,
                        },
                    },
                ],
            },
        )
        or not isinstance(queries, dict)
        or set(queries) != {"answered", "unknown"}
        or not isinstance(workspace, dict)
        or set(workspace) != {"original_commit", "restored_commit", "restored_lint"}
        or COMMIT.fullmatch(str(workspace.get("original_commit"))) is None
        or workspace.get("restored_commit") != workspace.get("original_commit")
        or not _strict_mapping(
            workspace.get("restored_lint"),
            {"status": "clean", "checked_pages": 2, "issues": []},
        )
        or (
            evidence_revision >= 14
            and (
                not _strict_mapping(fixture, RELEASE_DRILL_FIXTURE)
                or workspace.get("original_commit") != RELEASE_DRILL_WORKSPACE_COMMIT
            )
        )
    ):
        return False
    answered = queries["answered"]
    unknown = queries["unknown"]
    return (
        isinstance(answered, dict)
        and set(answered) == {"original", "restored"}
        and _strict_json_value(answered["original"], answered["restored"])
        and _release_drill_answered_query(answered["original"])
        and (
            evidence_revision < 14
            or answered["original"].get("source_id") == RELEASE_DRILL_FIXTURE["source_id"]
        )
        and isinstance(unknown, dict)
        and set(unknown) == {"original", "restored"}
        and _strict_json_value(unknown["original"], unknown["restored"])
        and _release_drill_unknown_query(unknown["original"])
    )


def _release_drill_answered_query(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    citations = payload.get("citations")
    evidence = payload.get("evidence")
    support = payload.get("support")
    if (
        set(payload)
        != {
            "status",
            "answer",
            "citations",
            "evidence",
            "source_id",
            "source_version",
            "locator",
            "quote",
            "support",
            "wiki_pages",
        }
        or not isinstance(citations, list)
        or len(citations) != 1
        or not isinstance(evidence, list)
        or len(evidence) != 1
    ):
        return False
    citation = citations[0]
    verified = evidence[0]
    if not isinstance(citation, dict) or not isinstance(verified, dict):
        return False
    source_id = citation.get("source_id")
    expected_answer = "`src.service.cache_ttl` (function): `def cache_ttl() -> int:`"
    expected_evidence = {
        **citation,
        "text": "def cache_ttl() -> int:\n    return CACHE_TTL",
    }
    return (
        payload.get("status") == "answered"
        and payload.get("answer") == expected_answer
        and set(citation) == {"source_id", "source_version", "locator", "quote"}
        and SHA256.fullmatch(str(source_id)) is not None
        and citation.get("source_version") == 2
        and citation.get("locator") == "chars:17-61"
        and citation.get("quote") == expected_answer
        and payload.get("source_id") == source_id
        and payload.get("source_version") == citation["source_version"]
        and payload.get("locator") == citation["locator"]
        and payload.get("quote") == citation["quote"]
        and _strict_mapping(verified, expected_evidence)
        and _release_drill_support_valid(
            support,
            {
                "exact_identifier_coverage": 1.0,
                "core_term_coverage": 0.6667,
                "fact_co_location": 1.0,
                "negation_alignment": 1.0,
                "multi_source_coverage": 1.0,
                "current_source_versions": 1.0,
            },
        )
        and isinstance(payload.get("wiki_pages"), list)
        and len(payload["wiki_pages"]) == 1
        and re.fullmatch(
            r"wiki/pages/code/[a-f0-9]{12}/src/service\.md",
            str(payload["wiki_pages"][0]),
        )
        is not None
    )


def _release_drill_unknown_query(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    support = payload.get("support")
    return (
        set(payload)
        == {
            "status",
            "answer",
            "citations",
            "source_id",
            "source_version",
            "locator",
            "quote",
            "support",
            "wiki_pages",
        }
        and payload.get("status") == "unknown"
        and payload.get("answer") == "不知道"
        and payload.get("citations") == []
        and all(
            payload.get(key) is None for key in ("source_id", "source_version", "locator", "quote")
        )
        and _release_drill_support_valid(
            support,
            {
                "exact_identifier_coverage": 0.0,
                "core_term_coverage": 0.3333,
                "fact_co_location": 1.0,
                "negation_alignment": 1.0,
                "multi_source_coverage": 1.0,
                "current_source_versions": 1.0,
            },
        )
        and payload.get("wiki_pages") == []
    )


def _release_drill_support_valid(
    payload: object,
    expected_components: dict[str, float],
) -> bool:
    if not isinstance(payload, dict) or not _strict_mapping(
        payload.get("components"),
        expected_components,
    ):
        return False
    components = cast(dict[str, float], payload["components"])
    score = round(
        100
        * (
            0.20 * components["exact_identifier_coverage"]
            + 0.35 * components["core_term_coverage"]
            + 0.15 * components["fact_co_location"]
            + 0.10 * components["negation_alignment"]
            + 0.10 * components["multi_source_coverage"]
            + 0.10 * components["current_source_versions"]
        ),
        1,
    )
    failed = []
    if components["exact_identifier_coverage"] < 1:
        failed.append("exact_identifier_not_covered")
    if score < 75.0:
        failed.append("score_below_threshold")
    return _strict_mapping(
        payload,
        {
            "score": score,
            "threshold": 75.0,
            "sufficient": not failed,
            "enforced": True,
            "components": expected_components,
            "failed_hard_gates": failed,
        },
    )


def _validate_pytest_component_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    cross_platform = experiment["suite_id"] == "cross-platform-delivery"
    runtime_cross_platform = cross_platform and artifact["evidence_revision"] >= 4
    diagnostic_cross_platform = cross_platform and artifact["evidence_revision"] >= 5
    evaluation = payload.get("development", {}).get("evaluation", {})
    cases = evaluation.get("cases", {}) if isinstance(evaluation, dict) else {}
    frozen = json.loads((REPO_ROOT / development["path"]).read_text(encoding="utf-8"))
    frozen_ids = [case.get("id") for case in frozen.get("cases", [])]
    runs = payload.get("runs")
    evaluation_sha256 = hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    test_artifact = {
        "path": development.get("test_file"),
        "sha256": development.get("test_sha256"),
    }
    _validate_artifact(test_artifact, str(experiment["suite_id"]))
    development_payload = payload.get("development", {})
    if cross_platform:
        test_binding_valid = (
            development_payload.get("test_file")
            == {
                "path": development["test_file"],
                "sha256": development["test_sha256"],
            }
            and "test_sha256" not in development_payload
        )
    else:
        test_binding_valid = (
            development_payload.get("test_file") == development["test_file"]
            and development_payload.get("test_sha256") == development["test_sha256"]
        )
    expected_payload_keys = (
        CROSS_PLATFORM_DEVELOPMENT_EVIDENCE_KEYS
        if runtime_cross_platform
        else MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS
    )
    runtime = payload.get("runtime")
    runtime_contract = CROSS_PLATFORM_DEVELOPMENT_RUNTIME_CONTRACTS.get(str(artifact.get("path")))
    runtime_valid = not runtime_cross_platform or (
        runtime_contract is not None and _strict_mapping(runtime, runtime_contract)
    )
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or set(payload) != expected_payload_keys
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or not test_binding_valid
        or type(payload.get("development", {}).get("case_count")) is not int
        or payload.get("development", {}).get("case_count") != development["case_count"]
        or not isinstance(evaluation, dict)
        or set(evaluation) != {"case_count", "metrics", "cases"}
        or type(evaluation.get("case_count")) is not int
        or evaluation.get("case_count") != development["case_count"]
        or not isinstance(cases, list)
        or [case.get("id") for case in cases] != frozen_ids
        or len(set(frozen_ids)) != len(frozen_ids)
        or not isinstance(runs, list)
        or len(runs) != 2
        or any(
            not isinstance(run, dict)
            or set(run) != {"name", "evaluation_sha256"}
            or SHA256.fullmatch(str(run.get("evaluation_sha256"))) is None
            for run in runs
        )
        or [run["name"] for run in runs] != ["first", "second"]
        or any(run["evaluation_sha256"] != evaluation_sha256 for run in runs)
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
        or not runtime_valid
    ):
        raise ValueError("pytest component experiment Evidence contract failed")
    gates = payload.get("gates")
    if (
        not isinstance(gates, dict)
        or set(gates) != FINAL_EXPERIMENT_GATE_KEYS[experiment["suite_id"]]
    ):
        raise ValueError("pytest component experiment gates are invalid")
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("pytest component experiment metrics missing")
    if cross_platform:
        expected_status = "failed" if artifact["status"] == "rejected" else "passed"
        expected_classification = "pytest_failure" if artifact["status"] == "rejected" else "none"
        expected_cases = [
            {
                "id": case["id"],
                "pytest_node": (f"tests/test_cross_platform_delivery.py::{case['test']}"),
                "status": expected_status,
                **({"return_code": 0} if runtime_cross_platform else {}),
                **(
                    {
                        "timed_out": False,
                        "diagnostic_sha256": hashlib.sha256(b"0:none:False").hexdigest(),
                    }
                    if diagnostic_cross_platform
                    else {}
                ),
                "error_classification": expected_classification,
            }
            for case in frozen["cases"]
        ]
        expected_metrics = (
            {
                "pass_rate": 0.0,
                "failed_cases": 7,
                "direct_platform_imports": 2,
                "windows_lock_offset": -1,
                "windows_lock_bytes": 0,
                "local_smoke": "failed",
            }
            if artifact["status"] == "rejected"
            else experiment["expected_metrics"]["development"]
        )
        expected_gates = (
            {
                "pass_rate": False,
                "failed_cases": False,
                "direct_platform_imports": False,
                "windows_lock_offset": False,
                "windows_lock_bytes": False,
                "local_smoke": False,
                "deterministic_replay": True,
                "stable_memoryforge_commit": True,
                "clean_worktree_after_run": True,
                "confirmation_not_run": True,
            }
            if artifact["status"] == "rejected"
            else {key: True for key in FINAL_EXPERIMENT_GATE_KEYS[experiment["suite_id"]]}
        )
        if (
            not _strict_json_value(cases, expected_cases)
            or not _strict_mapping(metrics, expected_metrics)
            or not _strict_mapping(gates, expected_gates)
        ):
            raise ValueError("cross-platform delivery case Evidence changed")
    if artifact["status"] == "rejected":
        if artifact["passed"] is not False or all(gates.values()):
            raise ValueError("rejected pytest component Evidence must fail")
        return
    if (
        artifact["status"] not in {"accepted_development", "accepted_development_superseded"}
        or artifact["passed"] is not True
        or not all(value is True for value in gates.values())
    ):
        raise ValueError("accepted pytest component Evidence gates failed")
    for metric, expected in experiment["expected_metrics"]["development"].items():
        if metrics.get(metric) != expected:
            raise ValueError(f"pytest component experiment metric mismatch: {metric}")


def _strict_mapping(actual: object, expected: object) -> bool:
    return (
        isinstance(actual, dict)
        and isinstance(expected, dict)
        and _strict_json_value(actual, expected)
    )


def _strict_json_value(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(_strict_json_value(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _strict_json_value(actual_value, expected_value)
                for actual_value, expected_value in zip(actual, expected, strict=True)
            )
        )
    return actual == expected


def _git_commit_descends_from(commit: str, ancestor: str) -> bool:
    if COMMIT.fullmatch(commit) is None or COMMIT.fullmatch(ancestor) is None:
        return False
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, commit],
            cwd=SOURCE_GIT_ROOT,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _payload_private_detail_leaks(payload: object) -> int:
    prefixes = (
        "/Users/",
        "/home/",
        "/private/var/",
        "/private/tmp/",
        "/tmp/",
        "C:\\Users\\",
    )
    secrets = ("api_key", "token=", "password=", "secret=", "authorization:")
    secret_keys = (
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "credential",
        "cookie",
        "private_key",
    )
    secret_values = (
        "sk-",
        "ghp_",
        "github_pat_",
        "bearer ",
        "basic ",
        "akia",
        "-----begin private key-----",
    )
    leaks = 0

    def visit(value: object) -> None:
        nonlocal leaks
        if isinstance(value, dict):
            for key, item in value.items():
                lowered_key = str(key).casefold()
                if any(secret in lowered_key for secret in secret_keys):
                    leaks += 1
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            lowered = value.casefold()
            if (
                any(prefix.casefold() in lowered for prefix in prefixes)
                or re.search(
                    r"(?<![A-Za-z0-9:/>])/(?!/)[^/\s]+(?:/[^/\s]*)*",
                    value,
                )
                is not None
                or re.search(r"(?i)(?:^|[^A-Za-z0-9])[A-Z]:[\\/]", value) is not None
                or value.startswith(("\\\\", "//"))
                or any(secret in lowered for secret in secrets)
                or any(secret in lowered for secret in secret_values)
            ):
                leaks += 1

    visit(payload)
    return leaks


def _fresh_venv_import_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and posix.as_posix() == value
        and len(posix.parts) >= 4
        and posix.parts[0] == ".venv"
        and posix.parts[-3:] == ("site-packages", "memoryforge", "__init__.py")
        and all(part not in {"", ".", ".."} for part in posix.parts)
    )


def _validate_bound_gate_artifacts(
    artifact_files: object,
    artifact_digests: object,
    gate_commit: str,
    *,
    require_clean_sdist: bool = False,
    require_replayable_sums: bool = False,
    require_code_evidence: bool = False,
) -> bool:
    digest_fields = {
        "wheel": "wheel_sha256",
        "sdist": "sdist_sha256",
        "provenance": "provenance_sha256",
        "sha256sums": "sha256sums_sha256",
    }
    if not isinstance(artifact_files, dict) or not isinstance(artifact_digests, dict):
        return False
    if require_code_evidence:
        digest_fields["code_wiki_evidence"] = "code_wiki_evidence_sha256"
    has_platform_smoke = (
        "platform_smoke" in artifact_files or "platform_smoke_sha256" in artifact_digests
    )
    if has_platform_smoke:
        digest_fields["platform_smoke"] = "platform_smoke_sha256"
    if set(artifact_files) != set(digest_fields) or set(artifact_digests) != set(
        digest_fields.values()
    ):
        return False
    paths: dict[str, Path] = {}
    for name, digest_field in digest_fields.items():
        artifact = artifact_files.get(name)
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256"}
            or artifact.get("sha256") != artifact_digests.get(digest_field)
        ):
            return False
        _validate_artifact(artifact, "cross-platform gate artifact")
        paths[name] = REPO_ROOT / str(artifact["path"])

    try:
        provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    package = provenance.get("package", {}) if isinstance(provenance, dict) else {}
    runtime = provenance.get("runtime", {}) if isinstance(provenance, dict) else {}
    checks = provenance.get("checks", {}) if isinstance(provenance, dict) else {}
    commands = provenance.get("commands") if isinstance(provenance, dict) else None
    code_wiki = provenance.get("code_wiki") if isinstance(provenance, dict) else None
    public_demo = provenance.get("public_demo") if isinstance(provenance, dict) else None
    metrics = code_wiki.get("metrics") if isinstance(code_wiki, dict) else None
    gates = code_wiki.get("gates") if isinstance(code_wiki, dict) else None
    incremental = code_wiki.get("incremental") if isinstance(code_wiki, dict) else None
    package_version = package.get("version") if isinstance(package, dict) else None
    expected_commands = [
        "python -m pip install <wheel>",
        "python -m pip check",
        "python -m memoryforge --help",
        "python demo/run_code_wiki_benchmark.py",
    ]
    if isinstance(checks, dict) and "cli_version" in checks:
        expected_commands.insert(3, "python -m memoryforge --version")
    if (
        not isinstance(provenance, dict)
        or type(provenance.get("schema_version")) is not int
        or provenance.get("schema_version") != 1
        or set(provenance)
        != {
            "schema_version",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "package",
            "runtime",
            "commands",
            "checks",
            "code_wiki",
            "public_demo",
        }
        or _payload_private_detail_leaks(provenance) != 0
        or provenance.get("memoryforge_commit") != gate_commit
        or provenance.get("memoryforge_worktree_dirty") is not False
        or not isinstance(package, dict)
        or set(package)
        != {
            "version",
            "wheel",
            "wheel_sha256",
            "import_path",
            "import_from_fresh_venv",
            "dependencies",
        }
        or not isinstance(package_version, str)
        or not paths["wheel"].name.startswith(f"memoryforge-{package_version}-")
        or package.get("wheel") != paths["wheel"].name
        or package.get("wheel_sha256") != artifact_digests.get("wheel_sha256")
        or package.get("import_from_fresh_venv") is not True
        or not _fresh_venv_import_path(package.get("import_path"))
        or not isinstance(package.get("dependencies"), dict)
        or not isinstance(runtime, dict)
        or set(runtime) != {"implementation", "python", "platform"}
        or not all(
            isinstance(runtime.get(key), str) and runtime[key]
            for key in ("implementation", "python", "platform")
        )
        or not isinstance(checks, dict)
        or frozenset(checks)
        not in {
            frozenset({"pip_check", "cli_help", "code_wiki_benchmark", "public_demo"}),
            frozenset(
                {"pip_check", "cli_help", "cli_version", "code_wiki_benchmark", "public_demo"}
            ),
        }
        or checks.get("pip_check") != "passed"
        or checks.get("cli_help") != "passed"
        or checks.get("code_wiki_benchmark") != "passed"
        or any(status not in {"passed", "not_run"} for status in checks.values())
        or commands != expected_commands
        or not isinstance(code_wiki, dict)
        or set(code_wiki) != {"evidence_sha256", "metrics", "gates", "incremental"}
        or SHA256.fullmatch(str(code_wiki.get("evidence_sha256"))) is None
        or not isinstance(metrics, dict)
        or set(metrics) != CODE_WIKI_METRICS
        or any(type(value) not in {int, float} or value != 100.0 for value in metrics.values())
        or not isinstance(gates, dict)
        or set(gates) != CODE_WIKI_GATES
        or any(value is not True for value in gates.values())
        or not _strict_mapping(incremental, CODE_WIKI_INCREMENTAL)
        or not _strict_mapping(
            public_demo,
            {
                "status": "not_run",
                "required_commit": "93f5dc05229da250b041850ad8deeeec886ef304",
            },
        )
    ):
        return False

    if require_code_evidence:
        try:
            code_evidence = json.loads(paths["code_wiki_evidence"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        evaluation = code_evidence.get("evaluation") if isinstance(code_evidence, dict) else None
        if (
            not _raw_code_wiki_evidence_contract(code_evidence, gate_commit)
            or not isinstance(evaluation, dict)
            or not _strict_mapping(evaluation.get("metrics"), metrics)
            or not _strict_mapping(evaluation.get("gates"), gates)
            or not _strict_mapping(code_evidence.get("incremental"), incremental)
            or code_wiki.get("evidence_sha256") != artifact_digests.get("code_wiki_evidence_sha256")
        ):
            return False

    if require_clean_sdist:
        try:
            with tarfile.open(paths["sdist"], "r:gz") as archive:
                members = archive.getnames()
        except (OSError, tarfile.TarError):
            return False
        if any(
            "/demo/results/artifacts/" in f"/{name}" or name.endswith((".whl", ".tar.gz"))
            for name in members
        ):
            return False

    sums: dict[str, str] = {}
    for line in paths["sha256sums"].read_text(encoding="ascii").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or parts[1] in sums:
            return False
        sums[parts[1]] = parts[0]
    expected_sums = {
        f"dist/{paths['wheel'].name}": artifact_digests["wheel_sha256"],
        f"dist/{paths['sdist'].name}": artifact_digests["sdist_sha256"],
        "release-provenance.json": artifact_digests["provenance_sha256"],
    }
    if require_code_evidence:
        expected_sums["code-wiki-evidence.json"] = artifact_digests["code_wiki_evidence_sha256"]
    if has_platform_smoke:
        expected_sums["platform-smoke.json"] = artifact_digests["platform_smoke_sha256"]
    if sums != expected_sums:
        return False
    if not require_replayable_sums:
        return True
    root = paths["sha256sums"].parent
    expected_paths = {
        "sha256sums": root / "SHA256SUMS",
        "wheel": root / f"dist/{paths['wheel'].name}",
        "sdist": root / f"dist/{paths['sdist'].name}",
        "provenance": root / "release-provenance.json",
    }
    if require_code_evidence:
        expected_paths["code_wiki_evidence"] = root / "code-wiki-evidence.json"
    if has_platform_smoke:
        expected_paths["platform_smoke"] = root / "platform-smoke.json"
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            return False
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
    return (
        all(paths[name] == expected for name, expected in expected_paths.items())
        and actual_files == {path.relative_to(root).as_posix() for path in expected_paths.values()}
        and _sha256sums_replays(root, sums)
    )


def _raw_code_wiki_evidence_contract(
    payload: object,
    commit: str,
) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        suite_payload = subprocess.run(
            ["git", "show", f"{commit}:demo/evaluation/code_wiki_eval.json"],
            cwd=SOURCE_GIT_ROOT,
            check=True,
            capture_output=True,
        ).stdout
        suite = json.loads(suite_payload)
    except (json.JSONDecodeError, subprocess.CalledProcessError):
        return False
    evaluation = payload.get("evaluation")
    cases = evaluation.get("cases") if isinstance(evaluation, dict) else None
    return (
        set(payload)
        == {
            "schema_version",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "fixture",
            "workflow",
            "evaluation",
            "incremental",
        }
        and type(payload.get("schema_version")) is int
        and payload.get("schema_version") == 1
        and payload.get("memoryforge_commit") == commit
        and payload.get("memoryforge_worktree_dirty") is False
        and _payload_private_detail_leaks(payload) == 0
        and _strict_mapping(
            payload.get("fixture"),
            {
                "path": "demo/fixtures/code_wiki_project",
                "suite_sha256": hashlib.sha256(suite_payload).hexdigest(),
                **CODE_WIKI_FIXTURE_COMMITS,
            },
        )
        and _strict_mapping(
            payload.get("workflow"),
            {
                "wiki_file_count": 8,
                "lint": {
                    "status": "clean",
                    "checked_pages": 8,
                    "issues": [],
                },
            },
        )
        and isinstance(evaluation, dict)
        and set(evaluation) == {"schema_version", "suite", "counts", "metrics", "gates", "cases"}
        and type(evaluation.get("schema_version")) is int
        and evaluation.get("schema_version") == 1
        and evaluation.get("suite") == suite.get("name")
        and _strict_mapping(evaluation.get("counts"), CODE_WIKI_COUNTS)
        and _strict_mapping(cases, _expected_code_wiki_cases(suite))
        and isinstance(evaluation.get("metrics"), dict)
        and set(evaluation["metrics"]) == CODE_WIKI_METRICS
        and all(value == 100.0 for value in evaluation["metrics"].values())
        and isinstance(evaluation.get("gates"), dict)
        and set(evaluation["gates"]) == CODE_WIKI_GATES
        and all(value is True for value in evaluation["gates"].values())
        and _strict_mapping(payload.get("incremental"), CODE_WIKI_INCREMENTAL)
    )


def _expected_code_wiki_cases(suite: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "sources": [{"path": path, "found": True} for path in suite["expected_source_paths"]],
        "symbols": [
            {
                "qualified_name": case["qualified_name"],
                "kind": case["kind"],
                "found": True,
            }
            for case in suite["symbols"]
        ],
        "relations": [
            {
                "tier": case["tier"],
                "type": case["type"],
                "source": case["source"],
                "target": case["target"],
                "found": True,
            }
            for case in suite["relations"]
        ],
        "modules": [
            {
                "symbol": case["symbol"],
                "module_path": case["module_path"],
                "found": True,
            }
            for case in suite["modules"]
        ],
    }


def _sha256sums_replays(root: Path, sums: dict[str, str]) -> bool:
    resolved_root = root.resolve()
    for relative, expected in sums.items():
        path = Path(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return False
        candidate = root.joinpath(*path.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return False
        if (
            candidate.is_symlink()
            or not resolved.is_relative_to(resolved_root)
            or not resolved.is_file()
            or hashlib.sha256(resolved.read_bytes()).hexdigest() != expected
        ):
            return False
    return True


def _validate_static_showcase_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != "static-showcase"
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or payload.get("confirmation", {}).get("status") != "not_run"
    ):
        raise ValueError("static-Showcase experiment Evidence contract failed")
    if artifact["status"] == "rejected":
        contract = STATIC_SHOWCASE_REJECTED_CONTRACTS.get(str(artifact["path"]))
        development_payload = payload.get("development", {})
        confirmation_payload = payload.get("confirmation", {})
        pytest_result = payload.get("development", {}).get("pytest", {})
        failures = payload.get("development", {}).get("failures")
        valid_failures = isinstance(failures, list) and all(
            isinstance(failure, dict)
            and set(failure) == {"case_id", "classification", "detail"}
            and all(
                isinstance(failure.get(field), str) and failure[field]
                for field in ("case_id", "classification", "detail")
            )
            for failure in failures
        )
        failure_identities = (
            [(failure.get("case_id"), failure.get("classification")) for failure in failures]
            if valid_failures
            else []
        )
        if (
            contract is None
            or set(payload)
            != {
                "schema_version",
                "suite_id",
                "suite_revision",
                "memoryforge_commit",
                "memoryforge_worktree_dirty",
                "development",
                "confirmation",
                "passed",
            }
            or type(payload.get("suite_revision")) is not int
            or payload.get("suite_revision") != contract["suite_revision"]
            or artifact["passed"] is not False
            or not isinstance(development_payload, dict)
            or set(development_payload)
            != {"path", "sha256", "test_file", "case_count", "pytest", "failures"}
            or development_payload.get("path") != "demo/evaluation/static_showcase_development.json"
            or development_payload.get("sha256") != contract["development_sha256"]
            or development_payload.get("test_file")
            != {"path": "tests/test_showcase.py", "sha256": contract["test_sha256"]}
            or type(development_payload.get("case_count")) is not int
            or development_payload.get("case_count") != 4
            or not _strict_mapping(pytest_result, contract["pytest"])
            or not valid_failures
            or failure_identities != contract["failures"]
            or not isinstance(confirmation_payload, dict)
            or confirmation_payload
            != {
                "path": "demo/evaluation/static_showcase_confirmation.json",
                "sha256": contract["confirmation_sha256"],
                "status": "not_run",
            }
        ):
            raise ValueError("rejected static-Showcase Evidence must retain failures")
        return

    evaluation = payload.get("development", {}).get("evaluation")
    runs = payload.get("runs")
    gates = payload.get("gates")
    frozen = json.loads((REPO_ROOT / development["path"]).read_text(encoding="utf-8"))
    frozen_cases = frozen.get("cases", [])
    expected_cases = [
        {
            "id": case.get("id"),
            "pytest_node": f"tests/test_showcase.py::{case.get('test')}",
            "status": "passed",
            "error_classification": "none",
        }
        for case in frozen_cases
        if isinstance(case, dict)
    ]
    actual_cases = evaluation.get("cases") if isinstance(evaluation, dict) else None
    metrics = evaluation.get("metrics") if isinstance(evaluation, dict) else None
    evaluation_sha256 = hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if (
        set(payload) != MULTI_SOURCE_DEVELOPMENT_EVIDENCE_KEYS
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or payload.get("development", {}).get("test_file")
        != {"path": development["test_file"], "sha256": development["test_sha256"]}
        or type(payload.get("development", {}).get("case_count")) is not int
        or payload.get("development", {}).get("case_count") != development["case_count"]
        or not isinstance(evaluation, dict)
        or set(evaluation) != {"case_count", "metrics", "cases"}
        or type(evaluation.get("case_count")) is not int
        or evaluation.get("case_count") != development["case_count"]
        or actual_cases != expected_cases
        or len(expected_cases) != development["case_count"]
        or not _strict_mapping(metrics, experiment["expected_metrics"]["development"])
        or not isinstance(runs, list)
        or [run.get("name") for run in runs if isinstance(run, dict)] != ["first", "second"]
        or any(
            not isinstance(run, dict)
            or set(run) != {"name", "evaluation_sha256"}
            or run.get("evaluation_sha256") != evaluation_sha256
            for run in runs
        )
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or not isinstance(gates, dict)
        or set(gates) != FINAL_EXPERIMENT_GATE_KEYS["static-showcase"]
        or not all(value is True for value in gates.values())
        or artifact["status"] not in {"accepted_development", "accepted_development_superseded"}
        or artifact["passed"] is not True
    ):
        raise ValueError("accepted static-Showcase Evidence contract failed")


def _validate_multi_source_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("evaluator") != "demo.run_multi_source_coverage_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [MULTI_SOURCE_REPOSITORY]
    ):
        raise ValueError("multi-source experiment metadata changed")


def _validate_folder_import_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("suite_type") != "source_lifecycle"
        or experiment.get("evaluator") != "demo.run_folder_import_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [FOLDER_IMPORT_REPOSITORY]
    ):
        raise ValueError("folder-import experiment metadata changed")


def _validate_github_thread_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("suite_type") != "source_lifecycle"
        or experiment.get("evaluator") != "demo.run_github_thread_import_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [GITHUB_THREAD_IMPORT_REPOSITORY]
    ):
        raise ValueError("GitHub thread-import experiment metadata changed")


def _validate_static_showcase_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("suite_revision") != 3
        or experiment.get("suite_type") != "source_lifecycle"
        or experiment.get("evaluator") != "demo.run_static_showcase_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [STATIC_SHOWCASE_REPOSITORY]
    ):
        raise ValueError("static-Showcase experiment metadata changed")


def _validate_cross_platform_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("suite_revision") != 1
        or experiment.get("suite_type") != "source_lifecycle"
        or experiment.get("evaluator") != "demo.run_cross_platform_delivery_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [CROSS_PLATFORM_REPOSITORY]
    ):
        raise ValueError("cross-platform delivery experiment metadata changed")


def _validate_release_candidate_experiment_metadata(experiment: dict[str, Any]) -> None:
    if (
        experiment.get("suite_revision") != 1
        or experiment.get("suite_type") != "source_lifecycle"
        or experiment.get("evaluator") != "demo.run_release_candidate_benchmark"
        or experiment.get("max_wiki_pages") != 3
        or experiment.get("repositories") != [RELEASE_CANDIDATE_REPOSITORY]
    ):
        raise ValueError("release-candidate delivery experiment metadata changed")


def _validate_regression_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("regression_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("regression-rejected experiment requires regression Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if COMMIT.fullmatch(commit) is None or artifact.get("passed") is not False:
        raise ValueError("invalid experiment regression Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    if experiment["suite_id"] == "release-candidate-delivery":
        return _validate_release_sdist_regression(
            experiment,
            development_artifact,
            payload,
            confirmation,
            commit,
        )
    pytest_result = payload.get("regression", {}).get("pytest", {})
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or set(payload) != REGRESSION_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or not _strict_mapping(
            payload.get("development_evidence"),
            {
                "path": development_artifact["path"],
                "sha256": development_artifact["sha256"],
                "memoryforge_commit": development_artifact["memoryforge_commit"],
                "passed": True,
            },
        )
        or not isinstance(pytest_result.get("failed"), int)
        or pytest_result["failed"] < 1
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
        or payload.get("passed") is not False
    ):
        raise ValueError(
            f"experiment regression Evidence contract failed: {experiment['suite_id']}"
        )
    return 1


def _release_review_state_valid(
    status: str,
    acceptance_evidence: object,
    review_evidence: object,
) -> bool:
    acceptance_passed = (
        isinstance(acceptance_evidence, dict) and acceptance_evidence.get("passed") is True
    )
    if status == "local_gates_passed_review_pending":
        return acceptance_passed and review_evidence is None
    if status == "development_passed_review_failed":
        return (
            acceptance_passed
            and isinstance(review_evidence, dict)
            and review_evidence.get("passed") is False
        )
    if status == "accepted_development":
        return (
            acceptance_passed
            and isinstance(review_evidence, dict)
            and review_evidence.get("passed") is True
        )
    return True


def _validate_review_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("review_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("review-rejected experiment requires review Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if (
        experiment["suite_id"] != "release-candidate-delivery"
        or COMMIT.fullmatch(commit) is None
        or type(artifact.get("passed")) is not bool
    ):
        raise ValueError("invalid experiment review Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    if artifact["passed"]:
        return _validate_release_static_review_acceptance(
            experiment,
            development_artifact,
            payload,
            confirmation,
            commit,
        )
    return _validate_release_static_review_regression(
        experiment,
        development_artifact,
        payload,
        confirmation,
        commit,
    )


def _validate_release_static_review_acceptance(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    commit: str,
) -> int:
    review = payload.get("review")
    holdout = experiment["splits"]["holdout"]
    acceptance = development_artifact.get("acceptance_evidence")
    local_gate_commit = (
        str(acceptance.get("memoryforge_commit")) if isinstance(acceptance, dict) else ""
    )
    base_commit = "569685c2f0bf790819820b821b4768d180c4ee0d"
    artifact_keys = {
        "raw_findings",
        "top_findings",
        "html_report",
        "markdown_report",
        "review_scope",
    }
    if not isinstance(review, dict):
        raise ValueError("release-candidate accepted review Evidence changed")
    candidate_match = re.fullmatch(
        r"demo/results/release_candidate_development_candidate_([1-9][0-9]*)\.json",
        str(development_artifact.get("path")),
    )
    expected_candidate = (
        f"release-development-candidate-{candidate_match.group(1)}"
        if candidate_match is not None
        else ""
    )
    artifacts = review.get("artifacts")
    stats = {
        key: review.get(key)
        for key in (
            "diff_files",
            "reviewed_files",
            "added_lines",
            "deleted_lines",
            "changed_lines",
        )
    }
    if not isinstance(artifacts, dict) or set(artifacts) != artifact_keys:
        raise ValueError("release-candidate accepted review Evidence changed")
    for artifact in artifacts.values():
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise ValueError("release-candidate accepted review Evidence changed")
        _validate_artifact(artifact, "release-candidate accepted static review")
    try:
        scope = json.loads(
            (REPO_ROOT / str(artifacts["review_scope"]["path"])).read_text(encoding="utf-8")
        )
        raw_findings = [
            json.loads(line)
            for line in (REPO_ROOT / str(artifacts["raw_findings"]["path"]))
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        top_findings = json.loads(
            (REPO_ROOT / str(artifacts["top_findings"]["path"])).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        raise ValueError("release-candidate accepted review Evidence changed") from None
    raw_counts = {
        severity: sum(
            _release_review_finding_valid(finding) and finding.get("severity") == severity
            for finding in raw_findings
        )
        for severity in ("P0", "P1", "P2")
    }
    if (
        set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "candidate",
            "review",
            "confirmation",
            "holdout",
            "passed",
        }
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("candidate") != expected_candidate
        or set(review)
        != {
            "scope",
            "base_commit",
            "source_commit",
            "local_gate_commit",
            "status",
            "p0",
            "p1",
            "p2",
            *stats,
            "artifacts",
        }
        or review.get("scope") != f"{base_commit}...{commit}"
        or review.get("base_commit") != base_commit
        or review.get("source_commit") != commit
        or review.get("local_gate_commit") != local_gate_commit
        or review.get("status") != "passed"
        or any(type(review.get(key)) is not int for key in ("p0", "p1", "p2"))
        or any(review.get(key.casefold()) != count for key, count in raw_counts.items())
        or any(raw_counts.values())
        or any(type(value) is not int or value < 0 for value in stats.values())
        or stats["reviewed_files"] < 1
        or stats["diff_files"] < stats["reviewed_files"]
        or stats["changed_lines"] != stats["added_lines"] + stats["deleted_lines"]
        or not _git_commit_descends_from(
            local_gate_commit,
            development_artifact["memoryforge_commit"],
        )
        or not _git_commit_descends_from(commit, local_gate_commit)
        or _git_diff_stats(base_commit, commit) != stats
        or not _strict_mapping(
            scope,
            {
                "schema_version": 1,
                "base_commit": base_commit,
                "source_commit": commit,
                "diff_mode": "merge_base_to_source",
                **stats,
            },
        )
        or not isinstance(raw_findings, list)
        or any(not _release_review_finding_valid(finding) for finding in raw_findings)
        or not isinstance(top_findings, list)
        or any(not _release_review_finding_valid(finding) for finding in top_findings)
        or not _strict_json_value(top_findings, raw_findings)
        or not _accepted_review_reports_valid(artifacts, f"{base_commit}...{commit}")
        or not _strict_mapping(
            payload.get("confirmation"),
            {
                "path": confirmation["path"],
                "sha256": confirmation["sha256"],
                "status": "not_run",
            },
        )
        or not isinstance(holdout, dict)
        or not _strict_mapping(
            payload.get("holdout"),
            {
                "path": holdout["path"],
                "sha256": holdout["sha256"],
                "status": "not_run",
            },
        )
        or payload.get("passed") is not True
    ):
        raise ValueError("release-candidate accepted review Evidence changed")
    return 1


def _release_review_finding_valid(finding: object) -> bool:
    if not isinstance(finding, dict) or set(finding) != {
        "title",
        "file",
        "start_line",
        "end_line",
        "severity",
        "category",
        "confidence",
        "suggestion",
        "rationale",
    }:
        return False
    return (
        all(
            isinstance(finding.get(key), str) and bool(finding[key])
            for key in ("title", "file", "category", "suggestion", "rationale")
        )
        and finding.get("severity") in {"P0", "P1", "P2"}
        and type(finding.get("start_line")) is int
        and type(finding.get("end_line")) is int
        and 1 <= finding["start_line"] <= finding["end_line"]
        and type(finding.get("confidence")) is int
        and 0 <= finding["confidence"] <= 10
    )


def _accepted_review_reports_valid(artifacts: dict[str, Any], scope: str) -> bool:
    try:
        reports = [
            (REPO_ROOT / str(artifacts[name]["path"])).read_text(encoding="utf-8")
            for name in ("html_report", "markdown_report")
        ]
    except (OSError, KeyError, TypeError, UnicodeError):
        return False
    return all(scope in report and "未发现缺陷" in report for report in reports)


def _validate_release_sdist_regression(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    commit: str,
) -> int:
    if payload.get("candidate") == "release-development-candidate-7-local-gate":
        return _validate_release_local_gate_regression(
            experiment,
            development_artifact,
            payload,
            confirmation,
            commit,
        )
    if payload.get("candidate") in {
        "release-development-candidate-2",
        "release-development-candidate-5",
        "release-development-candidate-6",
    }:
        return _validate_release_static_review_regression(
            experiment,
            development_artifact,
            payload,
            confirmation,
            commit,
        )
    holdout = experiment["splits"]["holdout"]
    if (
        set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "candidate",
            "release_build",
            "root_cause",
            "confirmation",
            "holdout",
            "passed",
        }
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or not _git_commit_descends_from(development_artifact["memoryforge_commit"], commit)
        or payload.get("candidate") != "release-development-candidate-2-preflight"
        or not _strict_mapping(
            payload.get("release_build"),
            {
                "command": "python scripts/build_release.py --output <external-release-directory>",
                "status": "failed",
                "classification": "sdist_clean_room_path_alias",
                "error": "sdist clean-room import escaped or reported the wrong version",
                "output_created": False,
                "confirmation_executed": False,
                "holdout_executed": False,
            },
        )
        or not _strict_mapping(
            payload.get("root_cause"),
            {
                "summary": (
                    "The macOS /var to /private/var alias made equivalent environment and import "
                    "paths compare as different paths."
                ),
                "fix": (
                    "Resolve the clean-room environment path before checking import ownership; "
                    "keep the strict ownership check."
                ),
            },
        )
        or payload.get("confirmation")
        != {
            "path": confirmation["path"],
            "sha256": confirmation["sha256"],
            "status": "not_run",
        }
        or not isinstance(holdout, dict)
        or payload.get("holdout")
        != {
            "path": holdout["path"],
            "sha256": holdout["sha256"],
            "status": "not_run",
        }
        or payload.get("passed") is not False
    ):
        raise ValueError("release-candidate sdist regression Evidence changed")
    return 1


def _validate_release_local_gate_regression(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    commit: str,
) -> int:
    holdout = experiment["splits"]["holdout"]
    if (
        set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "candidate",
            "local_gate",
            "root_cause",
            "confirmation",
            "holdout",
            "passed",
        }
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or not _git_commit_descends_from(commit, development_artifact["memoryforge_commit"])
        or payload.get("candidate") != "release-development-candidate-7-local-gate"
        or not _strict_mapping(
            payload.get("local_gate"),
            {
                "platform": "macos",
                "command": "scripts/check_local.sh",
                "status": "failed",
                "classification": "outdated_sdist_manifest_contract",
                "pytest": {"passed": 598, "failed": 1, "skipped": 0},
                "failed_test": (
                    "tests/test_local_tooling.py::test_package_and_cli_versions_match_v030"
                ),
                "linux_status": "not_run",
            },
        )
        or not _strict_mapping(
            payload.get("root_cause"),
            {
                "summary": (
                    "The local tooling test still required the superseded sdist.exclude "
                    "contract after Candidate 7 switched to an explicit stable sdist.include set."
                ),
                "fix": (
                    "Assert the exact stable sdist include set and package README instead of "
                    "the removed exclusion."
                ),
            },
        )
        or not _strict_mapping(
            payload.get("confirmation"),
            {
                "path": confirmation["path"],
                "sha256": confirmation["sha256"],
                "status": "not_run",
            },
        )
        or not isinstance(holdout, dict)
        or not _strict_mapping(
            payload.get("holdout"),
            {
                "path": holdout["path"],
                "sha256": holdout["sha256"],
                "status": "not_run",
            },
        )
        or payload.get("passed") is not False
    ):
        raise ValueError("release-candidate local gate regression Evidence changed")
    return 1


def _validate_release_static_review_regression(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    commit: str,
) -> int:
    holdout = experiment["splits"]["holdout"]
    candidate = payload.get("candidate")
    if candidate == "release-development-candidate-2":
        expected_review = {
            "scope": "origin/main...HEAD",
            "status": "failed",
            "p0": 0,
            "p1": 5,
            "p2": 1,
            "failures": [
                "benchmark_summary_source_race",
                "isolated_build_evidence_duplication",
                "document_claim_contradiction",
                "clean_room_not_run",
                "secret_key_privacy",
                "acceptance_boolean_identity",
            ],
        }
        expected_root_cause = {
            "summary": (
                "Release Evidence checks still allowed claims stronger than retained "
                "independently verifiable data."
            ),
            "fix": (
                "Retain both isolated builds, bind a structured release claim, require exact "
                "clean-room checks, scan secret-valued keys, and recheck source identity before "
                "summary publication."
            ),
        }
        acceptance = development_artifact.get("acceptance_evidence")
        review_ancestry_valid = (
            isinstance(acceptance, dict)
            and _git_commit_descends_from(commit, development_artifact["memoryforge_commit"])
            and _git_commit_descends_from(commit, str(acceptance.get("memoryforge_commit")))
            and _git_diff_stats(
                "569685c2f0bf790819820b821b4768d180c4ee0d",
                commit,
            ).get("changed_lines", 0)
            > 0
        )
    elif candidate == "release-development-candidate-5":
        expected_review = {
            "scope": "origin/main...HEAD",
            "status": "failed",
            "p0": 0,
            "p1": 10,
            "p2": 2,
            "failures": [
                "source_snapshot_toctou",
                "showcase_privacy_not_measured",
                "benchmark_summary_content",
                "sha256sums_contract",
                "retained_path_alias",
                "package_identity",
                "document_claim_contradiction",
                "reproducibility_binding",
                "acceptance_commit_binding",
                "evidence_type_strictness",
                "retained_support_artifacts",
                "provenance_schema",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_5/comments.jsonl"
                    ),
                    "sha256": ("3b20a97512440e1d5a71f3d5f7cab15358442c81e3520dedb7b744a8f3a8e821"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_5/"
                        "final_comments.json"
                    ),
                    "sha256": ("34f1263129d1e3f421c26007e47a1ce87e0e7ecbb41b7092c8825e2164a2f459"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_5/report.html"
                    ),
                    "sha256": ("0020c7214756fc82b2ab5065c1a5466b17406a96231ff5b0ef303001b614399f"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_5/report.md"
                    ),
                    "sha256": ("07847196414e274eb961ab1e092de1e80076b638be9ae1a1d7568884b8086dc4"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Release gates still allowed artifact, provenance, Commit, summary, and privacy "
                "claims stronger than retained independently verifiable data."
            ),
            "fix": (
                "Build from a fixed source snapshot and enforce exact package, provenance, "
                "SHA256SUMS, summary, artifact, Commit, document, privacy, and JSON type contracts."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        review_ancestry_valid = _git_commit_descends_from(
            commit,
            development_artifact["memoryforge_commit"],
        )
    elif candidate == "release-development-candidate-6":
        expected_review = {
            "scope": "origin/main...HEAD",
            "status": "failed",
            "p0": 0,
            "p1": 9,
            "p2": 3,
            "failures": [
                "isolated_build_pythonpath",
                "showcase_windows_path_privacy",
                "unlisted_release_file",
                "private_tmp_privacy",
                "artifact_root_symlink",
                "nested_sha256sums",
                "case_count_type",
                "support_schema_type",
                "candidate_3_reproducibility_audit",
                "development_gate_artifact_mismatch",
                "support_semantic_closure",
                "local_gate_benchmark_provenance",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_6/comments.jsonl"
                    ),
                    "sha256": ("c12104b0f0da87c8348eed667a8843c663030abe827f80dd020a0b0b34803dec"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_6/"
                        "final_comments.json"
                    ),
                    "sha256": ("dc6780587fd000d667a6c556bdb0312b475ee836075bd398cbac8c8b3dceae6a"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_6/report.html"
                    ),
                    "sha256": ("fd20f774f76ec959a021e4bc8de9b704bb34bb251bfe920a58cc57163b5d14c9"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_6/report.md"
                    ),
                    "sha256": ("2d1a2e77d2439ca1bf0abc5c364831fc20443ab84aeac40a1305c213fa364039"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Development and local-gate acceptance did not require the same release bytes, "
                "while retained support and privacy consumers remained weaker than their "
                "producers."
            ),
            "fix": (
                "Freeze stable package inputs before development, require byte-identical "
                "acceptance artifacts, and enforce exact semantic, path, privacy, and type "
                "contracts."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        review_ancestry_valid = _git_commit_descends_from(
            commit,
            development_artifact["memoryforge_commit"],
        )
    elif candidate == "release-development-candidate-7":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "a044337347b9c6884ea660c7568c4e3911c84521"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "a044337347b9c6884ea660c7568c4e3911c84521",
            "status": "failed",
            "p0": 0,
            "p1": 10,
            "p2": 3,
            "failures": [
                "cross_checkout_eol_instability",
                "retained_summary_semantic_closure",
                "frozen_manifest_status_closure",
                "confirmation_case_count_closure",
                "acceptance_summary_identity",
                "negative_summary_commit_binding",
                "release_symlink_ownership",
                "workspace_query_replay",
                "workspace_cli_environment",
                "showcase_evaluation_integrity",
                "retained_sha256sums_replay",
                "candidate_5_review_scope",
                "candidate_6_review_scope",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_7/comments.jsonl"
                    ),
                    "sha256": ("3ede07a4a7165f6d645bfb4deb9ab43b08518c3ab05c06d3385ff37a71388ad2"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_7/"
                        "final_comments.json"
                    ),
                    "sha256": ("0aa466e7eb7147e8286a6004c50db63abbfb09850cffb7abf001e02c46ec8f6e"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_7/report.html"
                    ),
                    "sha256": ("c2e007be529a457db92eda31015800dbd0f75e93f8da7898a491983adce2e194"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_7/report.md"
                    ),
                    "sha256": ("7af1d2138b50817f8add1aa686d7d53272f0064ee033a4455ec835adcd7b2081"),
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_7/"
                        "review-scope.json"
                    ),
                    "sha256": ("55dc5e0dd5512dd34c82f5e886f577f9ec098bb4b5f06d5231c56d8776be16ed"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Release reproducibility and Evidence consumers still accepted "
                "platform-dependent checkouts, incomplete semantic closure, and "
                "non-replayable retained support artifacts."
            ),
            "fix": (
                "Freeze checkout bytes, validate manifests and summaries semantically, reject "
                "symlinks, prove Workspace replay, and bind canonical SHA256 and review-scope "
                "artifacts."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        review_ancestry_valid = _git_commit_descends_from(
            commit,
            development_artifact["memoryforge_commit"],
        ) and _strict_mapping(
            scope_payload,
            {
                "schema_version": 1,
                "base_commit": expected_review["base_commit"],
                "source_commit": expected_review["source_commit"],
                "diff_mode": "merge_base_to_source",
                "reviewed_files": 79,
                "changed_lines": 14125,
            },
        )
    elif candidate == "release-development-candidate-8":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "f4dde0904e5bcaeb78be6d7a32e74a6beae5679a"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "f4dde0904e5bcaeb78be6d7a32e74a6beae5679a",
            "status": "failed",
            "p0": 0,
            "p1": 4,
            "p2": 2,
            "failures": [
                "summary_schema_downgrade",
                "registry_schema_type",
                "historical_review_scope_stats",
                "version_probe_environment",
                "showcase_metric_case_mismatch",
                "local_gate_code_wiki_semantic_closure",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_8/comments.jsonl"
                    ),
                    "sha256": ("22ed19f0cccfdf53ea1831f661eccd1c41414f8aab4993ca02798a7698a5fd26"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_8/"
                        "final_comments.json"
                    ),
                    "sha256": ("1f33de965191482cd3e38f37f3a41cde8b73c6a406c0f02cfcaaa7b07b74d23c"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_8/report.html"
                    ),
                    "sha256": ("4e2b3b16534d84dcd8bf4558ee5cab620b49a2406bd7b3d601ae5da54bae37a8"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_8/report.md"
                    ),
                    "sha256": ("12aa690de93c6f274411d04e9344a4679e5f96fd67da99970433a56255dbd8e6"),
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_8/"
                        "review-scope.json"
                    ),
                    "sha256": ("b838d75cdd3d75f44f99c85cd67cfb080063dea90aa209e4340b44242109a2f2"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Candidate 8 closed the first review findings, but several consumers still "
                "accepted weaker schemas, incomplete platform provenance, and inconsistent "
                "audit metadata."
            ),
            "fix": (
                "Freeze schema 2 by Commit ancestry, enforce strict root types and exact Code "
                "Wiki contracts, recompute review scope stats, isolate version probes, and "
                "derive Showcase metrics from final cases."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        review_ancestry_valid = _git_commit_descends_from(
            commit,
            development_artifact["memoryforge_commit"],
        ) and _strict_mapping(
            scope_payload,
            {
                "schema_version": 1,
                "base_commit": expected_review["base_commit"],
                "source_commit": expected_review["source_commit"],
                "diff_mode": "merge_base_to_source",
                "reviewed_files": 97,
                "diff_files": 161,
                "added_lines": 17612,
                "deleted_lines": 192,
                "changed_lines": 17804,
            },
        )
    elif candidate == "release-development-candidate-9":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "c23de5915e6237700c0aa8e03a14e44583ab1049"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "c23de5915e6237700c0aa8e03a14e44583ab1049",
            "status": "failed",
            "p0": 0,
            "p1": 10,
            "p2": 4,
            "failures": [
                "release_builder_host_runtime_dependency",
                "release_build_index_reproducibility",
                "retained_package_metadata",
                "summary_registry_snapshot_race",
                "experiment_summary_identity",
                "summary_acceptance_ancestry",
                "summary_schema_type",
                "workspace_drill_semantic_retention",
                "release_builder_coverage_environment",
                "code_wiki_raw_evidence_semantics",
                "powershell_environment_restoration",
                "release_review_state_machine",
                "historical_review_scope_recomputation",
                "retained_provenance_privacy",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_9/comments.jsonl"
                    ),
                    "sha256": ("03d0f3174e1e9952139fbe9aa732ba3f6b62f0a71473c543f8473609443fb7ab"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_9/"
                        "final_comments.json"
                    ),
                    "sha256": ("3b529d56b5367f8e7a38823e70b07ffb84519fc8cf4061a25b67d6f3a085da12"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_9/report.html"
                    ),
                    "sha256": ("a46154b192318327132a94a90e300e3111be21719da557a557fc50008573d4d5"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_9/report.md"
                    ),
                    "sha256": ("4d074a85f8afcc704a2672c271ac6877d488262a47c3a62370a52f8ad30273c0"),
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_9/"
                        "review-scope.json"
                    ),
                    "sha256": ("f2835d8aa5d2b3f7cbcbe4ddad57c6d7bc7448bccb4992077ad04ff77f9ce2f9"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Candidate 9 closed Candidate 8 findings, but final review found incomplete "
                "retained semantics, review-state governance, environment isolation, and "
                "historical replay checks."
            ),
            "fix": (
                "Retain independently verifiable query and Code Wiki cases, make review an "
                "explicit release state, isolate every build subprocess, and replay all package, "
                "privacy, and review identities from fixed inputs."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        expected_stats = {
            "diff_files": 189,
            "reviewed_files": 114,
            "added_lines": 21107,
            "deleted_lines": 181,
            "changed_lines": 21288,
        }
        review_ancestry_valid = (
            _git_commit_descends_from(
                commit,
                development_artifact["memoryforge_commit"],
            )
            and _git_diff_stats(
                str(expected_review["base_commit"]),
                str(expected_review["source_commit"]),
            )
            == expected_stats
            and _strict_mapping(
                scope_payload,
                {
                    "schema_version": 1,
                    "base_commit": expected_review["base_commit"],
                    "source_commit": expected_review["source_commit"],
                    "diff_mode": "merge_base_to_source",
                    **expected_stats,
                },
            )
        )
    elif candidate == "release-development-candidate-11":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "43d5c80852595c7b49e46c66f70ced82c53cf7d0"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "43d5c80852595c7b49e46c66f70ced82c53cf7d0",
            "status": "failed",
            "p0": 0,
            "p1": 8,
            "p2": 4,
            "failures": [
                "build_extra_package_sources",
                "package_metadata_contract",
                "source_version_assignment",
                "summary_registry_commit_binding",
                "workspace_verify_evidence",
                "query_privacy_paths",
                "retained_import_path",
                "code_wiki_git_identity",
                "powershell_initialization_restore",
                "accepted_review_unreachable",
                "delivery_artifact_closure",
                "passed_review_negative_classification",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_11/"
                        "comments.jsonl"
                    ),
                    "sha256": ("802a69ed54e7c820cbecdce401c41c05dfb7205fcde58e66f696537ed8b7f5c1"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_11/"
                        "final_comments.json"
                    ),
                    "sha256": ("ad1b68ab36cfec759597a0a399f6457dea93228fb2def6ac2981498850bbdcc2"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_11/report.html"
                    ),
                    "sha256": ("b7d8b30dc69f9ec9efdc2c4b6356c74e5fb375f2778c3e375cc1dd87007cd324"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_11/report.md"
                    ),
                    "sha256": ("1c2910525e2b940a0eced5adb6aff724cae971adf4fb2ff637d3ef64935ee7db"),
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_11/"
                        "review-scope.json"
                    ),
                    "sha256": ("08d3ca681251583605d32c1f18aa75ab70e94f67f706539c76eb5b3461120cc5"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Candidate 11 made retained Evidence replayable, but final review found remaining "
                "exact-schema, environment, privacy, artifact-root, and accepted-review "
                "transition gaps."
            ),
            "fix": (
                "Use strict package and query schemas, eliminate every host package-source "
                "override, close artifact roots, bind summaries to Git snapshots, and support "
                "both failed and passed review sidecars."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        expected_stats = {
            "diff_files": 228,
            "reviewed_files": 136,
            "added_lines": 28740,
            "deleted_lines": 162,
            "changed_lines": 28902,
        }
        review_ancestry_valid = (
            _git_commit_descends_from(
                commit,
                development_artifact["memoryforge_commit"],
            )
            and _git_diff_stats(
                str(expected_review["base_commit"]),
                str(expected_review["source_commit"]),
            )
            == expected_stats
            and _strict_mapping(
                scope_payload,
                {
                    "schema_version": 1,
                    "base_commit": expected_review["base_commit"],
                    "source_commit": expected_review["source_commit"],
                    "diff_mode": "merge_base_to_source",
                    **expected_stats,
                },
            )
        )
    elif candidate == "release-development-candidate-12":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "e7d3fef9312b4b6c1683e12984ae00cd1b21b343"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "e7d3fef9312b4b6c1683e12984ae00cd1b21b343",
            "status": "failed",
            "p0": 0,
            "p1": 9,
            "p2": 7,
            "failures": [
                "sdist_constraint_bypass",
                "snapshot_post_checkout_hook",
                "accepted_review_commit_ancestry",
                "malformed_passed_review_findings",
                "release_split_result_absence",
                "summary_package_commit_binding",
                "passed_review_candidate_identity",
                "powershell_artifact_contract",
                "development_output_immutability",
                "package_source_blob_binding",
                "drill_basic_credentials",
                "drill_workspace_commit_identity",
                "drill_citation_source_identity",
                "candidate2_review_binding",
                "candidate2_review_scope_replay",
                "code_wiki_suite_commit_binding",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_12/"
                        "comments.jsonl"
                    ),
                    "sha256": ("de7dd7ee078e57e37f11371a1f05f0246420a4eec990128f93070de6b69485ee"),
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_12/"
                        "final_comments.json"
                    ),
                    "sha256": ("4301c1ce292f7fe73f4e0e4026d1784283573ad188369eb84823c766d9686d1f"),
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_12/report.html"
                    ),
                    "sha256": ("058299f48a25b00e6b4ec5887d41c7df6e3fc9fb090d4e9bd94e38fb41bff1de"),
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_12/report.md"
                    ),
                    "sha256": ("2cae8c03b30ae96139d8679bd5e1a312265c07b3ea6e738caee488ba641c3760"),
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_12/"
                        "review-scope.json"
                    ),
                    "sha256": ("5c0d68d1a025bf09daf07fa2ab34dea4566217c3cd4702a33fe26f9a0bbaecc6"),
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Candidate 12 closed the prior exact-schema findings, but final review found "
                "remaining source-snapshot, package-member, split-closure, "
                "platform-publication, and accepted-review identity gaps."
            ),
            "fix": (
                "Bind every producer and consumer to the same Commit snapshot, fail closed on "
                "malformed review and credential data, make publication atomic, and close "
                "POSIX/PowerShell artifact contracts."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        expected_stats = {
            "diff_files": 258,
            "reviewed_files": 154,
            "added_lines": 34045,
            "deleted_lines": 249,
            "changed_lines": 34294,
        }
        review_ancestry_valid = (
            _git_commit_descends_from(
                commit,
                development_artifact["memoryforge_commit"],
            )
            and _git_diff_stats(
                str(expected_review["base_commit"]),
                str(expected_review["source_commit"]),
            )
            == expected_stats
            and _strict_mapping(
                scope_payload,
                {
                    "schema_version": 1,
                    "base_commit": expected_review["base_commit"],
                    "source_commit": expected_review["source_commit"],
                    "diff_mode": "merge_base_to_source",
                    **expected_stats,
                },
            )
        )
    elif candidate == "release-development-candidate-13":
        expected_review = {
            "scope": (
                "569685c2f0bf790819820b821b4768d180c4ee0d..."
                "2fff47c47f0cc3c4bbb98eb02c0acb77bce1718e"
            ),
            "base_commit": "569685c2f0bf790819820b821b4768d180c4ee0d",
            "source_commit": "2fff47c47f0cc3c4bbb98eb02c0acb77bce1718e",
            "status": "failed",
            "p0": 0,
            "p1": 10,
            "p2": 0,
            "failures": [
                "local_gate_worktree_snapshot_binding",
                "local_gate_python_checkout_binding",
                "local_gate_package_source_environment",
                "workspace_drill_git_environment",
                "development_runner_git_environment",
                "powershell_python_path",
                "registry_artifact_exact_schema",
                "accepted_review_archive_consistency",
            ],
            "artifacts": {
                "raw_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_13/"
                        "comments.jsonl"
                    ),
                    "sha256": "13a40d85a5f5837e6e655e6642456cd25619f97094327d9f00005f885c6ac630",
                },
                "top_findings": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_13/"
                        "final_comments.json"
                    ),
                    "sha256": "cabfc5ccc343b3ffe6c6bcc979b1bfcfc1230c449a3e39e59438d5e7bc855b05",
                },
                "html_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_13/report.html"
                    ),
                    "sha256": "c40857942bd2d3fc5032b0d2da53ec406c41f5fcc0f3053b5f8989611a07c188",
                },
                "markdown_report": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_13/report.md"
                    ),
                    "sha256": "536911849dab2fe1b8dbb0e8e5512248a7a1d242a7795591cdb0eadcb0878e87",
                },
                "review_scope": {
                    "path": (
                        "demo/results/artifacts/release_candidate_review_candidate_13/"
                        "review-scope.json"
                    ),
                    "sha256": "47d3a617a06b9399135ed83f43fbe09298f6c6bd0be5144a3d22db5b43509cb1",
                },
            },
        }
        expected_root_cause = {
            "summary": (
                "Candidate 13 closed Candidate 12 findings, but final review found local-gate "
                "source identity, package-source environment, Git-environment, Registry schema, "
                "and accepted-review archive gaps."
            ),
            "fix": (
                "Run every gate against one clean fixed Commit and its package source, scrub Git "
                "and package-manager environments, require exact Registry schemas, and bind "
                "accepted review archives to the zero-finding result."
            ),
        }
        for artifact in expected_review["artifacts"].values():
            _validate_artifact(artifact, "release-candidate static review")
        scope_artifact = expected_review["artifacts"]["review_scope"]
        scope_payload = json.loads(
            (REPO_ROOT / str(scope_artifact["path"])).read_text(encoding="utf-8")
        )
        expected_stats = {
            "diff_files": 288,
            "reviewed_files": 172,
            "added_lines": 39376,
            "deleted_lines": 477,
            "changed_lines": 39853,
        }
        review_ancestry_valid = (
            _git_commit_descends_from(
                commit,
                development_artifact["memoryforge_commit"],
            )
            and _git_diff_stats(
                str(expected_review["base_commit"]),
                str(expected_review["source_commit"]),
            )
            == expected_stats
            and _strict_mapping(
                scope_payload,
                {
                    "schema_version": 1,
                    "base_commit": expected_review["base_commit"],
                    "source_commit": expected_review["source_commit"],
                    "diff_mode": "merge_base_to_source",
                    **expected_stats,
                },
            )
        )
    else:
        raise ValueError("unknown release-candidate static review Evidence")
    if (
        set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "candidate",
            "review",
            "root_cause",
            "confirmation",
            "holdout",
            "passed",
        }
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or not review_ancestry_valid
        or not _strict_mapping(payload.get("review"), expected_review)
        or not _strict_mapping(payload.get("root_cause"), expected_root_cause)
        or payload.get("confirmation")
        != {
            "path": confirmation["path"],
            "sha256": confirmation["sha256"],
            "status": "not_run",
        }
        or not isinstance(holdout, dict)
        or payload.get("holdout")
        != {
            "path": holdout["path"],
            "sha256": holdout["sha256"],
            "status": "not_run",
        }
        or payload.get("passed") is not False
    ):
        raise ValueError("release-candidate static review Evidence changed")
    return 1


def _validate_linux_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("linux_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("cross-platform experiment requires Linux Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if (
        COMMIT.fullmatch(commit) is None
        or artifact.get("passed") is not True
        or not _git_commit_descends_from(commit, str(development_artifact["memoryforge_commit"]))
    ):
        raise ValueError("invalid Linux Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    contract = LINUX_EVIDENCE_CONTRACTS.get(str(artifact["path"]))
    local_gate = payload.get("local_gate")
    runtime = payload.get("runtime")
    bound_artifacts = isinstance(contract, dict) and contract.get("bound_artifacts") is True
    expected_local_gate_keys = (
        LOCAL_GATE_KEYS | {"artifacts"} | ({"artifact_files"} if bound_artifacts else set())
    )
    if (
        contract is None
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "runtime",
            "local_gate",
            "confirmation",
            "passed",
        }
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or not _strict_mapping(runtime, contract["runtime"])
        or not isinstance(local_gate, dict)
        or set(local_gate) != expected_local_gate_keys
        or local_gate.get("command") != "scripts/check_local.sh"
        or local_gate.get("ruff_check") != "passed"
        or local_gate.get("ruff_format") != "passed"
        or local_gate.get("strict_mypy") != "passed"
        or not _strict_mapping(
            local_gate.get("registry_validation"),
            contract["registry_validation"],
        )
        or local_gate.get("dependency_check") != "passed"
        or not _strict_mapping(local_gate.get("pytest"), contract["pytest"])
        or local_gate.get("wheel_clean_room") != "passed"
        or local_gate.get("sdist_clean_room") != "passed"
        or local_gate.get("pip_check") != "passed"
        or local_gate.get("cli_version_smoke") != "passed"
        or not isinstance(local_gate.get("artifacts"), dict)
        or set(local_gate["artifacts"])
        != {
            "wheel_sha256",
            "sdist_sha256",
            "provenance_sha256",
            "sha256sums_sha256",
        }
        or any(SHA256.fullmatch(str(value)) is None for value in local_gate["artifacts"].values())
        or (
            bound_artifacts
            and not _validate_bound_gate_artifacts(
                local_gate.get("artifact_files"),
                local_gate.get("artifacts"),
                commit,
                require_clean_sdist=contract.get("clean_sdist") is True,
            )
        )
        or payload.get("confirmation")
        != {
            "path": confirmation["path"],
            "sha256": confirmation["sha256"],
            "status": "not_run",
        }
        or payload.get("passed") is not True
    ):
        raise ValueError("Linux Evidence contract failed")
    return 1


def _validate_acceptance_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("acceptance_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("accepted experiment requires local gate Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if COMMIT.fullmatch(commit) is None or artifact.get("passed") is not True:
        raise ValueError("invalid experiment acceptance Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    if experiment["suite_id"] == "release-candidate-delivery":
        return _validate_release_candidate_acceptance_evidence(
            experiment,
            development_artifact,
            payload,
            confirmation,
            acceptance_commit=commit,
        )
    local_gate = payload.get("local_gate", {})
    pytest_result = local_gate.get("pytest", {})
    registry_result = local_gate.get("registry_validation", {})
    artifacts = local_gate.get("artifacts")
    multi_source = experiment["suite_id"] == "multi-source-coverage-selection"
    cross_platform_mac = (
        experiment["suite_id"] == "cross-platform-delivery"
        and development_artifact["evidence_revision"] >= 6
    )
    bound_artifacts = cross_platform_mac and development_artifact["evidence_revision"] >= 8
    mac_contract = (
        CROSS_PLATFORM_MAC_GATE_CONTRACTS.get(str(artifact["path"])) if cross_platform_mac else None
    )
    requires_artifacts = experiment["suite_id"] in {
        "support-score.learn-claude-code",
        "multi-source-coverage-selection",
        "folder-import-lifecycle",
        "github-thread-import-lifecycle",
        "static-showcase",
        "cross-platform-delivery",
    }
    expected_payload_keys = LOCAL_GATE_EVIDENCE_KEYS | (
        {"regression_evidence"} if multi_source else set()
    )
    if cross_platform_mac:
        expected_payload_keys.add("runtime")
    expected_local_gate_keys = (
        LOCAL_GATE_KEYS
        | ({"artifacts"} if requires_artifacts else set())
        | ({"artifact_files"} if bound_artifacts else set())
    )
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or set(payload) != expected_payload_keys
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or (
            cross_platform_mac
            and not _git_commit_descends_from(
                commit,
                str(development_artifact["memoryforge_commit"]),
            )
        )
        or (
            cross_platform_mac
            and not _strict_mapping(payload.get("runtime"), CROSS_PLATFORM_MAC_RUNTIME)
        )
        or (
            cross_platform_mac
            and (
                mac_contract is None
                or not _strict_mapping(
                    registry_result,
                    mac_contract["registry_validation"],
                )
                or not _strict_mapping(pytest_result, mac_contract["pytest"])
            )
        )
        or payload.get("development_evidence", {}).get("path") != development_artifact["path"]
        or payload.get("development_evidence", {}).get("sha256") != development_artifact["sha256"]
        or payload.get("development_evidence", {}).get("memoryforge_commit")
        != development_artifact["memoryforge_commit"]
        or payload.get("development_evidence", {}).get("passed") is not True
        or not isinstance(local_gate, dict)
        or set(local_gate) != expected_local_gate_keys
        or local_gate.get("command") != "scripts/check_local.sh"
        or local_gate.get("ruff_check") != "passed"
        or local_gate.get("ruff_format") != "passed"
        or local_gate.get("strict_mypy") != "passed"
        or not isinstance(registry_result, dict)
        or set(registry_result)
        != {"suite_count", "experiment_count", "evidence_count", "qa_case_count"}
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in registry_result.values()
        )
        or (
            development_artifact.get("status") == "accepted_development"
            and registry_result != FINAL_ACCEPTANCE_REGISTRY_COUNTS.get(str(experiment["suite_id"]))
        )
        or local_gate.get("dependency_check") != "passed"
        or not isinstance(pytest_result.get("passed"), int)
        or isinstance(pytest_result.get("passed"), bool)
        or pytest_result["passed"] < 1
        or not isinstance(pytest_result.get("failed"), int)
        or isinstance(pytest_result.get("failed"), bool)
        or pytest_result.get("failed") != 0
        or not isinstance(pytest_result.get("coverage_percent"), int)
        or isinstance(pytest_result.get("coverage_percent"), bool)
        or not 0 <= pytest_result["coverage_percent"] <= 100
        or local_gate.get("wheel_clean_room") != "passed"
        or local_gate.get("sdist_clean_room") != "passed"
        or local_gate.get("pip_check") != "passed"
        or local_gate.get("cli_version_smoke") != "passed"
        or (
            requires_artifacts
            and (
                not isinstance(artifacts, dict)
                or set(artifacts)
                != {
                    "wheel_sha256",
                    "sdist_sha256",
                    "provenance_sha256",
                    "sha256sums_sha256",
                }
                or any(SHA256.fullmatch(str(value)) is None for value in artifacts.values())
            )
        )
        or (
            bound_artifacts
            and not _validate_bound_gate_artifacts(
                local_gate.get("artifact_files"),
                artifacts,
                commit,
                require_clean_sdist=development_artifact["evidence_revision"] >= 10,
            )
        )
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
        or payload.get("passed") is not True
    ):
        raise ValueError(
            f"experiment acceptance Evidence contract failed: {experiment['suite_id']}"
        )
    if multi_source:
        _validate_multi_source_support_regression(payload.get("regression_evidence"))
        return 2
    return 1


def _validate_release_candidate_acceptance_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    *,
    acceptance_commit: str | None = None,
) -> int:
    holdout = experiment["splits"]["holdout"]
    commit = str(payload.get("memoryforge_commit"))
    platforms = payload.get("platforms")
    contracts = {
        "macos": {
            "runtime": {
                "virtualization": "native",
                "system": "Darwin",
                "machine": "arm64",
                "implementation": "CPython",
                "python": "3.11.15",
                "hosted_runner": False,
            },
            "pytest": {
                "passed": 574,
                "skipped": 0,
                "failed": 0,
                "coverage_percent": 88,
            },
            "artifacts": {
                "wheel_sha256": "05e3494a476bc46c1138ba45d9b732132c6f545c428d1a4e7ac47d405675cbe7",
                "sdist_sha256": "856fa7dc13eb9cd9420504d02145781a6673670162d01de034db13438b680c0e",
                "provenance_sha256": (
                    "33929dca45d711f64e6d4b29dfc43f49111643d83f2c1e366b29bd5516aae546"
                ),
                "sha256sums_sha256": (
                    "f3f92e553580bb7e282fee86283c69cb52d0766530c42c4a523461bfb1fd03de"
                ),
            },
        },
        "linux": {
            "runtime": {
                "virtualization": "Lima 2.2.0 local VM",
                "distribution": "Debian GNU/Linux 12",
                "kernel": "Linux 6.1.0-50-cloud-arm64",
                "architecture": "aarch64",
                "implementation": "CPython",
                "python": "3.11.2",
                "hosted_runner": False,
            },
            "pytest": {
                "passed": 571,
                "skipped": 3,
                "failed": 0,
                "coverage_percent": 88,
            },
            "artifacts": {
                "wheel_sha256": "05e3494a476bc46c1138ba45d9b732132c6f545c428d1a4e7ac47d405675cbe7",
                "sdist_sha256": "856fa7dc13eb9cd9420504d02145781a6673670162d01de034db13438b680c0e",
                "provenance_sha256": (
                    "f291b2f3b1eaa6dec209f33796ba893eea77335d9cf1cb10483b969f49f512d8"
                ),
                "sha256sums_sha256": (
                    "821edc4bdc9bfedff5dff08726633aae3af1caea939917c11fbf0273b2a2df20"
                ),
            },
        },
    }
    evidence_revision = development_artifact["evidence_revision"]
    development_package: dict[str, Any] | None = None
    if evidence_revision >= 8:
        try:
            development_payload = json.loads(
                (REPO_ROOT / str(development_artifact["path"])).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "release-candidate development package Evidence is unreadable"
            ) from exc
        candidate_package = development_payload.get("release_artifacts", {}).get("package")
        if not isinstance(candidate_package, dict):
            raise ValueError("release-candidate development package Evidence is missing")
        development_package = candidate_package
    if evidence_revision == 3:
        contracts["macos"]["pytest"] = {
            "passed": 583,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "b6f6cc6f869c9bcfbb83a162d5b1bb622a44f0b86855b6db9a183c21845fb803",
            "sdist_sha256": "f908bc5431d2486cc380c5abb30d255004a14ee9faf9c4040835c5f4539fc4d2",
            "provenance_sha256": "cebd3bea5a81dee64b00933fdf1a382d1788332941dd5467b3e2d5342d43816f",
            "sha256sums_sha256": "b43acb08dd053a34381121d9d42f62e89ea3ca287b7b3290b3913153a1006af6",
        }
        contracts["linux"]["pytest"] = {
            "passed": 580,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "b6f6cc6f869c9bcfbb83a162d5b1bb622a44f0b86855b6db9a183c21845fb803",
            "sdist_sha256": "f908bc5431d2486cc380c5abb30d255004a14ee9faf9c4040835c5f4539fc4d2",
            "provenance_sha256": "895175035ddfeff155a88e42c93b7bbf5790b1351f7fd600f871ddefc4ec2fc4",
            "sha256sums_sha256": "ee8782ce98d87d99af106e53b4a251e585d3705cd6c6c89ce6bc4496531700c3",
        }
    elif evidence_revision == 4:
        contracts["macos"]["pytest"] = {
            "passed": 586,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "466f8ebab1253a26bb2609596e54fb6539ebddff7df22598c95999ef525a7843",
            "sdist_sha256": "7c2de1bc1fff12a69e9b222f1291a624d9780ce18004a49c177fb8446e3434f7",
            "provenance_sha256": "246e08328a38fd5da2cb3a216c2b5bbfede87f34c021686134821f7804dd621a",
            "sha256sums_sha256": "13709bbf111dc7caa5978f2f20fd5d36364e37bf72838f0fea927bc06a73e5d9",
        }
        contracts["linux"]["pytest"] = {
            "passed": 583,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "466f8ebab1253a26bb2609596e54fb6539ebddff7df22598c95999ef525a7843",
            "sdist_sha256": "7c2de1bc1fff12a69e9b222f1291a624d9780ce18004a49c177fb8446e3434f7",
            "provenance_sha256": "0e8391102c84c744ad785e2c600a379b2f711f310b3708f8495c0eb421faccbb",
            "sha256sums_sha256": "0cef2bd5a6893a9dfbd3a10ddcd07bb881687760680f158ee09db4e912471115",
        }
    elif evidence_revision == 6:
        contracts["macos"]["pytest"] = {
            "passed": 586,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "f96013bf056ddfd5f0bc0da3a2df60b6ca819433b20095745bf9a84d89de6360",
            "sdist_sha256": "8d682330b75a93163dc13fdd4b1ae5b2e901d506b65c3edd18586463ccf78b88",
            "provenance_sha256": "1eb7550eee4798aac5471ff0e7d6e28cc8d15f89f64e977c911613b3fd607759",
            "sha256sums_sha256": "a8c47ec79aeea5fc8e00de64e19baf4183dec6ddde91f4e04de21718f74a607a",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 583,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "f96013bf056ddfd5f0bc0da3a2df60b6ca819433b20095745bf9a84d89de6360",
            "sdist_sha256": "8d682330b75a93163dc13fdd4b1ae5b2e901d506b65c3edd18586463ccf78b88",
            "provenance_sha256": "5371ed2f71f4764a879930f783fd4d71b9a02fec18232379170ece96f31d3a2f",
            "sha256sums_sha256": "2ea23b1d95e9f3ab55d8ddb429983f68dd9beb20b5b11ccaf7febb0be7687064",
        }
    elif evidence_revision == 7:
        contracts["macos"]["pytest"] = {
            "passed": 594,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "a0ddda5469074b9aa4222f42aea95a411316be5878d54833101d0dece3ccc811",
            "sdist_sha256": "dfb59503b821e8eaf641cfede9111753cae52faa74b7920182a64f1e4c6a2eab",
            "provenance_sha256": "b8eee0b5a80ed49fb9c47312441c5f1fe9fd8b4d2e8416f0bb215fdfc2c864ba",
            "sha256sums_sha256": "a81611296be0be13b1625fb126e8f0a11706659b1428fa44b447f243fc40a5b2",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 591,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "a0ddda5469074b9aa4222f42aea95a411316be5878d54833101d0dece3ccc811",
            "sdist_sha256": "dfb59503b821e8eaf641cfede9111753cae52faa74b7920182a64f1e4c6a2eab",
            "provenance_sha256": "5ccf9a5139c79c49d2983aab3dbbed6554344ceb921aff00f53db1f6db0bd7a1",
            "sha256sums_sha256": "e3bce41329457bd208e6a19bcfa3cb3f7c76fe41cce71930ab619b8fec8aaf87",
        }
    elif evidence_revision == 8:
        contracts["macos"]["pytest"] = {
            "passed": 599,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "provenance_sha256": "130fd699ffef0468a02213b79ae8c6dda89e18bcb00e7ab4bc5cdb46f35d671c",
            "sha256sums_sha256": "e96e48a094962b5408540cc8f068d54d3a65c5222c0fd28fc52dd4b0132af0b7",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 596,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "provenance_sha256": "4bd56b02aa13a12ffd1775c0aaa85d038d476bf593ab5efd185af7fd3f4ac2bb",
            "sha256sums_sha256": "5a26f58fd14b7aa7a69b83e898940604d69f13ba6f78c999ed3ed20941fbb527",
        }
    elif evidence_revision == 9:
        contracts["macos"]["pytest"] = {
            "passed": 604,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "provenance_sha256": "52795cdc1e0bdcfb4f74ef96397f7742c797c497c12f3b49a827477e2cc44fca",
            "sha256sums_sha256": "4a4869a388e895ca9186750029b4710aa498f7c9fab3ead331c7cbb42f2a70d5",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 601,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "provenance_sha256": "3c01c2675d45d0bd95dadfcebee5a7c97b2582640023173430e2c47065dd14c9",
            "sha256sums_sha256": "000b08a55111b34d1a3104cdcbe9f9b82552ee4068f8b298efc93fb63c31def7",
        }
    elif evidence_revision == 10:
        contracts["macos"]["pytest"] = {
            "passed": 607,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "f5fc93493984c1259a39f8d35578f98defb5c8adb14be59039bac17489097b9e"
            ),
            "provenance_sha256": "4e633d574d0e5f0cb06904a54949ef81ac07e7303c82bd16c0d823ac5544f381",
            "sha256sums_sha256": "e93b03b762cf9f9f9bee7151e844da223412de0e1375f7b5c5fe3026f678d556",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 604,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "f5fc93493984c1259a39f8d35578f98defb5c8adb14be59039bac17489097b9e"
            ),
            "provenance_sha256": "86e8e7d7518919aead512126d264da3ca31a361d1649bf30c664fdc606440841",
            "sha256sums_sha256": "6a1ad9f239c27e133c80598bfbd8642aea532e7791efa159c143693f8f962c1d",
        }
    elif evidence_revision == 12:
        contracts["macos"]["pytest"] = {
            "passed": 609,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "5421009566d32b77cd0aee34e1f5bf23ddfb9ebd7cad0b6932d9c38d7a84a001"
            ),
            "provenance_sha256": "f53eb9194815a53671688eedfda80cdd85aedde176237680691f4e313f56d6c4",
            "sha256sums_sha256": "025a435030ef4f3274b088e0da4761a359353052f67fb8b9b2af20756adc573d",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 606,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "5421009566d32b77cd0aee34e1f5bf23ddfb9ebd7cad0b6932d9c38d7a84a001"
            ),
            "provenance_sha256": "8c05f1cf087ceb6afebe16fe85c6305e60b9f7479b76fd10650bfe9b77dac3b6",
            "sha256sums_sha256": "22e08675eb0687bcae311abf4d3ae704bbf243d48c418adb11a4a227ba3d473b",
        }
    elif evidence_revision == 13:
        contracts["macos"]["pytest"] = {
            "passed": 623,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "cc55df0423640ebfe92ddf21036fabc3650ca14e342c951ea96c1d0b2c3b7df6"
            ),
            "provenance_sha256": "69fe29e41b3df3d11f5e45203b6182fcea238b0441ca3ea4597a150476107afe",
            "sha256sums_sha256": "4f4ba70f39796a7bec9c5ea0e5b969475e302f9fed1d805640be69eb2a846952",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 620,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096",
            "sdist_sha256": "2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05",
            "code_wiki_evidence_sha256": (
                "cc55df0423640ebfe92ddf21036fabc3650ca14e342c951ea96c1d0b2c3b7df6"
            ),
            "provenance_sha256": "164dd37d8c02adeaf3552a49a72c77aeb409a74b35bea4507f9efcc0bbe675ba",
            "sha256sums_sha256": "8fdb9a54f678a4be790e1879780d724da55b445dd5d39ab34386f1f6b4d67f42",
        }
    elif evidence_revision == 14:
        contracts["macos"]["pytest"] = {
            "passed": 628,
            "skipped": 0,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["macos"]["artifacts"] = {
            "wheel_sha256": "1176296c53051f5acc2d7015c0aab409a59b500b332fdeeccde8f9f23649e8db",
            "sdist_sha256": "40daea26eb78b56fb83ea81b9064fc19e98ecf55b473bed6dac8bdea21a7ca76",
            "code_wiki_evidence_sha256": (
                "b90c1bb651b422f26a1f96a0033435e26c9fc52b89162ff8efdb970a07e9dfde"
            ),
            "provenance_sha256": "c7249008c0b16d9772ad4a91864aa3bef3729b299f4d44afdc17817cf7fbbcf5",
            "sha256sums_sha256": "09ee98c0a13432f530bd66c24b3c42976a28c2ceb6ce4f2924f2ccae68ef29f2",
        }
        contracts["linux"]["runtime"]["kernel"] = "Linux 6.1.0-52-cloud-arm64"
        contracts["linux"]["pytest"] = {
            "passed": 625,
            "skipped": 3,
            "failed": 0,
            "coverage_percent": 88,
        }
        contracts["linux"]["artifacts"] = {
            "wheel_sha256": "1176296c53051f5acc2d7015c0aab409a59b500b332fdeeccde8f9f23649e8db",
            "sdist_sha256": "40daea26eb78b56fb83ea81b9064fc19e98ecf55b473bed6dac8bdea21a7ca76",
            "code_wiki_evidence_sha256": (
                "b90c1bb651b422f26a1f96a0033435e26c9fc52b89162ff8efdb970a07e9dfde"
            ),
            "provenance_sha256": "04d55026d28ab8c857094d2ee38c192c41d4022866aaad92efadf6e3a31e9335",
            "sha256sums_sha256": "63b1127de5b975e0a12bad3c4a973f3c5a90ec3ad2c81222994afcd4ecc0142e",
        }
    elif evidence_revision != 2:
        raise ValueError("unknown release-candidate local gate revision")
    if (
        set(payload)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "development_evidence",
            "platforms",
            "confirmation",
            "holdout",
            "passed",
        }
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("suite_id") != experiment["suite_id"]
        or type(payload.get("suite_revision")) is not int
        or payload.get("suite_revision") != experiment["suite_revision"]
        or COMMIT.fullmatch(commit) is None
        or (acceptance_commit is not None and commit != acceptance_commit)
        or payload.get("memoryforge_worktree_dirty") is not False
        or not _git_commit_descends_from(commit, development_artifact["memoryforge_commit"])
        or not _strict_mapping(
            payload.get("development_evidence"),
            {
                "path": development_artifact["path"],
                "sha256": development_artifact["sha256"],
                "memoryforge_commit": development_artifact["memoryforge_commit"],
                "passed": True,
            },
        )
        or not isinstance(platforms, dict)
        or set(platforms) != set(contracts)
        or not _strict_mapping(
            payload.get("confirmation"),
            {
                "path": confirmation["path"],
                "sha256": confirmation["sha256"],
                "status": "not_run",
            },
        )
        or not isinstance(holdout, dict)
        or not _strict_mapping(
            payload.get("holdout"),
            {
                "path": holdout["path"],
                "sha256": holdout["sha256"],
                "status": "not_run",
            },
        )
        or payload.get("passed") is not True
    ):
        raise ValueError("release-candidate local gate Evidence contract failed")

    expected_gate_keys = LOCAL_GATE_KEYS | {"artifacts", "artifact_files"}
    expected_registry = {
        "suite_count": 12,
        "experiment_count": 8,
        "evidence_count": {
            2: 97,
            3: 100,
            4: 103,
            6: 106,
            7: 109,
            8: 113,
            9: 116,
            10: 119,
            12: 123,
            13: 126,
            14: 129,
        }[evidence_revision],
        "qa_case_count": 121,
    }
    for name, contract in contracts.items():
        platform_payload = platforms[name]
        local_gate = (
            platform_payload.get("local_gate") if isinstance(platform_payload, dict) else None
        )
        expected_command = (
            "scripts/check_local.ps1"
            if contract["runtime"].get("system") == "Windows"
            else "scripts/check_local.sh"
        )
        if (
            not isinstance(platform_payload, dict)
            or set(platform_payload) != {"runtime", "local_gate"}
            or not _strict_mapping(platform_payload.get("runtime"), contract["runtime"])
            or not isinstance(local_gate, dict)
            or set(local_gate) != expected_gate_keys
            or local_gate.get("command") != expected_command
            or local_gate.get("ruff_check") != "passed"
            or local_gate.get("ruff_format") != "passed"
            or local_gate.get("strict_mypy") != "passed"
            or not _strict_mapping(local_gate.get("registry_validation"), expected_registry)
            or local_gate.get("dependency_check") != "passed"
            or not _strict_mapping(local_gate.get("pytest"), contract["pytest"])
            or local_gate.get("wheel_clean_room") != "passed"
            or local_gate.get("sdist_clean_room") != "passed"
            or local_gate.get("pip_check") != "passed"
            or local_gate.get("cli_version_smoke") != "passed"
            or not _strict_mapping(local_gate.get("artifacts"), contract["artifacts"])
            or (
                development_package is not None
                and (
                    local_gate.get("artifacts", {}).get("wheel_sha256")
                    != development_package.get("wheel_sha256")
                    or local_gate.get("artifacts", {}).get("sdist_sha256")
                    != development_package.get("sdist_sha256")
                )
            )
            or not _validate_bound_gate_artifacts(
                local_gate.get("artifact_files"),
                local_gate.get("artifacts"),
                commit,
                require_clean_sdist=True,
                require_replayable_sums=evidence_revision >= 9,
                require_code_evidence=evidence_revision >= 10,
            )
            or not _release_provenance_matches(local_gate, contract["runtime"])
        ):
            raise ValueError(f"release-candidate {name} local gate Evidence changed")
    return 1


def _release_provenance_matches(
    local_gate: dict[str, Any],
    expected_runtime: dict[str, Any],
) -> bool:
    artifact_files = local_gate.get("artifact_files")
    if not isinstance(artifact_files, dict):
        return False
    provenance_artifact = artifact_files.get("provenance")
    if not isinstance(provenance_artifact, dict):
        return False
    try:
        provenance = json.loads(
            (REPO_ROOT / str(provenance_artifact["path"])).read_text(encoding="utf-8")
        )
    except (KeyError, OSError, json.JSONDecodeError):
        return False
    runtime = provenance.get("runtime") if isinstance(provenance, dict) else None
    package = provenance.get("package") if isinstance(provenance, dict) else None
    checks = provenance.get("checks") if isinstance(provenance, dict) else None
    expected_checks = {
        "pip_check": "passed",
        "cli_help": "passed",
        "code_wiki_benchmark": "passed",
        "public_demo": "not_run",
    }
    if isinstance(checks, dict) and "cli_version" in checks:
        expected_checks["cli_version"] = "passed"
    platform_name = runtime.get("platform") if isinstance(runtime, dict) else None
    if "system" in expected_runtime:
        platform_valid = (
            isinstance(platform_name, str)
            and platform_name.startswith("macOS-")
            and expected_runtime["machine"] in platform_name
        )
    else:
        kernel = str(expected_runtime["kernel"]).removeprefix("Linux ")
        platform_valid = isinstance(platform_name, str) and platform_name.startswith(
            f"Linux-{kernel}-{expected_runtime['architecture']}"
        )
    return (
        isinstance(runtime, dict)
        and runtime.get("implementation") == expected_runtime["implementation"]
        and runtime.get("python") == expected_runtime["python"]
        and platform_valid
        and isinstance(package, dict)
        and package.get("version") == "0.3.0"
        and _strict_mapping(checks, expected_checks)
    )


def _validate_multi_source_support_regression(artifact: object) -> None:
    if not isinstance(artifact, dict):
        raise ValueError("multi-source acceptance requires support regression Evidence")
    expected_path, expected_sha256, expected_commit, expected_evaluation_sha256 = (
        MULTI_SOURCE_SUPPORT_REGRESSION
    )
    if (
        set(artifact)
        != {
            "path",
            "sha256",
            "memoryforge_commit",
            "evaluation_sha256",
            "passed",
        }
        or (
            artifact.get("path"),
            artifact.get("sha256"),
            artifact.get("memoryforge_commit"),
            artifact.get("evaluation_sha256"),
        )
        != MULTI_SOURCE_SUPPORT_REGRESSION
        or artifact.get("passed") is not True
    ):
        raise ValueError("multi-source support regression Evidence identity changed")
    _validate_artifact(artifact, "multi-source-coverage-selection")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / expected_path).read_text(encoding="utf-8")),
    )
    metrics = payload.get("development", {}).get("evaluation", {}).get("memoryforge", {})
    runs = payload.get("runs")
    if (
        payload.get("schema_version") != 1
        or set(payload) != DEVELOPMENT_EVIDENCE_KEYS
        or payload.get("suite_id") != "support-score.learn-claude-code"
        or payload.get("memoryforge_commit") != expected_commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not True
        or payload.get("confirmation", {}).get("status") != "not_run"
        or not isinstance(runs, list)
        or len(runs) != 2
        or any(run.get("evaluation_sha256") != expected_evaluation_sha256 for run in runs)
        or not isinstance(metrics, dict)
        or metrics.get("answer_accuracy") != 100.0
        or metrics.get("citation_grounding_accuracy") != 100.0
        or metrics.get("multi_source_coverage") != 100.0
        or metrics.get("selective_accuracy") != 100.0
        or metrics.get("coverage") != 90.0
        or metrics.get("risk") != 0.0
        or hashlib.sha256((REPO_ROOT / expected_path).read_bytes()).hexdigest() != expected_sha256
    ):
        raise ValueError("multi-source support regression Evidence contract failed")


def _support_benchmark_module() -> Any:
    script = REPO_ROOT / "demo/run_support_score_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_support_score_benchmark", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load support-score benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _support_case_identities_match(
    cases: list[dict[str, Any]],
    development: dict[str, Any],
) -> bool:
    frozen = json.loads((REPO_ROOT / development["path"]).read_text(encoding="utf-8"))
    expected = [
        (case.get("id"), case.get("category"), case.get("question"))
        for case in frozen.get("cases", [])
        if isinstance(case, dict)
    ]
    actual = [
        (case.get("id"), case.get("category"), case.get("question"))
        for case in cases
        if isinstance(case, dict)
    ]
    return actual == expected and len({identity[0] for identity in actual}) == len(actual)


def _support_runs_are_deterministic(payload: object) -> bool:
    if not isinstance(payload, list) or len(payload) != 2:
        return False
    expected_keys = {
        "name",
        "structural_passed",
        "structural_sha256",
        "evaluation_sha256",
        "metrics",
    }
    first, second = payload
    return (
        all(isinstance(run, dict) and set(run) == expected_keys for run in payload)
        and [run["name"] for run in payload] == ["first", "second"]
        and all(run["structural_passed"] is True for run in payload)
        and all(
            SHA256.fullmatch(str(run[key])) is not None
            for run in payload
            for key in ("structural_sha256", "evaluation_sha256")
        )
        and all(first[key] == second[key] for key in ("structural_sha256", "evaluation_sha256"))
    )


def _validate_repository(suite_id: str, repository: dict[str, Any]) -> None:
    if not repository.get("repository") or not repository.get("remote_url"):
        raise ValueError(f"repository identity missing: {suite_id}")
    if COMMIT.fullmatch(str(repository.get("commit"))) is None:
        raise ValueError(f"repository Commit is not frozen: {suite_id}")
    if not repository.get("license"):
        raise ValueError(f"repository license missing: {suite_id}")
    source_paths = repository.get("source_paths")
    if (
        not isinstance(source_paths, list)
        or not source_paths
        or any(not isinstance(path, str) or not path for path in source_paths)
    ):
        raise ValueError(f"repository source paths missing: {suite_id}")


def _validate_artifact(artifact: dict[str, Any], suite_id: str) -> None:
    relative = Path(str(artifact.get("path", "")))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"registered artifact path is unsafe: {suite_id}")
    candidate = REPO_ROOT.joinpath(*relative.parts)
    current = REPO_ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"registered artifact path is unsafe: {suite_id}")
    try:
        path = candidate.resolve(strict=True)
    except OSError:
        raise ValueError(f"registered artifact missing: {suite_id}") from None
    if not path.is_file() or not path.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError(f"registered artifact missing: {suite_id}")
    expected_sha = str(artifact.get("sha256"))
    if SHA256.fullmatch(expected_sha) is None:
        raise ValueError(f"registered artifact SHA256 invalid: {suite_id}")
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(f"registered artifact SHA256 mismatch: {suite_id}")


def _evidence_reference_valid(reference: object) -> bool:
    return (
        isinstance(reference, dict)
        and set(reference) == {"path", "sha256", "memoryforge_commit", "passed"}
        and SHA256.fullmatch(str(reference.get("sha256"))) is not None
        and COMMIT.fullmatch(str(reference.get("memoryforge_commit"))) is not None
        and type(reference.get("passed")) is bool
        and _payload_private_detail_leaks(reference) == 0
    )


def _suite_cases(split: dict[str, Any]) -> tuple[int, set[str], set[str]]:
    artifact = json.loads((REPO_ROOT / split["path"]).read_text(encoding="utf-8"))
    if "cases" in artifact and isinstance(artifact["cases"], list):
        return (
            len(artifact["cases"]),
            {str(case["category"]) for case in artifact["cases"] if "category" in case},
            {str(case["id"]) for case in artifact["cases"]},
        )
    repositories = artifact.get("repositories")
    if isinstance(repositories, list) and all(
        isinstance(repository.get("expected_source_count"), int) for repository in repositories
    ):
        return (
            sum(int(repository["expected_source_count"]) for repository in repositories),
            set(),
            set(),
        )
    source_count = len(artifact["expected_source_paths"])
    return (
        source_count
        + len(artifact["symbols"])
        + len(artifact["relations"])
        + len(artifact["modules"]),
        set(),
        set(),
    )


def _validate_release_split_manifest(
    split: dict[str, Any],
    *,
    split_name: str,
) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / split["path"]).read_text(encoding="utf-8")),
    )
    common_valid = (
        type(payload.get("schema_version")) is int
        and payload.get("schema_version") == 1
        and payload.get("suite_id") == "release-candidate-delivery"
        and type(payload.get("suite_revision")) is int
        and payload.get("suite_revision") == 1
        and payload.get("split") == split_name
        and isinstance(payload.get("expected_metrics"), dict)
    )
    if split_name == "development":
        valid = (
            set(payload)
            == {
                "schema_version",
                "suite_id",
                "suite_revision",
                "split",
                "cases",
                "expected_metrics",
            }
            and isinstance(payload.get("cases"), list)
            and len(payload["cases"]) == 6
            and len({case.get("id") for case in payload["cases"]}) == 6
        )
    elif split_name == "confirmation":
        valid = (
            set(payload)
            == {
                "schema_version",
                "suite_id",
                "suite_revision",
                "split",
                "status",
                "components",
                "expected_metrics",
            }
            and payload.get("status") == "not_run"
            and isinstance(payload.get("components"), list)
            and len(payload["components"]) == 7
        )
    elif split_name == "holdout":
        expected_ids = {
            "fresh-checkout-double-build",
            "installed-wheel-public-showcase",
            "workspace-backup-restore-replay",
            "release-assets-round-trip",
            "public-claims-privacy-audit",
        }
        valid = (
            set(payload)
            == {
                "schema_version",
                "suite_id",
                "suite_revision",
                "split",
                "status",
                "cases",
                "expected_metrics",
            }
            and payload.get("status") == "not_run"
            and isinstance(payload.get("cases"), list)
            and {case.get("id") for case in payload["cases"]} == expected_ids
            and len(payload["cases"]) == len(expected_ids)
        )
    else:
        valid = False
    result_path = REPO_ROOT / f"demo/results/release_candidate_{split_name}.json"
    if split_name in {"confirmation", "holdout"} and payload.get("status") == "not_run":
        valid = valid and not result_path.exists()
    if not common_valid or not valid:
        raise ValueError(f"release {split_name} manifest contract failed")
    return payload


def _release_confirmation_case_count(split: dict[str, Any], suite_id: str) -> int:
    payload = _validate_release_split_manifest(split, split_name="confirmation")
    components = payload.get("components")
    if not isinstance(components, list) or len(components) != 7:
        raise ValueError(f"release confirmation components changed: {suite_id}")
    expected_components = {
        "exact-symbol-routing": "CPython 3.11",
        "support-score": "CPython 3.11",
        "multi-source-coverage": "CPython 3.11",
        "folder-import-lifecycle": "CPython 3.11",
        "github-thread-import-lifecycle": "CPython 3.11",
        "static-showcase": "CPython 3.11",
        "native-windows-delivery": "native Windows, CPython 3.11",
    }
    if [component.get("id") for component in components] != list(expected_components):
        raise ValueError(f"release confirmation component IDs changed: {suite_id}")
    case_count = 0
    for component in components:
        if (
            not isinstance(component, dict)
            or set(component)
            != {
                "id",
                "path",
                "sha256",
                "case_count",
                "required_runtime",
            }
            or type(component.get("case_count")) is not int
            or component.get("required_runtime") != expected_components.get(component.get("id"))
        ):
            raise ValueError(f"release confirmation component invalid: {suite_id}")
        _validate_artifact(component, suite_id)
        actual_count, _, _ = _suite_cases(component)
        if component["case_count"] != actual_count:
            raise ValueError(f"release confirmation component case count changed: {suite_id}")
        case_count += component["case_count"]
    return case_count


def _validate_metrics(suite: dict[str, Any], evidence: dict[str, Any]) -> None:
    split = str(evidence["split"])
    expected = suite["expected_metrics"].get(split)
    if not isinstance(expected, dict):
        raise ValueError(f"expected metrics missing: {suite['suite_id']}/{split}")
    payload = json.loads((REPO_ROOT / evidence["path"]).read_text(encoding="utf-8"))
    actual = _evidence_metrics(payload, suite["repositories"][0]["commit"])
    for metric, value in expected.items():
        if isinstance(value, (int, float)) and actual.get(metric) != value:
            raise ValueError(f"evidence metric mismatch: {suite['suite_id']}/{split}/{metric}")


def _evidence_metrics(payload: dict[str, Any], repository_commit: str) -> dict[str, Any]:
    if isinstance(payload.get("evaluation"), dict) and isinstance(
        payload["evaluation"].get("memoryforge"), dict
    ):
        return cast(dict[str, Any], payload["evaluation"]["memoryforge"])
    if isinstance(payload.get("memoryforge"), dict):
        return cast(dict[str, Any], payload["memoryforge"])
    if isinstance(payload.get("metrics"), dict):
        return cast(dict[str, Any], payload["metrics"])
    if isinstance(payload.get("gates"), dict):
        return {
            **cast(dict[str, Any], payload.get("counts", {})),
            **cast(dict[str, Any], payload["gates"]),
            "passed": payload.get("passed"),
        }
    for repository in payload.get("repositories", []):
        if repository.get("commit") == repository_commit:
            return cast(dict[str, Any], repository["evaluation"]["metrics"])
    raise ValueError("could not locate evidence metrics")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
