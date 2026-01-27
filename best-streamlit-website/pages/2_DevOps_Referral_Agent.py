from __future__ import annotations

import io
import os
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from src.resume_parser import extract_text_from_file, extract_text_from_path, parse_resume, resume_profile_to_dict
from src.theme import set_theme


set_theme(page_title="DevOps Referral Agent", page_icon="🧑‍💼")


CONTAINER_KEYWORDS = {"docker", "kubernetes", "k8s", "helm", "containerd"}


def _years_to_score(years: Any) -> float:
    try:
        val = float(years) if str(years).strip() else 0.0
    except Exception:
        val = 0.0
    return min(1.0, val / 6.0)


def compute_devops_score(profile: Dict[str, Any]) -> Dict[str, Any]:
    cloud = 1.0 if (profile.get("cloud_platforms") or []) else 0.0
    iac = 1.0 if (profile.get("infra_as_code") or []) else 0.0
    cicd = 1.0 if (profile.get("ci_cd") or []) else 0.0
    monitoring = 1.0 if (profile.get("monitoring") or []) else 0.0

    skills_lower = [s.lower() for s in (profile.get("skills") or [])]
    containers = 1.0 if any(k in skills_lower for k in CONTAINER_KEYWORDS) else 0.0
    years = _years_to_score(profile.get("years_experience"))
    certs = 1.0 if (profile.get("certifications") or []) else 0.0

    weights = {
        "cloud": 0.2,
        "iac": 0.15,
        "cicd": 0.15,
        "monitoring": 0.15,
        "containers": 0.15,
        "years": 0.1,
        "certifications": 0.1,
    }
    parts = {
        "cloud": cloud,
        "iac": iac,
        "cicd": cicd,
        "monitoring": monitoring,
        "containers": containers,
        "years": years,
        "certifications": certs,
    }
    total = sum(parts[k] * weights[k] for k in weights) * 100.0
    return {"total": round(total, 2), "parts": parts}


def build_recommendations(profile: Dict[str, Any], parts: Dict[str, float]) -> List[str]:
    tips: List[str] = []
    if parts.get("cloud", 0) < 1:
        tips.append("Highlight hands-on cloud platform experience (AWS/Azure/GCP).")
    if parts.get("iac", 0) < 1:
        tips.append("Add Infrastructure-as-Code tools (Terraform/CloudFormation/Ansible).")
    if parts.get("cicd", 0) < 1:
        tips.append("List CI/CD pipelines (Jenkins, GitHub Actions, GitLab CI).")
    if parts.get("monitoring", 0) < 1:
        tips.append("Include observability tools (Prometheus, Grafana, ELK, Datadog).")
    if parts.get("containers", 0) < 1:
        tips.append("Show containerization/Kubernetes project experience.")
    if parts.get("certifications", 0) < 1:
        tips.append("Mention relevant certifications (CKA/CKAD, AWS/Azure, Terraform).")
    if parts.get("years", 0) < 0.5:
        tips.append("Add measurable experience history to strengthen seniority signals.")
    return tips


