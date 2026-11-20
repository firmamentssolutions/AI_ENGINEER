from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from database.db import init_db, list_files, load_events, load_records, log_event
from services.analytics import (
    add_delay_band, customer_summary, portfolio_kpis, prepare_dataframe,
    select_snapshot, trend_by_date, normalize_text,
)
from services.credit_search import search_records
from services.export_service import excel_workbook_bytes, pdf_report_bytes
from services.import_service import process_directory
from services.pdf_discovery import discover_pdfs
from services.openai_analysis import analyze_aggregates
from services.ui_config import load_ui_settings, save_ui_settings

load_dotenv()
init_db()

APP_TITLE = "Pilotage des impayés de crédit"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_DIRECTORY = os.getenv("PDF_DIRECTORY", "") or load_ui_settings().get("pdf_directory", "")

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide", initial_sidebar_state="expanded")

CUSTOM_CSS = """
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 2.5rem; max-width: 1500px;}
[data-testid="stMetric"] {background: #f4f7fa; padding: 13px 16px; border-radius: 12px; border: 1px solid #e2e8f0;}
[data-testid="stMetricLabel"] {color: #3d566b;}
.small-note {color:#657687; font-size:0.88rem;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def fmt_fcfa(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Non renseigné"
    return f"{int(round(float(value))):,}".replace(",", " ") + " FCFA"


def fmt_int(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Non renseigné"
    return f"{int(round(float(value))):,}".replace(",", " ")


def fmt_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Non renseignée"
    try:
        return pd.to_datetime(value).strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def date_options(df: pd.DataFrame) -> list[str]:
    if df.empty or "date_situation" not in df.columns:
        return []
    dates = df["date_situation"].dropna().drop_duplicates().sort_values(ascending=False)
    return [d.strftime("%Y-%m-%d") for d in dates]


def pick_snapshot(df: pd.DataFrame, key: str = "dashboard_date", deduplicate: bool = True) -> tuple[pd.DataFrame, str]:
    available = date_options(df)
    if not available:
        return df.copy(), "Date de situation non détectée"
    latest = available[0]
    default_index = 0
    options = ["Dernière date disponible"] + available
    picked = st.selectbox("Date de situation", options, index=default_index, key=key)
    snap = select_snapshot(df, latest if picked == "Dernière date disponible" else picked, deduplicate=deduplicate)
    shown = latest if picked == "Dernière date disponible" else picked
    return snap, fmt_date(shown)


def metric_cards(kpis: dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clients distincts", fmt_int(kpis.get("clients", 0)))
    c2.metric("Dossiers", fmt_int(kpis.get("dossiers", 0)))
    c3.metric(impayes_display_label().replace(" (FCFA)", ""), fmt_fcfa(kpis.get("impayes", 0)))
    c4.metric("Intérêts impayés", fmt_fcfa(kpis.get("interets", 0)))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Encours des dossiers analysés", fmt_fcfa(kpis.get("encours", 0)))
    c6.metric("Montant de crédit accordé", fmt_fcfa(kpis.get("montant_credit", 0)))
    c7.metric("Dossiers avec retard", fmt_int(kpis.get("retard_dossiers", 0)))
    c8.metric("Retard maximal", f"{fmt_int(kpis.get('jours_max'))} jours" if kpis.get("jours_max") is not None else "Non renseigné")


def impayes_display_label() -> str:
    setting = st.session_state.get("impayes_interpretation", "Libellé source : Impayés (non confirmé comme capital)")
    return "Capital impayé (FCFA)" if setting.startswith("Capital impayé") else "Impayés (libellé source, FCFA)"


def display_records(df: pd.DataFrame, include_source: bool = True) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = df.copy()
    date_map = {
        "client": "Client", "dossier": "N° dossier", "numero_manuel": "Numéro manuel",
        "date_situation": "Situation au", "montant_credit": "Montant crédit (FCFA)",
        "encours": "Encours (FCFA)", "impayes": impayes_display_label(),
        "interets": "Intérêts (FCFA)", "retard_jours": "Retard (jours)",
        "bareme_retard": "Barème de retard", "provision": "Provision (source)",
        "date_effet": "Date effet", "date_fin": "Date fin", "file_name": "Fichier source", "page": "Page PDF",
        "score_match": "Correspondance (%)", "beneficiaires": "Bénéf. (source)",
    }
    keep = [c for c in date_map if c in out.columns]
    out = out[keep].rename(columns={c: date_map[c] for c in keep})
    for col in ["Situation au"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%d/%m/%Y").fillna("Non renseignée")
    return out


def unique_name_options(df: pd.DataFrame) -> list[str]:
    if df.empty or "client" not in df.columns:
        return []
    names = df[["client"]].dropna().copy()
    names["normalized"] = names["client"].map(normalize_text)
    names = names[names["normalized"] != ""].drop_duplicates("normalized")
    return sorted(names["client"].astype(str).tolist(), key=str.casefold)


def login_gate() -> bool:
    expected = os.getenv("APP_PASSWORD", "").strip()
    if not expected:
        st.sidebar.warning("APP_PASSWORD non configuré : l'application doit rester accessible uniquement sur un poste ou réseau de confiance.")
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Accès à l'application")
    password = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter", type="primary"):
        if password == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    return False


st.sidebar.title("📊 Pilotage crédit")
st.sidebar.caption("Extraction et calculs locaux · assistance IA facultative")
if not login_gate():
    st.stop()

pages = ["Tableau de bord", "Importation des PDF", "Recherche client", "Analyse du portefeuille", "Qualité et historique", "Paramètres & déploiement"]
page = st.sidebar.radio("Navigation", pages, index=0)
st.sidebar.divider()
st.sidebar.caption("Les données financières restent locales par défaut. Les chiffres sont calculés par Python, pas générés par l'IA.")

# Load currently imported records. The application stores snapshots and the selected views deduplicate by date/dossier.
raw_records = load_records(status_filter="Importé")
df_all = prepare_dataframe(raw_records)
files_meta = list_files()

if page == "Tableau de bord":
    st.title("Tableau de bord exécutif")
    st.caption("Vue du portefeuille issue des rapports PDF importés, avec dédoublonnage par dossier à une date donnée.")
    if df_all.empty:
        st.info("Aucune donnée importée. Ouvrez **Importation des PDF**, indiquez le répertoire contenant les rapports puis lancez l'analyse.")
        st.stop()
    snapshot, date_label = pick_snapshot(df_all)
    st.caption(f"Périmètre courant : situation au {date_label}. Les libellés comptables reprennent ceux du PDF.")
    snapshot_conflicts = snapshot.attrs.get("duplicate_conflicts", [])
    if snapshot_conflicts:
        st.warning(f"{len(snapshot_conflicts)} dossier(s) apparaissant dans plusieurs sources avec des valeurs différentes ont été exclus des agrégats pour éviter un double compte non vérifié. Consultez Qualité et historique.")
        st.dataframe(pd.DataFrame(snapshot_conflicts), use_container_width=True, hide_index=True)
    kpis = portfolio_kpis(snapshot)
    metric_cards(kpis)
    if st.session_state.get("impayes_interpretation", "").startswith("Capital impayé"):
        st.caption("Le libellé « Capital impayé » a été sélectionné après validation métier par votre institution.")
    else:
        st.caption("« Impayés » est affiché selon le libellé du rapport. Confirmez avec le service financier s'il doit être interprété comme capital impayé.")

    left, right = st.columns(2)
    with left:
        delay_df = add_delay_band(snapshot)
        if not delay_df.empty:
            delays = delay_df.groupby("tranche_retard", as_index=False).agg(Dossiers=("dossier", "nunique"), Impayés=("impayes", "sum"))
            order = ["0–30 jours", "31–90 jours", "91–180 jours", "> 180 jours", "Non renseigné"]
            delays["_order"] = delays["tranche_retard"].map({v: i for i, v in enumerate(order)}).fillna(99)
            delays = delays.sort_values("_order")
            st.plotly_chart(px.bar(delays, x="tranche_retard", y="Dossiers", title="Nombre de dossiers par tranche de retard", labels={"tranche_retard":"Tranche de retard"}), use_container_width=True)
    with right:
        top = snapshot.groupby("client", dropna=False, as_index=False).agg(impayes=("impayes", "sum"), interets=("interets", "sum"), encours=("encours", "sum"), dossiers=("dossier", "nunique"))
        top["client"] = top["client"].fillna("Client non renseigné")
        top = top.sort_values("impayes", ascending=False).head(10)
        st.plotly_chart(px.bar(top.sort_values("impayes"), x="impayes", y="client", orientation="h", title="Top 10 clients par impayés", labels={"impayes":"Impayés (FCFA)", "client":"Client"}), use_container_width=True)
    left2, right2 = st.columns(2)
    with left2:
        delay_amt = add_delay_band(snapshot).groupby("tranche_retard", as_index=False).agg(Impayés=("impayes", "sum"), Intérêts=("interets", "sum"))
        st.plotly_chart(px.bar(delay_amt, x="tranche_retard", y=["Impayés", "Intérêts"], barmode="group", title="Montants par tranche de retard", labels={"tranche_retard":"Tranche de retard", "value":"Montant (FCFA)"}), use_container_width=True)
    with right2:
        st.plotly_chart(px.scatter(snapshot, x="encours", y="impayes", size="interets", hover_name="client", hover_data=["dossier", "retard_jours"], title="Encours et impayés par dossier", labels={"encours":"Encours (FCFA)", "impayes":"Impayés (FCFA)"}), use_container_width=True)

    st.subheader("Dossiers à examiner en priorité")
    priority = snapshot.copy()
    priority["score_indicatif"] = priority["impayes"].fillna(0) * (1 + priority["retard_jours"].fillna(0).clip(lower=0) / 365)
    priority = priority.sort_values("score_indicatif", ascending=False).head(20)
    st.dataframe(display_records(priority), use_container_width=True, hide_index=True)
    st.caption("Le score est un indicateur de tri explicable (impayés × facteur de retard), pas une notation réglementaire ni une décision automatique.")

    st.subheader("Exporter le tableau de bord")
    col1, col2 = st.columns(2)
    with col1:
        workbook = excel_workbook_bytes({"Indicateurs": pd.DataFrame([{"Indicateur": k, "Valeur": v} for k, v in kpis.items()]), "Dossiers": display_records(snapshot)})
        st.download_button("Télécharger Excel", workbook, file_name="tableau_bord_credit.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with col2:
        pdf_bytes = pdf_report_bytes("Tableau de bord du portefeuille de crédit", kpis, top.rename(columns={"client":"client", "impayes":"impayes", "interets":"interets", "encours":"encours"}), filters=f"Date de situation : {date_label}")
        st.download_button("Télécharger rapport PDF", pdf_bytes, file_name="rapport_portefeuille_credit.pdf", mime="application/pdf")

elif page == "Importation des PDF":
    st.title("Importation des rapports PDF")
    st.write("Indiquez le chemin du répertoire **accessible depuis l'ordinateur qui exécute Streamlit**.")
    default_dir = st.session_state.get("pdf_directory", DEFAULT_DIRECTORY)
    directory = st.text_input("Chemin du répertoire", value=default_dir, placeholder=r"D:\Rapports_Credits\Impayes", help="En mode local, ce chemin est celui de votre ordinateur. Sur un serveur, il désigne un répertoire du serveur.")
    include_subdirs = st.checkbox("Inclure les sous-répertoires", value=bool(load_ui_settings().get("include_subdirs", False)))
    force = st.checkbox("Forcer la réanalyse des fichiers déjà à jour", value=False)
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Vérifier le répertoire", type="secondary", use_container_width=True):
            try:
                found = discover_pdfs(directory, include_subdirs=include_subdirs)
                st.session_state["pdf_directory"] = directory
                save_ui_settings(pdf_directory=directory, include_subdirs=include_subdirs)
                st.success(f"Répertoire accessible. {len(found)} fichier(s) PDF trouvé(s).")
                if found:
                    st.dataframe(pd.DataFrame([{"Fichier": p.name, "Chemin": str(p), "Taille (Ko)": round(p.stat().st_size / 1024, 1)} for p in found]), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(str(exc))
    with c2:
        start = st.button("Analyser / actualiser les PDF", type="primary", use_container_width=True)
    if start:
        try:
            found = discover_pdfs(directory, include_subdirs=include_subdirs)
            if not found:
                st.warning("Aucun fichier PDF n'a été trouvé dans ce répertoire.")
            else:
                bar = st.progress(0)
                status_text = st.empty()
                def callback(done: int, total: int, name: str) -> None:
                    bar.progress(min(1.0, done / max(total, 1)))
                    status_text.text(f"Traitement {done}/{total} : {name}")
                with st.spinner("Extraction locale des tableaux PDF…"):
                    result = process_directory(directory, include_subdirs, callback, force=force)
                bar.progress(1.0)
                status_text.text("Traitement terminé.")
                st.session_state["pdf_directory"] = directory
                save_ui_settings(pdf_directory=directory, include_subdirs=include_subdirs)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Fichiers trouvés", result["found"])
                m2.metric("Importés", result["imported"])
                m3.metric("Déjà à jour", result["unchanged"])
                m4.metric("Erreurs / vérifications", result["errors"])
                st.dataframe(pd.DataFrame(result["results"]), use_container_width=True, hide_index=True)
                st.info("Les fichiers dont le contenu est identique à un document déjà importé sont signalés comme doublons de contenu et leurs lignes ne sont pas réimportées.")
                st.rerun()
        except Exception as exc:
            st.error(str(exc))
    st.divider()
    st.subheader("Fichiers déjà connus")
    if files_meta:
        shown = []
        for f in files_meta:
            shown.append({"Fichier": f["file_name"], "Statut": f["status"], "Situation au": fmt_date(f.get("report_date")), "Dossiers": f["record_count"], "Importé le": f["imported_at"], "Erreur / détail": f.get("error_message") or ""})
        st.dataframe(pd.DataFrame(shown), use_container_width=True, hide_index=True)
    else:
        st.caption("Aucun fichier importé pour le moment.")

elif page == "Recherche client":
    st.title("Recherche client et dossiers de crédit")
    st.caption("La recherche est locale et ne transmet aucun nom ou montant à OpenAI.")
    if df_all.empty:
        st.info("Importez d'abord des fichiers PDF depuis le menu Importation des PDF.")
        st.stop()
    base, date_label = pick_snapshot(df_all, key="search_date", deduplicate=False)
    st.caption(f"Recherche sur la situation au {date_label}. Pour éviter les doubles comptes, choisissez une date de situation à la fois.")
    with st.form("customer_search_form"):
        query = st.text_input("Nom du client, partie du nom ou numéro de dossier", placeholder="Ex. : AMEGADZE, Kossi, F02PRT202500180")
        fuzzy = st.checkbox("Autoriser les correspondances approximatives", value=True, help="Les résultats approximatifs doivent être vérifiés dans le PDF source.")
        submitted = st.form_submit_button("Rechercher", type="primary", use_container_width=True)
    if submitted:
        if len(query.strip()) < 2:
            st.warning("Saisissez au moins deux caractères.")
            st.session_state.pop("search_matches", None)
        else:
            matches = search_records(query, base.to_dict("records"), fuzzy=fuzzy)
            st.session_state["search_matches"] = matches
            st.session_state["search_query"] = query
            st.session_state["search_date_label"] = date_label
            log_event("recherche_client", f"Recherche locale effectuée; longueur de requête={len(query.strip())}; résultats={len(matches)}")
    matches = st.session_state.get("search_matches", [])
    if matches:
        match_df = pd.DataFrame(matches)
        candidates = unique_name_options(match_df)
        if len(candidates) > 1:
            st.warning(f"{len(candidates)} noms distincts correspondent à la recherche. Choisissez le client exact : ne pas confondre des homonymes.")
            chosen_name = st.selectbox("Client à consulter", candidates, key="selected_customer_name")
            selected_df = match_df[match_df["client"].astype(str) == chosen_name].copy()
        else:
            selected_df = match_df.copy()
        scores = selected_df["score_match"].unique().tolist() if "score_match" in selected_df.columns else [100]
        if any(int(x) < 100 for x in scores):
            st.warning("Au moins une correspondance est approximative. Vérifiez le nom, le numéro de dossier et la page PDF avant d'utiliser les données.")
        else:
            st.success(f"{len(selected_df)} dossier(s) correspondant(s) au nom choisi.")
        st.subheader("Synthèse du client sélectionné")
        # Le détail montre les sources originales, mais les totaux sont calculés
        # après déduplication des lignes identiques et exclusion des doublons conflictuels.
        selected_date_iso = selected_df["date_situation"].dropna().iloc[0].strftime("%Y-%m-%d") if "date_situation" in selected_df.columns and selected_df["date_situation"].notna().any() else None
        safe_summary_df = select_snapshot(selected_df, selected_date_iso, deduplicate=True)
        summary_conflicts = safe_summary_df.attrs.get("duplicate_conflicts", [])
        if summary_conflicts:
            st.warning("Certains dossiers correspondent à plusieurs sources avec des valeurs différentes. Ils sont exclus de la synthèse consolidée jusqu'à vérification.")
            st.dataframe(pd.DataFrame(summary_conflicts), use_container_width=True, hide_index=True)
        _, summary = customer_summary(safe_summary_df, str(safe_summary_df.iloc[0].get("client") or "")) if not safe_summary_df.empty else (safe_summary_df, {})
        if summary.get("ambiguous"):
            st.warning("Plusieurs variantes de nom restent présentes. Les lignes sont affichées séparément et aucun total consolidé n'est calculé automatiquement.")
        elif summary:
            a, b, c, d = st.columns(4)
            a.metric("Dossiers", fmt_int(summary.get("nombre_dossiers")))
            b.metric(impayes_display_label().replace(" (FCFA)", ""), fmt_fcfa(summary.get("impayes")))
            c.metric("Intérêts", fmt_fcfa(summary.get("interets")))
            d.metric("Encours", fmt_fcfa(summary.get("encours")))
            st.caption(f"Retard maximal : {fmt_int(summary.get('jours_max'))} jours. Totaux calculés pour la date de situation choisie, sans additionner plusieurs périodes.")
        st.subheader("Détail des dossiers")
        display_df = display_records(selected_df)
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("Télécharger les résultats CSV", display_df.to_csv(index=False).encode("utf-8-sig"), file_name="recherche_client_credit.csv", mime="text/csv")
        with c2:
            xlsx = excel_workbook_bytes({"Dossiers": display_df})
            st.download_button("Télécharger les résultats Excel", xlsx, file_name="recherche_client_credit.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("Les chiffres restent rattachés au fichier source et à la page. Le champ « Impayés » conserve son libellé d'origine tant que sa signification métier n'a pas été confirmée.")
    elif "search_matches" in st.session_state:
        st.warning("Aucune correspondance trouvée. Essayez le nom de famille, une partie plus courte du nom ou le numéro de dossier.")

elif page == "Analyse du portefeuille":
    st.title("Analyse approfondie du portefeuille")
    st.caption("Analyse déterministe locale des dossiers importés. OpenAI est facultatif et activé séparément avec consentement.")
    if df_all.empty:
        st.info("Aucune donnée disponible. Importez des fichiers PDF avant de lancer l'analyse.")
        st.stop()
    snapshot, date_label = pick_snapshot(df_all, key="analysis_date")
    snapshot_conflicts = snapshot.attrs.get("duplicate_conflicts", [])
    if snapshot_conflicts:
        st.warning(f"{len(snapshot_conflicts)} conflit(s) de dossiers/date exclus des calculs : des PDF donnent des montants différents pour le même dossier à la même date.")
        st.dataframe(pd.DataFrame(snapshot_conflicts), use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        min_days = st.number_input("Retard minimum (jours)", min_value=0, max_value=5000, value=90, step=30)
    with c2:
        top_n = st.selectbox("Nombre de clients dans les classements", [10, 15, 20, 50], index=0)
    with c3:
        cutoff = st.number_input("Seuil d'impayés à signaler (FCFA)", min_value=0, value=500000, step=100000)
    kpis = portfolio_kpis(snapshot)
    metric_cards(kpis)
    st.subheader("Dossiers répondant aux critères de vigilance")
    priority = snapshot.copy()
    priority = priority[(priority["retard_jours"].fillna(0) >= min_days) | (priority["impayes"].fillna(0) >= cutoff)]
    priority = priority.sort_values(["retard_jours", "impayes"], ascending=False)
    st.dataframe(display_records(priority.head(500)), use_container_width=True, hide_index=True)
    if not priority.empty:
        st.download_button("Exporter la liste de vigilance en Excel", excel_workbook_bytes({"Vigilance": display_records(priority)}), file_name="dossiers_vigilance.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.subheader("Concentration des impayés")
    by_client = snapshot.groupby("client", dropna=False, as_index=False).agg(impayes=("impayes", "sum"), interets=("interets", "sum"), encours=("encours", "sum"), dossiers=("dossier", "nunique"))
    by_client["client"] = by_client["client"].fillna("Client non renseigné")
    by_client = by_client.sort_values("impayes", ascending=False)
    denominator = by_client["impayes"].fillna(0).sum()
    by_client["Part des impayés (%)"] = (by_client["impayes"].fillna(0) / denominator * 100).round(2) if denominator else 0
    by_client["Part cumulée (%)"] = by_client["Part des impayés (%)"].cumsum().round(2)
    left, right = st.columns(2)
    with left:
        st.plotly_chart(px.bar(by_client.head(top_n).sort_values("impayes"), x="impayes", y="client", orientation="h", title=f"Top {top_n} clients par impayés", labels={"impayes":"Impayés (FCFA)", "client":"Client"}), use_container_width=True)
    with right:
        st.plotly_chart(px.line(by_client.head(top_n), x="client", y="Part cumulée (%)", markers=True, title="Concentration cumulée des impayés"), use_container_width=True)
    st.dataframe(by_client.head(top_n), use_container_width=True, hide_index=True)

    st.subheader("Évolution entre dates de situation")
    trend = trend_by_date(df_all)
    if len(trend) >= 2:
        trend_view = trend.copy()
        trend_view["date_affichee"] = trend_view["date_situation"].dt.strftime("%d/%m/%Y")
        st.plotly_chart(px.line(trend_view, x="date_affichee", y=["impayes", "interets", "encours"], markers=True, title="Évolution des principaux montants", labels={"date_affichee":"Date de situation", "value":"Montant (FCFA)", "variable":"Indicateur"}), use_container_width=True)
        st.dataframe(trend_view.rename(columns={"date_situation":"date_situation_timestamp", "date_affichee":"Date de situation", "dossiers":"Nombre de dossiers", "encours":"Encours (FCFA)", "impayes":"Impayés (FCFA)", "interets":"Intérêts (FCFA)"}), use_container_width=True, hide_index=True)
    else:
        st.info("L'évolution temporelle exige au moins deux dates de situation distinctes. Les totaux provenant de dates différentes ne sont pas additionnés dans les KPI.")

    st.subheader("Questions de gestion avec assistance IA (optionnelle)")
    api_key = st.text_input("Clé API OpenAI (ou configurez OPENAI_API_KEY dans .env)", value=os.getenv("OPENAI_API_KEY", ""), type="password", key="openai_key_analysis")
    model = st.text_input("Modèle OpenAI", value=DEFAULT_MODEL, key="openai_model_analysis")
    question = st.text_area("Question du manager", placeholder="Ex. : Quels constats ressortent de la répartition des impayés et quels points devraient être vérifiés ?")
    share_names = st.checkbox("Autoriser aussi l'envoi des noms de clients du classement à OpenAI", value=False)
    consent = st.checkbox("J'autorise l'envoi des indicateurs agrégés et des données cochées ci-dessus à OpenAI pour cette analyse.", value=False)
    st.warning("Même avec consentement, vérifiez les règles de confidentialité et l'autorisation de votre institution avant toute transmission de données de crédit à un service externe.")
    if st.button("Générer une synthèse IA", type="primary", disabled=not (api_key and question.strip() and consent)):
        top_for_ai = by_client.head(10).copy()
        if not share_names:
            top_for_ai = top_for_ai.drop(columns=["client"], errors="ignore")
        else:
            top_for_ai = top_for_ai.rename(columns={"client":"nom_client"})
        payload_metrics = {**kpis, "date_situation": date_label, "seuil_retard_analyse": int(min_days), "seuil_impayes_analyse": int(cutoff), "part_top_10_impayes_pct": round(float(by_client.head(10)["impayes"].sum() / denominator * 100), 2) if denominator else 0}
        quality_notes = f"{len(files_meta)} fichier(s) répertorié(s). Synthèse calculée sur la date de situation {date_label}. Libellé source Impayés non réinterprété."
        with st.spinner("Demande de synthèse à OpenAI…"):
            try:
                answer = analyze_aggregates(api_key, model, question, payload_metrics, top_for_ai.to_dict("records"), quality_notes)
                st.markdown("#### Synthèse de l'assistant")
                st.write(answer)
                log_event("openai_analysis", "Analyse d'agrégats initiée avec consentement explicite.")
            except Exception as exc:
                st.error(f"L'analyse OpenAI a échoué : {exc}")

elif page == "Qualité et historique":
    st.title("Qualité des données et historique des traitements")
    files_meta = list_files()
    if files_meta:
        file_rows = []
        for f in files_meta:
            file_rows.append({"Fichier": f["file_name"], "Statut": f["status"], "Situation au": fmt_date(f.get("report_date")), "Pages": f.get("page_count"), "Dossiers extraits": f.get("record_count"), "Importé le": f.get("imported_at"), "Chemin": f["path"], "Détail": f.get("error_message") or ""})
        st.subheader("Registre des fichiers")
        st.dataframe(pd.DataFrame(file_rows), use_container_width=True, hide_index=True)
        review_files = [f for f in files_meta if f.get("status") == "À vérifier" and f.get("record_count", 0) > 0]
        if review_files:
            st.subheader("Validation manuelle des fichiers à vérifier")
            review_options = {f"{f['file_name']} — {fmt_date(f.get('report_date'))} — {f['record_count']} dossiers": f for f in review_files}
            selected_review = st.selectbox("Fichier à valider après contrôle dans le PDF source", list(review_options.keys()))
            review_meta = review_options[selected_review]
            st.write(f"**Chemin source :** `{review_meta['path']}`")
            st.write(f"**Motif :** {review_meta.get('error_message') or 'Contrôle qualité requis'}")
            confirmed_review = st.checkbox("J'ai vérifié manuellement les lignes et les totaux de ce PDF source.", value=False, key="manual_file_validation")
            if st.button("Marquer ce fichier comme validé", type="primary", disabled=not confirmed_review):
                from database.db import update_file_status
                update_file_status(int(review_meta["id"]), "Importé", None)
                log_event("validation_manuelle_fichier", f"Fichier validé manuellement : {review_meta['file_name']}")
                st.success("Fichier marqué comme validé. Les dossiers seront inclus dans les tableaux de bord après actualisation.")
                st.rerun()
        st.subheader("Comparaison des totaux calculés et imprimés")
        compare_rows = []
        for f in files_meta:
            for metric, comparison in (f.get("comparisons") or {}).items():
                compare_rows.append({"Fichier": f["file_name"], "Indicateur": metric, "Somme calculée": comparison.get("calcule"), "Total imprimé": comparison.get("imprime"), "Écart": comparison.get("ecart"), "Correspond": comparison.get("correspond")})
        if compare_rows:
            compare_df = pd.DataFrame(compare_rows)
            st.dataframe(compare_df, use_container_width=True, hide_index=True)
            mismatches = compare_df[compare_df["Correspond"] == False]  # noqa: E712
            if not mismatches.empty:
                st.warning(f"{len(mismatches)} contrôle(s) de total présentent un écart. Vérifiez le PDF source.")
            else:
                st.success("Aucun écart parmi les contrôles de totaux reconnus. Les totaux non reconnus restent à vérifier manuellement.")
        else:
            st.info("Aucun total imprimé n'a été identifié dans les fichiers importés.")
    else:
        st.info("Aucun fichier connu.")
    st.subheader("Dossiers répétés à une même date de situation")
    if not df_all.empty and {"date_situation", "dossier"}.issubset(df_all.columns):
        repeated = df_all[df_all.duplicated(subset=["date_situation", "dossier"], keep=False)].copy()
        if repeated.empty:
            st.success("Aucun dossier présent plusieurs fois à la même date dans les données importées.")
        else:
            repeated["situation_affichee"] = repeated["date_situation"].dt.strftime("%d/%m/%Y")
            key_counts = repeated.groupby(["date_situation", "dossier"], dropna=False).size().rename("Nombre de sources").reset_index()
            repeated = repeated.merge(key_counts, on=["date_situation", "dossier"], how="left")
            repeated_out = repeated[[c for c in ["situation_affichee", "dossier", "client", "impayes", "interets", "encours", "file_name", "page", "Nombre de sources"] if c in repeated.columns]]
            st.dataframe(repeated_out.rename(columns={"situation_affichee":"Situation au", "dossier":"Dossier", "client":"Client", "impayes":"Impayés (source)", "interets":"Intérêts", "encours":"Encours", "file_name":"Fichier source", "page":"Page PDF"}), use_container_width=True, hide_index=True)
            st.caption("Les doublons strictement identiques peuvent être dédupliqués pour les KPI. Si les données diffèrent pour un même dossier et la même date, le groupe est exclu des agrégats jusqu'à vérification.")
    st.subheader("Historique des événements de l'application")
    events = load_events()
    if events:
        event_df = pd.DataFrame(events).rename(columns={"event_time":"Date/heure", "event_type":"Type", "details":"Détail"})
        st.dataframe(event_df[["Date/heure", "Type", "Détail"]], use_container_width=True, hide_index=True)
    else:
        st.caption("Aucun événement enregistré.")
    st.caption("L'historique ne journalise pas les clés API ni le contenu intégral des recherches, afin de réduire l'exposition des informations sensibles.")

elif page == "Paramètres & déploiement":
    st.title("Paramètres, sécurité et déploiement")
    st.subheader("Interprétation du champ « Impayés »")
    interpretation_options = ["Libellé source : Impayés (non confirmé comme capital)", "Capital impayé (validation métier effectuée)"]
    saved_interpretation = st.session_state.get("impayes_interpretation", interpretation_options[0])
    interpretation = st.selectbox("Signification métier confirmée par votre institution", interpretation_options, index=interpretation_options.index(saved_interpretation) if saved_interpretation in interpretation_options else 0, key="impayes_interpretation_widget")
    if interpretation.startswith("Capital impayé"):
        st.success("L'interface de recherche peut afficher « Capital impayé » pour ce périmètre si cette définition a été confirmée par votre institution.")
    else:
        st.info("L'application conserve le libellé « Impayés » du PDF. Elle n'affirme pas que ce montant est exclusivement le capital impayé.")
    st.session_state["impayes_interpretation"] = interpretation

    st.subheader("Chemin actuellement configuré")
    st.code(st.session_state.get("pdf_directory", load_ui_settings().get("pdf_directory", DEFAULT_DIRECTORY)) or "Aucun chemin enregistré", language="text")
    st.markdown("""
