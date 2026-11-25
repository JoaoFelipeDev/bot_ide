"""Integracao com Firestore compartilhado (projeto IDE) e utilitarios de nuvem."""

from id_monitor.cloud.settings import CloudSettings, cloud_configured, load_cloud_settings

__all__ = ["CloudSettings", "cloud_configured", "load_cloud_settings"]
