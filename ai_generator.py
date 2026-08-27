#!/usr/bin/env python3
"""Пакетная пре-генерация AI-разборов для FPL Syndicate.

Запускается отдельно от приложения (обычно по cron вскоре после дедлайна
тура), генерирует персональный разбор H2H-матча для каждого менеджера и
складывает результат в таблицу Supabase `ai_insights`. Streamlit-приложение
потом просто читает готовый текст — никаких обращений к LLM во время
отрисовки страницы.

Использование:
    python ai_generator.py                 # автоопределение тура из FPL API
    python ai_generator.py --gw 15         # конкретный тур
    python ai_generator.py --limit 5 --dry-run   # прогон без записи в БД

Переменные окружения (или файл .env рядом со скриптом):
    ZHIPU_API_KEY          ключ Zhipu AI (open.bigmodel.cn)
    SUPABASE_URL           https://<проект>.supabase.co
    SUPABASE_SERVICE_KEY   service_role-ключ (НЕ anon — anon не имеет прав
                           на запись в ai_insights)
    ADMIN_CSV_URL          опубликованный CSV админ-таблицы
    ZHIPU_MODEL            необязательно, по умолчанию glm-4-flash
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

try:
    from openai import OpenAI
except ImportError:
    sys.exit(
        "Не установлен пакет openai. Установи его:\n"
        "    pip install openai pandas requests"
    )

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

FPL_BASE_URL = "https://fantasy.premierleague.com/api"
ZHIPU_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.1-8b-instant"

AI_TABLE = "ai_insights"

# Воркеры для сети и для LLM разведены: у FPL и у Zhipu разные лимиты,
# и упереться в рейт-лимит модели гораздо неприятнее.
FPL_WORKERS = 15
LLM_WORKERS = 6

# Позиции игроков в FPL: 1 GK, 2 DEF, 3 MID, 4 FWD
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

ADMIN_TEAM_COL = "FPL_Team_ID"
ADMIN_TIER_COL = "League_Tier"
ADMIN_LEAGUE_ID_COL = "League_ID"


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def load_dotenv_if_present() -> None:
    """Простейший разбор .env рядом со скриптом — без внешних зависимостей."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# ---------------------------------------------------------------------------
# HTTP: отдельная сессия на поток (requests.Session не потокобезопасен)
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=FPL_WORKERS, pool_maxsize=FPL_WORKERS
        )
        s.mount("https://", adapter)
        _thread_local.session = s
    return s


# ---------------------------------------------------------------------------
# Загрузка исходных данных
# ---------------------------------------------------------------------------

@dataclass
class Manager:
    team_id: int
    tier: str
    manager: str = ""
    team_name: str = ""
    bank: float | None = None
    free_transfers: int | None = None
    squad: list[int] = field(default_factory=list)


def load_admin_sheet(csv_url: str):
    """(список менеджеров, {дивизион: league_id}) из опубликованного CSV."""
    raw = pd.read_csv(csv_url)
    raw.columns = [str(c).strip() for c in raw.columns]

    missing = [c for c in (ADMIN_TEAM_COL, ADMIN_TIER_COL) if c not in raw.columns]
    if missing:
        raise ValueError(
            f"В админ-таблице нет колонок: {', '.join(missing)}"
        )

    raw[ADMIN_TEAM_COL] = pd.to_numeric(raw[ADMIN_TEAM_COL], errors="coerce")
    raw = raw.dropna(subset=[ADMIN_TEAM_COL])

    managers = [
        Manager(team_id=int(r[ADMIN_TEAM_COL]), tier=str(r[ADMIN_TIER_COL]).strip())
        for _, r in raw.iterrows()
    ]

    league_ids: dict[str, int] = {}
    if ADMIN_LEAGUE_ID_COL in raw.columns:
        ids = pd.to_numeric(raw[ADMIN_LEAGUE_ID_COL], errors="coerce")
        for tier, group in ids.groupby(raw[ADMIN_TIER_COL]):
            valid = group.dropna()
            if not valid.empty:
                league_ids[str(tier).strip()] = int(valid.iloc[0])

    return managers, league_ids


def fetch_bootstrap() -> dict:
    resp = session().get(f"{FPL_BASE_URL}/bootstrap-static/", timeout=20)
    resp.raise_for_status()
    return resp.json()


def detect_next_gw(boot: dict) -> int:
    """Ближайший несыгранный тур: is_next, иначе current + 1."""
    events = boot.get("events", [])
    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    if nxt:
        return int(nxt)
    cur = next((e["id"] for e in events if e.get("is_current")), None)
    if cur:
        return int(cur) + 1
    finished = [e["id"] for e in events if e.get("finished")]
    return (max(finished) + 1) if finished else 1


def player_lookup(boot: dict) -> dict[int, dict]:
    return {
        e["id"]: {"name": e["web_name"], "pos": POSITIONS.get(e["element_type"], "?")}
        for e in boot.get("elements", [])
    }


