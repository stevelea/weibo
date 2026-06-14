"""Configuration loader — parses YAML configs into typed dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Account:
    uid: str
    name: str
    category: str
    priority: str = "medium"


@dataclass
class KeywordGroup:
    group: str
    terms: list[str]
    priority: str = "medium"
    note: str = ""


@dataclass
class DeepScrapeConfig:
    interval_minutes: int = 30
    max_posts_per_search: int = 25
    lookback_hours: int = 6


@dataclass
class Config:
    accounts: list[Account] = field(default_factory=list)
    keyword_groups: list[KeywordGroup] = field(default_factory=list)
    deep_scrape: DeepScrapeConfig = field(default_factory=DeepScrapeConfig)
    rsshub_base_url: str = "http://rsshub:1200"
    poll_interval_minutes: int = 10
    supertopics: list[dict] = field(default_factory=list)
    bilibili_accounts: list[Account] = field(default_factory=list)


def load_config(config_dir: str | None = None) -> Config:
    """Load configuration from YAML files.

    Looks for config files at:
    - CONFIG_DIR env var
    - ./config/ (default)
    """
    if config_dir is None:
        config_dir = os.environ.get("CONFIG_DIR", "config")

    base = Path(config_dir)

    accounts_path = base / "accounts.yaml"
    keywords_path = base / "keywords.yaml"
    supertopics_path = base / "supertopics.yaml"

    config = Config()

    if accounts_path.exists():
        with open(accounts_path) as f:
            data = yaml.safe_load(f)
        if data:
            config.accounts = [
                Account(
                    uid=str(a["uid"]),
                    name=a["name"],
                    category=a.get("category", "unknown"),
                    priority=a.get("priority", "medium"),
                )
                for a in data.get("accounts", [])
            ]
            config.rsshub_base_url = data.get("rsshub_base_url", config.rsshub_base_url)
            config.poll_interval_minutes = data.get(
                "poll_interval_minutes", config.poll_interval_minutes
            )

    if keywords_path.exists():
        with open(keywords_path) as f:
            data = yaml.safe_load(f)
        if data:
            config.keyword_groups = [
                KeywordGroup(
                    group=kg["group"],
                    terms=kg["terms"],
                    priority=kg.get("priority", "medium"),
                    note=kg.get("note", ""),
                )
                for kg in data.get("keywords", [])
            ]
            if ds := data.get("deep_scrape"):
                config.deep_scrape = DeepScrapeConfig(
                    interval_minutes=ds.get("interval_minutes", 30),
                    max_posts_per_search=ds.get("max_posts_per_search", 25),
                    lookback_hours=ds.get("lookback_hours", 6),
                )

    if supertopics_path.exists():
        with open(supertopics_path) as f:
            data = yaml.safe_load(f)
        if data:
            config.supertopics = data.get("supertopics", [])

    bilibili_path = base / "bilibili.yaml"
    if bilibili_path.exists():
        with open(bilibili_path) as f:
            data = yaml.safe_load(f)
        if data:
            config.bilibili_accounts = [
                Account(
                    uid=str(a["uid"]),
                    name=a["name"],
                    category=a.get("category", "unknown"),
                    priority=a.get("priority", "medium"),
                )
                for a in data.get("accounts", [])
            ]

    return config
