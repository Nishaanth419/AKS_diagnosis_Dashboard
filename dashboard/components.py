import streamlit as st
import json


def search_filters(namespaces, pods):
    """Sidebar search UI"""
    st.sidebar.header("🔍 Filters")

    query = st.sidebar.text_input("Query", placeholder="ImagePullBackOff, CrashLoopBackOff")

    namespace = st.sidebar.selectbox("Namespace", ["Any"] + namespaces)

    pod = st.sidebar.selectbox("Pod", ["Any"] + pods) if namespace != "Any" else "Any"

    k = st.sidebar.slider("Top Results", 1, 20, 5)

    run = st.sidebar.button("🚀 Run Analysis")

    return query, namespace, pod, k, run



def show_result(data: dict):
    """Renders RAG + LLM diagnosis output"""
    st.subheader("📌 Diagnosis Response")

    diagnosis = data.get("diagnosis", "")

    # Try JSON formatting
    try:
        st.json(json.loads(diagnosis))
    except:
        st.code(diagnosis)

    st.markdown("---")

    st.write(f"🔢 Retrieved: **{data.get('matched_chunks', 'N/A')}** related evidence logs")

    if data.get("pod"):
        st.write(f"🔧 Pod: `{data.get('pod')}`")

    if data.get("namespace"):
        st.write(f"📍 Namespace: `{data.get('namespace')}`")

    return diagnosis


def filters(namespaces, pods):
    namespace = st.selectbox("Namespace Filter:", ["All"] + namespaces)
    pod = st.selectbox("Pod Filter:", ["All"] + pods)
    return namespace, pod