### Déploiement local sous Windows

1. Installez Python 3.11 ou une version compatible et cochez **Add Python to PATH** pendant l'installation.
2. Décompressez l'archive ZIP dans un dossier local, par exemple `C:\\Applications\\credit_portfolio_app`.
3. Ouvrez PowerShell dans ce dossier.
4. Exécutez `python -m venv .venv`, puis `./.venv/Scripts/Activate.ps1` ou utilisez `INSTALLER.bat` puis `run.bat`.
5. Exécutez `python -m pip install --upgrade pip` puis `pip install -r requirements.txt`.
6. Copiez `.env.example` vers `.env`. Configurez un mot de passe d'application robuste dans `APP_PASSWORD`. La clé OpenAI est facultative.
7. Démarrez avec `streamlit run app.py` ou double-cliquez `run.bat`.
8. Ouvrez `http://localhost:8501` dans le navigateur.
9. Dans **Importation des PDF**, renseignez le chemin du répertoire contenant les rapports puis cliquez **Analyser / actualiser les PDF**.
10. Vérifiez l'onglet **Qualité et historique** et comparez les totaux avant toute décision de gestion.

### Déploiement sur un serveur interne

- Installez l'application sur un ordinateur/serveur de l'institution et donnez-lui accès au répertoire PDF.
- Le chemin configuré est un chemin vu par le serveur, y compris un partage réseau monté avec un compte de service dédié.
- Configurez authentification institutionnelle, pare-feu, HTTPS lorsque nécessaire, sauvegardes, droits fichiers et procédure de mise à jour.
- Ne partagez pas l'application directement sur Internet sans architecture de sécurité adaptée.

