import re

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="FPL Syndicate", layout="wide")

st.title("FPL Syndicate")

EXCEL_FILE = "Teams_2025.xlsx"

FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Все лиги турнира в иерархическом порядке — выводятся по мере наполнения
ALL_LEAGUES = ["Premier League", "A-1", "A-2", "B-1", "B-2", "B-3", "C", "D"]

# Маппинг названий лиг из Excel в наш единый стандарт league_tier
LEAGUE_NAME_MAP = {
    "H2H League PL": "Premier League",
    "H2H League A-1": "A-1",
    "H2H League A-2": "A-2",
    "H2H League B-1": "B-1",
    "H2H League B-2": "B-2",
    "H2H League B-3": "B-3",
    "H2H League C": "C",
    "H2H League D": "D",
}

# Постоянная ссылка на опубликованный CSV админ-панели
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vS8jhaEpFQVR8Sk78GKDUuUHjBwZT55ybatubqw7pPT48Vz7pLo_YWyKtek"
    "6dCuo4dS1R9V_tlJrFKH/pub?output=csv"
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


def normalize_league(raw_value) -> str:
    """Приводит название лиги из Excel к стандарту league_tier."""
    raw = str(raw_value).strip()
    if raw in LEAGUE_NAME_MAP:
        return LEAGUE_NAME_MAP[raw]
    # Запасной вариант: убираем префикс "H2H League" и сверяем остаток
    stripped = re.sub(r"^H2H\s+League\s+", "", raw, flags=re.IGNORECASE).strip()
    if stripped == "PL":
        return "Premier League"
    if stripped in ALL_LEAGUES:
        return stripped
    return raw  # неизвестная лига — вернём как есть, покажем в warning


# ---------- Загрузка данных сезона 2025 из Excel ----------

@st.cache_data
def load_mock_data(path: str) -> pd.DataFrame:
    raw_df = pd.read_excel(path)
    raw_df.columns = [str(c).strip() for c in raw_df.columns]

    rename_map = {
        "Fantasy ID": "team_id",
        "Manager": "manager_name",
        "Team Name": "team_name",
    }
    # Колонки туров: GW1..GW38 -> gw1_pts..gw38_pts
    for col in raw_df.columns:
        m = re.fullmatch(r"GW\s?(\d+)", col, flags=re.IGNORECASE)
        if m:
            rename_map[col] = gw_col(int(m.group(1)))

    df = raw_df.rename(columns=rename_map)

    required = ["team_id", "manager_name", "team_name", "League"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"В файле {path} отсутствуют колонки: {', '.join(missing)}"
        )

    df["league_tier"] = df["League"].apply(normalize_league)

    df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce")
    df = df.dropna(subset=["team_id"]).copy()
    df["team_id"] = df["team_id"].astype(int)

    for col in get_gw_cols(df):
        # round, а не truncate: в файле встречаются дробные значения (51.9)
        df[col] = (
            pd.to_numeric(df[col], errors="coerce").fillna(0).round().astype(int)
        )

    # Стоимости состава в файле нет — оставляем пустой колонкой
    if "team_value" not in df.columns:
        df["team_value"] = pd.NA
    df["team_value"] = pd.to_numeric(df["team_value"], errors="coerce")

    keep = (
        ["team_id", "manager_name", "team_name", "league_tier", "team_value"]
        + get_gw_cols(df)
    )
    return df[keep]


# ---------- Чтение админ-панели из Google Sheets ----------

@st.cache_data(ttl=60)
def load_admin_sheet(url: str):
    """Читает опубликованный CSV и возвращает
    (team_ids, team_tier_map, payment_map)."""
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
    payment_map = dict(
        zip(
            admin_df["FPL_Team_ID"],
            admin_df["Payment_Status"].astype(str).str.strip(),
        )
    )

    return team_ids, team_tier_map, payment_map


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
            "Переключись на данные сезона 2025 в боковой панели."
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


# ---------- Модуль «Игра в кальмара» ----------

SQUID_BANK_PER_GW = 1000  # тенге, прирост банка цикла за каждый тур


