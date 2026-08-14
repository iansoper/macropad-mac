CIRCUITPY ?= /Volumes/CIRCUITPY
PY := .venv/bin/python3

.PHONY: setup deploy libs run ports whoami test

setup:                ## create venv and install agent deps
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r agent/requirements.txt

libs:                 ## install CircuitPython libs onto the pad
	circup install adafruit_macropad adafruit_display_text adafruit_display_shapes

deploy:               ## copy firmware to the pad
	@test -d $(CIRCUITPY) || (echo "CIRCUITPY not mounted at $(CIRCUITPY)"; exit 1)
	cp firmware/boot.py firmware/code.py $(CIRCUITPY)/
	@echo "Copied. If boot.py changed, UNPLUG AND REPLUG the pad."

run:                  ## run the agent
	$(PY) agent/macropad_agent.py

ports:                ## list candidate serial ports
	$(PY) agent/macropad_agent.py ports

whoami:               ## print frontmost app bundle IDs
	$(PY) agent/macropad_agent.py whoami

test:
	$(PY) -m pytest -q
