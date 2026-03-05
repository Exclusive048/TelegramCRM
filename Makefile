.PHONY: lint typecheck encoding migrations smoke pytest health check

lint:
	python -m ruff check .

typecheck:
	python -X utf8 -m mypy app main.py

encoding:
	python -m scripts.check_encoding

migrations:
	python -m scripts.migrations_check

smoke:
	python -m scripts.smoke

pytest:
	pytest

health:
	curl -f http://localhost:8000/health

check: lint typecheck encoding migrations smoke pytest