def calculate_squid_game(df: pd.DataFrame, current_gw: int):
    """Последовательная симуляция циклов «Игры в кальмара» с GW1 по current_gw.

    Правила:
    - цикл стартует со всеми участниками ALIVE и банком 0;
    - каждый тур банк цикла растёт на SQUID_BANK_PER_GW;
    - средний балл тура считается строго среди живых; кто набрал строго
      меньше среднего — DEAD;
    - если выживших осталось 1 (или 0 — защитный случай), цикл завершается,
      победитель забирает банк, со следующего тура — новый цикл;
    - равные результаты не прерывают игру: дуэль тянется в следующие туры;
    - на GW38 при нескольких выживших побеждает лучший балл GW38.

    Возвращает (history, last_round):
    - history: список завершённых циклов
      [{cycle, winner_idx, bank, start_gw, end_gw}, ...]
    - last_round: словарь состояния последнего рассчитанного тура.
    """
    history = []
    alive = list(df.index)
    cycle_num = 1
    cycle_start = 1
    bank = 0
    last_round = None

    for gw in range(1, current_gw + 1):
        col = gw_col(gw)
        if col in df.columns:
            pts = df[col]
        else:
            pts = pd.Series(0, index=df.index)

        bank += SQUID_BANK_PER_GW
        alive_pts = pts.loc[alive]
        avg = float(alive_pts.mean()) if len(alive) else 0.0
        survivors = list(alive_pts[alive_pts >= avg].index)
        dead_now = [i for i in alive if i not in set(survivors)]

        # Определение победителя цикла
        winner_idx = None
        if len(survivors) == 1:
            winner_idx = survivors[0]
        elif len(survivors) == 0 and alive:
            # Математически недостижимо (максимум всегда >= среднего),
            # но страхуемся: побеждает лучший балл тура среди живых
            winner_idx = alive_pts.idxmax()
        elif gw == 38 and len(survivors) > 1:
            # Финал сезона: среди выживших побеждает лучший балл GW38
            winner_idx = pts.loc[survivors].idxmax()

        last_round = {
            "gw": gw,
            "avg": avg,
            "cycle_num": cycle_num,
            "cycle_start": cycle_start,
            "step": gw - cycle_start + 1,
            "bank": bank,
            "alive_before": list(alive),
            "survivors": survivors,
            "dead_now": dead_now,
            "winner_idx": winner_idx,
        }

        if winner_idx is not None:
            history.append(
                {
                    "cycle": cycle_num,
                    "winner_idx": winner_idx,
                    "bank": bank,
                    "start_gw": cycle_start,
                    "end_gw": gw,
                }
            )
            # Перезапуск: новый цикл со следующего тура
            alive = list(df.index)
            cycle_num += 1
            cycle_start = gw + 1
            bank = 0
        else:
            alive = survivors

    return history, last_round


# ---------- Модуль «Зал Славы» ----------

def calculate_hall_of_fame(df: pd.DataFrame):
    """Рекорды сезона: лучший тур, чемпион по тоталу, топ по среднему баллу."""
    cols = get_gw_cols(df)
    pts = df[cols]

    # Абсолютный рекорд очков за один тур
    best_per_team = pts.max(axis=1)
    record_idx = best_per_team.idxmax()
    record_gw_col = pts.loc[record_idx].idxmax()
    record = {
        "points": int(pts.loc[record_idx, record_gw_col]),
        "gw": int(re.findall(r"\d+", record_gw_col)[0]),
        "manager": df.loc[record_idx, "manager_name"],
        "team": df.loc[record_idx, "team_name"],
        "league": df.loc[record_idx, "league_tier"],
    }

    # Чемпион сезона по сумме очков
    totals = pts.sum(axis=1)
    champ_idx = totals.idxmax()
    champion = {
        "points": int(totals.loc[champ_idx]),
        "manager": df.loc[champ_idx, "manager_name"],
        "team": df.loc[champ_idx, "team_name"],
        "league": df.loc[champ_idx, "league_tier"],
    }

    # Таблица лидеров: средний балл за тур, разброс, лучший тур
    leaders = pd.DataFrame(
        {
            "Менеджер": df["manager_name"],
            "Команда": df["team_name"],
            "Лига": df["league_tier"],
            "Total": totals,
            "Средний балл": pts.mean(axis=1).round(2),
            "Лучший тур": best_per_team.astype(int),
            "Разброс (σ)": pts.std(axis=1).round(2),
        }
    ).sort_values("Средний балл", ascending=False).reset_index(drop=True)
    leaders.index = leaders.index + 1

    return record, champion, leaders


