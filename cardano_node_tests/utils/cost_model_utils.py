"""Utilities for cost model proposal generation."""

import json
import pathlib as pl

from cardano_clusterlib import clusterlib


def get_current_cost_models(cluster_obj: clusterlib.ClusterLib) -> dict[str, list[int]]:
    """Return the current cost models from live protocol parameters."""
    return cluster_obj.g_query.get_protocol_params()["costModels"]


def write_cost_model_proposal(
    cost_models: dict[str, list[int]],
    dest: pl.Path,
) -> pl.Path:
    """Write cost models to a JSON file suitable for --cost-model-file.

    Args:
        cost_models: Dict mapping Plutus version names to their cost model arrays.
        dest: Destination path for the JSON file.

    Returns:
        The destination path.
    """
    with open(dest, "w", encoding="utf-8") as fp:
        json.dump(cost_models, fp, indent=2)
    return dest