def fetch_manager(m: Manager, current_gw: int) -> Manager:
    """Догружает банк, трансферы и состав. Ошибки не фатальны."""
    s = session()
    try:
        entry = s.get(f"{FPL_BASE_URL}/entry/{m.team_id}/", timeout=15)
        entry.raise_for_status()
        entry = entry.json()
        m.manager = (
            f"{entry.get('player_first_name', '')} "
            f"{entry.get('player_last_name', '')}"
        ).strip()
        m.team_name = entry.get("name", f"Team {m.team_id}")

        hist = s.get(f"{FPL_BASE_URL}/entry/{m.team_id}/history/", timeout=15)
        hist.raise_for_status()
        current = hist.json().get("current", [])

        if current:
            last = current[-1]
            # Тот же расчёт, что в приложении: на старте сезона накопить
            # второй трансфер физически негде.
            if len(current) <= 1:
                m.free_transfers = 1
            else:
                m.free_transfers = 1 if last.get("event_transfers", 0) > 0 else 2
            if last.get("bank") is not None:
                m.bank = last["bank"] / 10
        else:
            m.free_transfers = 1

        gw = entry.get("current_event") or max(current_gw - 1, 1)
        picks = s.get(
            f"{FPL_BASE_URL}/entry/{m.team_id}/event/{gw}/picks/", timeout=15
        )
        if picks.ok:
            pj = picks.json()
            m.squad = [p["element"] for p in pj.get("picks", [])]
            bank = pj.get("entry_history", {}).get("bank")
            if bank is not None:
                m.bank = bank / 10
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"  ! команда {m.team_id}: не удалось загрузить ({e})")
    return m


def fetch_h2h_pairs(league_id: int, gw: int) -> list[tuple[int, int]]:
    """Пары соперников конкретного тура в H2H-лиге."""
    pairs, page = [], 1
    s = session()
    try:
        while page <= 50:
            resp = s.get(
                f"{FPL_BASE_URL}/leagues-h2h-matches/league/{league_id}/",
                params={"page": page},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            for match in data.get("results", []):
                if match.get("is_bye") or match.get("event") != gw:
                    continue
                a, b = match.get("entry_1_entry"), match.get("entry_2_entry")
                if a and b:
                    pairs.append((int(a), int(b)))
            if not data.get("has_next"):
                break
            page += 1
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  ! лига {league_id}: матчи не загрузились ({e})")
    return pairs


def build_pairs(managers, league_ids, gw) -> dict[int, int]:
    """{team_id: team_id соперника} на тур gw.

    Берёт реальный календарь FPL, а при отсутствии League_ID честно
    оставляет дивизион без разборов — выдумывать соперника нельзя.
    """
    opponents: dict[int, int] = {}
    tiers = sorted({m.tier for m in managers})
    for tier in tiers:
        league_id = league_ids.get(tier)
        if not league_id:
            print(f"  ! дивизион {tier}: нет League_ID, пропускаем")
            continue
        for a, b in fetch_h2h_pairs(league_id, gw):
            opponents[a] = b
            opponents[b] = a
    return opponents


def squad_diff(mine: list[int], theirs: list[int], players: dict) -> tuple[str, str]:
    """Дифференциалы составов в виде читаемых строк для промпта."""
    def fmt(ids):
        names = [
            f"{players[p]['name']} ({players[p]['pos']})"
            for p in ids
            if p in players
        ]
        return ", ".join(names[:8]) if names else "нет"

    their_set, my_set = set(theirs), set(mine)
    return (
        fmt([p for p in mine if p not in their_set]),
        fmt([p for p in theirs if p not in my_set]),
    )


# ---------------------------------------------------------------------------
# Генерация текста
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "Ты скаут FPL. У менеджера {bank} в банке и {fts} трансферов. "
    "У соперника {opp_bank} и {opp_fts}. "
    "Твои дифференциалы: {mine}. Угрозы соперника: {theirs}. "
    "Напиши 2 коротких предложения аналитики. Укажи главную угрозу и "
    "неочевидную возможность. Без приветствий."
)


def build_prompt(me: Manager, opp: Manager, players: dict) -> str:
    mine, theirs = squad_diff(me.squad, opp.squad, players)
    fmt_bank = lambda v: f"{v:.1f}m" if v is not None else "неизвестно"
    fmt_fts = lambda v: str(v) if v is not None else "неизвестно"
    return PROMPT_TEMPLATE.format(
        bank=fmt_bank(me.bank),
        fts=fmt_fts(me.free_transfers),
        opp_bank=fmt_bank(opp.bank),
        opp_fts=fmt_fts(opp.free_transfers),
        mine=mine,
        theirs=theirs,
    )


def generate_insight(client, model: str, prompt: str, retries: int = 3) -> str | None:
    """Один запрос к модели с повторами при рейт-лимите."""
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=220,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
            return None
        except Exception as e:  # SDK бросает разные типы, ловим широко
            wait = 2 ** attempt
            msg = str(e)
            transient = any(
                s in msg.lower()
                for s in ("rate", "timeout", "429", "500", "502", "503", "concurren")
            )
            if attempt < retries and transient:
                print(f"    повтор через {wait}s ({msg[:70]})")
                time.sleep(wait)
                continue
            print(f"    ! генерация не удалась: {msg[:120]}")
            return None
    return None