# ---------- Выбор источника данных ----------

st.sidebar.header("Источник данных")
data_source = st.sidebar.radio(
    "Откуда брать данные:",
    ("Данные сезона 2025 (Excel)", "Использовать API FPL"),
)

if data_source == "Данные сезона 2025 (Excel)":
    try:
        df = load_mock_data(EXCEL_FILE)
    except FileNotFoundError:
        st.error(
            f"Файл {EXCEL_FILE} не найден. Положи его в одну папку с app.py."
        )
        st.stop()
    except Exception as e:
        st.error(f"Не удалось прочитать {EXCEL_FILE}: {e}")
        st.stop()

    # Предупреждение о лигах, не попавших в стандарт ALL_LEAGUES
    unknown = sorted(set(df["league_tier"]) - set(ALL_LEAGUES))
    if unknown:
        st.warning(
            "В файле есть лиги вне стандартного списка, их команды не "
            f"отобразятся в таблицах: {', '.join(unknown)}. "
            "Проверь LEAGUE_NAME_MAP."
        )

    # Статусы взносов подтягиваем из админ-таблицы; её недоступность не критична
    try:
        _, _, payment_map = load_admin_sheet(CSV_URL)
    except Exception:
        payment_map = {}
else:
    try:
        team_ids, team_tier_map, payment_map = load_admin_sheet(CSV_URL)
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

# Игровой тур: слайдер 1–38, по умолчанию — последний тур в данных.
# Управляет и фазой еврокубков, и раундом «Игры в кальмара».
auto_gw = detect_current_gw(df)
current_gw = st.sidebar.slider(
    "Игровой тур (GW)",
    min_value=1,
    max_value=38,
    value=min(max(auto_gw, 1), 38),
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


# Селектор менеджера для Финансового Хаба (поиск набором текста)
st.sidebar.header("Кошелёк менеджера")
selected_manager = st.sidebar.selectbox(
    "Менеджер",
    options=sorted(df["manager_name"].astype(str).unique()),
)

# ---------- Вывод: вкладки ----------

tab_leagues, tab_cups, tab_squid, tab_fame, tab_wallet = st.tabs(
    ["🏆 Лиги", "🌍 Еврокубки", "🦑 Squid Game", "🏅 Зал Славы", "💰 Финансовый Хаб"]
)

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

with tab_squid:
    history, last_round = calculate_squid_game(df, current_gw)

    if last_round is None:
        st.info("Нет данных для расчёта.")
    else:
        gw = last_round["gw"]
        cycle_completed = last_round["winner_idx"] is not None

        st.header(f"🦑 Squid Game — GW{gw}")
        st.caption(
            "Средний балл считается среди живых. Набрал строго меньше "
            "среднего — выбыл. Последний выживший забирает банк цикла, "
            "и игра перезапускается со всеми участниками."
        )

        # --- Шапка текущего цикла ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Цикл",
            f"№{last_round['cycle_num']}, Шаг {last_round['step']}",
        )
        m2.metric("🟢 Живых", len(last_round["survivors"]))
        m3.metric("Средний балл тура", f"{last_round['avg']:.2f}")
        m4.metric(
            "Банк цикла",
            f"{last_round['bank']:,} ₸".replace(",", " "),
        )

        if cycle_completed:
            winner_row = df.loc[last_round["winner_idx"]]
            st.success(
                f"Цикл №{last_round['cycle_num']} завершён на GW{gw}! "
                f"Победитель — {winner_row['team_name']} "
                f"({winner_row['manager_name']}), банк "
                f"{last_round['bank']:,} ₸".replace(",", " ")
                + f". Со следующего тура стартует новый цикл: все 160 "
                "участников снова в игре, банк обнуляется."
            )

        # --- Победители прошлых циклов ---
        if history:
            st.subheader("Победители прошлых циклов")
            hist_rows = []
            for h in history:
                w = df.loc[h["winner_idx"]]
                hist_rows.append(
                    {
                        "Цикл": h["cycle"],
                        "Победитель": w["manager_name"],
                        "Команда": w["team_name"],
                        "Лига": w["league_tier"],
                        "Туры": f"GW{h['start_gw']}–GW{h['end_gw']}",
                        "Банк": f"{h['bank']:,} ₸".replace(",", " "),
                    }
                )
            hist_df = pd.DataFrame(hist_rows).set_index("Цикл")
            st.dataframe(hist_df, use_container_width=True)

        # --- Таблица участников текущего тура ---
        st.subheader(f"Участники тура GW{gw} (цикл №{last_round['cycle_num']})")
        col = gw_col(gw)
        table = df.copy()
        table["gw_pts"] = table[col] if col in table.columns else 0

        survivors_set = set(last_round["survivors"])
        dead_now_set = set(last_round["dead_now"])
        winner_idx = last_round["winner_idx"]

        def squid_status(idx):
            if idx == winner_idx:
                return "🏆 WINNER"
            if idx in survivors_set:
                return "ALIVE"
            if idx in dead_now_set:
                return "DEAD"
            return "DEAD (ранее)"

        table["Статус"] = [squid_status(i) for i in table.index]
        table = (
            table.sort_values("gw_pts", ascending=False)
            .reset_index(drop=True)[
                ["team_name", "manager_name", "league_tier", "gw_pts", "Статус"]
            ]
            .rename(
                columns={
                    "team_name": "Команда",
                    "manager_name": "Менеджер",
                    "league_tier": "Лига",
                    "gw_pts": f"Очки GW{gw}",
                }
            )
        )
        table.index = table.index + 1

        def color_status(val):
            if "WINNER" in val:
                return "background-color: #FFD700; color: #000000"
            if val == "ALIVE":
                return "background-color: #1E7A46; color: #FFFFFF"
            return "background-color: #8B1E1E; color: #FFFFFF"

        st.dataframe(
            table.style.map(color_status, subset=["Статус"]),
            use_container_width=True,
        )

