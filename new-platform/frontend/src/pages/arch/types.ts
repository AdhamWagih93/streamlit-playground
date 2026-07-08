/** Shared types for the Architecture slice (mirrors backend provider payloads). */

export type Provenance = {
  commit: string;
  author: string;
  commit_date: string;
  deployed_version: string;
  is_head: boolean;
};

export type ArchNode = {
  id: string;
  label: string;
  type: "service" | "db" | "queue" | "ldap" | "external";
  project: string;
  is_legacy: boolean;
  provenance: Provenance | null;
};

export type ArchEdge = {
  source: string;
  target: string;
  scheme: string;
  kind: string;
  async: boolean;
};

export type ArchStats = { services: number; stores: number; deps: number; legacy: number };

export type ArchModel = { nodes: ArchNode[]; edges: ArchEdge[]; stats: ArchStats; capped: boolean };

export type EnvInfo = { env: string; apps: number; projects: { project: string; count: number }[] };

export type ArchDiff = {
  only_a: string[];
  only_b: string[];
  changed: { app: string; removed: string[]; added: string[] }[];
  repeated_urls: { app: string; endpoint: string; envs: string[] }[];
};

export type Finding = { severity: "HIGH" | "MED" | "LOW"; title: string; detail: string; app: string };

export type Phase = { phase: number; name: string; horizon: string; actions: string[] };
