# Pure C with no ESP-IDF headers, so the same sources build for the host tests.
idf_component_register(INCLUDE_DIRS ".")
