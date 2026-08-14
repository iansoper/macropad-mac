CIRCUITPY ?= /Volumes/CIRCUITPY
PY := .venv/bin/python3
UI_PORT ?= 8765

.PHONY: setup dev deploy libs libs-reset run ui ports whoami test

setup:                ## create venv and install agent deps
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r agent/requirements.txt

dev:                  ## same, plus the test dependencies
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r agent/requirements-dev.txt

libs:                 ## install CircuitPython libs onto the pad (matches the board's version)
	circup install adafruit_macropad adafruit_display_text adafruit_display_shapes

libs-reset:           ## wipe lib/ and reinstall — use after a CircuitPython major upgrade
	@test -d $(CIRCUITPY) || (echo "CIRCUITPY not mounted at $(CIRCUITPY)"; exit 1)
	rm -rf $(CIRCUITPY)/lib
	circup install adafruit_macropad adafruit_display_text adafruit_display_shapes

deploy:               ## copy firmware to the pad
	@test -d $(CIRCUITPY) || (echo "CIRCUITPY not mounted at $(CIRCUITPY)"; exit 1)
	cp firmware/boot.py firmware/code.py $(CIRCUITPY)/
	@echo "Copied. If boot.py changed, UNPLUG AND REPLUG the pad."

run:                  ## run the agent (serves the editor too)
	$(PY) agent/macropad_agent.py

ui:                   ## open the editor in a browser (agent must be running)
	@open http://127.0.0.1:$(UI_PORT)

ports:                ## list candidate serial ports
	$(PY) agent/macropad_agent.py ports

whoami:               ## print frontmost app bundle IDs
	$(PY) agent/macropad_agent.py whoami

test:                 ## run the test suite (needs `make dev` first)
	$(PY) -m pytest -q
