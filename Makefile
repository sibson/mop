LEVELDB_PREFIX ?= $(shell brew --prefix leveldb 2>/dev/null)

.PHONY: setup plyvel test

# Full environment setup: resolve deps, then rebuild plyvel matched to leveldb.
setup: plyvel

# uv sync installs plyvel from a default build that assumes an -frtti leveldb.
# Homebrew's leveldb is -fno-rtti, so we force-rebuild plyvel with the matching
# flag; otherwise `import plyvel` dies on a missing leveldb::Comparator typeinfo.
plyvel:
	uv sync
	CPATH="$(LEVELDB_PREFIX)/include" \
	LIBRARY_PATH="$(LEVELDB_PREFIX)/lib" \
	CFLAGS="-fno-rtti" CXXFLAGS="-fno-rtti" \
	uv pip install --force-reinstall --no-binary plyvel --no-cache plyvel
	uv run python -c "import plyvel; print('plyvel OK')"

test:
	uv run pytest tests/ -v