# ---------------------------------------------------------------------------
# Запись в Supabase
# ---------------------------------------------------------------------------

def save_insights(rows: list[dict], supabase_url: str, service_key: str) -> bool:
    """Upsert пачкой по ключу (team_id, gw)."""
    if not rows:
        return True
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        # merge-duplicates = обновить существующие вместо ошибки конфликта
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    try:
        resp = requests.post(
            f"{supabase_url.rstrip('/')}/rest/v1/{AI_TABLE}",
            headers=headers,
            params={"on_conflict": "team_id,gw"},
            json=rows,
            timeout=60,
        )
        if not resp.ok:
            print(f"! Supabase отказал: {resp.status_code} {resp.text[:300]}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        print(f"! Не удалось записать в Supabase: {e}")
        return False


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> int:
    load_dotenv_if_present()

    parser = argparse.ArgumentParser(description="Пре-генерация AI-разборов FPL")
    parser.add_argument("--gw", type=int, help="Тур (по умолчанию — ближайший)")
    parser.add_argument("--limit", type=int, help="Обработать только N менеджеров")
    parser.add_argument("--dry-run", action="store_true",
                        help="Не писать в Supabase, показать примеры")
    parser.add_argument("--model", default=env("ZHIPU_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    api_key = env("ZHIPU_API_KEY")
    supabase_url = env("SUPABASE_URL")
    service_key = env("SUPABASE_SERVICE_KEY")
    csv_url = env("ADMIN_CSV_URL")

    if not api_key:
        return fail("Не задан ZHIPU_API_KEY")
    if not csv_url:
        return fail("Не задан ADMIN_CSV_URL")
    if not args.dry_run and not (supabase_url and service_key):
        return fail(
            "Не заданы SUPABASE_URL / SUPABASE_SERVICE_KEY "
            "(или запусти с --dry-run)"
        )

    client = OpenAI(api_key=api_key, base_url=ZHIPU_BASE_URL)

    print("Загружаю админ-таблицу...")
    managers, league_ids = load_admin_sheet(csv_url)
    print(f"  менеджеров: {len(managers)}, дивизионов с League_ID: {len(league_ids)}")

    print("Загружаю bootstrap FPL...")
    boot = fetch_bootstrap()
    players = player_lookup(boot)
    gw = args.gw or detect_next_gw(boot)
    print(f"  игроков в справочнике: {len(players)}; целевой тур: GW{gw}")

    print(f"Определяю пары соперников на GW{gw}...")
    opponents = build_pairs(managers, league_ids, gw)
    print(f"  найдено команд с соперником: {len(opponents)}")
    if not opponents:
        return fail(
            "Ни одной H2H-пары не найдено. Проверь League_ID в админ-таблице "
            f"и что календарь на GW{gw} уже опубликован."
        )

    print(f"Загружаю данные менеджеров ({FPL_WORKERS} потоков)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=FPL_WORKERS) as ex:
        managers = list(ex.map(lambda m: fetch_manager(m, gw), managers))
    by_id = {m.team_id: m for m in managers}

    # Готовим задания только для тех, у кого соперник тоже есть в синдикате
    jobs = []
    for m in managers:
        opp_id = opponents.get(m.team_id)
        opp = by_id.get(opp_id) if opp_id else None
        if opp is None:
            continue
        jobs.append((m, opp, build_prompt(m, opp, players)))

    if args.limit:
        jobs = jobs[: args.limit]
    print(f"К генерации: {len(jobs)} разборов, модель {args.model}")
    if not jobs:
        return fail("Нет ни одной пары, где оба менеджера из синдиката.")

    results, failures = [], 0
    lock = threading.Lock()
    done = 0

    def work(job):
        nonlocal done, failures
        me, opp, prompt = job
        text = generate_insight(client, args.model, prompt)
        with lock:
            done += 1
            if done % 20 == 0 or done == len(jobs):
                print(f"  сгенерировано {done}/{len(jobs)}")
            if text is None:
                failures += 1
                return None
        return {
            "team_id": me.team_id,
            "gw": gw,
            "insight_text": text,
            "model": args.model,
        }

    print(f"Генерирую ({LLM_WORKERS} потоков)...")
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=LLM_WORKERS) as ex:
        for row in ex.map(work, jobs):
            if row:
                results.append(row)
    elapsed = time.time() - started
    print(f"Готово за {elapsed:.0f}s: успешно {len(results)}, ошибок {failures}")

    if args.dry_run:
        print("\n--- dry-run, в базу ничего не пишется ---")
        for row in results[:3]:
            print(f"\nteam_id={row['team_id']} GW{row['gw']}:\n{row['insight_text']}")
        return 0

    print("Пишу в Supabase...")
    # Пачками, чтобы не упереться в лимит размера запроса
    ok = True
    for i in range(0, len(results), 100):
        ok = save_insights(results[i:i + 100], supabase_url, service_key) and ok
    if ok:
        print(f"✓ Сохранено {len(results)} разборов на GW{gw}")
        return 0
    return fail("Часть записей не сохранилась — см. сообщения выше")


def fail(message: str) -> int:
    print(f"ОШИБКА: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
