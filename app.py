import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

APP_VERSION = "v2.6"
SEASON_LABEL = "2026"
LOGO_FILE = "0BEA079A-1955-476D-AF71-DFBAE647ED7E.png"

st.set_page_config(
    page_title="FPL Syndicate 2026 | Платформа Лиги",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Порядок вкладок ----------

TAB_LEAGUES = "🏆 Лиги"
TAB_SOCIAL = "💬 Сообщество"
TAB_CUPS = "🌍 Еврокубки"
TAB_SQUID = "🦑 Squid Game"
TAB_FAME = "🥇 Зал Славы"
TAB_WALLET = "💰 Финансы"

TAB_LABELS = [
    TAB_LEAGUES, TAB_SOCIAL, TAB_CUPS, TAB_SQUID, TAB_FAME, TAB_WALLET,
]

# Человекочитаемые пути для GA4 (эмодзи в URL читать неудобно)
TAB_PATHS = {
    TAB_LEAGUES: "/leagues",
    TAB_SOCIAL: "/community",
    TAB_CUPS: "/eurocups",
    TAB_SQUID: "/squid-game",
    TAB_FAME: "/hall-of-fame",
    TAB_WALLET: "/finance",
}

# ---------- Google Analytics 4 ----------

GA_PLACEHOLDER = "G-XXXXXXXXXX"


def inject_ga():
    """Подключает gtag.js и шлёт page_view при переключении вкладок.

    Возвращает (measurement_id, активен ли счётчик).
    """
    ga_id = st.secrets.get("GA_MEASUREMENT_ID", GA_PLACEHOLDER)
    if not ga_id or ga_id == GA_PLACEHOLDER:
        # Заглушка — не шлём данные в несуществующее свойство
        return ga_id, False

    paths_json = json.dumps(TAB_PATHS, ensure_ascii=False)
    ga_code = f"""
<!-- Global site tag (gtag.js) - Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  // Компонент живёт в iframe, поэтому адрес страницы берём у родителя,
  // иначе в отчётах GA4 окажется about:srcdoc.
  var pageUrl = document.referrer || location.href;
  try {{ pageUrl = window.parent.location.href; }} catch (e) {{}}

  gtag('config', '{ga_id}', {{
    page_location: pageUrl,
    page_title: 'FPL Syndicate 2026'
  }});

  var TAB_PATHS = {paths_json};

  function trackTab(label) {{
    var path = TAB_PATHS[label] || '/' + label;
    gtag('event', 'page_view', {{
      page_title: label,
      page_path: path,
      page_location: pageUrl.split('#')[0] + '#' + path.slice(1)
    }});
  }}

  try {{
    var doc = window.parent.document;

    // Стартовый просмотр: активная вкладка при загрузке
    var active = doc.querySelector('button[data-baseweb="tab"][aria-selected="true"]');
    if (active) {{ trackTab(active.innerText.trim()); }}

    // Делегирование на body: переживает перерисовку вкладок при rerun.
    // Флаг на window родителя не даёт навесить обработчик дважды.
    if (!window.parent.__fplGaTabsBound) {{
      window.parent.__fplGaTabsBound = true;
      doc.body.addEventListener('click', function (ev) {{
        var btn = ev.target.closest
          ? ev.target.closest('button[data-baseweb="tab"]')
          : null;
        if (btn) {{ trackTab(btn.innerText.trim()); }}
      }}, true);
    }}
  }} catch (e) {{
    // Кросс-доменные ограничения — просмотры вкладок недоступны,
    // базовый page_view при этом всё равно отправлен.
  }}
</script>
"""
    components.html(ga_code, height=0)
    return ga_id, True


GA_ID, GA_ACTIVE = inject_ga()


# ---------- Кастомный стиль ----------

st.markdown(
    """
    <style>
    /* Компактные отступы — для телефонов */
    .block-container {
        padding-top: 2.2rem;
        padding-left: 0.9rem;
        padding-right: 0.9rem;
        padding-bottom: 4.5rem;
    }
    [data-testid="stHeader"] { height: 2.5rem; }

    /* Объёмные карточки метрик */
    div[data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        padding: 0.75rem 0.9rem;
    }
    [data-testid="stMetricValue"] { font-size: 1.35rem; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem; opacity: 0.85; }
    [data-testid="stMetricDelta"] { font-size: 0.75rem; }

    /* Вкладки: контрастный акцент на активной */
    div[data-testid="stTabs"] button[data-baseweb="tab"] {
        padding: 0.45rem 0.75rem;
        font-size: 0.9rem;
        border-radius: 10px 10px 0 0;
    }
    div[data-testid="stTabs"] button[aria-selected="true"] {
        background: rgba(0, 223, 122, 0.14);
        border-bottom: 3px solid #00DF7A;
        font-weight: 700;
    }

    div[data-testid="stDataFrame"] { font-size: 0.85rem; }

    /* Баннер */
    .fpl-banner {
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.6rem;
    }
    .fpl-banner h1 { margin: 0 0 0.35rem 0; font-size: 1.6rem; }
    .fpl-banner p { margin: 0; font-size: 0.92rem; opacity: 0.88; }
    .fpl-chips { margin-top: 0.7rem; }
    .fpl-chip {
        display: inline-block;
        padding: 0.22rem 0.65rem;
        margin: 0.15rem 0.35rem 0.15rem 0;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 600;
        background: rgba(0, 223, 122, 0.14);
        border: 1px solid rgba(0, 223, 122, 0.45);
    }

    /* HTML-таблицы лиг с кликабельными командами */
    .fpl-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    .fpl-table th, .fpl-table td {
        padding: 0.4rem 0.5rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
        text-align: right;
        white-space: nowrap;
    }
    .fpl-table th { text-align: right; font-size: 0.78rem; opacity: 0.8; }
    .fpl-table td:nth-child(1), .fpl-table th:nth-child(1) { text-align: left; }
    .fpl-table td:nth-child(2), .fpl-table th:nth-child(2) { text-align: left; }
    .fpl-table tbody tr:hover { background: rgba(0, 223, 122, 0.07); }
    .fpl-table a { text-decoration: none; font-weight: 600; }
    .fpl-table a:hover { text-decoration: underline; }
    .fpl-wrap { overflow-x: auto; }

    /* Карточки постов социальной ленты */
    .fpl-post-head {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.4rem;
        margin-bottom: 0.35rem;
    }
    .fpl-avatar {
        width: 2rem; height: 2rem;
        border-radius: 50%;
        display: inline-flex;
        align-items: center; justify-content: center;
        font-size: 0.85rem; font-weight: 700;
        background: rgba(0, 223, 122, 0.18);
        border: 1px solid rgba(0, 223, 122, 0.45);
        flex: 0 0 auto;
    }
    .fpl-author { font-weight: 700; font-size: 0.92rem; }
    .fpl-badge {
        display: inline-block;
        padding: 0.1rem 0.5rem;
        border-radius: 999px;
        font-size: 0.7rem; font-weight: 600;
        border: 1px solid rgba(128, 128, 128, 0.35);
        background: rgba(128, 128, 128, 0.12);
        white-space: nowrap;
    }
    .fpl-badge-ok {
        background: rgba(0, 223, 122, 0.16);
        border-color: rgba(0, 223, 122, 0.5);
    }
    .fpl-post-meta { font-size: 0.72rem; opacity: 0.62; }
    .fpl-post-body {
        font-size: 0.92rem;
        line-height: 1.45;
        white-space: pre-wrap;
        word-break: break-word;
        margin: 0.25rem 0 0.15rem 0;
    }

    /* Футер */
    .fpl-footer {
        margin-top: 2rem;
        padding: 0.9rem 0 0.4rem 0;
        border-top: 1px solid rgba(128, 128, 128, 0.25);
        text-align: center;
        font-size: 0.8rem;
        opacity: 0.7;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Логотип и баннер ----------

logo_col, banner_col = st.columns([1, 5])
with logo_col:
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, use_container_width=True)
    else:
        st.markdown("<div style='font-size:3.4rem'>🏆</div>",
                    unsafe_allow_html=True)
with banner_col:
    # Заполняется ниже — статус-чип зависит от текущего тура
    banner_slot = st.empty()


def season_status_chip(current_event, fallback_gw) -> str:
    """Динамический статус сезона.

    current_event — номер тура из FPL API (None, если API недоступен);
    fallback_gw — тур, выбранный слайдером (офлайн-режим).
    """
    gw = current_event if current_event is not None else fallback_gw
    if gw is None or gw <= 0:
        return "🟡 Межсезонье (до GW1)"
    if gw > 38:
        return "🏁 Сезон завершён"
    return f"🟢 LIVE (GW{gw})"


def render_banner(status_chip: str):
    banner_slot.markdown(
        f"""
        <div class="fpl-banner">
            <h1>⚽ FPL Syndicate {SEASON_LABEL}</h1>
            <p>Единый аналитический хаб 160 участников: Head-to-Head лиги,
            Еврокубки, цикличная «Игра в Кальмара» и прозрачный Финансовый Хаб
            с призовым фондом 1 600 000 ₸.</p>
            <div class="fpl-chips">
                <span class="fpl-chip">Статус: {status_chip}</span>
                <span class="fpl-chip">Сезон: {SEASON_LABEL}</span>
                <span class="fpl-chip">Призовой фонд: 1 600 000 ₸</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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

# Необязательная колонка: ID H2H-лиги в FPL (одинаковый для всех команд лиги).
# Если она заполнена, таблицы строятся по реальным матчам из API,
# иначе — по сгенерированному круговому календарю.
ADMIN_LEAGUE_ID_COL = "League_ID"

# Параметры еврокубков
CL_QUALIFY_TOP_N = 10      # топ-N из каждой лиги проходит в Лигу Чемпионов
QUALIFICATION_END_GW = 20  # квалификация длится до этого тура включительно
ELIMINATION_GWS = [25, 30, 35]  # контрольные точки отсева
ELIMINATED_PER_ROUND = 20       # сколько команд выбывает на каждой точке
FINAL_START_GW, FINAL_END_GW = 36, 38

# ---------- Призовая сетка сезона (итого 1 600 000 ₸) ----------

PRIZE_FUND_TOTAL = 1_600_000

# Лиги H2H: в каждой из 8 лиг
PRIZE_LEAGUE = {1: 100_000, 2: 40_000, 3: 10_000}          # 8 x 150 000 = 1 200 000

# Общий зачёт среди всех 160 команд
PRIZE_ABSOLUTE = {1: 40_000, 2: 20_000, 3: 10_000}         # 70 000

# Еврокубки и Кубок (в сумме 150 000, чтобы фонд сошёлся к 1.6 млн)
PRIZE_CL_WINNER = 70_000       # Лига Чемпионов
PRIZE_CONF_WINNER = 40_000     # Лига Конференций
PRIZE_CUP_WINNER = 40_000      # Кубок

# Специальные номинации
PRIZE_SQUID_TOTAL = 57_000     # Squid Game: 1 500 x 38 туров
PRIZE_MOM_TOTAL = 50_000       # Лучший TM месяца: 5 000 x 10 месяцев
PRIZE_MAX_PTS = 20_000         # Рекорд очков за тур
PRIZE_BEST_CAP = 20_000        # Лучший капитан/вице
PRIZE_EXPENSIVE_TEAM = 20_000  # Самая дорогая команда
PRIZE_MIN_TRANSFERS = 13_000   # Минимум трансферов


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
    (team_ids, team_tier_map, payment_map, league_id_map, pin_map,
     manager_team_map).

    league_id_map: {League_Tier: League_ID} — ID H2H-лиги в FPL.
    pin_map: {Manager_Name: последние 4 цифры телефона} — для верификации.
    manager_team_map: {Manager_Name: FPL_Team_ID}.
    """
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
    admin_df["League_Tier"] = admin_df["League_Tier"].astype(str).str.strip()

    team_ids = admin_df["FPL_Team_ID"].tolist()
    team_tier_map = dict(zip(admin_df["FPL_Team_ID"], admin_df["League_Tier"]))
    payment_map = dict(
        zip(
            admin_df["FPL_Team_ID"],
            admin_df["Payment_Status"].astype(str).str.strip(),
        )
    )

    # League_ID: один на дивизион, берём первое непустое значение по каждой лиге
    league_id_map = {}
    if ADMIN_LEAGUE_ID_COL in admin_df.columns:
        ids = pd.to_numeric(
            admin_df[ADMIN_LEAGUE_ID_COL], errors="coerce"
        )
        for tier, group in ids.groupby(admin_df["League_Tier"]):
            valid = group.dropna()
            if not valid.empty:
                league_id_map[tier] = int(valid.iloc[0])

    # PIN = последние 4 цифры телефона. Сам номер наружу не отдаём.
    pin_map = {}
    manager_team_map = {}
    for manager, phone, tid in zip(
        admin_df["Manager_Name"].astype(str).str.strip(),
        admin_df["Phone_Number"].astype(str),
        admin_df["FPL_Team_ID"],
    ):
        if not manager:
            continue
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 4:
            pin_map[manager] = digits[-4:]
        manager_team_map[manager] = int(tid)

    return (team_ids, team_tier_map, payment_map, league_id_map,
            pin_map, manager_team_map)


# ---------- Загрузка из FPL API ----------

@st.cache_data(ttl=300)
def fetch_current_event():
    """Номер текущего тура из FPL API (bootstrap-static).

    Возвращает 0 до старта сезона, 1–38 во время, 39 после завершения,
    либо None, если API недоступен.
    """
    try:
        resp = requests.get(f"{FPL_BASE_URL}/bootstrap-static/", timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None

    if not events:
        return None

    current = next((e["id"] for e in events if e.get("is_current")), None)
    if current is not None:
        return int(current)

    finished = [e["id"] for e in events if e.get("finished")]
    if len(finished) >= len(events):
        return len(events) + 1  # все туры сыграны — сезон завершён
    if finished:
        return int(max(finished))
    return 0  # ни один тур не начался — межсезонье


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

SQUID_BANK_PER_GW = 1500  # тенге за тур: 38 туров x 1 500 = 57 000 ₸ за сезон


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


# ---------- Модуль H2H-таблиц лиг ----------

@st.cache_data(ttl=600)
def fetch_h2h_matches(league_id: int) -> pd.DataFrame:
    """Реальные матчи H2H-лиги из FPL API (постранично).

    Возвращает DataFrame с колонками event, entry_1, pts_1, entry_2, pts_2.
    Пустой DataFrame — если матчей нет (сезон не начался) или лига закрыта.
    """
    rows = []
    page = 1
    while page <= 50:  # предохранитель от бесконечного цикла
        resp = requests.get(
            f"{FPL_BASE_URL}/leagues-h2h-matches/league/{league_id}/",
            params={"page": page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("results", []):
            # Пропускаем "выходные" и матчи против среднего по лиге
            if m.get("is_bye"):
                continue
            if m.get("entry_1_entry") is None or m.get("entry_2_entry") is None:
                continue
            rows.append(
                {
                    "event": m.get("event"),
                    "entry_1": int(m["entry_1_entry"]),
                    "pts_1": m.get("entry_1_points", 0),
                    "entry_2": int(m["entry_2_entry"]),
                    "pts_2": m.get("entry_2_points", 0),
                }
            )
        if not data.get("has_next"):
            break
        page += 1

    return pd.DataFrame(
        rows, columns=["event", "entry_1", "pts_1", "entry_2", "pts_2"]
    )


def build_h2h_schedule(team_ids: list, n_gws: int = 38) -> dict:
    """Круговой календарь H2H (метод «карусели») — запасной вариант,
    когда реальные матчи из API недоступны.

    Для 20 команд получается 19 туров полного круга, GW1–19 — первый круг,
    GW20–38 — второй. Календарь детерминирован: при одном и том же составе
    лиги расписание всегда одинаковое.
    """
    ids = sorted(team_ids)
    if len(ids) < 2:
        return {gw: [] for gw in range(1, n_gws + 1)}
    if len(ids) % 2:
        ids.append(None)  # нечётное число команд — фиктивный соперник (выходной)

    n = len(ids)
    rounds = []
    arr = ids[:]
    for _ in range(n - 1):
        pairs = [(arr[i], arr[n - 1 - i]) for i in range(n // 2)]
        rounds.append([(a, b) for a, b in pairs if a is not None and b is not None])
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # фиксируем первого, крутим остальных

    return {gw: rounds[(gw - 1) % len(rounds)] for gw in range(1, n_gws + 1)}


def calculate_h2h_table(
    league_df: pd.DataFrame,
    current_gw: int,
    matches: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """H2H-таблица дивизиона: 3 очка за победу, 1 за ничью, 0 за поражение.

    Если передан matches (реальные матчи из API) — считает по ним,
    иначе строит круговой календарь и берёт очки туров из league_df.
    """
    if league_df.empty:
        return league_df.assign(h2h_pts=[], wins=[], draws=[], losses=[])

    idx_by_team = {int(t): i for i, t in league_df["team_id"].items()}
    stats = {i: {"h2h_pts": 0, "wins": 0, "draws": 0, "losses": 0}
             for i in league_df.index}

    def score(ia, ib, pa, pb):
        if pa > pb:
            stats[ia]["h2h_pts"] += 3
            stats[ia]["wins"] += 1
            stats[ib]["losses"] += 1
        elif pb > pa:
            stats[ib]["h2h_pts"] += 3
            stats[ib]["wins"] += 1
            stats[ia]["losses"] += 1
        else:
            stats[ia]["h2h_pts"] += 1
            stats[ib]["h2h_pts"] += 1
            stats[ia]["draws"] += 1
            stats[ib]["draws"] += 1

    if matches is not None and not matches.empty:
        # --- Реальные результаты из API ---
        played = matches[matches["event"] <= current_gw]
        for row in played.itertuples():
            ia = idx_by_team.get(row.entry_1)
            ib = idx_by_team.get(row.entry_2)
            if ia is None or ib is None:
                continue  # соперник не из нашего синдиката
            score(ia, ib, row.pts_1, row.pts_2)
    else:
        # --- Запасной круговой календарь по очкам туров ---
        schedule = build_h2h_schedule(list(idx_by_team.keys()), 38)
        for gw in range(1, current_gw + 1):
            col = gw_col(gw)
            if col not in league_df.columns:
                continue
            pts = league_df[col]
            for team_a, team_b in schedule.get(gw, []):
                ia, ib = idx_by_team[team_a], idx_by_team[team_b]
                score(ia, ib, pts.loc[ia], pts.loc[ib])

    result = league_df.copy()
    for field in ("h2h_pts", "wins", "draws", "losses"):
        result[field] = [stats[i][field] for i in result.index]
    return result


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


# ---------- Расчёт призовых выплат ----------

def calculate_prizes(df: pd.DataFrame, current_gw: int) -> dict:
    """Начисляет призовые по всем рассчитываемым категориям.

    Возвращает {index команды: [(категория, сумма), ...]}.
    Категории без данных в файле (TM месяца, капитаны, стоимость состава,
    трансферы, Кубок) здесь не начисляются — см. PRIZE_PENDING.
    """
    payouts: dict = {i: [] for i in df.index}

    def award(idx, category, amount):
        payouts[idx].append((category, int(amount)))

    totals = sum_gw_range(df, 1, current_gw)

    # 1. Призовые мест в лигах H2H (по H2H-таблице: очки 3/1/0, затем Total)
    for league in ALL_LEAGUES:
        league_df = df[df["league_tier"] == league]
        if league_df.empty:
            continue
        standings = calculate_h2h_table(
            league_df, current_gw, h2h_matches_by_tier.get(league)
        )
        standings["_total"] = totals.loc[standings.index]
        standings = standings.sort_values(
            ["h2h_pts", "_total"], ascending=[False, False]
        )
        for place, prize in PRIZE_LEAGUE.items():
            if len(standings) >= place:
                award(
                    standings.index[place - 1],
                    f"Лига {league} — {place} место",
                    prize,
                )

    # 2. Общий зачёт среди всех команд
    ranked_abs = totals.sort_values(ascending=False)
    for place, prize in PRIZE_ABSOLUTE.items():
        if len(ranked_abs) >= place:
            award(
                ranked_abs.index[place - 1],
                f"Общий зачёт — {place} место",
                prize,
            )

    # 3. Squid Game: банки выигранных циклов
    squid_history, _ = calculate_squid_game(df, current_gw)
    for h in squid_history:
        award(
            h["winner_idx"],
            f"Squid Game — цикл №{h['cycle']} (GW{h['start_gw']}–{h['end_gw']})",
            h["bank"],
        )

    # 4. Еврокубки: победители определяются после GW38
    if current_gw > QUALIFICATION_END_GW:
        mode, payload = calculate_european_cups(df, current_gw)
        if mode == "tournament":
            cl_df, conf_df = payload
            cl_winner = cl_df[cl_df["status"] == "Победитель 🏆"]
            if not cl_winner.empty:
                award(cl_winner.index[0], "Лига Чемпионов — победитель",
                      PRIZE_CL_WINNER)
            conf_winner = conf_df[conf_df["status"] == "Победитель 🏆"]
            if not conf_winner.empty:
                award(conf_winner.index[0], "Лига Конференций — победитель",
                      PRIZE_CONF_WINNER)

    # 5. Рекорд очков за один тур
    record, _, _ = calculate_hall_of_fame(df)
    record_idx = df[
        (df["manager_name"] == record["manager"])
        & (df["team_name"] == record["team"])
    ].index
    if len(record_idx):
        award(
            record_idx[0],
            f"Max pts — рекорд тура ({record['points']} в GW{record['gw']})",
            PRIZE_MAX_PTS,
        )

    return payouts


# Номинации, которые пока невозможно начислить из имеющихся данных
PRIZE_PENDING = [
    ("Лучший TM месяца", PRIZE_MOM_TOTAL, "нужна разбивка туров по месяцам"),
    ("Best cap & vc", PRIZE_BEST_CAP, "нужны данные по капитанам из API"),
    ("Самая дорогая команда", PRIZE_EXPENSIVE_TEAM,
     "нужна стоимость составов (team_value из API)"),
    ("Минимум трансферов", PRIZE_MIN_TRANSFERS,
     "нужны данные по трансферам из API"),
    ("Кубок", PRIZE_CUP_WINNER, "модуль Кубка ещё не реализован"),
]


def prize_fund_summary() -> pd.DataFrame:
    """Сводка призового фонда для проверки, что всё сходится к 1.6 млн."""
    rows = [
        ("Лиги H2H (8 лиг: 100/40/10 тыс.)", 8 * sum(PRIZE_LEAGUE.values())),
        ("Общий зачёт (40/20/10 тыс.)", sum(PRIZE_ABSOLUTE.values())),
        ("Лига Чемпионов", PRIZE_CL_WINNER),
        ("Лига Конференций", PRIZE_CONF_WINNER),
        ("Кубок", PRIZE_CUP_WINNER),
        ("Squid Game (1 500 x 38)", PRIZE_SQUID_TOTAL),
        ("Лучший TM месяца (5 000 x 10)", PRIZE_MOM_TOTAL),
        ("Max pts (рекорд тура)", PRIZE_MAX_PTS),
        ("Best cap & vc", PRIZE_BEST_CAP),
        ("Самая дорогая команда", PRIZE_EXPENSIVE_TEAM),
        ("Минимум трансферов", PRIZE_MIN_TRANSFERS),
    ]
    summary = pd.DataFrame(rows, columns=["Категория", "Сумма, ₸"])
    return summary


# ---------- Модуль «Сообщество»: слой данных ----------

POST_CATEGORIES = ["😂 Мем", "📊 Аналитика/Состав", "📢 Объявление", "💬 Чат"]
POSTS_TABLE = "posts"
# Имена колонок в таблице posts (совпадают с существующей схемой Supabase)
COL_TEXT = "content"       # текст поста
COL_LIKES = "likes_count"  # счётчик лайков
MAX_IMAGE_BYTES = 300_000  # ~300 КБ: картинка кодируется в base64 и лежит в строке


def supabase_config():
    """(url, key) из st.secrets или (None, None), если секреты не настроены."""
    try:
        cfg = st.secrets["supabase"]
        url = str(cfg["SUPABASE_URL"]).rstrip("/")
        key = str(cfg["SUPABASE_KEY"])
        if url and key:
            return url, key
    except Exception:
        pass
    return None, None


def _sb_headers(key: str, extra: dict | None = None) -> dict:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _sb_error(resp) -> str:
    """Достаёт понятный текст ошибки из ответа PostgREST."""
    try:
        data = resp.json()
    except ValueError:
        return f"{resp.status_code}: {resp.text[:300]}"
    parts = [
        data.get("message"),
        data.get("details"),
        data.get("hint"),
        data.get("code"),
    ]
    text = " | ".join(str(p) for p in parts if p)
    return text or f"{resp.status_code}: {resp.text[:300]}"


def fetch_posts() -> list[dict]:
    """Все посты: из Supabase или из session_state в демо-режиме."""
    url, key = supabase_config()
    if not url:
        return list(st.session_state.get("demo_posts", []))
    try:
        resp = requests.get(
            f"{url}/rest/v1/{POSTS_TABLE}",
            headers=_sb_headers(key),
            params={"select": "*", "order": "created_at.desc", "limit": "300"},
            timeout=10,
        )
        if not resp.ok:
            st.warning(f"Не удалось загрузить ленту: {_sb_error(resp)}")
            return list(st.session_state.get("demo_posts", []))
        return resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        st.warning(f"Не удалось загрузить ленту из Supabase: {e}")
        return list(st.session_state.get("demo_posts", []))


def insert_post(post: dict) -> bool:
    """Публикует пост. True — успех."""
    url, key = supabase_config()
    if not url:
        st.session_state.setdefault("demo_posts", []).insert(0, post)
        return True

    # id генерирует база (uuid default или identity) — свой не навязываем,
    # иначе тип может не совпасть с колонкой существующей таблицы.
    payload = {k: v for k, v in post.items() if k != "id"}
    try:
        resp = requests.post(
            f"{url}/rest/v1/{POSTS_TABLE}",
            headers=_sb_headers(key, {"Prefer": "return=minimal"}),
            json=payload,
            timeout=10,
        )
        if not resp.ok:
            st.error(f"Не удалось опубликовать пост: {_sb_error(resp)}")
            with st.expander("Отладка: что отправлялось"):
                st.json({"columns": list(payload.keys()),
                         "status": resp.status_code})
            return False
        return True
    except requests.exceptions.RequestException as e:
        st.error(f"Не удалось опубликовать пост: {e}")
        return False


def like_post(post_id: str, current_likes: int) -> bool:
    """Инкремент лайка (read-modify-write — для лиги на 160 человек достаточно)."""
    url, key = supabase_config()
    if not url:
        for p in st.session_state.get("demo_posts", []):
            if str(p.get("id")) == str(post_id):
                p[COL_LIKES] = int(p.get(COL_LIKES, 0)) + 1
        return True
    try:
        resp = requests.patch(
            f"{url}/rest/v1/{POSTS_TABLE}",
            headers=_sb_headers(key, {"Prefer": "return=minimal"}),
            params={"id": f"eq.{post_id}"},
            json={COL_LIKES: int(current_likes) + 1},
            timeout=10,
        )
        if not resp.ok:
            st.error(f"Не удалось поставить лайк: {_sb_error(resp)}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        st.error(f"Не удалось поставить лайк: {e}")
        return False


def encode_image(uploaded_file) -> tuple[str | None, str | None]:
    """Кодирует загруженную картинку в data URL. Возвращает (data_url, ошибка)."""
    if uploaded_file is None:
        return None, None
    raw = uploaded_file.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        return None, (
            f"Файл {len(raw) // 1024} КБ — слишком большой "
            f"(лимит {MAX_IMAGE_BYTES // 1024} КБ). Сожми картинку "
            "или вставь ссылку на неё."
        )
    mime = uploaded_file.type or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}", None


def render_post_image(image_ref: str):
    """Показывает картинку поста: data URL или обычная ссылка."""
    if not image_ref:
        return
    try:
        if image_ref.startswith("data:"):
            payload = image_ref.split(",", 1)[1]
            st.image(base64.b64decode(payload), use_container_width=True)
        else:
            st.image(image_ref, use_container_width=True)
    except Exception:
        st.caption("🖼️ Картинку не удалось отобразить.")


def sort_posts(posts: list[dict], mode: str, gw: int) -> list[dict]:
    """Фильтрация и сортировка ленты."""
    def likes(p):
        return int(p.get(COL_LIKES) or 0)

    def created(p):
        return str(p.get("created_at") or "")

    if mode == "🔥 Топ за тур":
        subset = [p for p in posts if int(p.get("gw") or 0) == gw]
        return sorted(subset, key=lambda p: (likes(p), created(p)), reverse=True)
    if mode == "🏆 Топ за всё время":
        return sorted(posts, key=lambda p: (likes(p), created(p)), reverse=True)
    return sorted(posts, key=created, reverse=True)  # ⏱️ Свежее


# ---------- Выбор источника данных ----------

if os.path.exists(LOGO_FILE):
    st.sidebar.image(LOGO_FILE, use_container_width=True)
st.sidebar.markdown(
    f"**FPL Syndicate** · Платформа `{APP_VERSION}`  \nСезон {SEASON_LABEL}"
)
st.sidebar.divider()

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
        _, _, payment_map, _, pin_map, manager_team_map = load_admin_sheet(
            CSV_URL
        )
    except Exception:
        payment_map, pin_map, manager_team_map = {}, {}, {}
    # Реальные H2H-матчи относятся к текущему сезону, а в Excel — прошлый,
    # поэтому здесь всегда используем сгенерированный календарь.
    h2h_matches_by_tier = {}
else:
    try:
        (
            team_ids, team_tier_map, payment_map, league_id_map, pin_map,
            manager_team_map,
        ) = load_admin_sheet(CSV_URL)
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

    # Реальные H2H-матчи по League_ID из админ-таблицы
    h2h_matches_by_tier = {}
    if league_id_map:
        h2h_errors = []
        with st.spinner("Загружаю матчи H2H-лиг..."):
            for tier, league_id in league_id_map.items():
                try:
                    m = fetch_h2h_matches(league_id)
                    if not m.empty:
                        h2h_matches_by_tier[tier] = m
                except requests.exceptions.RequestException as e:
                    h2h_errors.append(f"{tier} (ID {league_id}): {e}")
        if h2h_errors:
            st.warning(
                "Не удалось загрузить матчи лиг:\n\n- "
                + "\n- ".join(h2h_errors)
                + "\n\nДля них используется сгенерированный календарь."
            )
    else:
        st.info(
            f"Колонка {ADMIN_LEAGUE_ID_COL} в админ-таблице пуста — "
            "таблицы строятся по сгенерированному круговому календарю. "
            "Заполни её, чтобы использовать реальные матчи FPL."
        )

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

# Статус сезона: из API в онлайн-режиме, иначе по выбранному туру
api_mode = data_source != "Данные сезона 2025 (Excel)"
current_event = fetch_current_event() if api_mode else None
render_banner(season_status_chip(current_event, current_gw))

# ---------- Расчёт общих очков ----------

# Total считается по сыгранным турам — до выбранного слайдером тура
df["total_pts"] = sum_gw_range(df, 1, current_gw)

# Для таблиц лиг показываем максимум 5 последних туров, чтобы не раздувать вывод
gw_cols_all = get_gw_cols(df)
gw_cols_display = gw_cols_all[-5:]

H2H_COL_LABELS = {
    "team_name": "Команда",
    "manager_name": "Менеджер",
    "h2h_pts": "Очки H2H",
    "wins": "В",
    "draws": "Н",
    "losses": "П",
    "total_pts": "Total",
    "team_value": "Стоимость",
}

FPL_ENTRY_URL = "https://fantasy.premierleague.com/entry/{team_id}/event/1"

DISPLAY_COLS = (
    ["team_name", "manager_name", "h2h_pts", "wins", "draws", "losses"]
    + gw_cols_display
    + ["total_pts", "team_value"]
)


def league_table(data: pd.DataFrame, tier: str) -> pd.DataFrame:
    """H2H-таблица дивизиона: сортировка по очкам 3/1/0, затем по Total."""
    league_df = data[data["league_tier"] == tier]
    if league_df.empty:
        return pd.DataFrame()

    table = calculate_h2h_table(
        league_df, current_gw, h2h_matches_by_tier.get(tier)
    )
    table = (
        table.sort_values(
            ["h2h_pts", "total_pts"], ascending=[False, False]
        )
        .reset_index(drop=True)
    )
    table.index = table.index + 1  # место в таблице с 1
    cols = ["team_id"] + [c for c in DISPLAY_COLS if c != "team_id"]
    return table[cols].rename(columns=H2H_COL_LABELS)


def render_league_html(table: pd.DataFrame):
    """Таблица лиги с названием команды как ссылкой на профиль FPL."""
    show = table.drop(columns=["team_id"])
    header = "".join(f"<th>{c}</th>" for c in ["#"] + list(show.columns))

    body = []
    for place, (idx, row) in enumerate(show.iterrows(), start=1):
        team_id = int(table.loc[idx, "team_id"])
        url = FPL_ENTRY_URL.format(team_id=team_id)
        cells = []
        for col in show.columns:
            value = row[col]
            if col == "Команда":
                cells.append(
                    f'<td><a href="{url}" target="_blank" '
                    f'rel="noopener">{value}</a></td>'
                )
            elif isinstance(value, float):
                cells.append(
                    "<td>—</td>" if pd.isna(value) else f"<td>{value:.1f}</td>"
                )
            else:
                cells.append(f"<td>{value}</td>")
        body.append(f"<tr><td>{place}</td>{''.join(cells)}</tr>")

    st.markdown(
        '<div class="fpl-wrap"><table class="fpl-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def division_rating(data: pd.DataFrame) -> pd.DataFrame:
    """Рейтинг дивизионов по среднему TOTAL участников."""
    rows = []
    for league in ALL_LEAGUES:
        subset = data[data["league_tier"] == league]
        if subset.empty:
            continue
        rows.append(
            {
                "Дивизион": league,
                "Команд": len(subset),
                "Средний Total": round(subset["total_pts"].mean(), 1),
                "Медиана": int(subset["total_pts"].median()),
                "Лучший": int(subset["total_pts"].max()),
                "Худший": int(subset["total_pts"].min()),
            }
        )
    rating = (
        pd.DataFrame(rows)
        .sort_values("Средний Total", ascending=False)
        .reset_index(drop=True)
    )
    rating.index = rating.index + 1
    return rating


# Селектор менеджера для Финансового Хаба (поиск набором текста)
st.sidebar.header("Кошелёк менеджера")
selected_manager = st.sidebar.selectbox(
    "Менеджер",
    options=sorted(df["manager_name"].astype(str).unique()),
)

st.sidebar.header("Отображение")
compact = st.sidebar.toggle(
    "📱 Компактный режим (телефон)",
    value=True,
    help="Меньше колонок в таблицах, метрики в два ряда.",
)

# Наборы колонок таблиц лиг: полный для десктопа, ужатый для телефона
if compact:
    DISPLAY_COLS = ["team_name", "h2h_pts", "wins", "draws", "losses",
                    "total_pts"]
else:
    DISPLAY_COLS = (
        ["team_name", "manager_name", "h2h_pts", "wins", "draws", "losses"]
        + gw_cols_display
        + ["total_pts", "team_value"]
    )


def metric_grid(items, per_row):
    """Раскладывает метрики сеткой: на телефоне 2 в ряд, на десктопе шире."""
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col_widget, item in zip(cols, items[i:i + per_row]):
            label, value, delta = item
            if delta is None:
                col_widget.metric(label, value)
            else:
                col_widget.metric(label, value, delta, delta_color="off")


METRICS_PER_ROW = 2 if compact else 4

# ---------- Вывод: вкладки ----------

(
    tab_leagues, tab_social, tab_cups, tab_squid, tab_fame, tab_wallet
) = st.tabs(TAB_LABELS)

with tab_leagues:
    if h2h_matches_by_tier:
        calendar_note = (
            f"Реальные матчи FPL по {ADMIN_LEAGUE_ID_COL} из админ-таблицы "
            f"({len(h2h_matches_by_tier)} из {len(ALL_LEAGUES)} лиг)."
        )
    else:
        calendar_note = (
            "Календарь сгенерирован (круговой, каждый с каждым в два круга) — "
            "реальные матчи FPL не загружены."
        )
    st.caption(
        f"Таблицы H2H по состоянию на GW{current_gw}: 3 очка за победу в "
        "матче тура, 1 за ничью, 0 за поражение. При равенстве очков выше "
        f"команда с большим Total. {calendar_note} Название команды — ссылка "
        "на профиль в FPL."
    )

    # --- Сводный рейтинг дивизионов ---
    with st.expander("📊 Рейтинг дивизионов по среднему TOTAL", expanded=False):
        rating = division_rating(df)
        rating_cols = (
            ["Дивизион", "Средний Total", "Лучший"]
            if compact
            else ["Дивизион", "Команд", "Средний Total", "Медиана",
                  "Лучший", "Худший"]
        )
        st.dataframe(rating[rating_cols], use_container_width=True)
        if not rating.empty:
            st.caption(
                f"Сильнейший дивизион: {rating.iloc[0]['Дивизион']} "
                f"(средний Total {rating.iloc[0]['Средний Total']})."
            )

    # --- Быстрая навигация по дивизионам ---
    available = [
        lg for lg in ALL_LEAGUES if not df[df["league_tier"] == lg].empty
    ]
    if not available:
        st.info("Нет данных ни по одному дивизиону.")
    else:
        nav_options = available + ["Все лиги"]
        selected_league = st.radio(
            "Дивизион",
            options=nav_options,
            horizontal=True,
            label_visibility="collapsed",
        )
        to_show = available if selected_league == "Все лиги" else [selected_league]
        for league_name in to_show:
            table = league_table(df, league_name)
            if not table.empty:
                st.subheader(league_name)
                render_league_html(table)

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
        qual_cols = (
            ["team_name", "qual_pts", "Направление"]
            if compact
            else ["team_name", "manager_name", "league_tier", "qual_pts",
                  "Направление"]
        )
        qual_table = ranking[qual_cols].rename(
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

        if compact:
            # На телефоне показываем только очки текущей фазы
            if current_gw >= FINAL_START_GW:
                phase_col, phase_name = "final_pts", "Финал (GW36–38)"
            elif current_gw >= 31:
                phase_col, phase_name = "seg_31_35", "GW31–35"
            elif current_gw >= 26:
                phase_col, phase_name = "seg_26_30", "GW26–30"
            else:
                phase_col, phase_name = "seg_21_25", "GW21–25"
            cup_cols = {
                "team_name": "Команда",
                phase_col: phase_name,
                "status": "Статус",
            }
        else:
            cup_cols = CUP_DISPLAY_COLS

        def render_cup(cup_df):
            table = cup_df[list(cup_cols)].rename(columns=cup_cols)
            table = table.reset_index(drop=True)
            table.index = table.index + 1
            st.dataframe(table, use_container_width=True)

        st.header("Лига Чемпионов")
        render_cup(cl_df)
        st.header("Лига Конференций")
        render_cup(conf_df)

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
            "среднего — выбыл. Последний выживший забирает банк цикла."
        )

        metric_grid(
            [
                ("Цикл", f"№{last_round['cycle_num']}, Шаг {last_round['step']}", None),
                ("🟢 Живых", str(len(last_round["survivors"])), None),
                ("Средний балл", f"{last_round['avg']:.2f}", None),
                ("Банк цикла", f"{last_round['bank']:,} ₸".replace(",", " "), None),
            ],
            METRICS_PER_ROW,
        )

        if cycle_completed:
            winner_row = df.loc[last_round["winner_idx"]]
            st.success(
                f"Цикл №{last_round['cycle_num']} завершён на GW{gw}! "
                f"Победитель — {winner_row['team_name']} "
                f"({winner_row['manager_name']}), банк "
                f"{last_round['bank']:,} ₸".replace(",", " ")
                + ". Со следующего тура — новый цикл: все снова в игре."
            )

        if history:
            st.subheader("Победители прошлых циклов")
            hist_rows = []
            for h in history:
                w = df.loc[h["winner_idx"]]
                hist_rows.append(
                    {
                        "Цикл": h["cycle"],
                        "Победитель": w["manager_name"],
                        "Туры": f"GW{h['start_gw']}–{h['end_gw']}",
                        "Банк": f"{h['bank']:,} ₸".replace(",", " "),
                    }
                )
            hist_df = pd.DataFrame(hist_rows).set_index("Цикл")
            st.dataframe(hist_df, use_container_width=True)

        st.subheader(f"Участники GW{gw} (цикл №{last_round['cycle_num']})")
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
        squid_cols = (
            ["team_name", "gw_pts", "Статус"]
            if compact
            else ["team_name", "manager_name", "league_tier", "gw_pts", "Статус"]
        )
        table = (
            table.sort_values("gw_pts", ascending=False)
            .reset_index(drop=True)[squid_cols]
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

    st.header("🥇 Зал Славы — рекорды сезона")

    metric_grid(
        [
            (
                "🔥 Рекорд одного тура",
                f"{record['points']} очков",
                f"GW{record['gw']} — {record['manager']}",
            ),
            (
                "👑 Чемпион сезона (Total)",
                f"{champion['points']} очков",
                f"{champion['manager']} ({champion['league']})",
            ),
            (
                "📈 Лучший средний балл",
                f"{leaders.iloc[0]['Средний балл']}",
                f"{leaders.iloc[0]['Менеджер']} ({leaders.iloc[0]['Лига']})",
            ),
        ],
        min(METRICS_PER_ROW, 3),
    )

    st.subheader("Таблица лидеров (по среднему баллу)")
    leaders_cols = (
        ["Менеджер", "Total", "Средний балл"]
        if compact
        else ["Менеджер", "Команда", "Лига", "Total", "Средний балл",
              "Лучший тур", "Разброс (σ)"]
    )
    st.dataframe(leaders[leaders_cols].head(15), use_container_width=True)

with tab_wallet:
    st.header("💰 Финансовый Хаб")

    with st.expander(
        f"Призовой фонд сезона: {PRIZE_FUND_TOTAL:,} ₸".replace(",", " ")
    ):
        summary = prize_fund_summary()
        fund_sum = int(summary["Сумма, ₸"].sum())
        show = summary.copy()
        show["Сумма, ₸"] = show["Сумма, ₸"].map(
            lambda v: f"{v:,} ₸".replace(",", " ")
        )
        st.dataframe(show.set_index("Категория"), use_container_width=True)
        if fund_sum == PRIZE_FUND_TOTAL:
            st.caption(
                f"Итого: {fund_sum:,} ₸ — сходится с фондом.".replace(",", " ")
            )
        else:
            st.error(
                f"Сумма категорий {fund_sum:,} ₸ не равна фонду "
                f"{PRIZE_FUND_TOTAL:,} ₸ — проверь константы!".replace(",", " ")
            )

    payouts = calculate_prizes(df, current_gw)

    # --- Персональная карточка выбранного менеджера ---
    row = df[df["manager_name"].astype(str) == selected_manager]
    if not row.empty:
        m = row.iloc[0]
        m_idx = row.index[0]
        manager_payouts = payouts.get(m_idx, [])
        prize_balance = sum(amount for _, amount in manager_payouts)
        payment_status = payment_map.get(int(m["team_id"]), "н/д")

        st.subheader(f"{m['team_name']}")
        metric_grid(
            [
                ("Лига", m["league_tier"], None),
                ("Очки за сезон", str(int(m["total_pts"])), None),
                (
                    "Баланс призовых",
                    f"{prize_balance:,} ₸".replace(",", " "),
                    None,
                ),
                ("Статус взноса", payment_status, None),
            ],
            METRICS_PER_ROW,
        )

        if manager_payouts:
            st.markdown("**Начисления по категориям:**")
            payout_df = pd.DataFrame(
                manager_payouts, columns=["Категория", "Сумма"]
            )
            payout_df["Сумма"] = payout_df["Сумма"].map(
                lambda v: f"{v:,} ₸".replace(",", " ")
            )
            payout_df.index = payout_df.index + 1
            st.dataframe(payout_df, use_container_width=True)
        else:
            st.caption(
                f"К GW{current_gw} начислений нет. Двигай слайдер тура — "
                "призовые пересчитываются на выбранный тур."
            )

    # --- Общая таблица призовых по всем менеджерам ---
    st.subheader("Призовые всех менеджеров")
    st.caption(
        "Сортировка по сумме; нажми на заголовок колонки, чтобы "
        "пересортировать."
    )
    board_rows = []
    for idx, plist in payouts.items():
        board_rows.append(
            {
                "Менеджер": df.loc[idx, "manager_name"],
                "Команда": df.loc[idx, "team_name"],
                "Лига": df.loc[idx, "league_tier"],
                "Категорий": len(plist),
                "Итого, ₸": sum(a for _, a in plist),
            }
        )
    board = (
        pd.DataFrame(board_rows)
        .sort_values(["Итого, ₸", "Менеджер"], ascending=[False, True])
        .reset_index(drop=True)
    )
    board.index = board.index + 1
    board_cols = (
        ["Менеджер", "Категорий", "Итого, ₸"]
        if compact
        else ["Менеджер", "Команда", "Лига", "Категорий", "Итого, ₸"]
    )
    st.dataframe(board[board_cols], use_container_width=True)
    paid_total = int(board["Итого, ₸"].sum())
    st.caption(
        f"Начислено к GW{current_gw}: {paid_total:,} ₸ из "
        f"{PRIZE_FUND_TOTAL:,} ₸.".replace(",", " ")
    )

    st.caption(
        "Номинации, ожидающие данных: "
        + "; ".join(
            f"{name} ({amount:,} ₸ — {reason})".replace(",", " ")
            for name, amount, reason in PRIZE_PENDING
        )
        + "."
    )


with tab_social:
    st.session_state.setdefault("liked_posts", set())
    st.session_state.setdefault("demo_posts", [])

    sb_url, _sb_key = supabase_config()
    if not sb_url:
        st.warning(
            "⚠️ Работает в локальном демо-режиме: посты и лайки хранятся "
            "только в этой сессии и исчезнут при перезагрузке. Добавь ключи "
            "Supabase в `.streamlit/secrets.toml`, чтобы включить сохранение."
        )

    # --- Форма публикации с верификацией автора ---
    with st.expander("✍️ Написать пост / Опубликовать мем", expanded=False):
        if not pin_map:
            st.error(
                "Список участников 2026 недоступен (не загрузилась "
                "админ-таблица), поэтому верификация авторов не работает "
                "и публикация закрыта."
            )
        else:
            managers = sorted(pin_map.keys())
            author = st.selectbox("Автор (участник 2026)", options=managers)
            pin = st.text_input(
                "Введи PIN-код (последние 4 цифры вашего номера телефона)",
                type="password",
                max_chars=4,
            )

            expected = pin_map.get(author)
            verified = bool(pin) and pin.strip() == expected
            if verified:
                st.session_state["verified_manager"] = author
                st.success("✅ Автор верифицирован")
            elif pin:
                st.session_state.pop("verified_manager", None)
                st.error("❌ Неверный PIN-код")
            else:
                st.caption("Введи PIN, чтобы активировать публикацию.")

            category = st.radio(
                "Категория",
                options=POST_CATEGORIES,
                horizontal=not compact,
            )
            body = st.text_area("Текст сообщения", height=110)

            upload = st.file_uploader(
                "Прикрепить скриншот/мем",
                type=["png", "jpg", "jpeg"],
            )
            image_url_input = st.text_input(
                "…или вставь ссылку на картинку",
                placeholder="https://...",
            )

            if st.button(
                "Опубликовать 🚀", type="primary", disabled=not verified
            ):
                image_ref, img_error = encode_image(upload)
                if img_error:
                    st.error(img_error)
                elif not image_ref and image_url_input.strip():
                    image_ref = image_url_input.strip()

                if not body.strip() and not image_ref:
                    st.error("Пост пустой — добавь текст или картинку.")
                elif img_error is None:
                    post = {
                        "id": str(uuid.uuid4()),  # для демо-режима; в БД свой
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "manager_name": author,
                        "fpl_team_id": int(
                            manager_team_map.get(author, 0)
                        ),
                        "category": category,
                        COL_TEXT: body.strip(),
                        "image_url": image_ref,
                        "gw": int(current_gw),
                        COL_LIKES: 0,
                        "verified": True,
                    }
                    if insert_post(post):
                        st.success("Опубликовано 🚀")
                        st.rerun()

    # --- Лента ---
    feed_mode = st.radio(
        "Сортировка",
        options=["⏱️ Свежее", "🔥 Топ за тур", "🏆 Топ за всё время"],
        horizontal=True,
        label_visibility="collapsed",
    )

    posts = sort_posts(fetch_posts(), feed_mode, current_gw)

    if not posts:
        st.info(
            "Пока ни одного поста. Открой форму выше и стань первым 🚀"
            if feed_mode != "🔥 Топ за тур"
            else f"За GW{current_gw} постов пока нет."
        )
    else:
        st.caption(f"Постов в ленте: {len(posts)}")

    for post in posts:
        post_id = str(post.get("id", ""))
        author = str(post.get("manager_name", "—"))
        likes = int(post.get(COL_LIKES) or 0)
        created_raw = str(post.get("created_at") or "")
        stamp = created_raw[:16].replace("T", " ")
        post_gw = post.get("gw")
        initials = "".join(w[0] for w in author.split()[:2]).upper() or "?"

        with st.container(border=True):
            verified_badge = (
                '<span class="fpl-badge fpl-badge-ok">✅ Автор верифицирован</span>'
                if post.get("verified")
                else ""
            )
            gw_badge = (
                f'<span class="fpl-badge">GW{int(post_gw)}</span>'
                if post_gw else ""
            )
            st.markdown(
                f"""
                <div class="fpl-post-head">
                    <span class="fpl-avatar">{initials}</span>
                    <span class="fpl-author">{author}</span>
                    <span class="fpl-badge">{post.get("category", "💬 Чат")}</span>
                    {gw_badge}
                    {verified_badge}
                </div>
                <div class="fpl-post-meta">{stamp}</div>
                """,
                unsafe_allow_html=True,
            )

            text = post.get(COL_TEXT) or post.get("body")
            if text:
                st.markdown(
                    f'<div class="fpl-post-body">{text}</div>',
                    unsafe_allow_html=True,
                )

            render_post_image(post.get("image_url"))

            already_liked = post_id in st.session_state["liked_posts"]
            if st.button(
                f"❤️ {likes}",
                key=f"like_{post_id}",
                disabled=already_liked,
                help="Лайк можно поставить один раз за сессию",
            ):
                if like_post(post_id, likes):
                    st.session_state["liked_posts"].add(post_id)
                    st.rerun()


st.markdown(
    '<div class="fpl-footer">FPL Syndicate © 2026 | '
    'Разработано для участников Синдиката</div>',
    unsafe_allow_html=True,
)
