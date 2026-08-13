.PHONY: up down reset test commercial-test contracts verify capacity-500 logs openapi backup restore
up:
	./scripts/up.sh
down:
	docker compose down
reset:
	./scripts/reset.sh
test:
	./scripts/test.sh
commercial-test:
	docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml --profile commercial-test run --rm acceptance-commercial
contracts:
	./scripts/contract-test.sh
verify:
	./scripts/static-verify.sh
capacity-500:
	./scripts/capacity-gate-500.sh
logs:
	docker compose logs -f api worker payment-emulator provider-emulator
openapi:
	./scripts/export-openapi.sh
backup:
	./scripts/backup.sh
restore:
	@echo 'Use: RESTORE_CONFIRM=YES ./scripts/restore.sh backups/<timestamp>'
