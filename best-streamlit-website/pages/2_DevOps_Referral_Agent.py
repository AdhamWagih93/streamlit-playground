from __future__ import annotations

import io
import os
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from src.resume_parser import (
    extract_text_from_file,
    extract_text_from_path,
    parse_resume,
    resume_profile_to_dict,
)


# ---- Scoring helpers ----
DEVOPS_WEIGHTS: Dict[str, float] = {
    "cloud": 0.25,
    "iac": 0.20,
    "cicd": 0.20,
    "monitoring": 0.15,
    "containers": 0.20,
}

CONTAINER_KEYWORDS: List[str] = [
    "docker",
    "kubernetes",
    "k8s",
    "helm",
    "containerd",
]


def compute_devops_score(rec: Dict[str, Any]) -> Dict[str, Any]:
    skills = [s.lower() for s in (rec.get("skills") or [])]
    cloud = rec.get("cloud_platforms") or []
    iac = rec.get("infra_as_code") or []
    cicd = rec.get("ci_cd") or []
    mon = rec.get("monitoring") or []

    parts: Dict[str, float] = {
        "cloud": min(len(cloud) / 3.0, 1.0),
        "iac": min(len(iac) / 3.0, 1.0),
        "cicd": min(len(cicd) / 3.0, 1.0),
        "monitoring": min(len(mon) / 3.0, 1.0),
        "containers": 1.0 if any(k in skills for k in CONTAINER_KEYWORDS) else 0.0,
    }

    total = sum(parts[k] * DEVOPS_WEIGHTS[k] for k in parts) * 100.0
    return {"parts": parts, "total": round(total, 1)}


def build_recommendations(rec: Dict[str, Any], parts: Dict[str, float]) -> List[str]:
    tips: List[str] = []
    if parts.get("cloud", 0) < 0.6:
        tips.append("Expand cloud exposure (AWS/Azure/GCP) with concrete projects.")
    if parts.get("iac", 0) < 0.6:
        tips.append("Show Infrastructure-as-Code (Terraform/Ansible) with modules and plans.")
    if parts.get("cicd", 0) < 0.6:
        tips.append("Highlight CI/CD pipelines (Jenkins/GitHub Actions/GitLab CI).")
    if parts.get("monitoring", 0) < 0.6:
        tips.append("Add observability: Prometheus/Grafana, logs, and alerts.")
    if parts.get("containers", 0) < 1.0:
        tips.append("Include containers/orchestration (Docker, Kubernetes, Helm).")
    return tips


# ---- Page layout ----


mode = st.radio(
    "Choose source",
    ["Single upload", "Scan local folder"],
    index=1,
    help="Upload one CV or automatically scan all PDFs/DOCXs under D:\\DevOps CVs\\**",
)

uploaded = None
folder_results: List[Dict[str, Any]] = []

if mode == "Single upload":
    uploaded = st.file_uploader(
        "Resume (PDF or DOCX)",
        type=["pdf", "docx", "doc"],
        accept_multiple_files=False,
    )
    parse_btn = st.button("Parse resume 🧬", type="primary")
    if parse_btn and not uploaded:
        st.warning("Please upload a resume file first.")
else:
    base_dir = r"D:\\DevOps CVs\\12-2025\\HR"
    st.write(f"Scanning base folder: `{base_dir}`")
    scan_btn = st.button("Scan all CVs in folder 📂", type="primary")

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
                        except Exception as e:  # noqa: BLE001
                            folder_results.append(
                                {
                                    "name": fn,
                                    "error": str(e),
                                    "_path": full_path,
                                    "_folder": os.path.relpath(root, base_dir),
                                }
                            )
            st.session_state["devops_folder_results"] = folder_results
