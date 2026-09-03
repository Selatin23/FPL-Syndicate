import base64
import concurrent.futures
import json
import os
import re
import threading
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

TAB_STATUS = "📊 Статус"
TAB_LEAGUES = "🏆 Лиги"
TAB_CABINET = "💼 Мой кабинет"
TAB_SOCIAL = "💬 Сообщество"
TAB_CUPS = "🌍 Еврокубки"
TAB_SQUID = "🦑 Squid Game"
TAB_FAME = "🥇 Зал Славы"
TAB_WALLET = "💰 Финансы"
TAB_EXCHANGE = "📈 Биржа"

TAB_LABELS = [
    TAB_STATUS, TAB_LEAGUES, TAB_CABINET, TAB_SOCIAL, TAB_CUPS, TAB_SQUID,
    TAB_FAME, TAB_WALLET, TAB_EXCHANGE,
]

# Человекочитаемые пути для GA4 (эмодзи в URL читать неудобно)
TAB_PATHS = {
    TAB_STATUS: "/status",
    TAB_LEAGUES: "/leagues",
    TAB_CABINET: "/dashboard",
    TAB_SOCIAL: "/community",
    TAB_CUPS: "/eurocups",
    TAB_SQUID: "/squid-game",
    TAB_FAME: "/hall-of-fame",
    TAB_WALLET: "/finance",
    TAB_EXCHANGE: "/exchange",
}

# ---------- Google Analytics 4 ----------

GA_DEFAULT_ID = "G-LTZ72RKJ6E"
GA_PLACEHOLDER = "G-XXXXXXXXXX"


def secret(key: str, default=None):
    """Безопасное чтение секрета: без secrets.toml st.secrets бросает
    StreamlitSecretNotFoundError, а не возвращает default."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def inject_ga():
    """Ставит тег gtag.js в <head> родительского документа.

    Компонент живёт в iframe, поэтому скрипт, вставленный «внутри себя»,
    робот Google на fpl-syndicate.streamlit.app не увидит. Поэтому тег
    добавляется в head родителя, а dataLayer заводится на его window.

    Возвращает (measurement_id, активен ли счётчик).
    """
    ga_id = secret("GA_MEASUREMENT_ID", GA_DEFAULT_ID)
    if not ga_id or ga_id == GA_PLACEHOLDER:
        return ga_id, False

    paths_json = json.dumps(TAB_PATHS, ensure_ascii=False)
    ga_code = f"""