def scan_folder_for_resumes(folder_path: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not folder_path or not os.path.isdir(folder_path):
        return results

    allowed_ext = {".pdf", ".doc", ".docx", ".txt"}
    for root, _, files in os.walk(folder_path):
        for file_name in files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in allowed_ext:
                continue
            path = os.path.join(root, file_name)
            text = extract_text_from_path(path)
            if not text:
                continue
            profile = parse_resume(text)
            data = resume_profile_to_dict(profile)
            data["_path"] = path
            data["_folder"] = os.path.basename(root)
            results.append(data)
    return results


st.markdown(
    """
    <style>
        .hero {
            background: linear-gradient(135deg, #0b63d6 0%, #6c5ce7 100%);
            padding: 28px 30px;
            border-radius: 20px;
            color: #fff;
            box-shadow: 0 12px 30px rgba(11, 99, 214, 0.25);
            margin-bottom: 18px;
        }
        .hero h1 { font-size: 2rem; margin: 0; }
        .hero p { margin: 6px 0 0; opacity: 0.95; }
        .card {
            background: #ffffff;
            border: 1px solid #eef1f6;
            border-radius: 16px;
            padding: 16px;
            box-shadow: 0 10px 18px rgba(15, 23, 42, 0.06);
        }
        .pill {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.8rem;
        }
        .pill-good { background: #e8fff3; color: #0f766e; }
        .pill-warn { background: #fff7ed; color: #c2410c; }
        .pill-bad { background: #fee2e2; color: #b91c1c; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class='hero'>
        <h1>DevOps Referral Agent</h1>
        <p>Parse CVs, score DevOps fit, and generate structured referral insights.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("### 1 · Source resumes")

mode = st.radio("Resume source", ["Single upload", "Scan local folder"], horizontal=True)

st.sidebar.header("Filters & Sorting")
min_score = st.sidebar.slider("Minimum score", 0, 100, 60)
min_years = st.sidebar.number_input("Min years", min_value=0.0, max_value=50.0, value=0.0, step=0.5)
max_years = st.sidebar.number_input("Max years", min_value=0.0, max_value=50.0, value=25.0, step=0.5)
must_have = st.sidebar.text_input("Must-have keyword")
only_working = st.sidebar.checkbox("Currently working only")
require_containers = st.sidebar.checkbox("Require containers/K8s")
sort_by = st.sidebar.selectbox("Sort by", ["Score", "Years", "Name"], index=0)

parsed: Dict[str, Any] | None = None
raw_text = ""
folder_results: List[Dict[str, Any]] = []

if mode == "Single upload":
    uploaded = st.file_uploader("Upload resume (PDF/DOCX)", type=["pdf", "doc", "docx", "txt"])
    if uploaded:
        st.session_state["devops_file_bytes"] = uploaded.getvalue()
        st.session_state["devops_file_name"] = uploaded.name
        if st.button("Parse resume", type="primary"):
            raw_text = extract_text_from_file(uploaded)
            profile = parse_resume(raw_text)
            parsed = resume_profile_to_dict(profile)
            st.session_state["devops_parsed"] = parsed
            st.session_state["devops_raw"] = raw_text
    elif "devops_parsed" in st.session_state:
        parsed = st.session_state.get("devops_parsed")
        raw_text = st.session_state.get("devops_raw", "")
else:
    folder_path = st.text_input("Folder path", value=st.session_state.get("devops_folder_path", ""))
    if folder_path:
        st.session_state["devops_folder_path"] = folder_path
    if st.button("Scan all CVs in folder 📂", type="primary"):
        with st.spinner("Scanning resumes..."):
            folder_results = scan_folder_for_resumes(folder_path)
        st.session_state["devops_folder_results"] = folder_results
    elif "devops_folder_results" in st.session_state:
        folder_results = st.session_state.get("devops_folder_results", [])

st.subheader("2 · Profiles")
overview_tab, deep_tab = st.tabs(["Overview", "Deep Dive"])

with overview_tab:
    if mode == "Scan local folder":
        if not folder_results:
            st.info("Click **Scan all CVs in folder 📂** above to load all PDFs/DOCXs.")
        else:
            st.markdown("### Detected CVs")
            records: List[Dict[str, Any]] = []
            filtered_pool: List[Dict[str, Any]] = []

            for rec in folder_results:
                scoring = compute_devops_score(rec)
                score_total = float(scoring["total"])
                years_val = rec.get("years_experience", "")
                try:
                    years_num = float(years_val) if str(years_val).strip() else 0.0
                except Exception:
                    years_num = 0.0

                skills_lower = [s.lower() for s in (rec.get("skills") or [])]
                has_containers = any(k in skills_lower for k in CONTAINER_KEYWORDS)

                if score_total < min_score:
                    continue
                if years_num < float(min_years) or years_num > float(max_years):
                    continue
                if must_have.strip() and must_have.lower() not in " ".join(skills_lower):
                    continue
                if only_working and not rec.get("currently_working"):
                    continue
                if require_containers and not has_containers:
                    continue

                filtered_pool.append(rec)
                records.append(
                    {
                        "Name": rec.get("name") or rec.get("summary") or "(unknown)",
                        "Email": rec.get("email", ""),
                        "Phone": rec.get("phone", ""),
                        "Cloud": ", ".join(rec.get("cloud_platforms") or []),
                        "CI/CD": ", ".join(rec.get("ci_cd") or []),
                        "Monitoring": ", ".join(rec.get("monitoring") or []),
                        "IaC": ", ".join(rec.get("infra_as_code") or []),
                        "Containers": "Yes" if has_containers else "No",
                        "Certifications": ", ".join(rec.get("certifications") or []),
                        "University": rec.get("university", ""),
                        "Degree": rec.get("degree", ""),
                        "Graduation Year": rec.get("graduation_year", ""),
                        "Years": years_num,
                        "Currently Working": "Yes" if rec.get("currently_working") else "No",
                        "Score": score_total,
                        "Folder": rec.get("_folder", ""),
                        "File": os.path.basename(rec.get("_path", "")),
                        "Path": rec.get("_path", ""),
                    }
                )

            df = pd.DataFrame(records)
            if not df.empty:
                if sort_by == "Score":
                    df = df.sort_values("Score", ascending=False)
                elif sort_by == "Years":
                    df = df.sort_values("Years", ascending=False)
                else:
                    df = df.sort_values("Name", ascending=True)

                st.markdown("<div class='card'>", unsafe_allow_html=True)
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.markdown("</div>", unsafe_allow_html=True)

                csv_bytes = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Download filtered CSV",
                    data=csv_bytes,
                    file_name="devops_referrals.csv",
                    mime="text/csv",
                )
            else:
                st.warning("No candidates match the current filters.")
                st.stop()

            st.markdown("#### 📊 Talent pool stats")
            try:
                avg_score = float(df["Score"].mean()) if not df.empty else 0.0
                avg_years = float(df["Years"].mean()) if not df.empty else 0.0
                total_candidates = len(df)
                cloud_counts = pd.Series(
                    sum([r.get("cloud_platforms") or [] for r in filtered_pool], [])
                ).value_counts()
                iac_counts = pd.Series(
                    sum([r.get("infra_as_code") or [] for r in filtered_pool], [])
                ).value_counts()
                cicd_counts = pd.Series(
                    sum([r.get("ci_cd") or [] for r in filtered_pool], [])
                ).value_counts()
                mon_counts = pd.Series(
                    sum([r.get("monitoring") or [] for r in filtered_pool], [])
                ).value_counts()
            except Exception:
                avg_score, avg_years, total_candidates = 0.0, 0.0, len(df)
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
            scoring = compute_devops_score(parsed)
            score_total = float(scoring["total"])
            years_val = parsed.get("years_experience", "")
            try:
                years_num = float(years_val) if str(years_val).strip() else 0.0
            except Exception:
                years_num = 0.0
            skills_lower = [s.lower() for s in (parsed.get("skills") or [])]
            has_containers = any(k in skills_lower for k in CONTAINER_KEYWORDS)

            row = {
                "Name": parsed.get("name") or parsed.get("summary") or "(unknown)",
                "Email": parsed.get("email", ""),
                "Phone": parsed.get("phone", ""),
                "Cloud": ", ".join(parsed.get("cloud_platforms") or []),
                "CI/CD": ", ".join(parsed.get("ci_cd") or []),
                "Monitoring": ", ".join(parsed.get("monitoring") or []),
                "IaC": ", ".join(parsed.get("infra_as_code") or []),
                "Containers": "Yes" if has_containers else "No",
                "Certifications": ", ".join(parsed.get("certifications") or []),
                "Degree": parsed.get("degree", ""),
                "Graduation Year": parsed.get("graduation_year", ""),
                "Years": years_num,
                "Currently Working": "Yes" if parsed.get("currently_working") else "No",
                "Score": score_total,
            }

            st.markdown("<div class='card'>", unsafe_allow_html=True)
            st.dataframe(pd.DataFrame([row]), use_container_width=True, hide_index=True)
            st.markdown("</div>", unsafe_allow_html=True)

            parts = scoring.get("parts", {})
            if parts:
                st.markdown("#### Score breakdown")
                part_df = pd.DataFrame(
                    [{"Area": k.upper(), "Score": round(v * 100, 1)} for k, v in parts.items()]
                )
                st.bar_chart(part_df.set_index("Area"))

            tips = build_recommendations(parsed, parts)
            if tips:
                st.markdown("#### Improvement signals")
                for tip in tips:
                    st.markdown(f"- {tip}")

            summary_lines = [
                f"Score: {score_total:.1f}/100",
                f"Cloud: {', '.join(parsed.get('cloud_platforms') or []) or '—'}",
                f"CI/CD: {', '.join(parsed.get('ci_cd') or []) or '—'}",
                f"Monitoring: {', '.join(parsed.get('monitoring') or []) or '—'}",
                f"IaC: {', '.join(parsed.get('infra_as_code') or []) or '—'}",
                f"Containers: {'Yes' if has_containers else 'No'}",
            ]
            st.text_area("Referral summary (copy/paste)", value="\n".join(summary_lines), height=140)
            st.info("Open the Deep Dive tab to see full details and the CV preview.")
        else:
            st.info("Upload a resume above, then parse to see it here.")

with deep_tab:
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
        scoring = compute_devops_score(rec)
        score = float(scoring.get("total", 0.0))

        badge_class = "pill-bad"
        if score >= 80:
            badge_class = "pill-good"
        elif score >= 60:
            badge_class = "pill-warn"

        st.markdown(
            f"""
            <div style='background:linear-gradient(135deg,#0b63d6,#6c5ce7);border-radius:18px;padding:18px;color:#fff;box-shadow:0 10px 30px rgba(11,99,214,0.3);margin-bottom:10px;'>
                <div style='font-size:1.35rem;font-weight:800;'>{title}</div>
                <div style='font-size:0.9rem;opacity:0.95;'>{email} — {phone}</div>
                <div style='margin-top:10px;'>
                    <span class='pill {badge_class}'>Score {score:.1f}/100</span>
                </div>
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
                    st.caption("DOC/DOCX preview as extracted text; formatting may differ from the original.")
                    try:
                        import docx2txt as _docx2txt

                        preview_text = _docx2txt.process(cv_path)
                    except Exception as e:  # noqa: BLE001
                        preview_text = f"(Could not extract DOC/DOCX preview: {e})"
                    st.text_area("Document preview", value=preview_text, height=700)
            else:
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
                        st.caption("DOC/DOCX preview as text (formatting may differ from the original document).")
                        try:
                            import docx2txt as _docx2txt

                            buf = io.BytesIO(file_bytes)
                            preview_text = _docx2txt.process(buf)
                        except Exception:  # noqa: BLE001
                            preview_text = raw_text or ""
                        st.text_area("Document preview", value=preview_text, height=700)

        with info_col:
            st.markdown("### Fit Summary")
            parts = scoring.get("parts", {})
            if parts:
                part_df = pd.DataFrame(
                    [{"Area": k.upper(), "Score": round(v * 100, 1)} for k, v in parts.items()]
                )
                st.bar_chart(part_df.set_index("Area"))

            tips = build_recommendations(rec, parts)
            if tips:
                st.markdown("#### Recommended focus areas")
                for tip in tips:
                    st.markdown(f"- {tip}")

            st.divider()

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

            base_df["Average"] = base_df[[col_a, col_b, col_c]].mean(axis=1).round(2)

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

            if st.button("Save interview scores", key=f"save_{scores_state_key}", type="primary"):
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