### Déploiement distant

- Le serveur distant ne peut pas lire automatiquement le disque local d'un utilisateur. Les PDF doivent être téléversés ou stockés dans un emplacement accessible au serveur.
- Protégez l'accès avec une authentification robuste, TLS, contrôle des rôles, limitation réseau et surveillance.
- Validez les règles de conservation, d'hébergement et de transmission des données financières.

### OpenAI et confidentialité

- Sans `OPENAI_API_KEY`, la recherche, l'extraction et les graphiques restent fonctionnels.
- Gardez la clé dans `.env` ou un gestionnaire de secrets; ne la commitez jamais dans Git.
- L'assistance IA n'envoie des agrégats qu'après consentement explicite dans l'interface. L'envoi de noms est optionnel.
- N'utilisez que des données que votre organisation est autorisée à transmettre.

### Limites actuelles

- Le parseur est calibré sur le format tabulaire PERFECT V6.3 de référence. Un format radicalement différent peut exiger un nouvel adaptateur.
- Les PDF scannés nécessitent une étape OCR qui n'est pas activée automatiquement dans cette version.
- Les totaux imprimés ne sont comparés que lorsqu'ils sont reconnus de manière fiable.
- Streamlit n'est pas, à lui seul, un système complet de gestion des identités pour une application financière exposée à plusieurs utilisateurs.
""")

    st.subheader("Sauvegarde de la base locale")
    from database.db import get_db_path, backup_database_bytes
    db_path = get_db_path()
    st.write(f"Emplacement actuel : `{db_path}`")
    if db_path.exists():
        st.download_button("Télécharger une copie de sauvegarde SQLite", backup_database_bytes(), file_name="credit_portfolio_backup.sqlite3", mime="application/vnd.sqlite3")
    if st.button("Effacer tous les fichiers et dossiers importés", type="secondary"):
        st.session_state["confirm_clear_data"] = True
    if st.session_state.get("confirm_clear_data"):
        st.error("Action irréversible : tous les métadonnées et dossiers importés dans la base locale seront supprimés.")
        yes, no = st.columns(2)
        with yes:
            if st.button("Confirmer la suppression", type="primary"):
                from database.db import clear_data
                clear_data()
                st.session_state["confirm_clear_data"] = False
                log_event("purge_database", "Suppression manuelle des données importées.")
                st.success("Base locale vidée.")
                st.rerun()
        with no:
            if st.button("Annuler"):
                st.session_state["confirm_clear_data"] = False
                st.rerun()
