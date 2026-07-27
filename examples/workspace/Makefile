# Example workspace.
#
# `make` builds the fictional project tree under projects/ with fixed commit dates
# and fixed file timestamps, so the snapshot is byte-identical on every machine.

AS_OF ?= 2026-03-16
NEXTBRIEF ?= nextbrief

.PHONY: all build sense render clean check

all: build

build:
	./scripts/build-example.sh

# Stage 1: no model, no network. Reads the tree, writes state/snapshot.json.
sense: build
	$(NEXTBRIEF) --workspace . sense --as-of $(AS_OF)

# Stage 3: turns state/brief.json into BRIEF.md and BRIEF.html.
render:
	$(NEXTBRIEF) --workspace . render

# Every hand-authored JSONC file in this workspace must parse.
check:
	cd ../.. && PYTHONPATH=src python3 -c "from nextbrief.jsonc import load_jsonc; \
	[load_jsonc(p) for p in ['examples/workspace/registry.jsonc', 'examples/workspace/config.jsonc']]" \
	&& echo "jsonc ok"

clean:
	rm -rf projects state log BRIEF.md BRIEF.html
