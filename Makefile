.PHONY: help build up down logs shell test lint clean

help:
	@echo "make build   - Build the Docker image"
	@echo "make up      - Start the stack (http://localhost:8000)"
	@echo "make down    - Stop the stack"
	@echo "make logs    - Tail container logs"
	@echo "make shell   - Open a shell inside the container"
	@echo "make test    - Run pytest inside the container"
	@echo "make lint    - Run flake8 inside the container"
	@echo "make clean   - Remove container and image"

build:
	docker compose build

up:
	docker compose up -d --build
	@echo "Kiro-Sentry running at http://localhost:8000"

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

shell:
	docker compose exec kiro-sentry /bin/bash

test:
	docker compose run --rm kiro-sentry pytest -q

lint:
	docker compose run --rm kiro-sentry flake8 .

clean:
	docker compose down --rmi local --volumes --remove-orphans
