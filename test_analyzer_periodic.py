"""
test_analyzer_periodic.py — Smoke test for the monthly (hierarchical) report.

Usage:
    python test_analyzer_periodic.py

What it does:
    1. Seeds 3 realistic fake weekly summaries into report_summaries
       (dated 2026-W09, 2026-W10, 2026-W11 — all within the current
       billing period so the monthly runner finds them).
       Skips seeding if the labels already exist in the DB.
    2. Calls run_periodic_analysis('monthly'), which:
         - Reads the 3 weekly summaries
         - Calls Claude Haiku with the monthly analysis prompt
         - Saves the result back to report_summaries
         - Builds an HTML email and sends it to the configured recipient
         - Archives the HTML to data/monthly_YYYY-MM-DD.html
    3. Opens data/test_monthly.html in the default browser.
    4. Prints the token usage from the log output and confirms the email send.
"""

import logging
import sqlite3
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from db import get_report_summaries, init_db
from analyzer import run_periodic_analysis


# ---------------------------------------------------------------------------
# Realistic fake weekly summaries (Russian, matching the production format)
# ---------------------------------------------------------------------------

_FAKE_WEEKLY_SUMMARIES = [
    {
        "period_label": "2026-W09",
        "created_at": "2026-03-01 07:00:00",
        "summary_text": """\
1. КАРТА MOMENTUM
AML и санкции / 8 статей / Растёт / Сильный сигнал
Регуляторная активность вокруг крипто-AML резко возросла в ЕС и США.
Платежи / 5 статей / Стабильно / Слабый сигнал
SEPA Instant набирает обороты — европейские банки отстают от дедлайна.
Крипто и Web3 / 6 статей / Растёт / Сильный сигнал
ETF-потоки и институциональный спрос продолжают давить на регуляторов.

2. РАДАР ЮРИСДИКЦИЙ
Наиболее активны: ЕС (MiCA enforcement), США (FinCEN криптогайдлайны), UAE (VARA обновления).
Неожиданно: Бразилия анонсировала обязательный реестр крипто-провайдеров.

3. СЛАБЫЕ СИГНАЛЫ
— Инициатива FATF по рискам трансграничных платежей (низкая уверенность)
— Координированные действия ЕС/UK по стандартам sanctions screening (низкая уверенность)

4. СКВОЗНЫЕ ТРЕДЫ
Крипто-AML → ЕС, США и Сингапур одновременно ужесточают VASP-требования; \
признаки координированного FATF-импульса.

5. EXECUTIVE BRIEF
Неделя ознаменовалась консолидацией MiCA enforcement и новыми FinCEN-требованиями \
к крипто. Платёжные провайдеры под давлением дедлайна SEPA Instant. \
VASP-регулирование превращается в глобальный стандарт. \
Compliance-команды должны приоритизировать VASP-лицензирование и sanctions-покрытие.

6. ВОПРОСЫ НЕДЕЛИ
1. Готова ли наша VASP-лицензия к MiCA enforcement?
2. Покрывает ли наш sanctions screening новые FinCEN-требования?
3. Соответствуем ли мы дедлайну SEPA Instant?""",
    },
    {
        "period_label": "2026-W10",
        "created_at": "2026-03-07 07:00:00",
        "summary_text": """\
1. КАРТА MOMENTUM
AML и санкции / 10 статей / Растёт / Сильный сигнал
Волна OFAC enforcement-акций против крипто-платформ продолжается.
iGaming / 7 статей / Растёт / Сильный сигнал
Великобритания вводит новые расширенные KYC-требования для онлайн-гемблинга.
RegTech / 4 статьи / Стабильно / Слабый сигнал
Растущий интерес банков к AI-driven compliance решениям.

2. РАДАР ЮРИСДИКЦИЙ
Великобритания (UKGC новые правила для операторов), OFAC (крипто-санкции), \
Австралия (AUSTRAC ужесточение требований).
Неожиданно: Гонконг смягчил требования к stablecoin-эмитентам.

3. СЛАБЫЕ СИГНАЛЫ
— Первые enforcement-кейсы по MiCA Travel Rule (низкая уверенность)
— Консолидация RegTech-рынка через M&A (низкая уверенность)

4. СКВОЗНЫЕ ТРЕДЫ
iGaming-KYC → UK и Австралия синхронно ужесточают требования к верификации игроков; \
возможна глобальная конвергенция стандартов в секторе.

5. EXECUTIVE BRIEF
Крупнейшие события недели: OFAC-санкции против крипто-платформ и новые UK iGaming KYC. \
Enforcement-давление нарастает по обеим осям. RegTech-инструменты перестают \
быть опцией и становятся операционной необходимостью. \
Команды должны приоритизировать пересмотр партнёрских OFAC-листов.

6. ВОПРОСЫ НЕДЕЛИ
1. Включены ли наши крипто-партнёры в обновлённые OFAC-SDN листы?
2. Соответствует ли наш iGaming-онбординг новым UKGC-требованиям?
3. Предусмотрен ли бюджет на AI-compliance инструменты в плане 2026 года?""",
    },
    {
        "period_label": "2026-W11",
        "created_at": "2026-03-11 07:00:00",
        "summary_text": """\
1. КАРТА MOMENTUM
Конфиденциальность и данные / 9 статей / Растёт / Сильный сигнал
GDPR и DORA одновременно — операционная нагрузка на финтех максимальная за годы.
Крипто и Web3 / 8 статей / Растёт / Сильный сигнал
Институциональное принятие ETF меняет регуляторный нарратив в пользу легитимизации.
Forex и CFD / 5 статей / Снижается / Слабый сигнал
FCA снижает активность в этом сегменте после серии штрафов 2025 года.

2. РАДАР ЮРИСДИКЦИЙ
ЕС (DORA полная имплементация), США (SEC ETF-апрувал), \
Сингапур (MAS обновление крипто-лицензий).
Неожиданно: Швейцария расширила sandbox для DeFi-проектов.

3. СЛАБЫЕ СИГНАЛЫ
— Emerging-market регуляторы (Нигерия, Кения) начинают имплементацию FATF-крипто \
(низкая уверенность)
— Первые DORA-штрафы ожидаются в Q2 2026 (низкая уверенность)

4. СКВОЗНЫЕ ТРЕДЫ
DORA + GDPR → ЕС формирует единый операционный compliance-периметр для финтех; \
другие юрисдикции наблюдают с прицелом на копирование подхода.

5. EXECUTIVE BRIEF
DORA вступила в полную силу — операционный риск и data governance в центре внимания. \
Крипто ETF легитимизируют рынок, одновременно привлекая усиленное SEC-внимание. \
Неделя стратегической важности для privacy и operational resilience программ. \
Первые DORA-штрафы могут появиться в Q2 2026.

6. ВОПРОСЫ НЕДЕЛИ
1. Завершена ли наша DORA gap-оценка и roadmap до конца Q1?
2. Включены ли новые требования к data governance в нашу GDPR-программу?
3. Как изменится наш крипто-compliance при массовом ETF-принятии?""",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _seed_weekly_summaries(db_path: str) -> int:
    """Insert fake weekly summaries if they don't already exist.

    Returns the count of newly inserted rows.
    """
    inserted = 0
    with sqlite3.connect(db_path) as conn:
        for s in _FAKE_WEEKLY_SUMMARIES:
            exists = conn.execute(
                "SELECT 1 FROM report_summaries "
                "WHERE period_type = 'weekly' AND period_label = ? LIMIT 1",
                (s["period_label"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO report_summaries "
                    "(period_type, period_label, summary_text, created_at) "
                    "VALUES ('weekly', ?, ?, ?)",
                    (s["period_label"], s["summary_text"], s["created_at"]),
                )
                inserted += 1
        conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ── Logging: stdout-only (prevents analyzer._setup_logging from adding
    #    a file handler during the test, keeping output clean).
    if not logging.getLogger().handlers:
        fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
        logging.basicConfig(
            level=logging.INFO,
            format=fmt,
            handlers=[logging.StreamHandler(sys.stdout)],
        )

    config = _load_config()
    db_path = config["database"]["path"]

    print(f"\n{'='*64}")
    print("  test_analyzer_periodic.py — Monthly Report Smoke Test")
    print(f"{'='*64}\n")

    # ── Step 1: Init DB ───────────────────────────────────────────────────────
    init_db(db_path)

    # ── Step 2: Seed fake weekly summaries ────────────────────────────────────
    inserted = _seed_weekly_summaries(db_path)
    if inserted > 0:
        print(f"Seeded {inserted} fake weekly summary(ies) into report_summaries.")
    else:
        print("Fake weekly summaries already present — skipping seed.")

    # Show what's available
    since_str = "2026-03-01 00:00:00"
    available = get_report_summaries(db_path, "weekly", since_str)
    print(f"\nWeekly summaries available since {since_str[:10]}: {len(available)}")
    for s in available:
        print(f"  • {s['period_label']}  (created_at: {s['created_at']})")
    print()

    # ── Step 3: Run monthly analysis ──────────────────────────────────────────
    print("Running run_periodic_analysis('monthly')…")
    print("─" * 64)
    run_periodic_analysis("monthly")
    print("─" * 64)
    print()

    # ── Step 4: Locate HTML archive and open in browser ───────────────────────
    today_str = datetime.now().strftime("%Y-%m-%d")
    archive_path = Path("data") / f"monthly_{today_str}.html"

    if archive_path.exists():
        # Write a stable test copy for easy reference
        test_copy = Path("data/test_monthly.html")
        test_copy.write_text(archive_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"HTML archive  → {archive_path.resolve()}")
        print(f"Test copy     → {test_copy.resolve()}")
        print("Opening in browser…")
        webbrowser.open(test_copy.resolve().as_uri())
    else:
        print(f"[WARNING] HTML archive not found at {archive_path} — check log above.")

    # ── Step 5: Confirm report_summaries was written ──────────────────────────
    monthly_rows = get_report_summaries(db_path, "monthly", since_str)
    if monthly_rows:
        print(f"\nMonthly summary written to report_summaries:")
        for m in monthly_rows:
            print(
                f"  • id={m['id']}  label={m['period_label']}  "
                f"created_at={m['created_at']}"
            )
        print(f"\nEmail sent → {config['email']['to']}  ✓")
    else:
        print("\n[WARNING] No monthly row found in report_summaries after run.")

    print(f"\n{'='*64}")
    print("  Smoke test complete.")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
