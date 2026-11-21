# Host build of the pure-C core: `make test` compiles and runs the unit tests,
# `make cli` builds proto_cli for the Python conformance test.
CORE := ../components/padcore
BUILD ?= build
CC ?= cc
CFLAGS := -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all -I$(CORE)
CORE_SRCS := $(CORE)/proto.c $(CORE)/padq.c
CORE_HDRS := $(wildcard $(CORE)/*.h) check.h

.PHONY: test cli clean

test: $(BUILD)/test_proto $(BUILD)/test_padq
	$(BUILD)/test_proto
	$(BUILD)/test_padq
	@echo "ALL TESTS PASSED"

cli: $(BUILD)/proto_cli

$(BUILD)/%: %.c $(CORE_SRCS) $(CORE_HDRS)
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ $< $(CORE_SRCS)

clean:
	rm -rf $(BUILD)
