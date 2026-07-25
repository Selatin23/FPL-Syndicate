import json

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="FPL Syndicate", layout="wide")

st.title("FPL Syndicate")

DATA_FILE = "mock_fpl_data.json"
GW_COLS = ["gw1_pts", "gw2_pts", "gw3_pts"]

FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Тестовые ID реальных команд FPL (замени на ID участников синдиката)
TEST_TEAM_IDS = [50220, 51764, 91928, 158533, 215432, 860834]

# В API нет понятия наших лиг, поэтому распределение задаём сами.
# ID, которого нет в словаре, попадёт в Premier League по умолчанию.
TEAM_TIER_MAP = {
    50220: "A-1",
    51764: "A-2",
    91928: "Premier League",
    158533: "B-1",
    215432: "B-2",
    860834: "Premier League",
}


# ---------- Загрузка mock-данных ----------

@st.cache_data
def load_mock_data(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    for col in GW_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")
    return df


# ---------- Загрузка из FPL API ----------

@st.cache_data(ttl=600)  # кэш 10 минут, чтобы не дёргать API на каждый rerun
def fetch_fpl_data(team_ids: list[int]) -> pd.DataFrame:
    rows = []
    errors = []

    for team_id in team_ids:
        try:
            # 1. Общая информация о команде
            entry_resp = requests.get(
                f"{FPL_BASE_URL}/entry/{team_id}/", timeout=10
            )
            entry_resp.raise_for_status()
            entry = entry_resp.json()

            # 2. История по турам
            history_resp = requests.get(
                f"{FPL_BASE_URL}/entry/{team_id}/history/", timeout=10
            )
            history_resp.raise_for_status()
            history = history_resp.json()

            current = history.get("current", [])

            # Очки за первые 3 тура; если сезон ещё не стартовал — нули
            gw_points = {}
            team_value = None
            for gw in (1, 2, 3):
                gw_entry = next(
                    (g for g in current if g.get("event") == gw), None
                )
                gw_points[f"gw{gw}_pts"] = (
                    gw_entry.get("points", 0) if gw_entry else 0
                )
                # value приходит в десятых долях (1005 -> 100.5),
                # берём из последнего доступного тура
                if gw_entry and gw_entry.get("value") is not None:
                    team_value = gw_entry["value"] / 10

            rows.append(
                {
                    "team_id": team_id,
                    "manager_name": (
                        f"{entry.get('player_first_name', '')} "
                        f"{entry.get('player_last_name', '')}"
                    ).strip(),
                    "team_name": entry.get("name", f"Team {team_id}"),
                    "league_tier": TEAM_TIER_MAP.get(
                        team_id, "Premier League"
                    ),
                    **gw_points,
                    "team_value": team_value,
                }
            )
        except requests.exceptions.RequestException as e:
            errors.append(f"ID {team_id}: {e}")
        except (ValueError, KeyError) as e:
            errors.append(f"ID {team_id}: неожиданный формат ответа ({e})")

    if errors:
        st.warning(
            "Не удалось загрузить часть команд:\n\n- " + "\n- ".join(errors)
        )

    if not rows:
        st.error(
            "Серверы FPL недоступны или ни одна команда не загрузилась. "
            "Переключись на Mock данные в боковой панели."
        )
        return pd.DataFrame(
            columns=[
                "team_id", "manager_name", "team_name",
                "league_tier", *GW_COLS, "team_value",
            ]
        )

    df = pd.DataFrame(rows)
    for col in GW_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")
    return df


# ---------- Выбор источника данных ----------

st.sidebar.header("Источник данных")
data_source = st.sidebar.radio(
    "Откуда брать данные:",
    ("Использовать Mock данные", "Использовать API FPL"),
)

if data_source == "Использовать Mock данные":
    df = load_mock_data(DATA_FILE)
else:
    with st.spinner("Загружаю данные из FPL API..."):
        df = fetch_fpl_data(TEST_TEAM_IDS)

# ---------- Расчёт и вывод таблиц ----------

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
