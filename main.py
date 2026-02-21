"""
main.py - Entry point for the Company Domain Classifier.

Usage:
    python main.py [--input FILE] [--output FILE] [--workers N]

The script validates configuration, loads Excel data, runs the async
classification pipeline, and writes enriched results back to Excel.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from config import config
from async_pipeline import ClassificationPipeline
from excel_handler import load_companies
from utils import Timer, setup_logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments and override config defaults where supplied."""
    parser = argparse.ArgumentParser(
        description="Classify engineering domains of companies using an LLM API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        default=config.input_excel_path,
        help="Path to the input Excel file (.xlsx).",
    )
    parser.add_argument(
        "--output", "-o",
        default=config.output_excel_path,
        help="Path for the output Excel file (.xlsx).",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=config.max_concurrent_workers,
        help="Maximum number of concurrent API calls.",
    )
    parser.add_argument(
        "--log-level",
        default=config.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Async main
# ─────────────────────────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace) -> int:
    """
    Async entry point.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    # Override config from CLI
    config.input_excel_path = args.input
    config.output_excel_path = args.output
    config.max_concurrent_workers = args.workers

    # Validate
    try:
        config.validate()
    except ValueError as exc:
        logger.critical("Configuration error: %s", exc)
        return 1

    logger.info("=" * 55)
    logger.info("Company Domain Classifier – Starting")
    logger.info("  Input  : %s", config.input_excel_path)
    logger.info("  Output : %s", config.output_excel_path)
    logger.info("  Model  : %s", config.model_name)
    logger.info("  Workers: %d", config.max_concurrent_workers)
    logger.info("=" * 55)

    # Load companies
    try:
        records = load_companies(config.input_excel_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.critical("Failed to load input file: %s", exc)
        return 1

    if not records:
        logger.warning("No company records found in the input file. Exiting.")
        return 0

    # Run pipeline
    pipeline = ClassificationPipeline(config)
    with Timer() as timer:
        await pipeline.run(records)

    logger.info("Total wall-clock time: %s", timer.elapsed_str)
    logger.info("Results saved → %s", config.output_excel_path)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parse args, set up logging, and run the async pipeline."""
    args = parse_args()
    setup_logging(args.log_level)

    try:
        exit_code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        exit_code = 130

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
