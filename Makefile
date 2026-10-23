.PHONY: all

# Redeploy a running server (see "Redeploy" in README.md). GoatCounter is
# stopped during the build to free RAM, and restarted even if the build fails.
all:
	git pull
	docker compose stop goatcounter
	docker compose up -d --build web || { docker compose start goatcounter; exit 1; }
	docker compose start goatcounter
