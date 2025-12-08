import json
import os
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from src.theme import set_theme
from src.resume_parser import (
    extract_text_from_file,
    extract_text_from_path,
    parse_resume,
    resume_profile_to_dict,
)

set_theme()

st.set_page_config(page_title="DevOps Referral Agent", page_icon="🛠", layout="wide")

# --- Hero / Intro ---
hero_css = """
<style>
.devops-hero {
    background: radial-gradient(circle at top left, #0b63d610 0, transparent 55%),
                linear-gradient(135deg, #e0ecff 0%, #f3f6ff 40%, #ffffff 100%);
    border-radius: 18px;
    padding: 1.8rem 1.6rem 1.4rem 1.6rem;
    box-shadow: 0 10px 40px rgba(11, 99, 214, 0.18);
    margin-bottom: 1.5rem;
}
.devops-hero-title {
    font-size: 1.8rem;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #0b2140;
}
.devops-hero-sub {
    font-size: 0.98rem;
    color: #51658a;
    max-width: 640px;
}
.devops-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(11, 99, 214, 0.06);
    color: #0b63d6;
    font-size: 0.75rem;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
</style>
"""

st.markdown(hero_css, unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([0.7, 0.3])
    with col1:
        st.markdown("<div class='devops-hero'>", unsafe_allow_html=True)
        st.markdown("<div class='devops-pill'>🛠 DEVOPS REFERRAL / SCREENING</div>", unsafe_allow_html=True)
        st.markdown("<div class='devops-hero-title'>DevOps Engineer Resume Deep-Dive</div>", unsafe_allow_html=True)
        st.markdown(
            "<p class='devops-hero-sub'>Upload a candidate's resume (PDF/DOCX) and we will peel it apart, extracting every DevOps-relevant detail into a clean, structured profile that you can scan in seconds.</p>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        st.metric("Ready for", "DevOps referrals", "+screening")

# --- Upload & parsing ---
left, right = st.columns([0.38, 0.62])

with left:
    st.subheader("1 · Source resumes")
    mode = st.radio(
        "Choose source",
        ["Single upload", "Scan local folder"],
        index=1,
        help="Upload one CV or automatically scan all PDFs/DOCXs under D:\\DevOps CVs\\**",
    )

    uploaded = None
    folder_results: List[Dict[str, Any]] = []

    if mode == "Single upload":
        uploaded = st.file_uploader("Resume (PDF or DOCX)", type=["pdf", "docx", "doc"], accept_multiple_files=False)
        show_preview = st.checkbox(
            "Show inline preview",
            value=False,
            help="Preview the uploaded file inside an expander (PDF iframe or DOCX text).",
        )
        parse_btn = st.button("Parse resume 🧬", type="primary", use_container_width=True)
        if parse_btn and not uploaded:
            st.warning("Please upload a resume file first.")
    else:
        base_dir = r"D:\\DevOps CVs"
        st.write(f"Scanning base folder: `{base_dir}`")
        scan_btn = st.button("Scan all CVs in folder 📂", type="primary", use_container_width=True)

        if scan_btn:
            if not os.path.isdir(base_dir):
                st.error(f"Folder not found: {base_dir}")
            else:
                with st.spinner("Scanning and parsing all PDFs/DOCXs under DevOps CVs…"):
                    exts = (".pdf", ".docx", ".doc")
                    for root, _dirs, files in os.walk(base_dir):
                        for fn in files:
                            if not fn.lower().endswith(exts):
                                continue
                            full_path = os.path.join(root, fn)
                            try:
                                text = extract_text_from_path(full_path)
                                profile = parse_resume(text)
                                data = resume_profile_to_dict(profile)
                                data["_path"] = full_path
                                data["_folder"] = os.path.relpath(root, base_dir)
                                folder_results.append(data)
                            except Exception as e:  # pragma: no cover - defensive
                                folder_results.append(
                                    {
                                        "name": fn,
                                        "error": str(e),
                                        "_path": full_path,
                                        "_folder": os.path.relpath(root, base_dir),
                                    }
                                )
                st.session_state["devops_folder_results"] = folder_results

    parsed: Dict[str, Any] | None = None
    raw_text: str = ""

    # State handling for single upload mode
    if mode == "Single upload" and uploaded and ("devops_parsed" not in st.session_state):
        if parse_btn:
            with st.spinner("Extracting and analyzing resume…"):
                file_bytes = uploaded.read()
                # Reuse the same file object for text extraction
                import io as _io

                fake_file = _io.BytesIO(file_bytes)
                fake_file.name = uploaded.name
                raw_text = extract_text_from_file(fake_file)
                profile = parse_resume(raw_text)
                parsed = resume_profile_to_dict(profile)
                st.session_state["devops_parsed"] = parsed
                st.session_state["devops_raw"] = raw_text
                st.session_state["devops_file_bytes"] = file_bytes
                st.session_state["devops_file_name"] = uploaded.name
                st.session_state["devops_show_preview"] = show_preview
    elif mode == "Single upload" and "devops_parsed" in st.session_state:
        parsed = st.session_state.get("devops_parsed")
        raw_text = st.session_state.get("devops_raw", "")

    if mode == "Scan local folder" and "devops_folder_results" in st.session_state:
        folder_results = st.session_state.get("devops_folder_results", [])

with right:
    st.subheader("2 · Structured DevOps profile(s)")
    if mode == "Single upload":
        if not parsed:
            st.info("Upload a resume on the left, then click **Parse resume** to see the structured view here.")
        else:
            name = parsed.get("name") or "(name not detected)"
            email = parsed.get("email") or "—"
            phone = parsed.get("phone") or "—"
            summary = parsed.get("summary") or "No summary detected."
            skills = parsed.get("skills") or []
            cloud = parsed.get("cloud_platforms") or []
            infra = parsed.get("infra_as_code") or []
            cicd = parsed.get("ci_cd") or []
            monitoring = parsed.get("monitoring") or []
            certs = parsed.get("certifications") or []
            exp = parsed.get("experience") or []
            years = parsed.get("years_experience")
            university = parsed.get("university") or "—"
            degree = parsed.get("degree") or "—"
            iti = parsed.get("iti") or False
            nti = parsed.get("nti") or False
            currently_working = parsed.get("currently_working") or False

            # Header card
            st.markdown(
            f"""
            <div style='background:linear-gradient(135deg,#0b63d6,#6c5ce7);border-radius:18px;padding:18px 18px 16px 18px;color:#fff;box-shadow:0 8px 26px rgba(11,99,214,0.35);margin-bottom:1rem;'>
                <div style='font-size:1.4rem;font-weight:800;margin-bottom:2px;'>{name}</div>
                <div style='font-size:0.82rem;opacity:0.95;margin-bottom:6px;'>{summary}</div>
                <div style='display:flex;flex-wrap:wrap;gap:8px;font-size:0.78rem;'>
                    <span style='background:rgba(255,255,255,0.18);padding:4px 10px;border-radius:999px;'>{email}</span>
                    <span style='background:rgba(255,255,255,0.18);padding:4px 10px;border-radius:999px;'>{phone}</span>
                    <span style='background:rgba(255,255,255,0.18);padding:4px 10px;border-radius:999px;'>DevOps-focused extraction</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
            )

            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                st.metric("Keywords detected", len(skills))
            with c2:
                st.metric("Years of experience", f"{years:.1f}" if years is not None else "—")
            with c3:
                status_label = "Currently working" if currently_working else "Not clearly current"
                st.metric("Status", status_label)

            st.markdown("#### Education & Programs")
            ed1, ed2, ed3 = st.columns([1, 1, 1])
            with ed1:
                st.markdown(f"**University / Institute**  \\n+{university}")
            with ed2:
                st.markdown(f"**Degree**  \\n+{degree}")
            with ed3:
                badges = []
                if iti:
                    badges.append("ITI")
                if nti:
                    badges.append("NTI")
                if badges:
                    st.markdown("**Programs**  \\n+" + ", ".join(badges))
                else:
                    st.markdown("**Programs**  \\n+—")

            st.markdown("---")

            # Skill clusters
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("#### DevOps stack")
                if not skills:
                    st.caption("No DevOps keywords detected.")
                else:
                    for s in skills:
                        st.markdown(f"- `{s}`")

                st.markdown("#### Cloud / IaC")
                st.write({"Cloud": cloud, "Infra-as-code": infra})

            with col_b:
                st.markdown("#### CI/CD & Observability")
                st.write({"CI/CD": cicd, "Monitoring": monitoring})

                st.markdown("#### Certifications")
                if not certs:
                    st.caption("No certifications detected (or not recognized by the heuristic).")
                else:
                    for c in certs:
                        st.markdown(f"- {c}")

            st.markdown("---")

            # Experience timeline-like table
            st.markdown("### Experience blocks")
            if not exp:
                st.caption("Could not segment explicit experience blocks; you can still inspect the raw text below.")
            else:
                exp_df = pd.DataFrame(exp)
                # Keep columns in a nice order if present
                cols = [c for c in ["company", "title", "start", "end", "description"] if c in exp_df.columns]
                st.dataframe(exp_df[cols], use_container_width=True, hide_index=True)

            with st.expander("Raw JSON profile", expanded=False):
                st.json(parsed)

            with st.expander("Raw extracted text", expanded=False):
                st.text_area("Raw text", value=raw_text, height=260)

            # Optional inline preview
            file_bytes = st.session_state.get("devops_file_bytes")
            file_name = st.session_state.get("devops_file_name", "")
            show_preview_state = st.session_state.get("devops_show_preview", False)

            if file_bytes and show_preview_state:
                with st.expander(f"Preview: {file_name}", expanded=False):
                    if file_name.lower().endswith(".pdf"):
                        import base64

                        b64 = base64.b64encode(file_bytes).decode("utf-8")
                        pdf_html = f"""
                        <iframe src="data:application/pdf;base64,{b64}"
                                width="100%" height="600px"
                                style="border: none;"></iframe>
                        """
                        st.markdown(pdf_html, unsafe_allow_html=True)
                    else:
                        st.caption("DOC/DOCX preview as text (formatting may differ from the original document).")
                        try:
                            import io as _io
                            import docx2txt as _docx2txt

                            buf = _io.BytesIO(file_bytes)
                            preview_text = _docx2txt.process(buf)
                        except Exception:
                            preview_text = raw_text or ""
                        st.text_area("Document preview", value=preview_text, height=400)
    else:
        # Folder scan mode: show a table of all detected CVs with folder info
        if not folder_results:
            st.info("Click **Scan all CVs in folder 📂** on the left to load all PDFs/DOCXs from D:\\DevOps CVs\\**.")
        else:
            st.markdown("### Detected CVs under `D:\\DevOps CVs\\**`")
            records = []
            for rec in folder_results:
                records.append(
                    {
                        "Name": rec.get("name") or rec.get("summary") or "(unknown)",
                        "Email": rec.get("email", ""),
                        "Cloud": ", ".join(rec.get("cloud_platforms") or []),
                        "CI/CD": ", ".join(rec.get("ci_cd") or []),
                        "Monitoring": ", ".join(rec.get("monitoring") or []),
                        "Folder": rec.get("_folder", ""),
                        "File": os.path.basename(rec.get("_path", "")),
                        "Path": rec.get("_path", ""),
                    }
                )
            df = pd.DataFrame(records)
            st.dataframe(df, use_container_width=True, hide_index=True)

            with st.expander("Raw parsed records (JSON)", expanded=False):
                st.json(folder_results)