# State handling for single upload mode
if mode == "Single upload" and uploaded and ("devops_parsed" not in st.session_state):
    if parse_btn:
        with st.spinner("Extracting and analyzing resume…"):
            import io as _io
            file_bytes = uploaded.read()
            fake_file = _io.BytesIO(file_bytes)
            fake_file.name = uploaded.name
            raw_text = extract_text_from_file(fake_file)
            profile = parse_resume(raw_text)
            parsed = resume_profile_to_dict(profile)
            st.session_state["devops_parsed"] = parsed
            st.session_state["devops_raw"] = raw_text
            st.session_state["devops_file_bytes"] = file_bytes
            st.session_state["devops_file_name"] = uploaded.name
elif mode == "Single upload" and "devops_parsed" in st.session_state:
    parsed = st.session_state.get("devops_parsed")
    raw_text = st.session_state.get("devops_raw", "")

if mode == "Scan local folder" and "devops_folder_results" in st.session_state:
    folder_results = st.session_state.get("devops_folder_results", [])

# --- Views ---
st.subheader("2 · Profiles")
overview_tab, deep_tab = st.tabs(["Overview", "Deep Dive"])

with overview_tab:
    if mode == "Scan local folder":
        if not folder_results:
            st.info("Click **Scan all CVs in folder 📂** above to load all PDFs/DOCXs.")
        else:
            st.markdown("### Detected CVs")
            records: List[Dict[str, Any]] = []
            for rec in folder_results:
                scoring = compute_devops_score(rec)
                records.append(
                    {
                        "Name": rec.get("name") or rec.get("summary") or "(unknown)",
                        "Email": rec.get("email", ""),
                        "Phone": rec.get("phone", ""),
                        "Cloud": ", ".join(rec.get("cloud_platforms") or []),
                        "CI/CD": ", ".join(rec.get("ci_cd") or []),
                        "Monitoring": ", ".join(rec.get("monitoring") or []),
                        "IaC": ", ".join(rec.get("infra_as_code") or []),
                        "Containers": "Yes" if any(k in [s.lower() for s in (rec.get("skills") or [])] for k in CONTAINER_KEYWORDS) else "No",
                        "Certifications": ", ".join(rec.get("certifications") or []),
                        "University": rec.get("university", ""),
                        "Degree": rec.get("degree", ""),
                        "Graduation Year": rec.get("graduation_year", ""),
                        "Years": rec.get("years_experience", ""),
                        "Currently Working": "Yes" if rec.get("currently_working") else "No",
                        "Score": scoring["total"],
                        "Folder": rec.get("_folder", ""),
                        "File": os.path.basename(rec.get("_path", "")),
                        "Path": rec.get("_path", ""),
                    }
                )
            df = pd.DataFrame(records)
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown("#### 📊 Talent pool stats")
            try:
                avg_score = float(df["Score"].mean()) if not df.empty else 0.0
                avg_years = pd.Series([r.get("years_experience", 0.0) for r in folder_results]).mean()
                total_candidates = len(folder_results)
                cloud_counts = pd.Series(sum([r.get("cloud_platforms") or [] for r in folder_results], [])).value_counts()
                iac_counts = pd.Series(sum([r.get("infra_as_code") or [] for r in folder_results], [])).value_counts()
                cicd_counts = pd.Series(sum([r.get("ci_cd") or [] for r in folder_results], [])).value_counts()
                mon_counts = pd.Series(sum([r.get("monitoring") or [] for r in folder_results], [])).value_counts()
            except Exception:
                avg_score, avg_years, total_candidates = 0.0, 0.0, len(folder_results)
                cloud_counts = pd.Series(dtype=int)
                iac_counts = pd.Series(dtype=int)
                cicd_counts = pd.Series(dtype=int)
                mon_counts = pd.Series(dtype=int)

            k1, k2, k3 = st.columns(3)
            k1.metric("Avg score", f"{avg_score:.1f}")
            k2.metric("Avg years", f"{avg_years:.1f}")
            k3.metric("Candidates", f"{total_candidates}")

            cA, cB = st.columns(2)
            with cA:
                st.markdown("##### Cloud platforms")
                if not cloud_counts.empty:
                    st.bar_chart(cloud_counts)
                else:
                    st.caption("No cloud platforms detected.")
                st.markdown("##### IaC tools")
                if not iac_counts.empty:
                    st.bar_chart(iac_counts)
                else:
                    st.caption("No IaC tools detected.")
            with cB:
                st.markdown("##### CI/CD tools")
                if not cicd_counts.empty:
                    st.bar_chart(cicd_counts)
                else:
                    st.caption("No CI/CD tools detected.")
                st.markdown("##### Monitoring tools")
                if not mon_counts.empty:
                    st.bar_chart(mon_counts)
                else:
                    st.caption("No monitoring tools detected.")

            with st.expander("Raw parsed records (JSON)", expanded=False):
                st.json(folder_results)
    else:
        if parsed:
            row = {
                "Name": parsed.get("name") or parsed.get("summary") or "(unknown)",
                "Email": parsed.get("email", ""),
                "Phone": parsed.get("phone", ""),
                "Cloud": ", ".join(parsed.get("cloud_platforms") or []),
                "CI/CD": ", ".join(parsed.get("ci_cd") or []),
                "Monitoring": ", ".join(parsed.get("monitoring") or []),
                "IaC": ", ".join(parsed.get("infra_as_code") or []),
                "Certifications": ", ".join(parsed.get("certifications") or []),
                "University": parsed.get("university", ""),
                "Degree": parsed.get("degree", ""),
                "Graduation Year": parsed.get("graduation_year", ""),
                "Years": parsed.get("years_experience", ""),
                "Currently Working": "Yes" if parsed.get("currently_working") else "No",
            }
            st.dataframe(pd.DataFrame([row]), use_container_width=True, hide_index=True)
            st.info("Open the Deep Dive tab to see full details and the CV preview.")
        else:
            st.info("Upload a resume above, then parse to see it here.")

