.PHONY: setup test bench

# The index store is native sqlite (stdlib) — no system libraries to build.
setup:
	uv sync

test:
	uv run pytest tests/ -v

# Run the perf benchmark harness with default corpus sizes and record the
# result under bench/results/<backend-label>.json. Override sizes on the CLI,
# e.g.  make bench BENCH_ARGS="--n 10000 --k 1000"
BENCH_ARGS ?=
bench:
	uv run bench.py --backend-label store $(BENCH_ARGS)
