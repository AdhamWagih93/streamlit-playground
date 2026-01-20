from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass
class JenkinsAuthConfig:
    base_url: str
    username: Optional[str] = None
    api_token: Optional[str] = None
    verify_ssl: bool = True


class JenkinsMCPServer:
    """Lightweight Jenkins client used as an MCP-style tool server."""

    def __init__(self, config: JenkinsAuthConfig) -> None:
        self.config = config
        base = config.base_url.rstrip("/")
        self._root_url = f"{base}"
        self._api_url = f"{base}/api/json"

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        auth = None
        if self.config.username and self.config.api_token:
            auth = (self.config.username, self.config.api_token)

        try:
            resp = requests.request(
                method,
                url,
                auth=auth,
                verify=self.config.verify_ssl,
                timeout=15,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "url": url}

        content_type = resp.headers.get("content-type", "")
        body: Any
        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = resp.text
        else:
            body = resp.text

        return {
            "ok": resp.ok,
            "status": resp.status_code,
            "url": url,
            "body": body,
        }

    def _create_job_with_xml(self, job_name: str, xml_config: str, folder_path: str | None = None) -> Dict[str, Any]:
        headers = {"Content-Type": "application/xml"}

        if folder_path:
            parts = [p for p in folder_path.strip("/").split("/") if p]
            folder_segment = "/".join(f"job/{p}" for p in parts)
            create_path = f"/{folder_segment}/createItem?name={job_name}"
            job_url = f"{self.config.base_url.rstrip('/')}/{folder_segment}/job/{job_name}/"
        else:
            create_path = f"/createItem?name={job_name}"
            job_url = f"{self.config.base_url.rstrip('/')}/job/{job_name}/"

        resp = self._request(
            "POST",
            create_path,
            data=xml_config.encode("utf-8"),
            headers=headers,
        )
        return {
            "ok": bool(resp.get("ok")),
            "status": resp.get("status"),
            "job_name": job_name,
            "job_url": job_url,
            "raw": {
                "url": resp.get("url"),
                "body": resp.get("body"),
            },
        }

    def get_server_info(self) -> Dict[str, Any]:
        return self._request("GET", "/api/json")

    def list_jobs(self) -> Dict[str, Any]:
        params = {"tree": "jobs[name,color,url]"}
        return self._request("GET", "/api/json", params=params)

    def get_job_info(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/api/json"
        params = {"depth": 1}
        return self._request("GET", path, params=params)

    def list_builds(self, job_name: str, depth: int = 1) -> Dict[str, Any]:
        path = f"/job/{job_name}/api/json"
        params = {"tree": "builds[number,url,result,timestamp,duration]", "depth": depth}
        return self._request("GET", path, params=params)

    def get_last_build_info(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/lastBuild/api/json"
        return self._request("GET", path)

    def get_build_info(self, job_name: str, build_number: int) -> Dict[str, Any]:
        path = f"/job/{job_name}/{build_number}/api/json"
        return self._request("GET", path)

    def get_build_console(self, job_name: str, build_number: int, tail_lines: int = 200) -> Dict[str, Any]:
        path = f"/job/{job_name}/{build_number}/logText/progressiveText"
        params = {"start": 0}
        result = self._request("GET", path, params=params)
        if isinstance(result.get("body"), str) and tail_lines > 0:
            lines = result["body"].splitlines()[-tail_lines:]
            result["body"] = "\n".join(lines)
        return result

    def trigger_build(self, job_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if parameters:
            path = f"/job/{job_name}/buildWithParameters"
            return self._request("POST", path, params=parameters)
        path = f"/job/{job_name}/build"
        return self._request("POST", path)

    def get_queue(self) -> Dict[str, Any]:
        return self._request("GET", "/queue/api/json")

    def cancel_queue_item(self, queue_id: int) -> Dict[str, Any]:
        path = f"/queue/cancelItem?id={queue_id}"
        return self._request("POST", path)

    def list_nodes(self) -> Dict[str, Any]:
        path = "/computer/api/json"
        params = {"tree": "computer[displayName,offline,temporarilyOffline,numExecutors,monitorData[*]]"}
        return self._request("GET", path, params=params)

    def get_node_info(self, node_name: str) -> Dict[str, Any]:
        path = f"/computer/{node_name}/api/json"
        return self._request("GET", path)

    def list_views(self) -> Dict[str, Any]:
        params = {"tree": "views[name,url]"}
        return self._request("GET", "/api/json", params=params)

    def get_view_info(self, view_name: str) -> Dict[str, Any]:
        path = f"/view/{view_name}/api/json"
        return self._request("GET", path)

    def disable_job(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/disable"
        return self._request("POST", path)

    def enable_job(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/enable"
        return self._request("POST", path)

    def delete_job(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/doDelete"
        return self._request("POST", path)

    def copy_job(self, from_job: str, new_job: str) -> Dict[str, Any]:
        params = {"name": new_job, "mode": "copy", "from": from_job}
        return self._request("POST", "/createItem", params=params)

    def get_job_config_xml(self, job_name: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/config.xml"
        return self._request("GET", path)

    def update_job_config_xml(self, job_name: str, xml_config: str) -> Dict[str, Any]:
        path = f"/job/{job_name}/config.xml"
        headers = {"Content-Type": "application/xml"}
        return self._request("POST", path, data=xml_config.encode("utf-8"), headers=headers)

    def create_inline_pipeline_job(
        self,
        job_name: str,
        pipeline_script: str,
        description: str | None = None,
        disable_concurrent_builds: bool = False,
        folder_path: str | None = None,
    ) -> Dict[str, Any]:
        desc = (description or "Created via JenkinsMCPServer.create_inline_pipeline_job").strip()
        concurrency_block = (
            "<properties>\n"
            "  <org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty/>\n"
            "</properties>\n"
            if disable_concurrent_builds
            else "<properties/>\n"
        )
        xml = f"""<flow-definition plugin=\"workflow-job\">\n    <description>{desc}</description>\n    <keepDependencies>false</keepDependencies>\n    {concurrency_block.rstrip()}\n\n    <definition class=\"org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition\" plugin=\"workflow-cps\">\n        <script><![CDATA[{pipeline_script}]]></script>\n        <sandbox>true</sandbox>\n    </definition>\n    <triggers/>\n    <disabled>false</disabled>\n</flow-definition>\n"""
        return self._create_job_with_xml(job_name, xml, folder_path=folder_path)

    def create_scm_pipeline_job(
        self,
        job_name: str,
        git_url: str,
        branch: str = "*/main",
        script_path: str = "Jenkinsfile",
        credentials_id: str | None = None,
        description: str | None = None,
        folder_path: str | None = None,
    ) -> Dict[str, Any]:
        desc = (description or "Created via JenkinsMCPServer.create_scm_pipeline_job").strip()
        cred_block = f"      <credentialsId>{credentials_id}</credentialsId>\n" if credentials_id else ""

        xml = f"""<flow-definition plugin=\"workflow-job\">\n    <description>{desc}</description>\n    <keepDependencies>false</keepDependencies>\n    <properties/>\n    <definition class=\"org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition\" plugin=\"workflow-cps\">\n        <scm class=\"hudson.plugins.git.GitSCM\" plugin=\"git\">\n            <configVersion>2</configVersion>\n            <userRemoteConfigs>\n                <hudson.plugins.git.UserRemoteConfig>\n                    <url>{git_url}</url>\n{cred_block}        </hudson.plugins.git.UserRemoteConfig>\n            </userRemoteConfigs>\n            <branches>\n                <hudson.plugins.git.BranchSpec>\n                    <name>{branch}</name>\n                </hudson.plugins.git.BranchSpec>\n            </branches>\n            <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>\n            <submoduleCfg class=\"list\"/>\n            <extensions/>\n        </scm>\n        <scriptPath>{script_path}</scriptPath>\n        <lightweight>true</lightweight>\n    </definition>\n    <triggers/>\n    <disabled>false</disabled>\n</flow-definition>\n"""
        return self._create_job_with_xml(job_name, xml, folder_path=folder_path)

    def list_plugins(self) -> Dict[str, Any]:
        path = "/pluginManager/api/json?depth=1"
        return self._request("GET", path)

    def get_system_info(self) -> Dict[str, Any]:
        params = {"tree": "mode,nodeDescription,numExecutors,quietingDown,useSecurity"}
        return self._request("GET", "/api/json", params=params)

    def list_artifacts(self, job_name: str, build_number: int) -> Dict[str, Any]:
        info = self.get_build_info(job_name, build_number)
        body = info.get("body", {}) if isinstance(info, dict) else {}
        artifacts = []
        if isinstance(body, dict):
            for art in body.get("artifacts", []):
                relative = art.get("relativePath")
                file_name = art.get("fileName")
                if relative and file_name:
                    artifacts.append({"fileName": file_name, "relativePath": relative})
        return {"ok": info.get("ok", False), "url": info.get("url"), "artifacts": artifacts}

    def get_build_changes(self, job_name: str, build_number: int) -> Dict[str, Any]:
        info = self.get_build_info(job_name, build_number)
        body = info.get("body", {}) if isinstance(info, dict) else {}
        changes: List[Dict[str, Any]] = []
        if isinstance(body, dict):
            for cs in body.get("changeSets", []):
                for item in cs.get("items", []):
                    changes.append({"author": item.get("author", {}).get("fullName"), "msg": item.get("msg"), "id": item.get("commitId")})
        return {"ok": info.get("ok", False), "url": info.get("url"), "changes": changes}

    def search_jobs(self, query: str, max_results: int = 20) -> Dict[str, Any]:
        jobs_resp = self.list_jobs()
        body = jobs_resp.get("body", {}) if isinstance(jobs_resp, dict) else {}
        results: List[Dict[str, Any]] = []
        q = (query or "").lower()
        if isinstance(body, dict):
            for job in body.get("jobs", []):
                name = str(job.get("name", ""))
                if q in name.lower():
                    results.append({"name": name, "url": job.get("url")})
                if len(results) >= max_results:
                    break
        return {"ok": jobs_resp.get("ok", False), "results": results}
