MACROPAD ?= /Volumes/MACROPAD
PY := .venv/bin/python3
# circup is not in requirements*.txt — the README installs it globally. Prefer a
# venv copy when one exists, otherwise fall back to PATH so both setups work.
CIRCUP := $(if $(wildcard .venv/bin/circup),.venv/bin/circup,circup)
LIBS := adafruit_macropad adafruit_display_text adafruit_display_shapes
UI_PORT ?= 8765

.PHONY: setup dev deploy libs libs-reset run ui ports whoami test

# A stale mount point left by an unclean unplug is a real directory, so a bare
# `test -d` passes against nothing at all and the recipe then fails somewhere
# far less obvious. boot_out.txt only exists on a mounted CircuitPython volume,
# which makes it the thing worth checking.
define require_pad
	@test -f $(MACROPAD)/boot_out.txt || { \
		echo "No CircuitPython drive mounted at $(MACROPAD)."; \
		test -d $(MACROPAD) && { \
			echo "  $(MACROPAD) exists but nothing is mounted on it — a stale mount"; \
			echo "  point left by an unclean unplug. macOS will not remount over it."; \
			echo "  Clear it, then replug the pad:  sudo rmdir $(MACROPAD)"; \
		}; \
		exit 1; \
	}
	@test -w $(MACROPAD) || { \
		echo "$(MACROPAD) is mounted read-only — the firmware holds the write lock."; \
		echo "  Reset the pad, or drop the storage.remount() call in boot.py."; \
		exit 1; \
	}
endef

define require_circup
	@command -v $(CIRCUP) >/dev/null 2>&1 || { \
		echo "circup not found. Install it:  pip3 install circup"; \
		exit 1; \
	}
endef

setup:                ## create venv and install agent deps
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r agent/requirements.txt

dev:                  ## same, plus the test dependencies
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r agent/requirements-dev.txt

libs:                 ## install CircuitPython libs onto the pad (matches the board's version)
	$(require_circup)
	$(CIRCUP) --path $(MACROPAD) install $(LIBS)

libs-reset:           ## wipe lib/ and reinstall — use after a CircuitPython major upgrade
	$(require_pad)
	$(require_circup)
	rm -rf $(MACROPAD)/lib
	$(CIRCUP) --path $(MACROPAD) install $(LIBS)

deploy:               ## copy firmware to the pad
	$(require_pad)
	cp firmware/boot.py firmware/code.py $(MACROPAD)/
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
