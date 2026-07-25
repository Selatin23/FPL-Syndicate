import json

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="FPL Syndicate", layout="wide")

st.title("FPL Syndicate")

DATA_FILE = "mock_fpl_data.json"
GW_COLS = ["gw1_pts", "gw2_pts", "gw3_pts"]

FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Все лиги турнира в иерархическом порядке — выводятся по мере наполнения
ALL_LEAGUES = ["Premier League", "A-1", "A-2", "B-1", "B-2", "B-3", "C", "D"]

# Ссылка на опубликованный CSV админ-панели (File -> Share -> Publish to web -> CSV)
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTEST_PLACEHOLDER_ID/pub?gid=0&single=true&output=csv"
)

ADMIN_REQUIRED_COLS = [
    "Phone_Number",
    "FPL_Team_ID",
    "Manager_Name",
    "League_Tier",
    "Payment_Status",
]


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


# ---------- Чтение админ-панели из Google Sheets ----------

@st.cache_data(ttl=60)
def load_admin_sheet(url: str):
    """Читает опубликованный CSV и возвращает (team_ids, team_tier_map).

    team_ids: список FPL_Team_ID (int, без NaN)
    team_tier_map: {FPL_Team_ID: League_Tier}
    """
    admin_df = pd.read_csv(url)

    missing = [c for c in ADMIN_REQUIRED_COLS if c not in admin_df.columns]
    if missing:
        raise ValueError(
            f"В админ-таблице отсутствуют колонки: {', '.join(missing)}"
        )

    # Чистим FPL_Team_ID: убираем NaN, строго приводим к int
    admin_df["FPL_Team_ID"] = pd.to_numeric(
        admin_df["FPL_Team_ID"], errors="coerce"
    )
    admin_df = admin_df.dropna(subset=["FPL_Team_ID"]).copy()
    admin_df["FPL_Team_ID"] = admin_df["FPL_Team_ID"].astype(int)

    team_ids = admin_df["FPL_Team_ID"].tolist()
    team_tier_map = dict(
        zip(admin_df["FPL_Team_ID"], admin_df["League_Tier"].astype(str).str.strip())
    )

    return team_ids, team_tier_map


# ---------- Загрузка из FPL API ----------

@st.cache_data(ttl=600)  # кэш 10 минут, чтобы не дёргать API на каждый rerun
def fetch_fpl_data(team_ids: list[int], team_tier_map: dict) -> pd.DataFrame:
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
                    "league_tier": team_tier_map.get(
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

df = None

if data_source == "Использовать Mock данные":
    df = load_mock_data(DATA_FILE)
else:
    # Сначала читаем админ-панель, затем идём в FPL API
    try:
        team_ids, team_tier_map = load_admin_sheet(CSV_URL)
    except Exception as e:
        st.error(
            "Не удалось загрузить админ-таблицу Google Sheets. "
            f"Проверь CSV_URL и доступ к публикации.\n\nДетали: {e}"
        )
        st.stop()

    if not team_ids:
        st.error(
            "Админ-таблица загрузилась, но не содержит ни одного "
            "валидного FPL_Team_ID. Заполни колонку FPL_Team_ID."
        )
        st.stop()

    with st.spinner("Загружаю данные из FPL API..."):
        df = fetch_fpl_data(team_ids, team_tier_map)

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


for league_name in ALL_LEAGUES:
    league_df = league_table(df, league_name)
    if not league_df.empty:
        st.header(league_name)
        st.dataframe(league_df, use_container_width=True)