with tab_fame:
    record, champion, leaders = calculate_hall_of_fame(df)

    st.header("🏅 Зал Славы — рекорды сезона")

    f1, f2, f3 = st.columns(3)
    f1.metric(
        "🔥 Рекорд одного тура",
        f"{record['points']} очков",
        f"GW{record['gw']} — {record['manager']}",
        delta_color="off",
    )
    f2.metric(
        "👑 Чемпион сезона (Total)",
        f"{champion['points']} очков",
        f"{champion['manager']} ({champion['league']})",
        delta_color="off",
    )
    best_avg = leaders.iloc[0]
    f3.metric(
        "📈 Лучший средний балл",
        f"{best_avg['Средний балл']}",
        f"{best_avg['Менеджер']} ({best_avg['Лига']})",
        delta_color="off",
    )

    st.caption(
        f"Рекорд тура: {record['team']} ({record['manager']}, "
        f"{record['league']}) — {record['points']} очков в GW{record['gw']}. "
        f"Чемпион: {champion['team']}."
    )

    st.subheader("Таблица лидеров (по среднему баллу за тур)")
    st.dataframe(leaders.head(15), use_container_width=True)

with tab_wallet:
    st.header("💰 Финансовый Хаб")

    row = df[df["manager_name"].astype(str) == selected_manager]
    if row.empty:
        st.info("Выбери менеджера в боковой панели.")
    else:
        m = row.iloc[0]
        m_idx = row.index[0]

        # Призовые Squid Game: сумма банков выигранных циклов
        squid_history, _ = calculate_squid_game(df, current_gw)
        won_cycles = [h for h in squid_history if h["winner_idx"] == m_idx]
        prize_balance = sum(h["bank"] for h in won_cycles)

        # Статус взноса из админ-таблицы (по FPL Team ID)
        payment_status = payment_map.get(int(m["team_id"]), "н/д")

        st.subheader(f"{m['team_name']}")
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Лига", m["league_tier"])
        w2.metric("Очки за сезон 2025", int(m["total_pts"]))
        w3.metric(
            "Виртуальный баланс призовых",
            f"{prize_balance:,} ₸".replace(",", " "),
        )
        w4.metric("Статус взноса", payment_status)

        if won_cycles:
            st.markdown("**Выигранные циклы Squid Game:**")
            for h in won_cycles:
                bank_str = f"{h['bank']:,} ₸".replace(",", " ")
                st.markdown(
                    f"- Цикл №{h['cycle']} (GW{h['start_gw']}–GW{h['end_gw']}) "
                    f"— {bank_str}"
                )
        else:
            st.caption(
                f"Выигранных циклов Squid Game к GW{current_gw} пока нет."
            )
