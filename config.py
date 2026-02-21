"""
config.py - Central configuration for Company Domain Classifier
Loads environment variables and defines all tunable parameters.
Configured for Google Gemini API.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    """Application-wide configuration."""

    # ── API settings ──────────────────────────────────────────────────────────
    api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # Support multiple keys for rotation: GEMINI_API_KEYS=key1,key2,key3
    # Falls back to single GEMINI_API_KEY if GEMINI_API_KEYS not set.
    api_keys: list[str] = field(
        default_factory=lambda: [
            k.strip()
            for k in os.getenv(
                "GEMINI_API_KEYS",
                os.getenv("GEMINI_API_KEY", ""),
            ).split(",")
            if k.strip()
        ]
    )
    # Base URL template; {model} is substituted at call time.
    api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        )
    )
    model_name: str = field(
        default_factory=lambda: os.getenv("MODEL_NAME", "gemini-2.0-flash")
    )

    # ── Concurrency & network ─────────────────────────────────────────────────
    max_concurrent_workers: int = field(
        default_factory=lambda: int(os.getenv("MAX_WORKERS", "10"))
    )
    # Rate limiter: stay safely under Gemini free-tier (15 RPM)
    requests_per_minute: int = field(
        default_factory=lambda: int(os.getenv("REQUESTS_PER_MINUTE", "14"))
    )
    request_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "30"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("MAX_RETRIES", "3"))
    )
    retry_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("RETRY_DELAY", "2.0"))
    )

    # ── File paths ────────────────────────────────────────────────────────────
    input_excel_path: str = field(
        default_factory=lambda: os.getenv("INPUT_EXCEL", "companies_input.xlsx")
    )
    output_excel_path: str = field(
        default_factory=lambda: os.getenv("OUTPUT_EXCEL", "classified_companies.xlsx")
    )
    output_sheet_name: str = "classified_companies"

    # ── LLM generation parameters ─────────────────────────────────────────────
    llm_max_tokens: int = 6000          # batch of 20 needs more output tokens
    llm_temperature: float = 0.1
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("BATCH_SIZE", "20"))
    )

    # ── Progress / logging ────────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    progress_every_n: int = 10          # log progress every N companies

    # ── Partial save ──────────────────────────────────────────────────────────
    partial_save_every_n: int = field(
        default_factory=lambda: int(os.getenv("PARTIAL_SAVE_EVERY", "20"))
    )

    def validate(self) -> None:
        """Raise ValueError for critical missing configuration."""
        if not self.api_keys:
            raise ValueError(
                "No API key found. Set GEMINI_API_KEY or GEMINI_API_KEYS in .env.\n"
                "Get a key at https://aistudio.google.com/app/apikey"
            )
        if not self.input_excel_path:
            raise ValueError("INPUT_EXCEL path must be specified.")


# ── Module-level singleton ────────────────────────────────────────────────────
config = AppConfig()


# ── Classification prompt template ───────────────────────────────────────────
CLASSIFICATION_PROMPT_TEMPLATE = """You are an expert technical company analyst.
Classify engineering relevance for placement and hiring purposes.
Return strict JSON ONLY (no markdown, no extra text):

{{
  "domain": "<ECE|CSE|BOTH|NEITHER>",
  "confidence": "<HIGH|MEDIUM|LOW>",
  "primary_domain_area": "<brief phrase, e.g. 'Semiconductor design', 'Cloud SaaS'>",
  "hardware_or_software": "<Hardware|Software|Both|Neither>",
  "hiring_possible": "<YES|NO|UNKNOWN>",
  "fresher_friendly": "<YES|NO|UNKNOWN>",
  "likely_roles": "<comma-separated roles or UNKNOWN>",
  "reason": "<1-2 sentence justification>"
}}

Domain classification rules:
- ECE: electronics, semiconductors, telecom, embedded systems, VLSI, PCB, hardware, robotics hardware, IoT devices
- CSE: pure software, AI/ML, cloud, data engineering, cybersecurity, SaaS, web, mobile apps
- BOTH: significant hardware AND software activities
- NEITHER: finance, FMCG, retail, legal, or non-engineering domains

Company information:
{company_text}
"""


# ── Batch classification prompt ────────────────────────────────────────────────
BATCH_CLASSIFICATION_PROMPT_TEMPLATE = """You are an expert technical company analyst.
Classify each company below for engineering placement purposes.

Return a JSON ARRAY with EXACTLY {{count}} objects in the SAME ORDER as the input.
Return ONLY the JSON array – no markdown, no extra text, no explanations.

Each object must have these exact keys:
{{
  "domain": "<ECE|CSE|BOTH|NEITHER>",
  "confidence": "<HIGH|MEDIUM|LOW>",
  "primary_domain_area": "<brief phrase, e.g. Semiconductor design, Cloud SaaS>",
  "hardware_or_software": "<Hardware|Software|Both|Neither>",
  "hiring_possible": "<YES|NO|UNKNOWN>",
  "fresher_friendly": "<YES|NO|UNKNOWN>",
  "likely_roles": "<comma-separated roles or UNKNOWN>",
  "reason": "<max 1 sentence justification>"
}}

Domain rules:
- ECE: electronics, semiconductors, telecom, embedded systems, VLSI, hardware, robotics, IoT devices
- CSE: pure software, AI/ML, cloud, data, cybersecurity, SaaS, web, mobile apps
- BOTH: significant hardware AND software
- NEITHER: finance, FMCG, retail, legal, non-engineering

Companies to classify:
{{companies_list}}
"""
