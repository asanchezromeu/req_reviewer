import datetime as dt
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from req_analysis import Conflict, RequirementReview, parse_requirements, review_requirements, to_dicts


DEFAULT_SERVER_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:1b"
PROMPTS = {
    "Zone controller": "system_prompt_ZC.txt",
    "ADAS camera": "system_prompt_ADAS.txt",
}


def main() -> None:
    st.set_page_config(page_title="Requirement Set Reviewer", layout="wide")
    st.title("Requirement Set Reviewer")

    with st.sidebar:
        domain = st.selectbox("Review context", list(PROMPTS))
        use_llm = st.checkbox("Use Ollama refinement", value=False)
        server_url = st.text_input("Ollama server", DEFAULT_SERVER_URL, disabled=not use_llm)
        model = st.text_input("Model", DEFAULT_MODEL, disabled=not use_llm)
        st.download_button(
            "Download CSV template",
            data=_csv_template(),
            file_name="requirements_template.csv",
            mime="text/csv",
        )

    uploaded_file = st.file_uploader("Upload requirements (.csv or .json)", type=["csv", "json"])
    pasted_json = st.text_area("Or paste JSON", height=130, placeholder='{"requirements": [{"id": "REQ-001", "text": "..."}]}')

    requirements = []
    try:
        if uploaded_file:
            requirements = parse_requirements(uploaded_file.name, uploaded_file.getvalue())
        elif pasted_json.strip():
            requirements = parse_requirements("pasted.json", pasted_json.encode("utf-8"))
    except Exception as exc:
        st.error(str(exc))
        return

    if not requirements:
        st.info("Upload a CSV/JSON file or paste JSON to begin.")
        st.caption("Accepted text columns: requirement, text, description, or statement. Optional trace columns: source, parents.")
        return

    st.subheader("Input")
    st.dataframe(to_dicts(requirements), use_container_width=True, hide_index=True)

    if not st.button("Analyze requirement set", type="primary"):
        return

    started = dt.datetime.now()
    reviews, conflicts = review_requirements(requirements)

    if use_llm:
        with st.spinner("Running one compact Ollama batch review..."):
            llm_reviews, llm_conflicts = _refine_with_ollama(
                requirements=requirements,
                current_reviews=reviews,
                current_conflicts=conflicts,
                prompt_path=PROMPTS[domain],
                server_url=server_url,
                model=model,
            )
        if llm_reviews:
            reviews = _merge_reviews(reviews, llm_reviews)
        if llm_conflicts is not None:
            conflicts = _merge_conflicts(conflicts, llm_conflicts)

    elapsed = dt.datetime.now() - started
    _render_results(reviews, conflicts, elapsed)


def _render_results(reviews: list[RequirementReview], conflicts: list[Conflict], elapsed: dt.timedelta) -> None:
    scores = [review.score for review in reviews]
    weak_count = sum(1 for review in reviews if review.needs_improvement)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Requirements", len(reviews))
    metric_cols[1].metric("Average score", f"{sum(scores) / len(scores):.0f}%" if scores else "n/a")
    metric_cols[2].metric("Below 85%", weak_count)
    metric_cols[3].metric("Conflicts", len(conflicts))

    st.caption(f"Analysis time: {elapsed.total_seconds():.1f} s")

    st.subheader("Requirement Scores")
    review_df = pd.DataFrame(to_dicts(reviews))
    st.dataframe(
        review_df[["id", "score", "needs_improvement", "flags", "improvement", "requirement"]],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "Export scores as CSV",
        data=review_df.to_csv(index=False).encode("utf-8"),
        file_name="requirement_scores.csv",
        mime="text/csv",
    )

    st.subheader("Conflicts")
    if conflicts:
        conflict_df = pd.DataFrame(to_dicts(conflicts))
        st.dataframe(conflict_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Export conflicts as CSV",
            data=conflict_df.to_csv(index=False).encode("utf-8"),
            file_name="requirement_conflicts.csv",
            mime="text/csv",
        )
    else:
        st.success("No conflicts detected by the current checks.")