with deep_tab:
    # Deep dive into a single candidate
    rec: Dict[str, Any] | None = None
    if mode == "Scan local folder" and folder_results:
        candidate_options = [
            f"{r.get('name') or r.get('summary') or '(unknown)'} — {os.path.basename(r.get('_path',''))}"
            for r in folder_results
        ]
        chosen = (
            st.selectbox("Choose a candidate", candidate_options, key="deep_dive_select")
            if candidate_options
            else None
        )
        if chosen:
            idx = candidate_options.index(chosen)
            rec = folder_results[idx]
    elif mode == "Single upload" and parsed:
        rec = parsed
    else:
        st.info("Load candidates using Source resumes above.")

    if rec:
        title = rec.get("name") or rec.get("summary") or "(unknown)"
        email = rec.get("email") or "—"
        phone = rec.get("phone") or "—"
        score = compute_devops_score(rec).get("total", 0.0)
        st.markdown(
            f"""
            <div style='background:linear-gradient(135deg,#0b63d6,#6c5ce7);border-radius:18px;padding:18px;color:#fff;box-shadow:0 10px 30px rgba(11,99,214,0.3);margin-bottom:10px;'>
                <div style='font-size:1.35rem;font-weight:800;'>{title}</div>
                <div style='font-size:0.9rem;opacity:0.95;'>{email} — {phone}</div>
                <div style='margin-top:6px;font-size:0.9rem;'>Score: {score:.1f} / 100</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Years", f"{rec.get('years_experience','—')}")
        k2.metric("Graduation", f"{rec.get('graduation_year','—')}")
        k3.metric("Cloud", ", ".join(rec.get("cloud_platforms") or []) or "—")
        k4.metric("IaC", ", ".join(rec.get("infra_as_code") or []) or "—")

        cv_col, info_col = st.columns([2, 3])

        with cv_col:
            st.markdown("### CV Preview")
            cv_path = rec.get("_path") if isinstance(rec, dict) else None
            if cv_path and os.path.isfile(cv_path):
                if cv_path.lower().endswith(".pdf"):
                    import base64

                    try:
                        with open(cv_path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        pdf_html = f"""
                        <iframe src=\"data:application/pdf;base64,{b64}\"
                                width=\"100%\" height=\"700px\"
                                style=\"border: none; border-radius: 12px; background:#fff;\"></iframe>
                        """
                        st.markdown(pdf_html, unsafe_allow_html=True)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Failed to load PDF: {e}")
                else:
                    st.caption(
                        "DOC/DOCX preview as extracted text; formatting may differ from the original."
                    )
                    try:
                        import docx2txt as _docx2txt

                        preview_text = _docx2txt.process(cv_path)
                    except Exception as e:  # noqa: BLE001
                        preview_text = f"(Could not extract DOC/DOCX preview: {e})"
                    st.text_area("Document preview", value=preview_text, height=700)
            else:
                # Single upload bytes preview (embedded where possible)
                file_bytes = st.session_state.get("devops_file_bytes")
                file_name = st.session_state.get("devops_file_name", "")
                if file_bytes and file_name:
                    if file_name.lower().endswith(".pdf"):
                        import base64

                        b64 = base64.b64encode(file_bytes).decode("utf-8")
                        pdf_html = f"""
                        <iframe src=\"data:application/pdf;base64,{b64}\"
                                width=\"100%\" height=\"700px\"
                                style=\"border: none; border-radius: 12px; background:#fff;\"></iframe>
                        """
                        st.markdown(pdf_html, unsafe_allow_html=True)
                    else:
                        st.caption(
                            "DOC/DOCX preview as text (formatting may differ from the original document)."
                        )
                        try:
                            import docx2txt as _docx2txt

                            buf = io.BytesIO(file_bytes)
                            preview_text = _docx2txt.process(buf)
                        except Exception:  # noqa: BLE001
                            preview_text = raw_text or ""
                        st.text_area("Document preview", value=preview_text, height=700)

        with info_col:
            # --- Interview scorecard for 3 interviewers ---
            st.markdown("### Interview Scorecard")

            criteria = [
                "English",
                "SDLC",
                "Scripting",
                "CI/CD",
                "Git",
                "Docker",
                "K8s",
                "Ansible",
                "Jenkins",
                "Nexus",
                "ELK",
            ]

            # Mock interviewer configuration section
            default_names = ["Interviewer 1", "Interviewer 2", "Interviewer 3"]
            names_state = st.session_state.get("devops_interviewer_names", default_names)
            c_name1, c_name2, c_name3 = st.columns(3)
            names_state[0] = c_name1.text_input(
                "Interviewer 1 name", value=names_state[0], key="int_name_1"
            )
            names_state[1] = c_name2.text_input(
                "Interviewer 2 name", value=names_state[1], key="int_name_2"
            )
            names_state[2] = c_name3.text_input(
                "Interviewer 3 name", value=names_state[2], key="int_name_3"
            )
            st.session_state["devops_interviewer_names"] = names_state

            col_a, col_b, col_c = names_state
            candidate_key = title

            scorecard_csv = os.path.join("data", "interview_scorecards.csv")
            existing = None
            if os.path.isfile(scorecard_csv):
                try:
                    existing = pd.read_csv(scorecard_csv)
                except Exception:  # noqa: BLE001
                    existing = None

            if existing is not None and "candidate" in existing.columns:
                subset = existing[existing["candidate"] == candidate_key]
            else:
                subset = pd.DataFrame()

            if not subset.empty and "criterion" in subset.columns:
                subset = subset.sort_values("criterion")
                base_df = pd.DataFrame(
                    {
                        "Criterion": subset["criterion"].tolist(),
                        col_a: subset.get("score_1", 0).tolist(),
                        col_b: subset.get("score_2", 0).tolist(),
                        col_c: subset.get("score_3", 0).tolist(),
                    }
                )
                comments_default = {
                    "c1": subset.get("comment_1", pd.Series([""])).iloc[0],
                    "c2": subset.get("comment_2", pd.Series([""])).iloc[0],
                    "c3": subset.get("comment_3", pd.Series([""])).iloc[0],
                    "overall": subset.get("comment_overall", pd.Series([""])).iloc[0],
                }
            else:
                base_df = pd.DataFrame(
                    {
                        "Criterion": criteria,
                        col_a: [0] * len(criteria),
                        col_b: [0] * len(criteria),
                        col_c: [0] * len(criteria),
                    }
                )
                comments_default = {"c1": "", "c2": "", "c3": "", "overall": ""}

            # Compute averages column for display
            base_df["Average"] = (
                base_df[[col_a, col_b, col_c]].mean(axis=1).round(2)
            )

            scores_state_key = f"devops_interview_scores_{candidate_key}"
            current_df = st.session_state.get(scores_state_key, base_df)

            edited_df = st.data_editor(
                current_df,
                num_rows="fixed",
                hide_index=True,
                column_config={
                    "Average": st.column_config.NumberColumn(disabled=True),
                },
                key=f"editor_{scores_state_key}",
            )
            st.session_state[scores_state_key] = edited_df

            st.markdown("#### Interviewer comments")
            c1 = st.text_area(
                f"{col_a} comments",
                value=comments_default["c1"],
                key=f"c1_{scores_state_key}",
            )
            c2 = st.text_area(
                f"{col_b} comments",
                value=comments_default["c2"],
                key=f"c2_{scores_state_key}",
            )
            c3 = st.text_area(
                f"{col_c} comments",
                value=comments_default["c3"],
                key=f"c3_{scores_state_key}",
            )
            overall_comment = st.text_area(
                "Overall comments",
                value=comments_default["overall"],
                key=f"overall_{scores_state_key}",
            )

            if st.button(
                "Save interview scores", key=f"save_{scores_state_key}", type="primary"
            ):
                rows = []
                for _, row in edited_df.iterrows():
                    rows.append(
                        {
                            "candidate": candidate_key,
                            "criterion": row["Criterion"],
                            "score_1": row[col_a],
                            "score_2": row[col_b],
                            "score_3": row[col_c],
                            "comment_1": c1,
                            "comment_2": c2,
                            "comment_3": c3,
                            "comment_overall": overall_comment,
                            "interviewer_1": col_a,
                            "interviewer_2": col_b,
                            "interviewer_3": col_c,
                        }
                    )

                new_df = pd.DataFrame(rows)

                if existing is not None and not existing.empty:
                    keep_mask = existing["candidate"] != candidate_key
                    existing_clean = existing[keep_mask]
                    final_df = pd.concat([existing_clean, new_df], ignore_index=True)
                else:
                    final_df = new_df

                os.makedirs(os.path.dirname(scorecard_csv), exist_ok=True)
                final_df.to_csv(scorecard_csv, index=False)
                st.success("Interview scores saved.")

            # Additional candidate info in collapsible sections to reduce clutter
            with st.expander("Skills & Tools", expanded=False):
                st.write(
                    {
                        "DevOps": rec.get("skills") or [],
                        "CI/CD": rec.get("ci_cd") or [],
                        "Monitoring": rec.get("monitoring") or [],
                    }
                )

            with st.expander("Certifications", expanded=False):
                certs = rec.get("certifications") or []
                if certs:
                    for c in certs:
                        st.markdown(f"- {c}")
                else:
                    st.caption("No certifications listed.")

            with st.expander("Education", expanded=False):
                programs = ", ".join(
                    [
                        p
                        for p in (
                            [
                                "ITI" if rec.get("iti") else None,
                                "NTI" if rec.get("nti") else None,
                            ]
                        )
                        if p
                    ]
                ) or "—"
                st.write(
                    {
                        "University": rec.get("university") or "—",
                        "Degree": rec.get("degree") or "—",
                        "Graduation": rec.get("graduation_year") or "—",
                        "Programs": programs,
                    }
                )

            with st.expander("Experience", expanded=False):
                exp = rec.get("experience") or []
                if exp:
                    exp_df = pd.DataFrame(exp)
                    cols = [
                        c
                        for c in ["company", "title", "start", "end", "description"]
                        if c in exp_df.columns
                    ]
                    st.dataframe(exp_df[cols], use_container_width=True, hide_index=True)
                else:
                    st.caption("No structured experience blocks parsed.")
