cmake_minimum_required(VERSION 3.18)
project(stratum_embedded CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED STRATUM_LOG_ENABLED)
    set(STRATUM_LOG_ENABLED 1)
endif()

set(STRATUM_PYTHON_VERSION "{{PYTHON_VERSION}}")
set(STRATUM_PYTHON_INCLUDE "{{PYTHON_INCLUDE}}")
set(STRATUM_PYTHON_LIB_DIR "{{PYTHON_LIB_DIR}}")

add_subdirectory("{{NANOBIND_DIR}}" nanobind EXCLUDE_FROM_ALL)

set(STRATUM_SOURCES
    "{{CORE_INCLUDE_DIR}}/bridge_core.cpp"
    "{{CORE_INCLUDE_DIR}}/stratum_engine.cpp"
    "{{CORE_INCLUDE_DIR}}/metadata_table.cpp"
    "{{CORE_INCLUDE_DIR}}/stratum_pybundle.cpp"
    "{{CORE_INCLUDE_DIR}}/bridge_main.cpp"
)

nanobind_add_module(stratum NB_STATIC ${STRATUM_SOURCES})

target_include_directories(stratum PRIVATE
    "{{CORE_INCLUDE_DIR}}"
    "${STRATUM_PYTHON_INCLUDE}"
)

target_compile_definitions(stratum PRIVATE STRATUM_LOG_ENABLED=${STRATUM_LOG_ENABLED})

target_compile_options(stratum PRIVATE
    -O2
    -Wno-unused-parameter
    -ffunction-sections
    -fdata-sections
)

find_library(log-lib log)
find_library(android-lib android)
find_library(z-lib z)

target_link_libraries(stratum PRIVATE
    ${log-lib}
    ${android-lib}
    ${z-lib}
    "-L${STRATUM_PYTHON_LIB_DIR}"
    "-lpython${STRATUM_PYTHON_VERSION}"
)

target_link_options(stratum PRIVATE
    -Wl,--gc-sections
    -Wl,--as-needed
)

set_target_properties(stratum PROPERTIES
    OUTPUT_NAME "stratum"
    PREFIX      "lib"
    SUFFIX      ".so"
)