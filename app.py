import json
import re

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="FPL Syndicate", layout="wide")

st.title("FPL Syndicate")

DATA_FILE = "mock_fpl_data.json"

FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Все лиги турнира в иерархическом порядке — выводятся по мере наполнения
ALL_LEAGUES = ["Premier League", "A-1", "A-2", "B-1", "B-2", "B-3", "C", "D"]

# Ссылка на опубликованный CSV админ-панели (File -> Share -> Publish to web -> CSV)
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vS8jhaEpFQVR8Sk78GKDUuUHjBwZT55ybatubqw7pPT48Vz7pLo_YWyKtek6dCuo4dS1R9V_tlJrFKH/pub?output=csv"
)

ADMIN_REQUIRED_COLS = [
    "Phone_Number",
    "FPL_Team_ID",
    "Manager_Name",
    "League_Tier",
    "Payment_Status",
]

# Параметры еврокубков
CL_QUALIFY_TOP_N = 10      # топ-N из каждой лиги проходит в Лигу Чемпионов
QUALIFICATION_END_GW = 20  # квалификация длится до этого тура включительно
ELIMINATION_GWS = [25, 30, 35]  # контрольные точки отсева
ELIMINATED_PER_ROUND = 20       # сколько команд выбывает на каждой точке
FINAL_START_GW, FINAL_END_GW = 36, 38


# ---------- Утилиты по турам ----------

def gw_col(n: int) -> str:
    return f"gw{n}_pts"


def get_gw_cols(df: pd.DataFrame) -> list[str]:
    """Все колонки вида gw{N}_pts, отсортированные по номеру тура."""
    cols = [c for c in df.columns if re.fullmatch(r"gw\d+_pts", c)]
    return sorted(cols, key=lambda c: int(re.findall(r"\d+", c)[0]))


def detect_current_gw(df: pd.DataFrame) -> int:
    cols = get_gw_cols(df)
    if not cols:
        return 0
    return max(int(re.findall(r"\d+", c)[0]) for c in cols)


def sum_gw_range(df: pd.DataFrame, start: int, end: int) -> pd.Series:
    """Сумма очков за туры [start, end]. Отсутствующие туры считаются нулями."""
    cols = [gw_col(n) for n in range(start, end + 1) if gw_col(n) in df.columns]
    if not cols:
        return pd.Series(0, index=df.index)
    return df[cols].sum(axis=1)


# ---------- Загрузка mock-данных ----------

@st.cache_data
def load_mock_data(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    for col in get_gw_cols(df):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")
    return df


# ---------- Чтение админ-панели из Google Sheets ----------

@st.cache_data(ttl=60)
def load_admin_sheet(url: str):
    """Читает опубликованный CSV и возвращает (team_ids, team_tier_map)."""
    admin_df = pd.read_csv(url)

    missing = [c for c in ADMIN_REQUIRED_COLS if c not in admin_df.columns]
    if missing:
        raise ValueError(
            f"В админ-таблице отсутствуют колонки: {', '.join(missing)}"
        )

    admin_df["FPL_Team_ID"] = pd.to_numeric(
        admin_df["FPL_Team_ID"], errors="coerce"
    )
    admin_df = admin_df.dropna(subset=["FPL_Team_ID"]).copy()
    admin_df["FPL_Team_ID"] = admin_df["FPL_Team_ID"].astype(int)

    team_ids = admin_df["FPL_Team_ID"].tolist()
    team_tier_map = dict(
        zip(
            admin_df["FPL_Team_ID"],
            admin_df["League_Tier"].astype(str).str.strip(),
        )
    )

    return team_ids, team_tier_map


# ---------- Загрузка из FPL API ----------

@st.cache_data(ttl=600)  # кэш 10 минут, чтобы не дёргать API на каждый rerun
def fetch_fpl_data(team_ids: list[int], team_tier_map: dict) -> pd.DataFrame:
    rows = []
    errors = []

    for team_id in team_ids:
        try:
            entry_resp = requests.get(
                f"{FPL_BASE_URL}/entry/{team_id}/", timeout=10
            )
            entry_resp.raise_for_status()
            entry = entry_resp.json()

            history_resp = requests.get(
                f"{FPL_BASE_URL}/entry/{team_id}/history/", timeout=10
            )
            history_resp.raise_for_status()
            history = history_resp.json()

            current = history.get("current", [])

            # Собираем очки за все сыгранные туры (1..38)
            gw_points = {}
            team_value = None
            for gw_entry in current:
                event = gw_entry.get("event")
                if not event:
                    continue
                gw_points[gw_col(event)] = gw_entry.get("points", 0)
                # value приходит в десятых долях (1005 -> 100.5)
                if gw_entry.get("value") is not None:
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
                "league_tier", "team_value",
            ]
        )

    df = pd.DataFrame(rows)
    for col in get_gw_cols(df):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")
    return df


