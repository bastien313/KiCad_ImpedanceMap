"""Orchestration de l'analyse (échantillonnage, coupes, cache, calcul parallèle, statistiques)."""

from .engine import AnalysisOptions, AnalysisRun, Cancelled, Engine, SampleResult, TargetReport, compute_stats

__all__ = ["AnalysisOptions", "AnalysisRun", "Cancelled", "Engine", "SampleResult", "TargetReport",
           "compute_stats"]
