"""
async_pipeline.py - Async batch processing pipeline.

Groups companies into batches (BATCH_SIZE per API call) so 1047 companies
only need ~53 API calls instead of 1047, staying well within free-tier limits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Callable

from ai_classifier import AIClassifier
from config import AppConfig
from excel_handler import save_results
from models import ClassificationResult, CompanyRecord, PipelineStats
from utils import RateLimiter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Batch task runner
# ─────────────────────────────────────────────────────────────────────────────

async def _classify_batch_task(
    batch: list[CompanyRecord],
    classifier: AIClassifier,
    semaphore: asyncio.Semaphore,
    company_info_fn: Callable[[CompanyRecord], str],
    stats: PipelineStats,
    batch_index: int,
) -> list[tuple[str, ClassificationResult]]:
    """
    Classify one batch of companies under the semaphore.

    Args:
        batch:           List of CompanyRecord to classify together.
        classifier:      Shared AIClassifier instance.
        semaphore:       Controls max concurrent batches.
        company_info_fn: Maps a record to descriptive text.
        stats:           Shared stats (retried counter).
        batch_index:     For logging.

    Returns:
        List of (company_name, ClassificationResult) tuples.
    """
    async with semaphore:
        logger.debug(
            "Processing batch %d (%d companies: %s … %s)",
            batch_index, len(batch),
            batch[0].company_name[:30], batch[-1].company_name[:30],
        )
        results = await classifier.classify_batch(batch, company_info_fn)
        # Count retried if any result has an error
        for _, res in results:
            if res.error and res.error not in ("empty_input",):
                stats.retried += 1
                break
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Default company info resolver
# ─────────────────────────────────────────────────────────────────────────────

def default_company_info(record: CompanyRecord) -> str:
    """
    Return a compact company description for batch prompts.
    In batch mode this is not directly used in the prompt (the batch prompt
    inlines only the name + CIN), but kept for single-company fallback.
    """
    return (
        f"Company Name: {record.company_name}\n"
        f"CIN: {record.cin}\n"
        "Classify based on name/CIN. If unknown, set confidence to LOW."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

class ClassificationPipeline:
    """
    Orchestrates batch classification of all companies.

    Companies are grouped into batches of ``config.batch_size``.
    Each batch is classified in one API call, reducing total calls by 20×.
    """

    def __init__(
        self,
        config: AppConfig,
        company_info_fn: Callable[[CompanyRecord], str] | None = None,
    ) -> None:
        self._config = config
        self._company_info_fn = company_info_fn or default_company_info
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._all_tasks: list[asyncio.Task] = []

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        records: list[CompanyRecord],
    ) -> dict[str, ClassificationResult]:
        """
        Classify all company records using batched API calls.

        Args:
            records: Full list of CompanyRecord objects.

        Returns:
            Mapping of company_name → ClassificationResult.
        """
        stats = PipelineStats(total=len(records))
        results: dict[str, ClassificationResult] = {}
        semaphore = asyncio.Semaphore(self._config.max_concurrent_workers)

        # Group records into batches
        bs = self._config.batch_size
        batches = [records[i: i + bs] for i in range(0, len(records), bs)]

        rate_limiter = RateLimiter(
            max_calls=self._config.requests_per_minute,
            period_seconds=60.0,
        )

        self._install_signal_handlers()
        start_time = time.perf_counter()

        logger.info(
            "Starting pipeline: %d companies | batch_size=%d | %d batches | "
            "workers=%d | rate_limit=%d RPM.",
            len(records), bs, len(batches),
            self._config.max_concurrent_workers,
            self._config.requests_per_minute,
        )

        async with AIClassifier(self._config, rate_limiter=rate_limiter) as classifier:
            tasks = [
                asyncio.create_task(
                    _classify_batch_task(
                        batch, classifier, semaphore,
                        self._company_info_fn, stats, idx,
                    ),
                    name=f"batch-{idx}",
                )
                for idx, batch in enumerate(batches, start=1)
            ]
            self._all_tasks = tasks

            completed_companies = 0
            completed_batches = 0
            partial_save_interval = self._config.partial_save_every_n

            for coro in asyncio.as_completed(tasks):
                if self._shutdown_event.is_set():
                    logger.warning("Shutdown signal – cancelling remaining batches.")
                    for t in tasks:
                        t.cancel()
                    break

                try:
                    batch_results = await coro

                    for name, result in batch_results:
                        results[name] = result
                        stats.record(result)
                        completed_companies += 1

                    completed_batches += 1

                    # Progress log after each batch
                    pct = completed_companies / len(records) * 100
                    logger.info(
                        "Progress: %d/%d companies (%.1f%%) | "
                        "batch %d/%d | ok=%d err=%d",
                        completed_companies, len(records), pct,
                        completed_batches, len(batches),
                        stats.succeeded, stats.failed,
                    )

                    # Partial save every N batches
                    if completed_batches % partial_save_interval == 0:
                        self._partial_save(records, results, stats)

                except asyncio.CancelledError:
                    stats.skipped += len(batches[0])   # approximate
                except Exception as exc:  # noqa: BLE001
                    logger.error("Unexpected batch error: %s", exc, exc_info=True)
                    stats.failed += 1

        stats.elapsed_seconds = time.perf_counter() - start_time

        save_results(
            records, results,
            self._config.output_excel_path,
            self._config.output_sheet_name,
        )
        self._print_summary(stats)
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _partial_save(
        self,
        records: list[CompanyRecord],
        results: dict[str, ClassificationResult],
        stats: PipelineStats,
    ) -> None:
        try:
            partial_path = self._config.output_excel_path.replace(".xlsx", "_partial.xlsx")
            save_results(records, results, partial_path, self._config.output_sheet_name)
            stats.partial_saves += 1
            logger.info("Partial save → %s (%d results)", partial_path, len(results))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Partial save failed: %s", exc)

    def _install_signal_handlers(self) -> None:
        """First Ctrl+C cancels tasks gracefully; second kills immediately."""
        loop = asyncio.get_event_loop()
        _count = [0]

        def _handler(sig: signal.Signals) -> None:
            _count[0] += 1
            if _count[0] == 1:
                logger.warning(
                    "Received %s – cancelling batches (press again to force-exit)…",
                    sig.name,
                )
                self._shutdown_event.set()
                for t in self._all_tasks:
                    t.cancel()
            else:
                logger.warning("Force-exit.")
                os._exit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handler, sig)
            except (NotImplementedError, ValueError):
                pass

    @staticmethod
    def _print_summary(stats: PipelineStats) -> None:
        logger.info("=" * 55)
        logger.info("PIPELINE COMPLETE – EXECUTION SUMMARY")
        logger.info("=" * 55)
        for line in stats.summary_lines():
            logger.info(line)
        logger.info("=" * 55)
