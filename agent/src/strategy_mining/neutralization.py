"""Sector and market-cap neutralisation helpers.

Neutralisation is applied *after* the composite score is computed. For each
configured grouping field we demean the score within the group, which prevents
the final Top-N selection from being dominated by a single sector or size
bucket.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Neutralizer:
    """Cross-sectional neutraliser for composite factor scores."""

    def __init__(self, fields: list[str] | None = None, market_cap_buckets: int = 5) -> None:
        """Args:
            fields: Panel fields to neutralise on, e.g. ``["sector"]`` or
                ``["sector", "market_cap"]``.
            market_cap_buckets: Number of quantile buckets when neutralising on
                a numeric market-cap field.
        """
        self.fields = fields or ["sector"]
        self.market_cap_buckets = max(2, market_cap_buckets)

    def neutralize(
        self,
        scores: pd.Series,
        panel: dict[str, pd.DataFrame],
        asof: pd.Timestamp,
    ) -> pd.Series:
        """Return ``scores`` with group means removed for each configured field."""
        out = scores.copy()
        for field in self.fields:
            if field not in panel:
                logger.debug("neutralize: panel missing %s, skipping", field)
                continue
            group_labels = self._group_labels(field, panel[field], asof)
            if group_labels is None or group_labels.empty:
                continue
            out = self._demean_within_groups(out, group_labels)
        return out

    def _group_labels(
        self,
        field: str,
        df: pd.DataFrame,
        asof: pd.Timestamp,
    ) -> pd.Series | None:
        """Get a per-stock group label series at ``asof``."""
        if asof < df.index.min():
            return None
        idx = df.index.asof(asof)
        if idx is pd.NaT or idx is None:
            return None
        row = df.loc[idx]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        labels = pd.Series(row, index=df.columns)

        if field == "market_cap" and pd.api.types.is_numeric_dtype(labels):
            # Bin market cap into quantile buckets.
            finite = labels.replace([np.inf, -np.inf], np.nan).dropna()
            if len(finite) < self.market_cap_buckets:
                return None
            labels = pd.Series(
                pd.qcut(finite, self.market_cap_buckets, labels=False, duplicates="drop"),
                index=finite.index,
            )
        return labels

    @staticmethod
    def _demean_within_groups(
        scores: pd.Series,
        group_labels: pd.Series,
    ) -> pd.Series:
        """Subtract group mean and add back overall mean to preserve scale."""
        aligned = pd.concat([scores, group_labels], axis=1, keys=["score", "group"])
        aligned = aligned.dropna(subset=["score", "group"])
        if aligned.empty:
            return scores
        overall_mean = aligned["score"].mean()
        demeaned = aligned["score"] - aligned.groupby("group")["score"].transform("mean")
        return demeaned + overall_mean