<!-- Global site tag (gtag.js) - Google Analytics -->
<script>
(function () {{
  var GA_ID = '{ga_id}';
  var TAB_PATHS = {paths_json};

  // Работаем с родительским документом; если он недоступен
  // (кросс-доменные ограничения) — откатываемся на собственное окно.
  var win = window, doc = document;
  try {{
    if (window.parent && window.parent.document) {{
      win = window.parent;
      doc = window.parent.document;
    }}
  }} catch (e) {{}}

  var pageUrl;
  try {{ pageUrl = win.location.href; }}
  catch (e) {{ pageUrl = document.referrer || location.href; }}

  function gtag() {{ win.dataLayer.push(arguments); }}

  if (!win.__fplGaLoaded) {{
    win.__fplGaLoaded = true;
    win.dataLayer = win.dataLayer || [];
    win.gtag = gtag;

    // Сам тег — в <head> родителя, чтобы Google видел его на странице
    var src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_ID;
    if (!doc.querySelector('script[src="' + src + '"]')) {{
      var s = doc.createElement('script');
      s.async = true;
      s.src = src;
      (doc.head || doc.documentElement).appendChild(s);
    }}

    gtag('js', new Date());
    gtag('config', GA_ID, {{
      page_location: pageUrl,
      page_title: 'FPL Syndicate 2026'
    }});
  }}

  var send = win.gtag || gtag;

  function trackTab(label) {{
    var path = TAB_PATHS[label] || '/' + label;
    send('event', 'page_view', {{
      page_title: label,
      page_path: path,
      page_location: pageUrl.split('#')[0] + '#' + path.slice(1)
    }});
  }}

  try {{
    // Стартовый просмотр — один раз за загрузку страницы.
    // Дальнейшие переходы ловит обработчик кликов ниже, иначе каждый
    // rerun (движение слайдера) слал бы лишний page_view.
    if (!win.__fplGaInitialView) {{
      win.__fplGaInitialView = true;
      var active = doc.querySelector(
        'button[data-baseweb="tab"][aria-selected="true"]'
      );
      if (active) {{ trackTab(active.innerText.trim()); }}
    }}

    // Делегирование на body: переживает перерисовку вкладок при rerun.
    // Флаг не даёт навесить обработчик повторно.
    if (!win.__fplGaTabsBound && doc.body) {{
      win.__fplGaTabsBound = true;
      doc.body.addEventListener('click', function (ev) {{
        var btn = ev.target.closest
          ? ev.target.closest('button[data-baseweb="tab"]')
          : null;
        if (btn) {{ trackTab(btn.innerText.trim()); }}
      }}, true);
    }}
  }} catch (e) {{
    // Просмотры вкладок недоступны; базовый page_view уже отправлен.
  }}
}})();
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

    div[data-testid="stDataFrame"] {
        font-size: 0.85rem;
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid rgba(128, 128, 128, 0.22);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
    }
    /* Горизонтальный скролл таблиц на смартфонах без ломки вёрстки */
    div[data-testid="stDataFrame"] > div { max-width: 100%; overflow-x: auto; }
    div[data-testid="stDataFrameResizable"] { min-width: 0; }

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

    /* Кабинет менеджера — строгий брокерский стиль */
    .dash-card {
        border: 1px solid rgba(128, 128, 128, 0.28);
        border-radius: 10px;
        padding: 0.7rem 0.9rem;
        background: rgba(128, 128, 128, 0.05);
    }
    .dash-vs {
        border: 1px solid rgba(0, 223, 122, 0.4);
        border-radius: 10px;
        padding: 0.7rem 0.9rem;
        background: rgba(0, 223, 122, 0.06);
        font-weight: 700;
        font-size: 1.05rem;
        margin: 0.3rem 0 0.6rem 0;
    }
    .dash-diff { font-size: 0.88rem; line-height: 1.7; }
    .dash-diff-me { color: #16A34A; }
    .dash-diff-opp { color: #DC2626; }

    /* Стартовый дашборд «Статус» */
    .status-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin: 0.3rem 0 0.8rem 0;
    }
    .status-lg {
        flex: 1 1 0;
        min-width: 78px;
        border-radius: 10px;
        padding: 0.55rem 0.4rem;
        text-align: center;
        color: #0b1120;
        border: 1px solid rgba(0, 0, 0, 0.25);
    }
    .status-lg b { display: block; font-size: 0.92rem; line-height: 1.2; }
    .status-lg span { font-size: 0.72rem; opacity: 0.85; }
    .status-panel {
        border: 1px solid rgba(0, 223, 122, 0.28);
        border-radius: 12px;
        padding: 0.8rem 1rem;
        background: rgba(10, 20, 16, 0.35);
        height: 100%;
    }
    .status-panel h4 {
        margin: 0 0 0.6rem 0;
        font-size: 0.85rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #00DF7A;
    }
    .status-metrics { display: flex; flex-wrap: wrap; gap: 0.6rem; }
    .status-metric {
        flex: 1 1 40%;
        border-radius: 8px;
        padding: 0.45rem 0.6rem;
        background: rgba(0, 223, 122, 0.07);
        border: 1px solid rgba(0, 223, 122, 0.18);
    }
    .status-metric .lbl {
        font-size: 0.66rem; letter-spacing: 0.06em;
        text-transform: uppercase; opacity: 0.7;
    }
    .status-metric .val { font-size: 1.25rem; font-weight: 700; }
    .status-badge {
        display: inline-block;
        padding: 0.05rem 0.5rem;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 700;
        color: #0b1120;
        background: #FFD700;
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
            <p>Единый аналитический хаб 180 участников: Head-to-Head лиги,
            Еврокубки, цикличная «Игра в Кальмара» и прозрачный Финансовый Хаб
            с призовым фондом 1 800 000 ₸.</p>
            <div class="fpl-chips">
                <span class="fpl-chip">Статус: {status_chip}</span>
                <span class="fpl-chip">Сезон: {SEASON_LABEL}</span>
                <span class="fpl-chip">Призовой фонд: 1 800 000 ₸</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Все лиги турнира в иерархическом порядке — выводятся по мере наполнения
ALL_LEAGUES = [
    "Premier League", "A-1", "A-2", "B-1", "B-2", "B-3", "C-1", "C-2", "D",
]

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

# ---------- Призовая сетка сезона (итого 1 800 000 ₸) ----------

PRIZE_FUND_TOTAL = 1_800_000

# Лиги H2H: в каждой из 9 лиг
PRIZE_LEAGUE = {1: 100_000, 2: 40_000, 3: 10_000}          # 9 x 150 000 = 1 350 000

# Общий зачёт среди всех участников
PRIZE_ABSOLUTE = {1: 40_000, 2: 20_000, 3: 10_000}         # 70 000

# Еврокубки и Кубок (в сумме 150 000, чтобы фонд сошёлся к 1.8 млн)
PRIZE_CL_WINNER = 70_000       # Лига Чемпионов
PRIZE_CONF_WINNER = 40_000     # Лига Конференций
PRIZE_CUP_WINNER = 40_000      # Кубок

# Специальные номинации
PRIZE_SQUID_TOTAL = 57_000     # Squid Game: 1 500 x 38 туров
PRIZE_MOM_TOTAL = 100_000      # Лучший TM месяца: 10 000 x 10 месяцев
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


# ---------- Чтение админ-панели из Google Sheets ----------

@st.cache_data(ttl=60)
def load_admin_sheet(url: str):
    """Читает опубликованный CSV и возвращает
    (team_ids, team_tier_map, payment_map, league_id_map, pin_map,
     manager_team_map).

    league_id_map: {League_Tier: League_ID} — ID H2H-лиги в FPL.
    pin_map: {Manager_Name: последние 4 цифры FPL ID} — для верификации.
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

    # PIN = последние 4 цифры FPLID. Сам номер наружу не отдаём.
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


# Больше воркеров = быстрее, но выше риск, что FPL забанит по IP.
FPL_MAX_WORKERS = 15

_fpl_thread_local = threading.local()


def _fpl_session() -> requests.Session:
    """Отдельная HTTP-сессия на каждый поток.

    requests.Session не потокобезопасен, поэтому одну общую сессию делить
    между воркерами нельзя. Зато сессия внутри потока переиспользует
    TCP-соединение (keep-alive), что само по себе заметно ускоряет серию
    запросов к одному хосту.
    """
    session = getattr(_fpl_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=FPL_MAX_WORKERS,
            pool_maxsize=FPL_MAX_WORKERS,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _fpl_thread_local.session = session
    return session


@st.cache_data(ttl=600)  # кэш 10 минут, чтобы не дёргать API на каждый rerun
def fetch_fpl_data(team_ids: list[int], team_tier_map: dict) -> pd.DataFrame:
    """Загружает данные всех команд из FPL API параллельно.

    Раньше запросы шли последовательно: 180 команд x 2 запроса = 360 обращений
    друг за другом. Теперь их разбирает пул из FPL_MAX_WORKERS потоков.
    """

    def fetch_single_team(team_id):
        """Данные одной команды. Возвращает (строка, ошибка) — ровно одно из двух.

        Выполняется в рабочем потоке, поэтому здесь НЕЛЬЗЯ вызывать st.*:
        у потока нет контекста Streamlit, вызов молча потеряется или упадёт.
        Все сообщения показывает основной поток после сборки результатов.
        """
        session = _fpl_session()
        try:
            entry_resp = session.get(
                f"{FPL_BASE_URL}/entry/{team_id}/", timeout=10
            )
            entry_resp.raise_for_status()
            entry = entry_resp.json()

            history_resp = session.get(
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

            row = {
                "team_id": team_id,
                "manager_name": (
                    f"{entry.get('player_first_name', '')} "
                    f"{entry.get('player_last_name', '')}"
                ).strip(),
                "team_name": entry.get("name", f"Team {team_id}"),
                "league_tier": team_tier_map.get(team_id, "Premier League"),
                **gw_points,
                "team_value": team_value,
            }
            return row, None
        except requests.exceptions.RequestException as e:
            return None, f"ID {team_id}: {e}"
        except (ValueError, KeyError) as e:
            return None, f"ID {team_id}: неожиданный формат ответа ({e})"

    rows = []
    errors = []

    if team_ids:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=FPL_MAX_WORKERS
        ) as executor:
            # executor.map сохраняет порядок team_ids, поэтому таблица
            # собирается детерминированно — важно для стабильных тай-брейков.
            for row, error in executor.map(fetch_single_team, team_ids):
                if error:
                    errors.append(error)
                else:
                    rows.append(row)

    if errors:
        st.warning(
            "Не удалось загрузить часть команд:\n\n- " + "\n- ".join(errors)
        )

    if not rows:
        st.error(
            "Серверы FPL недоступны или ни одна команда не загрузилась. "
            "Попробуй обновить страницу через пару минут — "
            "данные кэшируются на 10 минут."
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
    try:
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
    except (requests.exceptions.RequestException, ValueError, KeyError):
        # Недоступная/закрытая/несуществующая лига — пустой результат,
        # вызывающий код откатится на сгенерированный календарь.
        return pd.DataFrame(
            columns=["event", "entry_1", "pts_1", "entry_2", "pts_2"]
        )

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


FORM_ICONS = {"W": "🟢W", "D": "⚪D", "L": "🔴L"}
FORM_LENGTH = 5


def calculate_h2h_table(
    league_df: pd.DataFrame,
    current_gw: int,
    matches: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """H2H-таблица дивизиона: 3 очка за победу, 1 за ничью, 0 за поражение.

    Если передан matches (реальные матчи из API) — считает по ним,
    иначе строит круговой календарь и берёт очки туров из league_df.

    Помимо очков возвращает две колонки для минималистичной вёрстки:
    - form: последние 5 исходов строкой ("🟢W ⚪D 🔴L ...");
    - next_opp: имя соперника на тур current_gw + 1 ("—", если календарь кончился).
    """
    if league_df.empty:
        return league_df.assign(
            h2h_pts=[], wins=[], draws=[], losses=[], form=[], next_opp=[]
        )

    idx_by_team = {int(t): i for i, t in league_df["team_id"].items()}
    name_by_idx = league_df["team_name"].to_dict()
    stats = {i: {"h2h_pts": 0, "wins": 0, "draws": 0, "losses": 0}
             for i in league_df.index}
    # История исходов: {индекс команды: [(тур, "W"|"D"|"L"), ...]}
    outcomes = {i: [] for i in league_df.index}

    def score(ia, ib, pa, pb, gw):
        if pa > pb:
            stats[ia]["h2h_pts"] += 3
            stats[ia]["wins"] += 1
            stats[ib]["losses"] += 1
            outcomes[ia].append((gw, "W"))
            outcomes[ib].append((gw, "L"))
        elif pb > pa:
            stats[ib]["h2h_pts"] += 3
            stats[ib]["wins"] += 1
            stats[ia]["losses"] += 1
            outcomes[ib].append((gw, "W"))
            outcomes[ia].append((gw, "L"))
        else:
            stats[ia]["h2h_pts"] += 1
            stats[ib]["h2h_pts"] += 1
            stats[ia]["draws"] += 1
            stats[ib]["draws"] += 1
            outcomes[ia].append((gw, "D"))
            outcomes[ib].append((gw, "D"))

    next_gw = current_gw + 1
    next_opp = {i: "—" for i in league_df.index}

    if matches is not None and not matches.empty:
        # --- Реальные результаты из API ---
        played = matches[matches["event"] <= current_gw]
        for row in played.itertuples():
            ia = idx_by_team.get(row.entry_1)
            ib = idx_by_team.get(row.entry_2)
            if ia is None or ib is None:
                continue  # соперник не из нашего синдиката
            score(ia, ib, row.pts_1, row.pts_2, row.event)

        if next_gw <= 38:
            for row in matches[matches["event"] == next_gw].itertuples():
                ia = idx_by_team.get(row.entry_1)
                ib = idx_by_team.get(row.entry_2)
                if ia is not None and ib is not None:
                    next_opp[ia] = name_by_idx[ib]
                    next_opp[ib] = name_by_idx[ia]
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
                score(ia, ib, pts.loc[ia], pts.loc[ib], gw)

        if next_gw <= 38:
            for team_a, team_b in schedule.get(next_gw, []):
                ia, ib = idx_by_team[team_a], idx_by_team[team_b]
                next_opp[ia] = name_by_idx[ib]
                next_opp[ib] = name_by_idx[ia]

    def form_string(idx):
        """Последние FORM_LENGTH исходов, от старых к новым."""
        rounds = sorted(outcomes[idx], key=lambda t: t[0])[-FORM_LENGTH:]
        if not rounds:
            return "—"
        return " ".join(FORM_ICONS[o] for _, o in rounds)

    result = league_df.copy()
    for field in ("h2h_pts", "wins", "draws", "losses"):
        result[field] = [stats[i][field] for i in result.index]
    result["form"] = [form_string(i) for i in result.index]
    result["next_opp"] = [next_opp[i] for i in result.index]
    return result


# ---------- Модуль «Зал Славы» ----------

def calculate_hall_of_fame(df: pd.DataFrame):
    """Рекорды сезона: лучший тур, чемпион по тоталу, топ по среднему баллу.

    До старта сезона очков ещё нет (нет GW-колонок или таблица пуста),
    поэтому record/champion могут быть None, а leaders — пустым DataFrame.
    Вызывающий код обязан это проверять.
    """
    cols = get_gw_cols(df)
    leaders_cols = [
        "Менеджер", "Команда", "Лига", "Total",
        "Средний балл", "Лучший тур", "Разброс (σ)",
    ]
    if df.empty or not cols:
        return None, None, pd.DataFrame(columns=leaders_cols)

    pts = df[cols]

    # Абсолютный рекорд очков за один тур
    best_per_team = pts.max(axis=1)
    if best_per_team.isna().all():
        # Все туры пустые — сезон ещё не стартовал
        return None, None, pd.DataFrame(columns=leaders_cols)

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
            "Лучший тур": best_per_team.fillna(0).astype(int),
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
    if record is not None:
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
    """Сводка призового фонда для проверки, что всё сходится к 1.8 млн."""
    rows = [
        ("Лиги H2H (9 лиг: 100/40/10 тыс.)", 9 * sum(PRIZE_LEAGUE.values())),
        ("Общий зачёт (40/20/10 тыс.)", sum(PRIZE_ABSOLUTE.values())),
        ("Лига Чемпионов", PRIZE_CL_WINNER),
        ("Лига Конференций", PRIZE_CONF_WINNER),
        ("Кубок", PRIZE_CUP_WINNER),
        ("Squid Game (1 500 x 38)", PRIZE_SQUID_TOTAL),
        ("Лучший TM месяца (10 000 x 10)", PRIZE_MOM_TOTAL),
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
    """Инкремент лайка (read-modify-write — для сообщества такого размера хватает)."""
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
    """Показывает картинку поста в компактном виде, не растягивая на всю ширину.

    На широком экране изображение занимает левую часть карточки (≈2/3),
    на телефоне компактный режим отдаёт ему всю доступную ширину.
    """
    if not image_ref:
        return
    try:
        data = (
            base64.b64decode(image_ref.split(",", 1)[1])
            if image_ref.startswith("data:")
            else image_ref
        )
    except Exception:
        st.caption("🖼️ Картинку не удалось отобразить.")
        return

    try:
        if compact:
            # Телефон: узкий экран, полная ширина карточки уместна
            st.image(data, use_container_width=True)
        else:
            # Десктоп: ограничиваем колонкой, чтобы картинка не разъезжалась
            img_col, _ = st.columns([2, 1])
            with img_col:
                st.image(data, use_container_width=True)
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


# ---------- Модуль «Биржа» (FPL Exchange) ----------

# element_type в FPL: 1 GK, 2 DEF, 3 MID, 4 FWD
POSITION_NAMES = {1: "Вратари", 2: "Защитники", 3: "Полузащитники", 4: "Нападающие"}
POSITION_SHORT = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@st.cache_data(ttl=600)
def fetch_exchange_data():
    """Игроки + средний FDR на 3 ближайших тура из FPL API.

    Возвращает (DataFrame, источник): источник — 'api' или 'demo'.
    В DataFrame: web_name, team_name, pos, now_cost, total_points,
    net_transfers, roi, fdr3.
    """
    try:
        boot = requests.get(
            f"{FPL_BASE_URL}/bootstrap-static/", timeout=12
        )
        boot.raise_for_status()
        boot = boot.json()

        fixtures = requests.get(
            f"{FPL_BASE_URL}/fixtures/", params={"future": 1}, timeout=12
        )
        fixtures.raise_for_status()
        fixtures = fixtures.json()

        return _build_exchange(boot, fixtures), "api"
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return _demo_exchange(), "demo"


def _team_fdr3(fixtures: list[dict]) -> dict:
    """Средний FDR по 3 ближайшим матчам для каждой команды.

    В fixtures difficulty указан отдельно для хозяев и гостей:
    team_h_difficulty относится к team_h, team_a_difficulty — к team_a.
    """
    from collections import defaultdict

    by_team = defaultdict(list)
    ordered = sorted(
        fixtures,
        key=lambda f: (f.get("event") is None, f.get("event") or 0,
                       f.get("kickoff_time") or ""),
    )
    for f in ordered:
        h, a = f.get("team_h"), f.get("team_a")
        if h is not None:
            by_team[h].append(f.get("team_h_difficulty"))
        if a is not None:
            by_team[a].append(f.get("team_a_difficulty"))

    fdr = {}
    for team_id, diffs in by_team.items():
        vals = [d for d in diffs[:3] if d is not None]
        if vals:
            fdr[team_id] = round(sum(vals) / len(vals), 2)
    return fdr


def _build_exchange(boot: dict, fixtures: list[dict]) -> pd.DataFrame:
    team_name = {t["id"]: t["name"] for t in boot["teams"]}
    fdr3 = _team_fdr3(fixtures)

    rows = []
    for e in boot["elements"]:
        cost = e["now_cost"] / 10  # цена приходит в десятых (55 -> 5.5)
        pts = e["total_points"]
        net = e["transfers_in_event"] - e["transfers_out_event"]
        rows.append(
            {
                "web_name": e["web_name"],
                "team_name": team_name.get(e["team"], "—"),
                "team_id": e["team"],
                "element_type": e["element_type"],
                "pos": POSITION_SHORT.get(e["element_type"], "?"),
                "now_cost": cost,
                "total_points": pts,
                "net_transfers": net,
                "roi": round(pts / cost, 2) if cost else 0.0,
                "fdr3": fdr3.get(e["team"]),
            }
        )
    df = pd.DataFrame(rows)
    return df


def _demo_exchange() -> pd.DataFrame:
    """Синтетические данные, когда FPL API недоступен (межсезонье/офлайн).

    Форма правдоподобна: цены 4.0–14.0, очки коррелируют с ценой, FDR 2–5.
    """
    import numpy as np

    rng = np.random.default_rng(2026)
    teams = [
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton",
        "Chelsea", "Crystal Palace", "Everton", "Fulham", "Ipswich",
        "Leicester", "Liverpool", "Man City", "Man Utd", "Newcastle",
        "Forest", "Southampton", "Spurs", "West Ham", "Wolves",
    ]
    names = [
        "Saka", "Palmer", "Salah", "Haaland", "Watkins", "Isak", "Foden",
        "Son", "Bruno", "Mbeumo", "Gordon", "Bowen", "Mateta", "Wood",
        "Cunha", "Rogers", "Semenyo", "Gakpo", "Rice", "Trippier",
    ]
    team_fdr = {t: round(rng.uniform(2, 5), 2) for t in teams}
    rows = []
    for i in range(200):
        et = int(rng.choice([1, 2, 3, 4], p=[0.1, 0.32, 0.4, 0.18]))
        team = teams[i % len(teams)]
        cost = round(float(rng.uniform(4.0, 14.0)), 1)
        pts = int(max(0, rng.normal(cost * 12, 25)))
        tin = int(rng.integers(0, 400_000))
        tout = int(rng.integers(0, 400_000))
        base = names[i % len(names)]
        rows.append(
            {
                "web_name": base if i < len(names) else f"{base}{i}",
                "team_name": team, "team_id": i % len(teams) + 1,
                "element_type": et, "pos": POSITION_SHORT[et],
                "now_cost": cost, "total_points": pts,
                "net_transfers": tin - tout,
                "roi": round(pts / cost, 2) if cost else 0.0,
                "fdr3": team_fdr[team],
            }
        )
    return pd.DataFrame(rows)


# ---------- Модуль «Мой кабинет» (Manager Dashboard) ----------

POSITION_LONG = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@st.cache_data(ttl=300)
def fetch_fpl_bootstrap():
    """bootstrap-static один раз: игроки, позиции, текущий тур. None при сбое."""
    try:
        resp = requests.get(f"{FPL_BASE_URL}/bootstrap-static/", timeout=12)
        resp.raise_for_status()
        return resp.json()
    except (requests.exceptions.RequestException, ValueError):
        return None


@st.cache_data(ttl=300)
def fetch_manager_summary(team_id: int) -> dict | None:
    """Банк, стоимость команды, свободные трансферы и последний состав.

    Собирает из entry/{id}/, entry/{id}/history/ и entry/{id}/event/{gw}/picks/.
    Возвращает None, если API недоступен.
    """
    try:
        entry = requests.get(
            f"{FPL_BASE_URL}/entry/{team_id}/", timeout=10
        )
        entry.raise_for_status()
        entry = entry.json()

        hist = requests.get(
            f"{FPL_BASE_URL}/entry/{team_id}/history/", timeout=10
        )
        hist.raise_for_status()
        hist_json = hist.json()
        current = hist_json.get("current", [])
        used_chips = hist_json.get("chips", [])

        gw = entry.get("current_event")
        bank = None
        squad = []
        if gw:
            picks = requests.get(
                f"{FPL_BASE_URL}/entry/{team_id}/event/{gw}/picks/",
                timeout=10,
            )
            if picks.ok:
                pj = picks.json()
                bank = pj.get("entry_history", {}).get("bank")
                squad = [p["element"] for p in pj.get("picks", [])]

        # Свободные трансферы FPL напрямую не отдаёт — оцениваем по истории.
        # На старте сезона (истории нет или сыгран один тур) накопить второй
        # трансфер физически негде, поэтому строго 1.
        free_transfers = None
        if current:
            if len(current) <= 1:
                free_transfers = 1
            else:
                # если в прошлом туре не было трансферов — накопился 2-й
                made = current[-1].get("event_transfers", 0)
                free_transfers = 1 if made > 0 else 2
            if bank is None:
                bank = current[-1].get("bank")
        else:
            free_transfers = 1

        return {
            "team_id": team_id,
            "manager": (
                f"{entry.get('player_first_name', '')} "
                f"{entry.get('player_last_name', '')}"
            ).strip(),
            "team_name": entry.get("name", f"Team {team_id}"),
            "gw": gw,
            "bank": (bank / 10) if bank is not None else None,
            "free_transfers": free_transfers,
            "squad": squad,
            "chips": used_chips,
            "overall_rank": entry.get("summary_overall_rank"),
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


# Стандартный набор фишек на сезон: Wildcard даётся дважды, остальные по разу
CHIP_ALLOWANCE = [
    ("wildcard", "Wildcard", 2),
    ("freehit", "Free Hit", 1),
    ("3xc", "Triple Captain", 1),
    ("bboost", "Bench Boost", 1),
]


def chip_inventory(used_chips):
    """Разбирает список использованных фишек из FPL API.

    Возвращает (использованные, оставшиеся) как списки строк для показа.
    В API каждая запись выглядит как {"name": "wildcard", "event": 5, ...}.
    """
    counts = {}
    for chip in used_chips or []:
        name = (chip.get("name") or "").lower() if isinstance(chip, dict) else ""
        if name:
            counts[name] = counts.get(name, 0) + 1

    used, left = [], []
    for key, label, allowance in CHIP_ALLOWANCE:
        spent = min(counts.get(key, 0), allowance)
        remaining = allowance - spent
        if spent:
            # Показываем туры, в которых фишку сыграли
            events = [
                str(c.get("event"))
                for c in (used_chips or [])
                if isinstance(c, dict)
                and (c.get("name") or "").lower() == key
                and c.get("event") is not None
            ]
            tag = f"{label} x{spent}" if spent > 1 else label
            if events:
                tag += f" (GW{', GW'.join(events)})"
            used.append(tag)
        if remaining:
            left.append(f"{label} x{remaining}" if remaining > 1 else label)

    return used, left


AI_INSIGHTS_TABLE = "ai_insights"
AI_FALLBACK = (
    "Разбор для этого тура ещё не сгенерирован. Он появляется после "
    "публикации календаря на следующий тур — обычно вскоре после дедлайна."
)


@st.cache_data(ttl=300)
def fetch_ai_insight(team_id: int, gw: int) -> str:
    """Готовый разбор от AI для команды на конкретный тур.

    Тексты генерируются заранее скриптом ai_generator.py и лежат в Supabase,
    поэтому здесь только быстрое чтение — никаких обращений к LLM во время
    отрисовки страницы. Если записи нет, возвращает текст-заглушку.
    """
    if not team_id or not gw:
        return AI_FALLBACK

    url, key = supabase_config()
    if not url:
        return AI_FALLBACK

    try:
        resp = requests.get(
            f"{url}/rest/v1/{AI_INSIGHTS_TABLE}",
            headers=_sb_headers(key),
            params={
                "select": "insight_text",
                "team_id": f"eq.{int(team_id)}",
                "gw": f"eq.{int(gw)}",
                "limit": "1",
            },
            timeout=10,
        )
        if not resp.ok:
            return AI_FALLBACK
        rows = resp.json()
        if rows and rows[0].get("insight_text"):
            return rows[0]["insight_text"]
    except (requests.exceptions.RequestException, ValueError, KeyError):
        pass
    return AI_FALLBACK


def player_lookup(boot: dict) -> dict:
    """{element_id: {name, pos, team, news, chance}}."""
    if not boot:
        return {}
    out = {}
    for e in boot["elements"]:
        out[e["id"]] = {
            "name": e["web_name"],
            "pos": POSITION_LONG.get(e["element_type"], "?"),
            "team": e["team"],
            "news": e.get("news") or "",
            "chance": e.get("chance_of_playing_this_round"),
        }
    return out


def squad_alerts(squad: list[int], players: dict) -> list[str]:
    """Игроки состава с травмой/дисквалификацией или новостью."""
    alerts = []
    for pid in squad:
        p = players.get(pid)
        if not p:
            continue
        chance = p["chance"]
        flagged = (chance is not None and chance < 100) or p["news"]
        if flagged:
            tag = f"{p['name']} ({p['pos']})"
            if chance is not None and chance < 100:
                tag += f" — {chance}% на выход"
            if p["news"]:
                tag += f": {p['news']}"
            alerts.append(tag)
    return alerts


def next_h2h_opponent(league_id: int, team_id: int, next_gw: int):
    """Соперник по H2H-лиге на предстоящий тур. (team_id соперника, имя) | None."""
    matches = fetch_h2h_matches(league_id)
    if matches.empty:
        return None
    upcoming = matches[matches["event"] == next_gw]
    for row in upcoming.itertuples():
        if row.entry_1 == team_id:
            return row.entry_2
        if row.entry_2 == team_id:
            return row.entry_1
    return None


def squad_diff(my_squad: list[int], opp_squad: list[int], players: dict):
    """(мои дифференциалы, дифференциалы соперника) как списки 'Имя (POS)'."""
    def fmt(ids):
        out = []
        for pid in ids:
            p = players.get(pid)
            if p:
                out.append(f"{p['name']} ({p['pos']})")
        return out

    mine = [p for p in my_squad if p not in set(opp_squad)]
    theirs = [p for p in opp_squad if p not in set(my_squad)]
    return fmt(mine), fmt(theirs)


# ---------- Выбор источника данных ----------

if os.path.exists(LOGO_FILE):
    st.sidebar.image(LOGO_FILE, use_container_width=True)
st.sidebar.markdown(
    f"**FPL Syndicate** · Платформа `{APP_VERSION}`  \nСезон {SEASON_LABEL}"
)
st.sidebar.divider()

# Единственный источник данных — реальный FPL API плюс админ-таблица
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

# Статус сезона берём напрямую из FPL API
current_event = fetch_current_event()
render_banner(season_status_chip(current_event, current_gw))

# ---------- Расчёт общих очков ----------

# Total считается по сыгранным турам — до выбранного слайдером тура
df["total_pts"] = sum_gw_range(df, 1, current_gw)

# Для таблиц лиг показываем максимум 5 последних туров, чтобы не раздувать вывод
gw_cols_all = get_gw_cols(df)
gw_cols_display = gw_cols_all[-5:]

FPL_ENTRY_URL = "https://fantasy.premierleague.com/entry/{team_id}/event/1"



def league_table(data: pd.DataFrame, tier: str) -> pd.DataFrame:
    """H2H-таблица дивизиона: сортировка по очкам 3/1/0, затем по Total."""
    league_df = data[data["league_tier"] == tier]
    if league_df.empty:
        return pd.DataFrame()

    table = calculate_h2h_table(
        league_df, current_gw, h2h_matches_by_tier.get(tier)
    )
    return (
        table.sort_values(["h2h_pts", "total_pts"], ascending=[False, False])
        .reset_index(drop=True)
    )


def form_series(row, gw: int, n: int = 5) -> list:
    """Очки за последние n туров — для мини-графика формы."""
    start = max(1, gw - n + 1)
    return [
        int(row.get(gw_col(i), 0) or 0)
        for i in range(start, gw + 1)
        if gw_col(i) in row.index
    ]


def render_league_table(table: pd.DataFrame, tier: str):
    """Минималистичная H2H-таблица в духе официального приложения АПЛ.

    Только шесть колонок: место, кликабельное имя команды, очки H2H,
    форма за 5 матчей, очки текущего тура и соперник на следующий.
    """
    gw_c = gw_col(current_gw)
    gw_points = (
        table[gw_c].fillna(0).astype(int).values
        if gw_c in table.columns
        else [0] * len(table)
    )

    # LinkColumn показывает в ячейке текст, извлечённый из URL регуляркой.
    # Кладём имя команды в якорь: FPL его игнорирует, а мы получаем
    # кликабельное имя команды вместо «голого» адреса.
    team_links = [
        f"{FPL_ENTRY_URL.format(team_id=int(t))}#{name}"
        for t, name in zip(table["team_id"], table["team_name"])
    ]

    view = pd.DataFrame(
        {
            "Pos": range(1, len(table) + 1),
            "Team": team_links,
            "Pts": table["h2h_pts"].values,
            "Form": table["form"].values,
            "GW": gw_points,
            "Next": table["next_opp"].values,
        }
    )

    config = {
        "Pos": st.column_config.NumberColumn(
            "Pos", width="small", alignment="center"
        ),
        "Team": st.column_config.LinkColumn(
            "Team",
            width="medium",
            display_text=r"#(.*)$",  # текст ссылки = имя команды из якоря
        ),
        "Pts": st.column_config.NumberColumn(
            "Pts",
            width="small",
            alignment="center",
            help="3 очка за победу, 1 за ничью, 0 за поражение",
        ),
        "Form": st.column_config.TextColumn(
            "Form",
            width="medium",
            alignment="center",
            help="Последние 5 матчей, от старых к новым",
        ),
        "GW": st.column_config.NumberColumn(
            f"GW{current_gw}",
            width="small",
            alignment="center",
            help="Очки, набранные в текущем туре",
        ),
        "Next": st.column_config.TextColumn(
            "Next", width="medium", help="Соперник в следующем туре"
        ),
    }

    st.dataframe(
        view,
        column_config=config,
        hide_index=True,
        use_container_width=True,
    )


RATING_COLUMNS = [
    "Дивизион", "Команд", "Средний Total", "Медиана", "Лучший", "Худший",
]


def division_rating(data: pd.DataFrame) -> pd.DataFrame:
    """Рейтинг дивизионов по среднему TOTAL участников.

    До старта сезона очков ещё нет, а в API-режиме данных может не быть вовсе,
    поэтому при отсутствии строк возвращаем пустой DataFrame с нужными
    колонками — иначе sort_values падал бы с KeyError.
    """
    rows = []
    for league in ALL_LEAGUES:
        subset = data[data["league_tier"] == league] if not data.empty else data
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

    if not rows:
        return pd.DataFrame(columns=RATING_COLUMNS)

    rating = (
        pd.DataFrame(rows, columns=RATING_COLUMNS)
        .sort_values("Средний Total", ascending=False)
        .reset_index(drop=True)
    )
    rating.index = rating.index + 1
    return rating


# ---------- Глобальная авторизация ----------

st.sidebar.header("🔐 Авторизация")


def logout():
    """Полный выход: чистим всё, что относится к сессии пользователя."""
    for key in ("logged_in", "current_user", "current_tier", "verified_manager"):
        st.session_state.pop(key, None)


if st.session_state.get("logged_in"):
    st.sidebar.success(f"👤 {st.session_state['current_user']}")
    if st.session_state.get("current_tier"):
        st.sidebar.caption(f"Дивизион: {st.session_state['current_tier']}")
    if st.sidebar.button("Выйти", use_container_width=True):
        logout()
        st.rerun()
elif not pin_map:
    st.sidebar.info(
        "Вход недоступен: не загрузился список участников из админ-таблицы."
    )
else:
    login_name = st.sidebar.selectbox(
        "Менеджер", options=sorted(pin_map.keys()), key="login_name"
    )
    login_pin = st.sidebar.text_input(
        "PIN-код (последние 4 цифры FPL ID)",
        type="password",
        max_chars=4,
        key="login_pin",
    )
    if st.sidebar.button("Войти", type="primary", use_container_width=True):
        if login_pin.strip() and login_pin.strip() == pin_map.get(login_name):
            st.session_state["logged_in"] = True
            st.session_state["current_user"] = login_name
            tier_rows = df.loc[
                df["manager_name"].astype(str) == login_name, "league_tier"
            ]
            st.session_state["current_tier"] = (
                tier_rows.iloc[0] if not tier_rows.empty else None
            )
            st.rerun()
        else:
            st.sidebar.error("❌ Неверный PIN-код")

current_user = st.session_state.get("current_user")
is_logged_in = bool(st.session_state.get("logged_in"))

st.sidebar.header("Отображение")
compact = st.sidebar.toggle(
    "📱 Компактный режим (телефон)",
    value=True,
    help="Меньше колонок в таблицах, метрики в два ряда.",
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
    tab_status, tab_leagues, tab_cabinet, tab_social, tab_cups, tab_squid,
    tab_fame, tab_wallet, tab_exchange,
) = st.tabs(TAB_LABELS)


def _lg_grad_color(rank: int, total: int) -> str:
    """Плавный переход от зелёного (лучшая лига) к красному (худшая)."""
    if total <= 1:
        return "#4ade80"
    t = rank / (total - 1)  # 0 у лучшей, 1 у худшей
    top = (74, 222, 128)      # #4ade80
    bot = (248, 113, 113)     # #f87171
    r = round(top[0] + (bot[0] - top[0]) * t)
    g = round(top[1] + (bot[1] - top[1]) * t)
    b = round(top[2] + (bot[2] - top[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _status_top_table(frame, value_col, value_fmt):
    """Топ-3 строки в единой раскладке: #, Лига, Команда, значение."""
    out = []
    for place, (_, row) in enumerate(frame.iterrows(), start=1):
        out.append(
            {
                "#": place,
                "Лига": row["league_tier"],
                "Команда": row["team_name"],
                value_col: value_fmt(row),
            }
        )
    table = pd.DataFrame(out)
    if not table.empty:
        table = table.set_index("#")
    return table


with tab_status:
    st.header("📊 Статус")
    st.caption(f"Сводка по состоянию на GW{current_gw}.")

    if df.empty:
        st.info("Данных пока нет — дашборд заполнится после старта сезона.")
    else:
        # ===== Ряд 1: рейтинг дивизионов =====
        st.subheader("Top H2H Average — рейтинг дивизионов")
        rating = division_rating(df)
        if rating.empty:
            st.info("Рейтинг появится после первого сыгранного тура.")
        else:
            n = len(rating)
            cards = []
            for i, (_, r) in enumerate(rating.iterrows()):
                color = _lg_grad_color(i, n)
                cards.append(
                    f'<div class="status-lg" style="background:{color}">'
                    f'<b>{r["Дивизион"]}</b>'
                    f'<span>{r["Средний Total"]:.1f} ср.</span></div>'
                )
            st.markdown(
                f'<div class="status-row">{"".join(cards)}</div>',
                unsafe_allow_html=True,
            )

        # ===== Ряд 2: Squid Game + Classic League =====
        c1, c2 = st.columns([1, 1])

        with c1:
            history, last_round = calculate_squid_game(df, current_gw)
            st.markdown('<div class="status-panel">', unsafe_allow_html=True)
            st.markdown("<h4>🦑 Squid Game</h4>", unsafe_allow_html=True)
            if last_round is None:
                st.caption("Расчёт станет доступен после первого тура.")
            else:
                total_players = len(df)
                alive_before = len(last_round["alive_before"])
                eliminated_earlier = total_players - alive_before
                dead = len(last_round["dead_now"]) + eliminated_earlier
                alive = len(last_round["survivors"])
                metrics = [
                    ("DEAD", str(dead)),
                    ("ALIVE", str(alive)),
                    ("AVERAGE PTS", f"{last_round['avg']:.1f}"),
                    ("BANK", f"{last_round['bank']:,} ₸".replace(",", " ")),
                ]
                cells = "".join(
                    f'<div class="status-metric"><div class="lbl">{lbl}</div>'
                    f'<div class="val">{val}</div></div>'
                    for lbl, val in metrics
                )
                st.markdown(
                    f'<div class="status-metrics">{cells}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        with c2:
            st.markdown('<div class="status-panel">', unsafe_allow_html=True)
            st.markdown(
                "<h4>🏆 Classic League — топ-3</h4>", unsafe_allow_html=True
            )
            top3 = df.nlargest(3, "total_pts")
            classic = _status_top_table(
                top3, "PTS", lambda r: int(r["total_pts"])
            )
            classic = classic.rename(columns={"Команда": "TEAM"})
            st.dataframe(classic, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

        # ===== Ряд 3: макс очков за тур + самая дорогая команда =====
        c3, c4 = st.columns([1, 1])

        with c3:
            st.markdown('<div class="status-panel">', unsafe_allow_html=True)
            st.markdown(
                '<h4>🔥 Максимум очков за тур '
                '<span class="status-badge">20k</span></h4>',
                unsafe_allow_html=True,
            )
            gw_c = gw_col(current_gw)
            if gw_c in df.columns and df[gw_c].notna().any():
                top_gw = df.nlargest(3, gw_c)
                gw_tbl = _status_top_table(
                    top_gw, "Очки", lambda r: int(r[gw_c])
                )
                st.dataframe(gw_tbl, use_container_width=True)
            else:
                st.caption(f"Очки за GW{current_gw} пока не сыграны.")
            st.markdown("</div>", unsafe_allow_html=True)

        with c4:
            st.markdown('<div class="status-panel">', unsafe_allow_html=True)
            st.markdown(
                '<h4>💎 Самая дорогая команда '
                '<span class="status-badge">20k</span></h4>',
                unsafe_allow_html=True,
            )
            if "team_value" in df.columns and df["team_value"].notna().any():
                top_val = (
                    df[df["team_value"].notna()]
                    .nlargest(3, "team_value")
                )
                val_tbl = _status_top_table(
                    top_val, "Цена", lambda r: f"{r['team_value']:.1f}m"
                )
                st.dataframe(val_tbl, use_container_width=True)
            else:
                st.caption(
                    "Стоимость составов появится, когда FPL отдаст данные "
                    "первого тура."
                )
            st.markdown("</div>", unsafe_allow_html=True)

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
        if rating.empty:
            st.info(
                "Рейтинг появится после первого сыгранного тура — "
                "пока очков ни у кого нет."
            )
        else:
            rating_cols = (
                ["Дивизион", "Средний Total", "Лучший"]
                if compact
                else ["Дивизион", "Команд", "Средний Total", "Медиана",
                      "Лучший", "Худший"]
            )
            st.dataframe(rating[rating_cols], use_container_width=True)
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
                render_league_table(table, league_name)

with tab_cabinet:
    st.header("💼 Мой кабинет")

    if not is_logged_in:
        st.warning(
            "Авторизуйтесь в боковом меню слева, чтобы открыть личный кабинет."
        )
    else:
        my_team_id = manager_team_map.get(current_user)
        my_tier = st.session_state.get("current_tier")
        boot = fetch_fpl_bootstrap()
        players = player_lookup(boot)

        # Текущий/предстоящий тур
        cur_gw = None
        if boot:
            for e in boot.get("events", []):
                if e.get("is_current"):
                    cur_gw = e["id"]
            if cur_gw is None:
                nxt = next((e for e in boot.get("events", []) if e.get("is_next")), None)
                cur_gw = (nxt["id"] - 1) if nxt else 0
        next_gw = (cur_gw or 0) + 1

        summary = fetch_manager_summary(my_team_id) if my_team_id else None

        if summary is None:
            st.info(
                "Данные FPL API недоступны (межсезонье или нет соединения). "
                "Личная статистика появится с началом сезона."
            )

        # === Блок 1: Радар команды ===
        st.subheader("Радар команды")
        rank = summary and summary.get("overall_rank")
        bank = summary and summary.get("bank")
        fts = summary and summary.get("free_transfers")
        metric_grid(
            [
                (
                    "Overall Rank",
                    f"{rank:,}".replace(",", " ") if rank else "—",
                    None,
                ),
                (
                    "Банк",
                    f"{bank:.1f}m" if bank is not None else "—",
                    None,
                ),
                (
                    "Свободные трансферы",
                    str(fts) if fts is not None else "—",
                    None,
                ),
            ],
            min(3, METRICS_PER_ROW),
        )

        if summary and summary.get("squad"):
            alerts = squad_alerts(summary["squad"], players)
            if alerts:
                st.error(
                    "⚠️ Проблемные игроки в составе:\n\n- "
                    + "\n- ".join(alerts)
                )
            else:
                st.success("✅ Весь состав доступен — травм и дисквалификаций нет.")

        # === Блок 2: H2H Скаутинг ===
        st.subheader("H2H Скаутинг")

        opp_id = None
        league_id = league_id_map.get(my_tier) if my_tier else None
        if league_id and my_team_id:
            opp_id = next_h2h_opponent(league_id, my_team_id, next_gw)

        if not league_id:
            st.info(
                "H2H-скаутинг недоступен: для дивизиона не задан League_ID "
                "в админ-таблице."
            )
        elif opp_id is None:
            st.info(f"Соперник на GW{next_gw} ещё не определён календарём лиги.")
        else:
            opp = fetch_manager_summary(opp_id)
            opp_name = opp["manager"] if opp else f"Team {opp_id}"
            st.markdown(
                f'<div class="dash-vs">⚔️ Твой соперник в GW{next_gw}: '
                f"{opp_name}</div>",
                unsafe_allow_html=True,
            )

            if opp:
                o1, o2 = st.columns(2)
                o1.metric(
                    "Банк соперника",
                    f"{opp['bank']:.1f}m" if opp.get("bank") is not None else "—",
                )
                o2.metric(
                    "Трансферы соперника",
                    str(opp["free_transfers"])
                    if opp.get("free_transfers") is not None else "—",
                )

                # Фишки соперника: что уже потрачено и что ещё в запасе
                chips_used, chips_left = chip_inventory(opp.get("chips"))
                ch1, ch2 = st.columns(2)
                with ch1:
                    st.markdown("**🎲 Фишки использованы**")
                    st.markdown(
                        '<div class="dash-diff dash-diff-opp">'
                        + ("<br>".join(chips_used) if chips_used else "пока ни одной")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                with ch2:
                    st.markdown("**🃏 Фишки в запасе**")
                    st.markdown(
                        '<div class="dash-diff dash-diff-me">'
                        + ("<br>".join(chips_left) if chips_left else "все потрачены")
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                # Дифференциалы составов
                if summary and summary.get("squad") and opp.get("squad"):
                    mine, theirs = squad_diff(
                        summary["squad"], opp["squad"], players
                    )
                    d1, d2 = st.columns(2)
                    with d1:
                        st.markdown("**✅ Мои угрозы**")
                        st.caption("Игроки, которых нет у соперника")
                        st.markdown(
                            '<div class="dash-diff dash-diff-me">'
                            + ("<br>".join(mine) if mine else "—")
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                    with d2:
                        st.markdown("**⚠️ Опасность**")
                        st.caption("Игроки соперника, которых нет у меня")
                        st.markdown(
                            '<div class="dash-diff dash-diff-opp">'
                            + ("<br>".join(theirs) if theirs else "—")
                            + "</div>",
                            unsafe_allow_html=True,
                        )

        # === Блок 3: AI-Аналитика ===
        st.subheader("AI-Аналитика")
        insight = fetch_ai_insight(my_team_id, next_gw)
        st.info(f"🤖 **Мнение AI-ассистента на GW{next_gw}**\n\n{insight}")
        st.caption(
            "Разбор готовится заранее пакетным скриптом, поэтому "
            "открывается мгновенно и не зависит от нагрузки на AI-сервис."
        )

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

        # Числа берём из фактического размера сетки, чтобы текст не устаревал
        # при изменении числа лиг или константы отсева.
        _cl_total = len(cl_df)
        _finalists = max(_cl_total - ELIMINATED_PER_ROUND * len(ELIMINATION_GWS),
                         ELIMINATED_PER_ROUND)
        st.caption(
            f"Плей-офф: стартуют {_cl_total} команд в каждом турнире. "
            f"На GW{', GW'.join(str(g) for g in ELIMINATION_GWS)} выбывает "
            f"по {ELIMINATED_PER_ROUND} команд с худшей суммой за 5-туровый "
            f"отрезок. Финал среди {_finalists} — побеждает лучшая сумма за "
            f"GW{FINAL_START_GW}–{FINAL_END_GW}."
        )

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
        table = table.sort_values("gw_pts", ascending=False).reset_index(
            drop=True
        )

        squid_view = pd.DataFrame(
            {
                "Место": range(1, len(table) + 1),
                "Команда": table["team_name"].values,
                "Менеджер": table["manager_name"].values,
                "Лига": table["league_tier"].values,
                "Очки тура": table["gw_pts"].values,
                "Форма": [
                    form_series(row, gw) for _, row in table.iterrows()
                ],
                "Статус": table["Статус"].values,
            }
        )

        max_gw_pts = max(int(squid_view["Очки тура"].max()), 1)
        squid_config = {
            "Место": st.column_config.NumberColumn("#", width="small"),
            "Команда": st.column_config.TextColumn("Команда", width="medium"),
            "Менеджер": st.column_config.TextColumn("Менеджер", width="medium"),
            "Лига": st.column_config.TextColumn("Лига", width="small"),
            "Очки тура": st.column_config.ProgressColumn(
                f"Очки GW{gw}",
                help=f"Порог выживания — {last_round['avg']:.2f}",
                format="%d",
                min_value=0,
                max_value=max_gw_pts,
            ),
            "Форма": st.column_config.LineChartColumn(
                "Форма (5 туров)", y_min=0
            ),
            "Статус": st.column_config.TextColumn("Статус", width="medium"),
        }

        if compact:
            drop = ["Менеджер", "Лига"]
            squid_view = squid_view.drop(columns=drop)
            squid_config = {
                k: v for k, v in squid_config.items() if k not in drop
            }

        def color_status(val):
            if "WINNER" in str(val):
                return "background-color: rgba(255, 215, 0, 0.3)"
            if val == "ALIVE":
                return "background-color: rgba(0, 223, 122, 0.18)"
            return "background-color: rgba(220, 60, 60, 0.16)"

        st.dataframe(
            squid_view.style.map(color_status, subset=["Статус"]),
            column_config=squid_config,
            hide_index=True,
            use_container_width=True,
        )

with tab_fame:
    record, champion, leaders = calculate_hall_of_fame(df)

    st.header("🥇 Зал Славы — рекорды сезона")

    if record is None or leaders.empty:
        st.info(
            "Рекорды появятся после первого сыгранного тура — "
            "пока статистики нет."
        )
    else:
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

    # --- Персональная карточка авторизованного менеджера ---
    if not is_logged_in:
        st.info(
            "Авторизуйтесь в боковом меню слева, чтобы увидеть свой кошелёк. "
            "Общая таблица призовых доступна ниже."
        )
        row = df.iloc[0:0]
    else:
        row = df[df["manager_name"].astype(str) == current_user]
        if row.empty:
            st.info(
                f"{current_user} не найден в данных сезона — "
                "персональная карточка недоступна."
            )

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
    st.subheader("Прогноз призовых всех менеджеров")
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
                "Категории": ", ".join(c for c, _ in plist) or "—",
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
        ["Менеджер", "Категории", "Итого, ₸"]
        if compact
        else ["Менеджер", "Команда", "Лига", "Категории", "Итого, ₸"]
    )
    st.dataframe(board[board_cols], use_container_width=True)
    paid_total = int(board["Итого, ₸"].sum())
    st.caption(
        f"Начислено к GW{current_gw}: {paid_total:,} ₸ из "
        f"{PRIZE_FUND_TOTAL:,} ₸.".replace(",", " ")
    )

    st.caption(
        "Номинации, ожидающие результаты: "
        + "; ".join(name for name, _, _ in PRIZE_PENDING)
        + ". Эти номинации будут внесены в таблицу по фактическим "
        "результатам."
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

    # --- Форма публикации: автор берётся из глобальной авторизации ---
    if not is_logged_in:
        st.warning(
            "Пожалуйста, авторизуйтесь в боковом меню слева, "
            "чтобы писать посты."
        )
    else:
        with st.expander("✍️ Написать пост / Опубликовать мем", expanded=False):
            st.caption(f"Публикация от имени: **{current_user}**")

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

            if st.button("Опубликовать 🚀", type="primary"):
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
                        "manager_name": current_user,
                        "fpl_team_id": int(
                            manager_team_map.get(current_user, 0)
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


with tab_exchange:
    st.header("📈 FPL Exchange")
    st.caption(
        "Игроки как активы: форма, спрос рынка (нетто-трансферы), отдача на "
        "цену (ROI) и сложность ближайших матчей (FDR). Данные — из FPL API."
    )

    exch_df, exch_src = fetch_exchange_data()

    if exch_src == "demo":
        st.warning(
            "⚠️ FPL API недоступен (межсезонье или нет соединения) — показаны "
            "демонстрационные данные. В сезоне здесь будут реальные игроки."
        )

    if exch_df.empty:
        st.info("Нет данных для отображения.")
    else:
        # --- Market Movers ---
        st.subheader("Market Movers")
        movers_in = exch_df.nlargest(3, "net_transfers")
        movers_out = exch_df.nsmallest(3, "net_transfers")

        st.markdown("**🟢 Лидеры закупок (нетто-трансферы за тур)**")
        metric_grid(
            [
                (
                    f"{r['web_name']} ({r['pos']})",
                    f"+{int(r['net_transfers']):,}".replace(",", " "),
                    f"{r['team_name']} · {r['now_cost']}m",
                )
                for _, r in movers_in.iterrows()
            ],
            min(3, METRICS_PER_ROW),
        )

        st.markdown("**🔴 Лидеры продаж**")
        metric_grid(
            [
                (
                    f"{r['web_name']} ({r['pos']})",
                    f"{int(r['net_transfers']):,}".replace(",", " "),
                    f"{r['team_name']} · {r['now_cost']}m",
                )
                for _, r in movers_out.iterrows()
            ],
            min(3, METRICS_PER_ROW),
        )

        # --- Скринер ---
        st.subheader("Скринер")

        positions = st.multiselect(
            "Позиции",
            options=list(POSITION_NAMES.values()),
            default=list(POSITION_NAMES.values()),
        )
        sel_types = [k for k, v in POSITION_NAMES.items() if v in positions]

        pmin = float(exch_df["now_cost"].min())
        pmax = float(exch_df["now_cost"].max())
        price_range = st.slider(
            "Диапазон цены, m",
            min_value=round(pmin, 1),
            max_value=round(pmax, 1),
            value=(round(pmin, 1), round(pmax, 1)),
            step=0.1,
        )

        max_fdr = st.slider(
            "Максимальный FDR (меньше — легче календарь)",
            min_value=2.0, max_value=5.0, value=5.0, step=0.1,
        )

        screened = exch_df[
            exch_df["element_type"].isin(sel_types)
            & exch_df["now_cost"].between(price_range[0], price_range[1])
        ].copy()
        # FDR может отсутствовать (нет будущих матчей) — такие не режем фильтром
        screened = screened[
            screened["fdr3"].isna() | (screened["fdr3"] <= max_fdr)
        ]
        screened = screened.sort_values(
            ["roi", "total_points"], ascending=[False, False]
        )

        st.caption(f"Найдено активов: {len(screened)}")

        view = pd.DataFrame(
            {
                "Игрок": screened["web_name"].values,
                "Клуб": screened["team_name"].values,
                "Поз": screened["pos"].values,
                "Цена": screened["now_cost"].values,
                "Очки": screened["total_points"].values,
                "Net Transfers": screened["net_transfers"].values,
                "ROI": screened["roi"].values,
                "FDR (Next 3 GW)": screened["fdr3"].values,
            }
        )

        max_roi = max(float(view["ROI"].max()) if len(view) else 1.0, 0.1)
        exch_config = {
            "Игрок": st.column_config.TextColumn("Игрок", width="medium"),
            "Клуб": st.column_config.TextColumn("Клуб", width="small"),
            "Поз": st.column_config.TextColumn("Поз", width="small"),
            "Цена": st.column_config.NumberColumn("Цена", format="%.1fm"),
            "Очки": st.column_config.NumberColumn("Очки", format="%d"),
            "Net Transfers": st.column_config.NumberColumn(
                "Net Transfers", format="%+d",
                help="Нетто-трансферы за тур: закупки минус продажи",
            ),
            "ROI": st.column_config.ProgressColumn(
                "ROI", help="Очки на 1m цены", format="%.2f",
                min_value=0, max_value=max_roi,
            ),
            "FDR (Next 3 GW)": st.column_config.NumberColumn(
                "FDR (Next 3 GW)", format="%.2f",
                help="Средняя сложность 3 ближайших матчей — меньше лучше",
            ),
        }

        if compact:
            drop = ["Клуб", "Очки"]
            view = view.drop(columns=drop)
            exch_config = {k: v for k, v in exch_config.items() if k not in drop}

        st.dataframe(
            view, column_config=exch_config,
            hide_index=True, use_container_width=True,
        )


st.markdown(
    '<div class="fpl-footer">FPL Syndicate © 2026 | '
    'Разработано для участников Синдиката</div>',
    unsafe_allow_html=True,
)
