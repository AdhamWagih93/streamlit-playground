import io
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional

import docx2txt
import pdfplumber


@dataclass
class ExperienceItem:
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    description: str = ""


@dataclass
class ResumeProfile:
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""
    years_experience: Optional[float] = None
    university: str = ""
    degree: str = ""
    iti: bool = False
    nti: bool = False
    currently_working: bool = False
    skills: List[str] = None
    devops_tools: List[str] = None
    cloud_platforms: List[str] = None
    ci_cd: List[str] = None
    infra_as_code: List[str] = None
    monitoring: List[str] = None
    certifications: List[str] = None
    experience: List[ExperienceItem] = None
    raw_text: str = ""


DEVOPS_KEYWORDS = [
    "docker", "kubernetes", "helm", "terraform", "ansible", "packer", "jenkins",
    "gitlab ci", "github actions", "azure devops", "teamcity", "bamboo",
    "aws", "azure", "gcp", "google cloud", "cloudformation",
    "prometheus", "grafana", "datadog", "new relic", "splunk", "elastic", "elk",
    "linux", "bash", "shell", "python", "golang", "go",
]


CERT_PATTERNS = [
    "aws certified", "azure administrator", "azure devops engineer",
    "ckad", "cka", "ckad", "ckad", "terraform associate", "cka", "ckad", "cksa",
]


def _normalize_text(text: str) -> str:
    return (text or "").replace("\r", " ").replace("\n", " \n ")


def extract_text_from_file(uploaded_file) -> str:
    """Best-effort text extraction from PDF or DOCX; falls back to binary read."""
    if uploaded_file is None:
        return ""
    name = (uploaded_file.name or "").lower()
    data = uploaded_file.read()
    bio = io.BytesIO(data)
    text = ""
    try:
        if name.endswith(".pdf"):
            with pdfplumber.open(bio) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
            text = "\n".join(pages)
        elif name.endswith(".docx") or name.endswith(".doc"):
            bio.seek(0)
            text = docx2txt.process(bio)
        else:
            # Try utf-8 decode as fallback
            text = data.decode("utf-8", errors="ignore")
    except Exception:
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
    return _normalize_text(text)


def extract_text_from_path(path: str) -> str:
    """Text extraction variant that works from a filesystem path (for local CV folders)."""
    if not path or not os.path.isfile(path):
        return ""
    name = os.path.basename(path).lower()
    text = ""
    try:
        if name.endswith(".pdf"):
            with open(path, "rb") as f, pdfplumber.open(f) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
            text = "\n".join(pages)
        elif name.endswith(".docx") or name.endswith(".doc"):
            text = docx2txt.process(path)
        else:
            with open(path, "rb") as f:
                data = f.read()
            text = data.decode("utf-8", errors="ignore")
    except Exception:
        try:
            with open(path, "rb") as f:
                data = f.read()
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
    return _normalize_text(text)


