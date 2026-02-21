"""
ai_classifier.py - Async Google Gemini classifier with batch support.

Sends up to BATCH_SIZE companies per API call and parses a JSON array back.
Falls back to individual classification if batch parsing fails.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

import httpx

from config import AppConfig, BATCH_CLASSIFICATION_PROMPT_TEMPLATE, CLASSIFICATION_PROMPT_TEMPLATE
from models import ClassificationResult, CompanyRecord
from utils import (
    VALID_CONFIDENCE,
    VALID_DOMAINS,
    VALID_HW_SW,
    VALID_YES_NO,
    ApiKeyPool,
    RateLimiter,
    build_fallback_result,
    normalise_field,
    safe_parse_json,
)

logger = logging.getLogger(__name__)


class AIClassifier:
    """
    Async wrapper around the Google Gemini generateContent REST API.

    Supports **batch classification**: multiple companies are sent in a single
    API call and a JSON array is returned — dramatically reducing the number of
    requests needed and helping stay within free-tier rate limits.

    Usage::

        limiter = RateLimiter(max_calls=8)
        async with AIClassifier(config, rate_limiter=limiter) as clf:
            results = await clf.classify_batch(records, info_fn)
    """

    def __init__(
        self,
        config: AppConfig,
        rate_limiter: RateLimiter | None = None,
        key_pool: ApiKeyPool | None = None,
    ) -> None:
        self._config = config
        self._rate_limiter = rate_limiter or RateLimiter(
            max_calls=config.requests_per_minute
        )
        # Build key pool from config if not injected
        self._key_pool = key_pool or ApiKeyPool(config.api_keys)
        self._client: httpx.AsyncClient | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "AIClassifier":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._config.request_timeout_seconds),
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── Public: batch interface ───────────────────────────────────────────────

    async def classify_batch(
        self,
        records: list[CompanyRecord],
        company_info_fn: Callable[[CompanyRecord], str],
    ) -> list[tuple[str, ClassificationResult]]:
        """
        Classify a batch of companies in a single API call.

        Sends all company names to the LLM at once and asks for a JSON array
        response. Falls back to a safe default for any company whose result
        can't be parsed from the array.

        Args:
            records:         List of CompanyRecord objects to classify.
            company_info_fn: Function mapping a record to its description text.

        Returns:
            List of (company_name, ClassificationResult) in the same order
            as ``records``.
        """
        if not records:
            return []

        prompt = self._build_batch_prompt(records, company_info_fn)
        last_error: str = "unknown"

        for attempt in range(1, self._config.max_retries + 1):
            try:
                raw = await self._call_gemini(prompt)
                results = self._parse_batch_response(raw, records)
                if attempt > 1:
                    logger.info(
                        "Batch of %d succeeded on attempt %d.", len(records), attempt
                    )
                return results

            except httpx.TimeoutException:
                last_error = "timeout"
                logger.warning(
                    "Batch attempt %d/%d timed out.", attempt, self._config.max_retries
                )
            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}"
                logger.warning(
                    "Batch attempt %d/%d HTTP error: %s",
                    attempt, self._config.max_retries, last_error,
                )
                if exc.response.status_code == 429:
                    retry_after = _parse_retry_after(exc.response)
                    # Mark THIS key exhausted in the pool
                    self._key_pool.mark_exhausted(
                        getattr(exc.response, "_used_key", ""),
                        retry_after,
                    )
                    if self._key_pool.has_available_key():
                        # Another key is free — switch instantly, no sleep
                        logger.info(
                            "429 on key …%s – switching to next key immediately.",
                            getattr(exc.response, "_used_key", "?")[-6:],
                        )
                        continue  # skip the sleep at the bottom, retry now
                    else:
                        # All keys exhausted — must wait
                        self._rate_limiter.update_after_429(retry_after)
                elif exc.response.status_code not in (500, 502, 503, 504):
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "Batch attempt %d/%d error: %s",
                    attempt, self._config.max_retries, last_error,
                )

            if attempt < self._config.max_retries:
                wait = self._config.retry_delay_seconds * attempt
                await asyncio.sleep(wait)

        # All retries failed — return fallback for every record
        logger.error(
            "Batch of %d FAILED after %d attempts: %s",
            len(records), self._config.max_retries, last_error,
        )
        return [
            (
                rec.company_name,
                ClassificationResult(
                    **build_fallback_result(
                        rec.company_name,
                        f"Batch classification failed: {last_error}",
                    ),
                    error=last_error,
                ),
            )
            for rec in records
        ]

    # ── Legacy: single-company interface (used by fallback) ───────────────────

    async def classify(
        self,
        company_text: str,
        company_name: str = "Unknown",
    ) -> ClassificationResult:
        """
        Classify a single company (legacy / fallback path).

        Args:
            company_text: Free-form description of the company.
            company_name: Used for logging.

        Returns:
            ClassificationResult.
        """
        if not company_text or not company_text.strip():
            fallback = build_fallback_result(company_name, "No company information provided.")
            return ClassificationResult(**fallback, error="empty_input")

        prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(company_text=company_text.strip())
        last_error = "unknown"

        for attempt in range(1, self._config.max_retries + 1):
            try:
                raw = await self._call_gemini(prompt)
                return self._parse_single_response(raw, company_name)

            except httpx.TimeoutException:
                last_error = "timeout"
                logger.warning("'%s' – attempt %d timed out.", company_name, attempt)
            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}"
                logger.warning("'%s' – attempt %d HTTP error: %s", company_name, attempt, last_error)
                if exc.response.status_code == 429:
                    self._rate_limiter.update_after_429(_parse_retry_after(exc.response))
                elif exc.response.status_code not in (500, 502, 503, 504):
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("'%s' – attempt %d error: %s", company_name, attempt, last_error)

            if attempt < self._config.max_retries:
                await asyncio.sleep(self._config.retry_delay_seconds * attempt)

        fallback = build_fallback_result(company_name, f"Failed: {last_error}")
        return ClassificationResult(**fallback, error=last_error)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_batch_prompt(
        self,
        records: list[CompanyRecord],
        company_info_fn: Callable[[CompanyRecord], str],
    ) -> str:
        """Build the batch prompt listing all companies."""
        lines: list[str] = []
        for i, rec in enumerate(records, start=1):
            # Use just company name + CIN to keep prompt compact
            lines.append(f"{i}. {rec.company_name} (CIN: {rec.cin})")
        companies_list = "\n".join(lines)
        return BATCH_CLASSIFICATION_PROMPT_TEMPLATE.format(
            count=len(records),
            companies_list=companies_list,
        )

    def _parse_batch_response(
        self,
        raw: str,
        records: list[CompanyRecord],
    ) -> list[tuple[str, ClassificationResult]]:
        """
        Parse a JSON array response from the LLM into per-company results.

        If the array has fewer items than expected, or parsing fails for an
        individual item, that company gets a fallback result.

        Args:
            raw:     Raw text from the LLM.
            records: Original records (defines order and company names).

        Returns:
            List of (company_name, ClassificationResult) tuples.
        """
        parsed = safe_parse_json(raw)

        # The model should return a list; handle if it wraps in a dict
        if isinstance(parsed, dict):
            # Try common wrapper keys
            for key in ("results", "companies", "classifications", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break

        if not isinstance(parsed, list):
            logger.warning(
                "Batch response is not a list (type=%s); falling back per company.",
                type(parsed).__name__,
            )
            return [
                (
                    rec.company_name,
                    ClassificationResult(
                        **build_fallback_result(rec.company_name, "Batch response was not a JSON array."),
                        error="invalid_json",
                    ),
                )
                for rec in records
            ]

        results: list[tuple[str, ClassificationResult]] = []
        for i, rec in enumerate(records):
            if i < len(parsed) and isinstance(parsed[i], dict):
                item = parsed[i]
                results.append((
                    rec.company_name,
                    ClassificationResult(
                        domain=normalise_field(item.get("domain"), VALID_DOMAINS, "NEITHER"),
                        confidence=normalise_field(item.get("confidence"), VALID_CONFIDENCE, "LOW"),
                        primary_domain_area=str(item.get("primary_domain_area", "UNKNOWN")).strip() or "UNKNOWN",
                        hardware_or_software=normalise_field(item.get("hardware_or_software"), VALID_HW_SW, "Neither"),
                        hiring_possible=normalise_field(item.get("hiring_possible"), VALID_YES_NO, "UNKNOWN"),
                        fresher_friendly=normalise_field(item.get("fresher_friendly"), VALID_YES_NO, "UNKNOWN"),
                        likely_roles=str(item.get("likely_roles", "UNKNOWN")).strip() or "UNKNOWN",
                        reason=str(item.get("reason", "")).strip(),
                    ),
                ))
            else:
                logger.warning(
                    "No array item for '%s' (index %d, array len %d); using fallback.",
                    rec.company_name, i, len(parsed),
                )
                results.append((
                    rec.company_name,
                    ClassificationResult(
                        **build_fallback_result(rec.company_name, "Missing from batch response."),
                        error="missing_from_batch",
                    ),
                ))
        return results

    def _parse_single_response(self, raw: str, company_name: str) -> ClassificationResult:
        """Parse a single-company JSON response."""
        parsed = safe_parse_json(raw)
        if parsed is None:
            fallback = build_fallback_result(company_name, "Invalid JSON from Gemini.")
            return ClassificationResult(**fallback, error="invalid_json")
        return ClassificationResult(
            domain=normalise_field(parsed.get("domain"), VALID_DOMAINS, "NEITHER"),
            confidence=normalise_field(parsed.get("confidence"), VALID_CONFIDENCE, "LOW"),
            primary_domain_area=str(parsed.get("primary_domain_area", "UNKNOWN")).strip() or "UNKNOWN",
            hardware_or_software=normalise_field(parsed.get("hardware_or_software"), VALID_HW_SW, "Neither"),
            hiring_possible=normalise_field(parsed.get("hiring_possible"), VALID_YES_NO, "UNKNOWN"),
            fresher_friendly=normalise_field(parsed.get("fresher_friendly"), VALID_YES_NO, "UNKNOWN"),
            likely_roles=str(parsed.get("likely_roles", "UNKNOWN")).strip() or "UNKNOWN",
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _call_gemini(self, prompt: str) -> str:
        """Send a prompt to the Gemini API using next available key."""
        assert self._client is not None, "Client not initialised – use 'async with'."

        # Acquire rate-limiter slot AND a live API key
        await self._rate_limiter.acquire()
        api_key = await self._key_pool.acquire_key()

        url = self._config.api_base_url.format(model=self._config.model_name)
        url_with_key = f"{url}?key={api_key}"

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": self._config.llm_max_tokens,
                "temperature": self._config.llm_temperature,
            },
        }

        response = await self._client.post(url_with_key, json=payload)
        # Attach the key used so error handlers can mark it exhausted
        response._used_key = api_key  # type: ignore[attr-defined]
        if response.status_code == 429:
            # Mark this key exhausted before raise_for_status()
            retry_after = _parse_retry_after(response)
            self._key_pool.mark_exhausted(api_key, retry_after)
        response.raise_for_status()

        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected Gemini response structure: {data}") from exc


# ── Module-level helper ───────────────────────────────────────────────────────

def _parse_retry_after(response: httpx.Response) -> float:
    """Extract retry wait from Gemini 429 response (header or JSON body)."""
    header_val = response.headers.get("Retry-After")
    if header_val:
        try:
            return max(65.0, float(header_val))
        except ValueError:
            pass
    try:
        body = response.json()
        msg = body.get("error", {}).get("message", "")
        match = re.search(r"retry in ([\d.]+)s", msg, re.IGNORECASE)
        if match:
            wait = float(match.group(1)) + 5.0
            logger.info("Gemini retry-after from body: %.1fs (+ 5s buffer = %.1fs)", float(match.group(1)), wait)
            return wait
    except Exception:  # noqa: BLE001
        pass
    return 65.0
