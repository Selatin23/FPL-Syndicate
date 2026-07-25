import json

import pandas as pd
import streamlit as st

st.set_page_config(page_title="FPL Syndicate", layout="wide")

st.title("FPL Syndicate — тестовые таблицы")

DATA_FILE = "mock_fpl_data.json"
GW_COLS = ["gw1_pts", "gw2_pts", "gw3_pts"]


@st.cache_data
def load_data(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    # Страховка от строковых типов при переходе на реальный API
    for col in GW_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")
    return df


df = load_data(DATA_FILE)

# Расчёт общей суммы очков за 3 тура
df["total_pts"] = df[GW_COLS].sum(axis=1)

DISPLAY_COLS = [
    "team_name",
    "manager_name",
    "gw1_pts",
    "gw2_pts",
    "gw3_pts",
    "total_pts",
    "team_value",
]


def league_table(data: pd.DataFrame, tier: str) -> pd.DataFrame:
    table = (
        data[data["league_tier"] == tier]
        .sort_values("total_pts", ascending=False)
        .reset_index(drop=True)
    )
    table.index = table.index + 1  # место в таблице с 1
    return table[DISPLAY_COLS]


st.header("Premier League")
st.dataframe(league_table(df, "Premier League"), use_container_width=True)

st.header("A-1")
st.dataframe(league_table(df, "A-1"), use_container_width=True)
