#!/usr/bin/env python3
"""Unified live job-source coordinator.

Fetches every configured Greenhouse board and Lever site in one operation,
then returns the combined raw jobs. Deduplication and prefiltering happen
in ingest_jobs(). This module never calls OpenAI.
"""

from greenhouse_source import GreenhouseJobSource, configured_greenhouse_boards
from job_sources import JobSource
from lever_source import LeverJobSource, configured_lever_sites


def _unpack_fetch_result(result):
    """Accept (jobs, error) tuples, or a bare list from a test double."""
    if isinstance(result, tuple):
        jobs = result[0] if result else []
        error = result[1] if len(result) > 1 else None
        return list(jobs or []), error
    return list(result or []), None


class AllLiveJobSource(JobSource):
    """Fetch Greenhouse and Lever together, then let ingest dedupe and prefilter."""

    name = "All Live Sources"
    source_id = "all_live"

    def __init__(self, greenhouse_source=None, lever_source=None):
        self.greenhouse_source = greenhouse_source
        self.lever_source = lever_source
        self.last_source_counts = {"greenhouse": 0, "lever": 0}
        self.last_partial = False

    def _fetch_adapter(self, label, adapter):
        """Call one live adapter. Unexpected errors do not abort the other source."""
        try:
            jobs, error = _unpack_fetch_result(adapter.fetch())
        except Exception:
            self.last_partial = True
            return [], (
                f"{label} fetch failed unexpectedly. "
                "Results from other sources were kept."
            )
        if error:
            self.last_partial = True
        return jobs, error

    def fetch(self):
        """Return (incoming_jobs, error_message). Partial results are kept."""
        all_jobs = []
        errors = []
        self.last_source_counts = {"greenhouse": 0, "lever": 0}
        self.last_partial = False

        boards = configured_greenhouse_boards()
        sites = configured_lever_sites()
        fetch_greenhouse = self.greenhouse_source is not None or bool(boards)
        fetch_lever = self.lever_source is not None or bool(sites)
        if not fetch_greenhouse and not fetch_lever:
            return [], (
                "No live sources are configured. "
                "Add Greenhouse boards or Lever sites in job_source_config.py "
                "or approve them in Source Discovery."
            )

        if fetch_greenhouse:
            greenhouse = self.greenhouse_source or GreenhouseJobSource(boards=boards)
            jobs, error = self._fetch_adapter("Greenhouse", greenhouse)
            self.last_source_counts["greenhouse"] = len(jobs)
            all_jobs.extend(jobs)
            if error:
                errors.append(error)

        if fetch_lever:
            lever = self.lever_source or LeverJobSource(sites=sites)
            jobs, error = self._fetch_adapter("Lever", lever)
            self.last_source_counts["lever"] = len(jobs)
            all_jobs.extend(jobs)
            if error:
                errors.append(error)

        warning = None
        if errors:
            if all_jobs:
                warning = "Discovery results are partial. " + " ".join(errors)
            else:
                warning = " ".join(errors)
        return all_jobs, warning
