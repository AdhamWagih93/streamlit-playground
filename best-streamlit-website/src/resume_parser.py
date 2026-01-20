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
    graduation_year: Optional[int] = None
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

    # University / degree / graduation year heuristics
    university = ""
    degree = ""
    graduation_year: Optional[int] = None
    education_entry = ""

    edu_keywords = [
        "bachelor", "master", "b.sc", "msc", "m.sc", "phd", "bachelor of", "master of",
        "bs", "ms", "ba", "ma", "mba", "be", "b.eng", "m.eng",
    ]
    degree_patterns = [
        r"bachelor\s+of\s+[a-zA-Z &]+",
        r"master\s+of\s+[a-zA-Z &]+",
        r"(b\.?sc\.?|m\.?sc\.?)\s*[a-zA-Z &/\-]*",
        r"(bs|ms|ba|ma|be|me|mba)\s+[a-zA-Z &/\-]*",
        r"computer\s+science|information\s+technology|software\s+engineering|electrical\s+engineering",
    ]
    uni_markers = [
        "university", "faculty", "institute", "college", "academy",
        "school of", "polytechnic", "faculty of",
    ]

    import re
    # Scan lines to find the most likely education line(s)
    # Prefer lines within the Education section when present; otherwise fallback.
    edu_lines: List[str] = []
    edu_section_idxs = [i for i, l in enumerate(lines) if "education" in l.lower()]
    if edu_section_idxs:
        start = edu_section_idxs[0]
        window = lines[start : min(len(lines), start + 15)]
        for l in window:
            low = l.lower()
            if any(m in low for m in uni_markers) or any(k in low for k in edu_keywords):
                edu_lines.append(l)
    else:
        for l in lines:
            low = l.lower()
            # Exclude lines heavy in DevOps keywords to avoid false positives
            devops_hit_count = sum(1 for kw in DEVOPS_KEYWORDS if kw in low)
            if devops_hit_count >= 2:
                continue
            if any(m in low for m in uni_markers) or any(k in low for k in edu_keywords):
                edu_lines.append(l)

    # Choose the longest education line as the university line
    if edu_lines:
        # Pick best candidate by score (presence of markers + length)
        def _edu_score(s: str) -> int:
            ls = s.lower()
            score = sum(m in ls for m in uni_markers) + sum(k in ls for k in edu_keywords)
            return score * 10 + len(s)
        uni_line = max(edu_lines, key=_edu_score)
        university = uni_line
        low_uni = uni_line.lower()
        # Extract degree from the same line or nearby
        for pat in degree_patterns:
            m = re.search(pat, low_uni)
            if m:
                degree = uni_line[m.start():m.end()]
                break
        if not degree:
            # Look ahead within next 3 lines for degree keywords
            try:
                idx = lines.index(uni_line)
                for l2 in lines[idx+1: idx+4]:
                    low2 = l2.lower()
                    for pat in degree_patterns:
                        m2 = re.search(pat, low2)
                        if m2:
                            degree = l2[m2.start():m2.end()]
                            break
                    if degree:
                        break
            except Exception:
                pass
        # Build combined education entry
        if degree and university:
            education_entry = f"{degree} – {university}"
        elif university:
            education_entry = university
        elif degree:
            education_entry = degree

    # Graduation year detection near education lines or common labels
    grad_patterns = [
        r"graduat(?:ed|ion)\s*(?:in|year)?\s*(19\d{2}|20\d{2})",
        r"(19\d{2}|20\d{2})\s*[-–]\s*(19\d{2}|20\d{2})",  # degree span
        r"(19\d{2}|20\d{2})\s*(?:graduation|degree|b\.?sc|m\.?sc|bs|ms)",
    ]
    # Search in edu lines first
    for el in edu_lines[:3]:
        lel = el.lower()
        for pat in grad_patterns:
            m = re.search(pat, lel)
            if m:
                # prefer the last year in a range
                years = [int(y) for y in m.groups() if y and y.isdigit()]
                if years:
                    graduation_year = max(years)
                    break
        if graduation_year:
            break
    # Fallback: global search
    if graduation_year is None:
        m = re.search(r"(graduation|graduated|degree)\s*(in\s*)?(19\d{2}|20\d{2})", low_txt)
        if m:
            try:
                graduation_year = int(m.group(3))
            except Exception:
                graduation_year = None
    # If still none, try any year near education entry
    if graduation_year is None and education_entry:
        try:
            idx = lines.index(university) if university in lines else -1
            window = []
            if idx >= 0:
                window = lines[max(0, idx-2): idx+3]
            else:
                window = edu_lines[:3]
            for w in window:
                yrs = re.findall(r"(19\d{2}|20\d{2})", w)
                yrs = [int(y) for y in yrs]
                if yrs:
                    graduation_year = max(yrs)
                    break
        except Exception:
            pass

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

    # Years of experience heuristic (enhanced):
    import datetime as _dt
    years_experience: Optional[float] = None

    # 1) Explicit "X years of experience"
    m_years = re.search(r"(\d+(?:\.\d+)?)\s+year[s]? of experience", low_txt)
    if m_years:
        try:
            years_experience = float(m_years.group(1))
        except Exception:
            years_experience = None

    # 2) Derive from job year spans (after graduation year, ignoring trainings/internships)
    def _line_is_training(s: str) -> bool:
        ls = s.lower()
        return any(k in ls for k in ["training", "trainee", "bootcamp", "workshop", "course", "internship", "intern"])

    if years_experience is None:
        job_spans: List[tuple[int, int]] = []
        for item in exp_items:
            desc = item.description or ""
            # Find year ranges
            ranges = re.findall(r"(19\d{2}|20\d{2}).{0,20}(present|current|19\d{2}|20\d{2})", desc.lower())
            for a, b in ranges:
                try:
                    start_y = int(a)
                    end_y = _dt.datetime.utcnow().year if b in ("present", "current") else int(b)
                except Exception:
                    continue
                # Skip trainings/internships
                if _line_is_training(desc):
                    continue
                # Skip jobs before graduation if we have that info
                if graduation_year and start_y < graduation_year:
                    # If end_y <= graduation_year, skip; else clamp to graduation_year
                    if end_y <= graduation_year:
                        continue
                    start_y = graduation_year
                if end_y >= start_y:
                    job_spans.append((start_y, end_y))
        # Merge overlapping spans and sum durations
        job_spans = sorted(job_spans)
        merged: List[tuple[int, int]] = []
        for s, e in job_spans:
            if not merged or s > merged[-1][1]:
                merged.append((s, e))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        if merged:
            total_years = sum(e - s for s, e in merged)
            years_experience = float(total_years)

    # Currently working heuristic
    currently_working = bool(re.search(r"(present|current)", low_txt))

    return ResumeProfile(
        name=name,
        email=email,
        phone=phone,
        years_experience=years_experience,
        university=university,
        degree=degree,
        graduation_year=graduation_year,
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