def parse_resume(text: str) -> ResumeProfile:
    """Very lightweight heuristic parser focused on DevOps signals.

    This is intentionally simple but structured; it can be evolved later.
    """
    import re

    txt = text or ""
    lines = [l.strip() for l in txt.split("\n") if l.strip()]

    # Simple guesses
    name = lines[0] if lines else ""
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", txt)
    email = email_match.group(0) if email_match else ""

    phone_match = re.search(r"(\+?\d[\d .\-()]{7,}\d)", txt)
    phone = phone_match.group(0) if phone_match else ""

    # Summary as first 3-6 lines until an obvious section header
    summary_lines: List[str] = []
    for l in lines[1:10]:
        low = l.lower()
        if any(h in low for h in ["experience", "employment", "work history", "professional experience", "skills", "technical skills"]):
            break
        summary_lines.append(l)
    summary = " ".join(summary_lines).strip()

    # Skills: keyword-based with de-dup
    low_txt = txt.lower()
    skills_found: List[str] = []
    for kw in DEVOPS_KEYWORDS:
        if kw in low_txt:
            skills_found.append(kw)
    skills_found = sorted(set(skills_found))

    # Simple buckets
    cloud_platforms = [s for s in skills_found if s in ["aws", "azure", "gcp", "google cloud"]]
    infra_as_code = [s for s in skills_found if s in ["terraform", "cloudformation", "ansible", "packer"]]
    ci_cd = [s for s in skills_found if s in [
        "jenkins", "gitlab ci", "github actions", "azure devops", "teamcity", "bamboo"
    ]]

    monitoring = [s for s in skills_found if s in [
        "prometheus", "grafana", "datadog", "new relic", "splunk", "elastic", "elk"
    ]]

    # University / degree heuristics
    university = ""
    degree = ""
    edu_keywords = [
        "bachelor", "master", "b.sc", "msc", "phd", "bachelor of", "master of", "b.sc.", "m.sc.",
    ]
    uni_markers = ["university", "faculty", "institute", "college", "academy"]

    for l in lines:
        low = l.lower()
        if any(k in low for k in edu_keywords) and any(m in low for m in uni_markers):
            university = l
            break

    if university:
        low = university.lower()
        for k in edu_keywords:
            if k in low:
                idx = low.index(k)
                degree = university[idx:].strip()
                break

    # ITI / NTI detection
    iti = " iti " in f" {low_txt} " or "information technology institute" in low_txt
    nti = " nti " in f" {low_txt} " or "national telecommunication institute" in low_txt

    # Certifications (substring search)
    certs: List[str] = []
    for pat in CERT_PATTERNS:
        for m in re.finditer(pat, low_txt):
            snippet = txt[m.start():m.start()+80].split("\n")[0]
            certs.append(snippet.strip())
    certs = sorted(set(certs))

    # Naive experience extraction: grab blocks starting with a year range or YYYY-MM
    exp_items: List[ExperienceItem] = []
    exp_pattern = re.compile(r"(20\d{2}|19\d{2}).{0,40}(present|current|20\d{2}|19\d{2})", re.IGNORECASE)
    current_block: List[str] = []
    for l in lines:
        if exp_pattern.search(l):
            if current_block:
                exp_items.append(ExperienceItem(description=" ".join(current_block)))
                current_block = []
        current_block.append(l)
    if current_block:
        exp_items.append(ExperienceItem(description=" ".join(current_block)))

    # Years of experience heuristic
    import datetime as _dt
    years_experience: Optional[float] = None

    # 1) Explicit "X years of experience"
    m_years = re.search(r"(\d+(?:\.\d+)?)\s+year[s]? of experience", low_txt)
    if m_years:
        try:
            years_experience = float(m_years.group(1))
        except Exception:
            years_experience = None

    # 2) Derive from year span if not found explicitly
    if years_experience is None:
        years = re.findall(r"(19\d{2}|20\d{2})", txt)
        if years:
            ys = sorted(int(y) for y in years)
            now_year = _dt.datetime.utcnow().year
            start_y = max(min(ys), 1970)
            end_y = min(max(ys), now_year)
            if end_y >= start_y:
                years_experience = float(end_y - start_y)

    # Currently working heuristic
    currently_working = bool(re.search(r"(present|current)", low_txt))

    return ResumeProfile(
        name=name,
        email=email,
        phone=phone,
        years_experience=years_experience,
        university=university,
        degree=degree,
        iti=iti,
        nti=nti,
        currently_working=currently_working,
        summary=summary,
        skills=skills_found,
        devops_tools=skills_found,
        cloud_platforms=cloud_platforms,
        infra_as_code=infra_as_code,
        ci_cd=ci_cd,
        monitoring=monitoring,
        certifications=certs,
        experience=exp_items,
        raw_text=txt,
    )


def resume_profile_to_dict(profile: ResumeProfile) -> Dict[str, Any]:
    data = asdict(profile)
    # Flatten experience for display convenience
    data["experience"] = [asdict(e) for e in (profile.experience or [])]
    return data