def _refine_with_ollama(
    requirements,
    current_reviews: list[RequirementReview],
    current_conflicts: list[Conflict],
    prompt_path: str,
    server_url: str,
    model: str,
) -> tuple[list[RequirementReview] | None, list[Conflict] | None]:
    try:
        from ollama import Client
    except ImportError:
        st.warning("Ollama package is not installed. Showing deterministic review only.")
        return None, None

    system_prompt = Path(prompt_path).read_text(encoding="utf-8")
    user_prompt = {
        "task": (
            "Review this requirement set for ASPICE SYS.2 and INCOSE wording quality. "
            "Return compact JSON only. Each requirement must have score 0-100 and an improvement "
            "only when score is below 85. Also identify contradictions, duplicates, threshold "
            "inconsistencies, and completeness/traceability gaps across the set."
        ),
        "output_schema": {
            "reviews": [
                {
                    "id": "REQ-ID",
                    "requirement": "original text",
                    "score": 0,
                    "needs_improvement": True,
                    "improvement": "short proposal, empty when score >= 85",
                    "flags": "short comma-separated reasons",
                }
            ],
            "conflicts": [
                {
                    "requirements": "REQ-1, REQ-2",
                    "type": "Contradictory behavior",
                    "evidence": "short reason",
                    "mitigation": "short solution",
                }
            ],
        },
        "requirements": to_dicts(requirements),
        "deterministic_baseline": {
            "reviews": to_dicts(current_reviews),
            "conflicts": to_dicts(current_conflicts),
        },
    }

    try:
        response = Client(host=server_url).chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt)},
            ],
            options={"temperature": 0.1},
        )
        payload = _extract_json(response["message"]["content"])
        reviews = [
            RequirementReview(
                id=str(item.get("id", "")),
                requirement=str(item.get("requirement", "")),
                score=max(0, min(100, int(item.get("score", 0)))),
                needs_improvement=bool(item.get("needs_improvement", int(item.get("score", 0)) < 85)),
                improvement=str(item.get("improvement", "")),
                flags=str(item.get("flags", "")),
            )
            for item in payload.get("reviews", [])
            if item.get("id")
        ]
        conflicts = [
            Conflict(
                requirements=str(item.get("requirements", "")),
                type=str(item.get("type", "")),
                evidence=str(item.get("evidence", "")),
                mitigation=str(item.get("mitigation", "")),
            )
            for item in payload.get("conflicts", [])
            if item.get("requirements")
        ]
        return reviews or None, conflicts
    except Exception as exc:
        st.warning(f"Ollama refinement failed: {exc}. Showing deterministic review only.")
        return None, None


def _merge_reviews(
    baseline: list[RequirementReview], llm_reviews: list[RequirementReview]
) -> list[RequirementReview]:
    baseline_ids = {review.id for review in baseline}
    llm_by_id = {review.id: review for review in llm_reviews}
    if set(llm_by_id) != baseline_ids:
        missing = ", ".join(sorted(baseline_ids - set(llm_by_id)))
        extra = ", ".join(sorted(set(llm_by_id) - baseline_ids))
        parts = (
            f"missing {missing}" if missing else "",
            f"unexpected {extra}" if extra else "",
        )
        details = "; ".join(part for part in parts if part)
        st.warning(
            f"Ollama review was incomplete ({details}). Keeping deterministic scores for all requirements."
        )
        return baseline
    return [llm_by_id[review.id] for review in baseline]


def _merge_conflicts(baseline: list[Conflict], llm_conflicts: list[Conflict]) -> list[Conflict]:
    merged: dict[tuple[str, str, str], Conflict] = {}
    for conflict in baseline + llm_conflicts:
        key = (
            ", ".join(sorted(part.strip() for part in conflict.requirements.split(",") if part.strip())),
            conflict.type.strip().lower(),
            conflict.evidence.strip().lower(),
        )
        merged[key] = conflict
    return list(merged.values())


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return JSON.")
    return json.loads(text[start : end + 1])


def _csv_template() -> str:
    return (
        "id,domain,component,requirement_type,asil,source,parents,text\n"
        'REQ-001,Zone Controller,Power Input,System Requirement,B,SN-001,SN-001,'
        '"The zone controller shall tolerate reverse battery connection of -14 V for 60 s without permanent damage."\n'
    )


if __name__ == "__main__":
    main()
