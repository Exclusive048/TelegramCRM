"""
Утилита для управления миграциями базы данных.

Использование:
    python migrate.py dev          — применить все новые миграции
    python migrate.py generate     — сгенерировать новую миграцию по изменениям моделей
    python migrate.py status       — показать текущее состояние
    python migrate.py rollback     — откатить последнюю миграцию
    python migrate.py reset        — откатить ВСЕ миграции (осторожно!)
    python migrate.py history      — список всех миграций
"""
import sys
import subprocess


def run(cmd: str, capture: bool = False):
    result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if capture:
        return result.stdout.strip()
    return result.returncode


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1].lower()

    # ── migrate dev — применить все миграции ──────────
    if command == "dev":
        print("⚙️  Применяю миграции...")
        code = run("alembic upgrade head")
        if code == 0:
            print("✅ Готово — база данных актуальна.")
        else:
            print("❌ Ошибка при применении миграций.")
            sys.exit(1)

    # ── generate — создать новую миграцию ─────────────
    elif command == "generate":
        if len(sys.argv) < 3:
            print("❌ Укажите название миграции:")
            print("   python migrate.py generate add_tags_to_leads")
            sys.exit(1)
        name = "_".join(sys.argv[2:])
        print(f"⚙️  Генерирую миграцию: {name}")
        code = run(f'alembic revision --autogenerate -m "{name}"')
        if code == 0:
            print("✅ Миграция создана в alembic/versions/")
            print("   Проверьте файл перед применением!")
        else:
            print("❌ Ошибка при генерации.")
            sys.exit(1)

    # ── status — текущее состояние ────────────────────
    elif command == "status":
        print("📋 Текущая версия БД:")
        run("alembic current")
        print("\n📋 Ожидают применения:")
        run("alembic heads")

    # ── rollback — откатить последнюю миграцию ────────
    elif command == "rollback":
        current = run("alembic current", capture=True)
        print(f"⚠️  Откатываю последнюю миграцию (текущая: {current})")
        confirm = input("Продолжить? [y/N]: ")
        if confirm.lower() != "y":
            print("Отменено.")
            return
        code = run("alembic downgrade -1")
        if code == 0:
            print("✅ Откат выполнен.")
        else:
            print("❌ Ошибка при откате.")
            sys.exit(1)

    # ── reset — сбросить всё ──────────────────────────
    elif command == "reset":
        print("🚨 ВНИМАНИЕ: это удалит ВСЕ таблицы и данные!")
        confirm = input("Введите 'reset' для подтверждения: ")
        if confirm != "reset":
            print("Отменено.")
            return
        code = run("alembic downgrade base")
        if code == 0:
            print("✅ База сброшена. Примените миграции заново: python migrate.py dev")
        else:
            print("❌ Ошибка при сбросе.")
            sys.exit(1)

    # ── history — история миграций ────────────────────
    elif command == "history":
        print("📋 История миграций:")
        run("alembic history --verbose")

    else:
        print(f"❌ Неизвестная команда: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
