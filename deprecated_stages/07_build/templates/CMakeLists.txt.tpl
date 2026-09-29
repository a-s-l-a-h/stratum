cmake_minimum_required(VERSION 3.18)
project(stratum CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# STRATUM_LOG_LEVEL: 0 = production (LOGD/LOGV fully stripped, zero cost),
# 1 = basic logging, 2 = deep/trace logging. Passed in by 07_build/main.py
# via --log-level. Never edit this file to change the level — use the
# command-line flag instead, so the value stays in one place.
if(NOT DEFINED STRATUM_LOG_ENABLED)
    set(STRATUM_LOG_ENABLED 1)
endif()

set(STRATUM_PYTHON_VERSION "{{PYTHON_VERSION}}")
set(STRATUM_PYTHON_INCLUDE "{{PYTHON_INCLUDE}}")
set(STRATUM_PYTHON_LIB_DIR "{{PYTHON_LIB_DIR}}")

add_subdirectory("{{NANOBIND_DIR}}" nanobind EXCLUDE_FROM_ALL)

# Stratum v9: exactly 4 static C++ files, regardless of how many Java
# classes the metadata table describes. This is why compile time no
# longer grows with the size of the Android API surface you include —
# only the size of the generated metadata_table.cpp DATA does.
set(STRATUM_SOURCES
    "{{CORE_INCLUDE_DIR}}/bridge_core.cpp"
    "{{CORE_INCLUDE_DIR}}/stratum_engine.cpp"
    "{{CORE_INCLUDE_DIR}}/metadata_table.cpp"
    "{{CORE_INCLUDE_DIR}}/bridge_main.cpp"
)

nanobind_add_module(_stratum NB_STATIC ${STRATUM_SOURCES})

target_include_directories(_stratum PRIVATE
    "{{CORE_INCLUDE_DIR}}"
    "${STRATUM_PYTHON_INCLUDE}"
)

target_compile_definitions(_stratum PRIVATE STRATUM_LOG_ENABLED=${STRATUM_LOG_ENABLED})

target_compile_options(_stratum PRIVATE
    -O2
    -Wno-unused-parameter
    -ffunction-sections
    -fdata-sections
)

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(_stratum PRIVATE
    ${log-lib}
    ${android-lib}
    "-L${STRATUM_PYTHON_LIB_DIR}"
    "-lpython${STRATUM_PYTHON_VERSION}"
)

target_link_options(_stratum PRIVATE
    -Wl,--gc-sections
    -Wl,--as-needed
)

set_target_properties(_stratum PROPERTIES
    OUTPUT_NAME "_stratum"
    PREFIX      ""
    SUFFIX      ".so"
)