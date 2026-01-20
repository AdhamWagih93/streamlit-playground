from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kubernetes import client, config


@dataclass(frozen=True)
class KubernetesClientSet:
    core: client.CoreV1Api
    apps: client.AppsV1Api
    networking: client.NetworkingV1Api
    version: client.VersionApi


def load_clients(*, kubeconfig: Optional[str] = None, context: Optional[str] = None) -> KubernetesClientSet:
    """Create Kubernetes API clients using kubeconfig/context.

    This is the single place where we load kubeconfig so tool functions stay pure.
    """

    if kubeconfig:
        if context:
            config.load_kube_config(config_file=kubeconfig, context=context)
        else:
            config.load_kube_config(config_file=kubeconfig)
    else:
        if context:
            config.load_kube_config(context=context)
        else:
            config.load_kube_config()

    return KubernetesClientSet(
        core=client.CoreV1Api(),
        apps=client.AppsV1Api(),
        networking=client.NetworkingV1Api(),
        version=client.VersionApi(),
    )