# ---------- Модуль Еврокубков ----------

def _run_cup(cup_df: pd.DataFrame, current_gw: int) -> pd.DataFrame:
    """Прогоняет один еврокубок через фазы отсева и финал."""
    cup = cup_df.copy()
    cup["seg_21_25"] = sum_gw_range(cup, 21, 25)
    cup["seg_26_30"] = sum_gw_range(cup, 26, 30)
    cup["seg_31_35"] = sum_gw_range(cup, 31, 35)
    cup["final_pts"] = sum_gw_range(cup, FINAL_START_GW, FINAL_END_GW)
    cup["status"] = "Участвует"

    active = list(cup.index)

    seg_map = {25: "seg_21_25", 30: "seg_26_30", 35: "seg_31_35"}
    for checkpoint_gw in ELIMINATION_GWS:
        if current_gw < checkpoint_gw:
            break
        if len(active) <= ELIMINATED_PER_ROUND:
            break
        seg_col_name = seg_map[checkpoint_gw]
        segment = cup.loc[active, seg_col_name].sort_values(ascending=True)
        eliminated = list(segment.index[:ELIMINATED_PER_ROUND])
        cup.loc[eliminated, "status"] = f"Выбыл (GW{checkpoint_gw})"
        active = [i for i in active if i not in set(eliminated)]

    # Финал: строго очки GW36–38
    if current_gw >= FINAL_END_GW and active:
        winner_idx = cup.loc[active, "final_pts"].idxmax()
        cup.loc[active, "status"] = "Финалист"
        cup.loc[winner_idx, "status"] = "Победитель 🏆"

    # Сортировка: активные сверху по очкам текущей фазы, выбывшие ниже
    if current_gw >= FINAL_START_GW:
        cup["sort_pts"] = cup["final_pts"]
    else:
        cup["sort_pts"] = (
            cup["seg_21_25"] + cup["seg_26_30"] + cup["seg_31_35"]
        )
    cup["is_out"] = cup["status"].str.startswith("Выбыл").astype(int)
    cup = cup.sort_values(
        ["is_out", "sort_pts"], ascending=[True, False]
    ).drop(columns=["sort_pts", "is_out"])
    return cup


def calculate_european_cups(df: pd.DataFrame, current_gw: int):
    """Возвращает ("qualification", ranking_df) до GW20 включительно,
    либо ("tournament", (cl_df, conf_df)) начиная с GW21."""
    data = df.copy()
    data["qual_pts"] = sum_gw_range(data, 1, QUALIFICATION_END_GW)

    # --- Фаза квалификации: промежуточный срез по текущим очкам ---
    if current_gw <= QUALIFICATION_END_GW:
        data["Направление"] = "Лига Конференций"
        for league in ALL_LEAGUES:
            league_idx = data[data["league_tier"] == league].index
            top_idx = (
                data.loc[league_idx, "qual_pts"]
                .sort_values(ascending=False)
                .index[:CL_QUALIFY_TOP_N]
            )
            data.loc[top_idx, "Направление"] = "Лига Чемпионов"
        ranking = data.sort_values("qual_pts", ascending=False).reset_index(
            drop=True
        )
        ranking.index = ranking.index + 1
        return "qualification", ranking

    # --- После GW20: раздача по турнирам ---
    cl_idx = []
    for league in ALL_LEAGUES:
        league_idx = data[data["league_tier"] == league].index
        top_idx = (
            data.loc[league_idx, "qual_pts"]
            .sort_values(ascending=False)
            .index[:CL_QUALIFY_TOP_N]
        )
        cl_idx.extend(top_idx)

    cl_df = _run_cup(data.loc[cl_idx], current_gw)
    conf_df = _run_cup(data.drop(index=cl_idx), current_gw)
    return "tournament", (cl_df, conf_df)


CUP_DISPLAY_COLS = {
    "team_name": "Команда",
    "manager_name": "Менеджер",
    "league_tier": "Лига",
    "qual_pts": "Отбор (GW1–20)",
    "seg_21_25": "GW21–25",
    "seg_26_30": "GW26–30",
    "seg_31_35": "GW31–35",
    "final_pts": "Финал (GW36–38)",
    "status": "Статус",
}


def render_cup_table(cup_df: pd.DataFrame):
    table = cup_df[list(CUP_DISPLAY_COLS)].rename(columns=CUP_DISPLAY_COLS)
    table = table.reset_index(drop=True)
    table.index = table.index + 1
    st.dataframe(table, use_container_width=True)


# ---------- Выбор источника данных ----------

st.sidebar.header("Источник данных")
data_source = st.sidebar.radio(
    "Откуда брать данные:",
    ("Использовать Mock данные", "Использовать API FPL"),
)

if data_source == "Использовать Mock данные":
    df = load_mock_data(DATA_FILE)
else:
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

if df.empty:
    st.stop()

# Текущий тур: определяем по данным, с возможностью симуляции для тестов
auto_gw = detect_current_gw(df)
current_gw = st.sidebar.number_input(
    "Текущий тур (симуляция для тестов)",
    min_value=0,
    max_value=38,
    value=auto_gw,
)

# ---------- Расчёт общих очков ----------

df["total_pts"] = sum_gw_range(df, 1, 38)

# Для таблиц лиг показываем максимум 5 последних туров, чтобы не раздувать вывод
gw_cols_all = get_gw_cols(df)
gw_cols_display = gw_cols_all[-5:]
DISPLAY_COLS = (
    ["team_name", "manager_name"]
    + gw_cols_display
    + ["total_pts", "team_value"]
)


def league_table(data: pd.DataFrame, tier: str) -> pd.DataFrame:
    table = (
        data[data["league_tier"] == tier]
        .sort_values("total_pts", ascending=False)
        .reset_index(drop=True)
    )
    table.index = table.index + 1  # место в таблице с 1
    return table[DISPLAY_COLS]


# ---------- Вывод: вкладки ----------

tab_leagues, tab_cups = st.tabs(["🏆 Лиги", "🌍 Еврокубки"])

with tab_leagues:
    for league_name in ALL_LEAGUES:
        league_df = league_table(df, league_name)
        if not league_df.empty:
            st.header(league_name)
            st.dataframe(league_df, use_container_width=True)

with tab_cups:
    mode, payload = calculate_european_cups(df, current_gw)

    if mode == "qualification":
        st.header("Квалификация еврокубков")
        st.caption(
            f"Промежуточный срез по очкам после GW{current_gw}. "
            f"Топ-{CL_QUALIFY_TOP_N} каждой лиги проходит в Лигу Чемпионов, "
            "остальные — в Лигу Конференций. Распределение станет "
            f"окончательным после GW{QUALIFICATION_END_GW}."
        )
        ranking = payload
        qual_table = ranking[
            ["team_name", "manager_name", "league_tier", "qual_pts", "Направление"]
        ].rename(
            columns={
                "team_name": "Команда",
                "manager_name": "Менеджер",
                "league_tier": "Лига",
                "qual_pts": "Очки",
            }
        )
        st.dataframe(qual_table, use_container_width=True)
    else:
        cl_df, conf_df = payload
        st.header("Лига Чемпионов")
        render_cup_table(cl_df)
        st.header("Лига Конференций")
        render_cup_table(conf_df)
